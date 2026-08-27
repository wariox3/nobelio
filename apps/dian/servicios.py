"""
Servicios de orquestación del ciclo de vida de un documento electrónico.

Encadena el pipeline completo:
    documento → XML UBL → CUFE → firma XAdES → envío a la DIAN → estado

Cada paso actualiza el estado del documento y guarda los artefactos (CUFE, XML
firmado, respuesta DIAN). Las credenciales (certificado) y el cliente SOAP se
pueden inyectar para facilitar las pruebas sin red ni .p12 reales.
"""
from __future__ import annotations

import re

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.dian import firma, soap, ubl
from apps.documentos.models import (
    Documento,
    DocumentoError,
    DocumentoEstado,
    DocumentoTipo,
)
from apps.emisores.servicios import (
    MENSAJE_EMISOR_INACTIVO,
    certificado_activo,
    motivo_no_puede_emitir,
)


def _estado(nombre: str) -> DocumentoEstado:
    """Devuelve la instancia de estado por su nombre (FK de Documento.estado)."""
    return DocumentoEstado.objects.get(nombre=nombre)


class ErrorEmision(Exception):
    """Error en el proceso de emisión de un documento."""


MENSAJE_SIN_ACUSE = (
    "La DIAN todavía no ha acreditado este documento: no hay ApplicationResponse "
    "con el que armar el contenedor. Consulte el estado y espere a que quede "
    "aceptado."
)


# "Regla: <regla>, <Tipo>: <mensaje>" (Rechazo / Notificación).
_RE_ERROR = re.compile(
    r"Regla:\s*(?P<regla>[^,]+),\s*(?P<tipo>[^:]+):\s*(?P<mensaje>.*)",
    re.IGNORECASE | re.DOTALL,
)


def _parsear_error(texto: str) -> dict:
    """Parsea un error de la DIAN a ``{regla, tipo, mensaje}``."""
    m = _RE_ERROR.match(texto.strip())
    if not m:
        return {"regla": "", "tipo": DocumentoError.Tipo.OTRO, "mensaje": texto.strip()}
    etiqueta = m.group("tipo").strip().lower()
    if etiqueta.startswith("rechaz"):
        tipo = DocumentoError.Tipo.RECHAZO
    elif etiqueta.startswith("notif"):
        tipo = DocumentoError.Tipo.NOTIFICACION
    else:
        tipo = DocumentoError.Tipo.OTRO
    return {"regla": m.group("regla").strip(), "tipo": tipo, "mensaje": m.group("mensaje").strip()}


def _ya_procesado(respuesta) -> bool:
    """La regla 90 de la DIAN ("Documento procesado anteriormente"), **sola**.

    Dice que la DIAN ya tiene ese CUFE de un envío previo, no que lo haya
    aprobado: el envío duplicado vuelve con código 99 tanto si aquella vez se
    aceptó como si se rechazó. Solo cuenta como aceptación cuando llega sin
    ningún otro rechazo; si la acompañan reglas de contenido (FAJ26, FAK26…),
    lo que hubo antes fue un rechazo y darlo por aceptado dejaría el documento
    con un estado que la DIAN no comparte —y, al ser terminal, sin forma de
    corregirlo ni de borrarlo—.

    Las notificaciones no estorban: son informativas y no rechazan nada.
    """
    procesado = False
    for texto in respuesta.errores:
        datos = _parsear_error(texto)
        if datos["tipo"] == DocumentoError.Tipo.NOTIFICACION:
            continue
        if "procesado anteriormente" in datos["mensaje"].lower():
            procesado = True
            continue
        return False
    return procesado


def _set_pruebas_cerrado(respuesta) -> bool:
    """La DIAN dice que el Set de Pruebas ya está aceptado.

    ``SendTestSetAsync`` deja de admitir envíos en cuanto la habilitación se
    aprueba y responde "Set de prueba con identificador <uuid> se encuentra
    Aceptado" —sin ``ErrorMessage``, así que no es un rechazo del documento:
    es que la operación ya no aplica y toca pasarse a ``SendBillSync``.
    """
    textos = [respuesta.descripcion_estado, *respuesta.errores]
    return any(
        "set de prueba" in t.lower() and "aceptado" in t.lower()
        for t in textos if t
    )


def _marcar_habilitacion_superada(software, emisor):
    """Deja constancia de que la habilitación terminó.

    Es el único momento en que la DIAN lo dice por sí misma; hasta ahora ambas
    banderas se marcaban a mano y olvidarlas dejaba al emisor enviando al Set
    de Pruebas para siempre.
    """
    if not software.set_pruebas_aceptado:
        software.set_pruebas_aceptado = True
        software.save(update_fields=["set_pruebas_aceptado", "actualizado_en"])
    if not emisor.habilitado_facturacion:
        emisor.habilitado_facturacion = True
        emisor.save(update_fields=["habilitado_facturacion", "actualizado_en"])


def _guardar_respuesta(documento, respuesta):
    """Registra el resultado DIAN: SOAP crudo en B2 y los rechazos como filas.

    El ``respuesta_archivo`` se asigna sin guardar (el caller hace ``save``);
    las filas ``DocumentoError`` se reemplazan de inmediato.
    """
    if respuesta.xml_crudo:
        documento.respuesta_archivo.save(
            f"{documento.numero}.dian.xml",
            ContentFile(respuesta.xml_crudo.encode("utf-8")),
            save=False,
        )
    # Solo se guardan los rechazos (las notificaciones son informativas).
    documento.errores.all().delete()
    filas = []
    for e in respuesta.errores:
        datos = _parsear_error(e)
        if datos["tipo"] == DocumentoError.Tipo.NOTIFICACION:
            continue
        filas.append(DocumentoError(documento=documento, **datos))
    DocumentoError.objects.bulk_create(filas)


# Campos del documento que se actualizan al registrar una respuesta de la DIAN.
_CAMPOS_RESPUESTA = ["respuesta_archivo"]


def _software_activo_emisor(emisor):
    software = emisor.softwares.filter(activo=True).first()
    if software is None:
        raise ErrorEmision("El emisor no tiene un software DIAN activo.")
    return software


def _certificado_activo_emisor(emisor):
    """El certificado con el que firmar, comprobando que se pueda usar.

    Misma regla que al crear el documento (``motivo_no_puede_emitir``): sin ella
    se podría firmar con un certificado vencido y la DIAN rechazaría el envío.
    """
    motivo = motivo_no_puede_emitir(emisor)
    if motivo:
        raise ErrorEmision(motivo)
    return certificado_activo(emisor)


def _software_activo(documento):
    return _software_activo_emisor(documento.emisor)


def _certificado_activo(documento):
    return _certificado_activo_emisor(documento.emisor)


def construir_firmador(documento, *, llave=None, certificado=None, cadena=None):
    """Crea el FirmadorXAdES, cargando el .p12 del emisor si no se inyecta."""
    return construir_firmador_emisor(
        documento.emisor, llave=llave, certificado=certificado, cadena=cadena
    )


def construir_firmador_emisor(emisor, *, llave=None, certificado=None, cadena=None):
    """Igual que ``construir_firmador`` pero partiendo del emisor."""
    if llave is None or certificado is None:
        cert_modelo = _certificado_activo_emisor(emisor)
        with cert_modelo.archivo.open("rb") as fh:
            llave, certificado, cadena = firma.cargar_pkcs12(fh.read(), cert_modelo.clave)
    return firma.FirmadorXAdES(
        llave, certificado, cadena=cadena,
        policy_id=settings.DIAN_POLICY_ID,
        policy_hash=settings.DIAN_POLICY_HASH,
        policy_name=settings.DIAN_POLICY_NAME,
    )


def generar_y_firmar(documento, *, firmador=None, ambiente=None, **cred):
    """Genera el XML UBL, calcula el CUFE y firma el documento.

    Guarda ``cufe_cude`` y ``xml_firmado`` y deja el documento en estado FIRMADO.
    Devuelve los bytes del XML firmado.

    La ``hora_emision`` se fija aquí, en el momento de firmar, y la
    ``fecha_emision`` tiene que ser la de hoy (regla FAD09): lo que el XML
    declara como emisión y lo que la firma sella como ``SigningTime`` son el
    mismo acto.
    """
    ambiente = ambiente if ambiente is not None else settings.DIAN_ENVIRONMENT

    bloqueados = {
        DocumentoEstado.Nombre.FIRMADO: "El documento ya está firmado.",
        DocumentoEstado.Nombre.ENVIADO: "El documento ya fue enviado a la DIAN.",
        DocumentoEstado.Nombre.ACEPTADO: "El documento ya fue aceptado por la DIAN.",
    }
    if documento.estado_id and documento.estado.nombre in bloqueados:
        raise ErrorEmision(bloqueados[documento.estado.nombre])

    # Firmar es emitir: el SigningTime es de ahora, así que un IssueDate de otro
    # día es un rechazo seguro (regla FAD09). Se corta antes de construir el XML
    # porque el documento ya tiene su consecutivo reservado y el rechazo lo
    # gastaría. El serializer valida lo mismo al crear, pero entre crear y
    # firmar puede haber pasado un día: aquí es donde la comparación es cierta.
    hoy = timezone.localdate()
    if documento.fecha_emision != hoy:
        raise ErrorEmision(
            f"El documento tiene fecha de emisión {documento.fecha_emision} y se "
            f"firmaría hoy ({hoy}); la DIAN lo rechazaría (regla FAD09). "
            "Actualice la fecha de emisión antes de emitir."
        )

    # La hora sí se corrige en vez de exigirse: emitir y firmar son el mismo
    # acto, así que la hora de emisión es la de ahora y no la que trajera el
    # documento desde que se creó. Va antes de construir el XML porque entra en
    # el CUFE, y la fecha ya se comprobó justo arriba (así el par fecha+hora que
    # se firma es coherente).
    documento.hora_emision = timezone.localtime().time()

    # Dar de baja un emisor (activo=False) tiene que cortar la emisión: es la
    # forma de suspender a un cliente sin tocar las credenciales de su cuenta.
    # El certificado no se exige aquí sino en `construir_firmador`, que es quien
    # lo necesita (las pruebas inyectan el firmador y no cargan .p12).
    if not documento.emisor.activo:
        raise ErrorEmision(MENSAJE_EMISOR_INACTIVO)

    software = _software_activo(documento)

    codigo_tipo = documento.documento_tipo.codigo
    # La factura y el documento soporte se numeran con resolución: sin ella no
    # hay sts:InvoiceControl que emitir. Cada uno con la suya, que la DIAN
    # autoriza por separado.
    if codigo_tipo in DocumentoTipo.CODIGOS_CON_RESOLUCION and documento.resolucion is None:
        raise ErrorEmision(
            f"{documento.documento_tipo.nombre} no tiene resolución de "
            "numeración asociada."
        )
    if (
        codigo_tipo in DocumentoTipo.CODIGOS_CON_REFERENCIA
        and documento.documento_referencia is None
    ):
        raise ErrorEmision("La nota debe referenciar el documento que corrige.")

    # El identificador se recalcula en cada firma. `construir` reutiliza el
    # `cufe_cude` que ya tenga el documento, y la `hora_emision` que acaba de
    # fijarse arriba entra en el CUFE/CUDE/CUDS: al re-firmar (p. ej. tras un
    # rechazo) el XML saldría con el hash del intento anterior y su propio
    # contenido no lo reproduciría -> rechazo FAD06/DSAD06.
    documento.cufe_cude = ""

    constructor = ubl.constructor_para(
        documento,
        software=software,
        resolucion=documento.resolucion,
        ambiente=ambiente,
        clave_tecnica=documento.resolucion.clave_tecnica if documento.resolucion else "",
    )
    xml = constructor.generar_xml()
    documento.cufe_cude = constructor.cufe

    if firmador is None:
        firmador = construir_firmador(documento, **cred)
    xml_firmado = firmador.firmar(xml)

    documento.xml_archivo.save(
        f"{documento.numero}.xml", ContentFile(xml_firmado), save=False
    )
    documento.estado = _estado(DocumentoEstado.Nombre.FIRMADO)
    documento.save(update_fields=[
        "cufe_cude", "hora_emision", "xml_archivo", "estado", "actualizado_en",
    ])
    return xml_firmado


def generar_attached_document(documento, *, firmador=None, ambiente=None,
                              exigir_acuse=False, **cred):
    """Arma y firma el AttachedDocument: el paquete que se entrega al comprador.

    Envuelve el XML firmado y el ApplicationResponse de la DIAN en un solo
    documento. No se guarda: se genera cuando se pide, porque no contiene nada
    que no esté ya en el XML del documento y en su respuesta.

    Con ``exigir_acuse`` falla si la DIAN todavía no ha respondido. Se usa al
    notificar al adquiriente —donde entregar un documento sin validar sería
    engañoso— y no al descargarlo, donde el emisor puede querer verlo antes.
    """
    ambiente = ambiente if ambiente is not None else settings.DIAN_ENVIRONMENT
    if not documento.xml_archivo:
        raise ErrorEmision("El documento no está firmado; no hay nada que adjuntar.")
    if not documento.cufe_cude:
        raise ErrorEmision("El documento no tiene CUFE; emítalo primero.")

    # El acuse es lo que prueba la validación. Sin él el contenedor se arma
    # igual —sirve para entregar el documento—, pero no acredita nada.
    acuse = b""
    if documento.respuesta_archivo:
        with documento.respuesta_archivo.open("rb") as fh:
            acuse = soap.extraer_application_response(fh.read())
    if exigir_acuse and not acuse:
        raise ErrorEmision(MENSAJE_SIN_ACUSE)

    constructor = ubl.ConstructorAttachedDocument(
        documento,
        software=_software_activo(documento),
        ambiente=ambiente,
        xml_documento=documento.leer_xml(),
        application_response=acuse,
    )
    xml = constructor.generar_xml()
    if firmador is None:
        firmador = construir_firmador(documento, **cred)
    return firmador.firmar(xml)


def construir_cliente_emisor(emisor, ambiente, *, llave=None, certificado=None):
    """Crea el ClienteDian con la URL del ambiente y el certificado del emisor."""
    if llave is None or certificado is None:
        cert_modelo = _certificado_activo_emisor(emisor)
        with cert_modelo.archivo.open("rb") as fh:
            llave, certificado, _ = firma.cargar_pkcs12(fh.read(), cert_modelo.clave)
    url = settings.DIAN_WSDL[ambiente].replace("?wsdl", "")
    return soap.ClienteDian(url, llave, certificado)


def construir_cliente(documento, ambiente, *, llave=None, certificado=None):
    """Crea el ClienteDian para el emisor del documento."""
    return construir_cliente_emisor(
        documento.emisor, ambiente, llave=llave, certificado=certificado,
    )


def consultar_rangos_numeracion(emisor, *, cliente=None, ambiente=None,
                                software=None, **cred):
    """Consulta los rangos de numeración (resoluciones) del emisor en la DIAN.

    Usa el software DIAN activo del emisor. El WS pide por separado el NIT del
    OFE y el del proveedor tecnológico; en software propio son el mismo, así que
    va el del emisor en los dos. Devuelve un ``soap.RespuestaRangos`` con el
    código/descripción de la DIAN y los rangos (cada uno con su clave técnica).
    """
    ambiente = ambiente if ambiente is not None else settings.DIAN_ENVIRONMENT
    software = software or _software_activo_emisor(emisor)
    if cliente is None:
        cliente = construir_cliente_emisor(emisor, ambiente, **cred)
    return cliente.consultar_rangos_numeracion(
        emisor.numero_identificacion,
        emisor.numero_identificacion,
        software.identificador,
    )


def enviar_a_dian(documento, *, cliente=None, ambiente=None, **cred):
    """Empaqueta y envía el XML firmado a la DIAN; actualiza el estado.

    Usa SendTestSetAsync (con el TestSetId del software) solo mientras se está
    en habilitación y el Set de Pruebas aún NO ha sido aceptado. Una vez
    aceptado (``software.set_pruebas_aceptado``) o en producción, usa
    SendBillSync (síncrono).

    Si la DIAN responde que el Set de Pruebas ya está aceptado, se marcan las
    banderas de habilitación y el documento sale por SendBillSync en el mismo
    envío (ver ``_set_pruebas_cerrado``).
    """
    ambiente = ambiente if ambiente is not None else settings.DIAN_ENVIRONMENT
    if documento.estado_id and documento.estado.nombre == DocumentoEstado.Nombre.ACEPTADO:
        raise ErrorEmision("El documento ya fue aceptado por la DIAN.")
    if not documento.xml_archivo:
        raise ErrorEmision("El documento no está firmado; ejecute generar_y_firmar primero.")

    software = _software_activo(documento)
    if cliente is None:
        cliente = construir_cliente(documento, ambiente, **cred)

    xml = documento.leer_xml()
    nombre = f"{documento.numero}.xml"

    usar_set_pruebas = ambiente == 2 and not software.set_pruebas_aceptado
    if usar_set_pruebas:
        respuesta = cliente.enviar_set_pruebas(xml, nombre, software.test_set_id)
        # La DIAN aceptó la habilitación entre un envío y otro: se anota y se
        # reenvía por el camino que ya corresponde, en vez de dejar el
        # documento en 'enviado' con un mensaje que no habla de él.
        if _set_pruebas_cerrado(respuesta):
            _marcar_habilitacion_superada(software, documento.emisor)
            usar_set_pruebas = False
            respuesta = cliente.enviar_factura_sincrono(xml, nombre)
    else:
        respuesta = cliente.enviar_factura_sincrono(xml, nombre)

    # Queda anotado con qué operación salió: es lo que decide cómo se consulta
    # después. Antes se deducía comparando el track_id con el CUFE, que es una
    # inferencia que falla cuando la DIAN devuelve un trackId propio.
    documento.envio = (
        Documento.Envio.SET_PRUEBAS if usar_set_pruebas else Documento.Envio.SINCRONO
    )

    _guardar_respuesta(documento, respuesta)
    if respuesta.track_id:
        documento.track_id = respuesta.track_id
    # "Documento procesado anteriormente" (regla 90): la DIAN ya tiene ese CUFE
    # de un envío previo; en la práctica ya está aceptado, no es un rechazo.
    if respuesta.es_valido or _ya_procesado(respuesta):
        documento.estado = _estado(DocumentoEstado.Nombre.ACEPTADO)
        if not documento.fecha_validacion:
            documento.fecha_validacion = respuesta.fecha_validacion or timezone.now()
    elif respuesta.errores:
        documento.estado = _estado(DocumentoEstado.Nombre.RECHAZADO)
    else:
        documento.estado = _estado(DocumentoEstado.Nombre.ENVIADO)
    documento.save(update_fields=[
        *_CAMPOS_RESPUESTA, "track_id", "envio", "estado", "fecha_validacion",
        "actualizado_en",
    ])
    return respuesta


def _cliente_para(documento, cliente, ambiente, **cred):
    """El cliente SOAP de las consultas, ya resuelto el ambiente."""
    ambiente = ambiente if ambiente is not None else settings.DIAN_ENVIRONMENT
    if cliente is None:
        cliente = construir_cliente(documento, ambiente, **cred)
    return cliente


def consultar_estado(documento, *, cliente=None, ambiente=None, track_id=None, **cred):
    """GetStatus: pregunta por el **documento**, por su CUFE.

    Solo lectura: NO modifica el documento; devuelve lo que responde la DIAN.
    Para aplicar el resultado usa ``actualizar_estado``.

    El identificador por defecto es el CUFE porque es el del documento: el
    ``track_id`` de un envío al Set de Pruebas es un ZipKey, que aquí no
    significa nada. Es también la única forma de saber cómo quedó un documento
    cuya entrega ya no dice nada útil —cuando el zip responde "procesado
    anteriormente"—. ``track_id`` sigue disponible para forzar otro.
    """
    cliente = _cliente_para(documento, cliente, ambiente, **cred)
    clave = track_id or documento.cufe_cude
    if not clave:
        raise ErrorEmision("El documento no tiene CUFE; emítalo primero.")
    return cliente.consultar_estado(clave)


def consultar_estado_zip(documento, *, cliente=None, ambiente=None, zip_key=None, **cred):
    """GetStatusZip: pregunta por el **envío**, por su ZipKey.

    Solo lectura, igual que ``consultar_estado``. Es la consulta de lo que se
    mandó con SendTestSetAsync, que es asíncrono y devuelve un ZipKey —el
    ``track_id`` del documento— en vez de un veredicto. Cuidado: responde por
    esa entrega concreta, no por el documento; un reenvío del mismo CUFE sale
    como duplicado (regla 90) aunque el documento esté aceptado.
    """
    cliente = _cliente_para(documento, cliente, ambiente, **cred)
    clave = zip_key or documento.track_id
    if not clave:
        raise ErrorEmision("El documento no tiene track_id; envíelo a la DIAN primero.")
    return cliente.consultar_estado_zip(clave)


def consultar_segun_envio(documento, *, cliente=None, ambiente=None, **cred):
    """La consulta que corresponde a cómo se envió el documento.

    Lo dice ``documento.envio``, que rellena ``enviar_a_dian``. Los documentos
    enviados antes de que existiera ese campo lo tienen vacío: para ellos se
    conserva la heurística de siempre —un track_id distinto del CUFE en
    ambiente 2 es un ZipKey—, que es lo mejor que se puede deducir.
    """
    ambiente = ambiente if ambiente is not None else settings.DIAN_ENVIRONMENT
    if documento.envio:
        es_zip = documento.envio == Documento.Envio.SET_PRUEBAS
    else:
        es_zip = ambiente == 2 and documento.track_id != documento.cufe_cude
    consulta = consultar_estado_zip if es_zip else consultar_estado
    return consulta(documento, cliente=cliente, ambiente=ambiente, **cred)


# Estados desde los que tiene sentido refrescar contra la DIAN (enviados, no
# terminales). Un ``aceptado`` es terminal y un borrador/firmado no se ha enviado.
_ESTADOS_ACTUALIZABLES = {
    DocumentoEstado.Nombre.ENVIADO,
    DocumentoEstado.Nombre.RECHAZADO,
}


def actualizar_estado(documento, *, cliente=None, ambiente=None, **cred):
    """Consulta la DIAN y aplica el resultado al documento.

    Solo para documentos enviados y no aceptados (``enviado``/``rechazado``):
    un ``aceptado`` es terminal y no se toca; un documento sin enviar no aplica.
    """
    codigo_actual = documento.estado.nombre if documento.estado_id else ""
    if codigo_actual not in _ESTADOS_ACTUALIZABLES:
        raise ErrorEmision(
            "Solo se puede actualizar el estado de documentos enviados o "
            "rechazados (no aceptados ni en borrador)."
        )

    respuesta = consultar_segun_envio(
        documento, cliente=cliente, ambiente=ambiente, **cred
    )
    _guardar_respuesta(documento, respuesta)
    if respuesta.es_valido or _ya_procesado(respuesta):
        documento.estado = _estado(DocumentoEstado.Nombre.ACEPTADO)
        if not documento.fecha_validacion:
            documento.fecha_validacion = respuesta.fecha_validacion or timezone.now()
    elif respuesta.errores:
        documento.estado = _estado(DocumentoEstado.Nombre.RECHAZADO)
    documento.save(update_fields=[
        *_CAMPOS_RESPUESTA, "estado", "fecha_validacion", "actualizado_en",
    ])
    return respuesta
