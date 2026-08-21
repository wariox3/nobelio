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
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from apps.dian import firma, soap, ubl
from apps.emisores.models import ResolucionFacturacion
from apps.documentos.models import (
    Adquiriente,
    Documento,
    DocumentoDetalle,
    DocumentoDetalleImpuesto,
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


def _ya_procesado(respuesta) -> bool:
    """Detecta la regla 90 de la DIAN ("Documento procesado anteriormente").

    Significa que el CUFE ya fue recibido y aceptado en un envío previo, así que
    no es un rechazo de contenido sino un documento ya aceptado.
    """
    return any("procesado anteriormente" in e.lower() for e in respuesta.errores)


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
    """
    ambiente = ambiente if ambiente is not None else settings.DIAN_ENVIRONMENT

    bloqueados = {
        DocumentoEstado.Nombre.FIRMADO: "El documento ya está firmado.",
        DocumentoEstado.Nombre.ENVIADO: "El documento ya fue enviado a la DIAN.",
        DocumentoEstado.Nombre.ACEPTADO: "El documento ya fue aceptado por la DIAN.",
    }
    if documento.estado_id and documento.estado.nombre in bloqueados:
        raise ErrorEmision(bloqueados[documento.estado.nombre])

    # Dar de baja un emisor (activo=False) tiene que cortar la emisión: es la
    # forma de suspender a un cliente sin tocar las credenciales de su cuenta.
    # El certificado no se exige aquí sino en `construir_firmador`, que es quien
    # lo necesita (las pruebas inyectan el firmador y no cargan .p12).
    if not documento.emisor.activo:
        raise ErrorEmision(MENSAJE_EMISOR_INACTIVO)

    software = _software_activo(documento)

    codigo_tipo = documento.documento_tipo.codigo
    es_factura = codigo_tipo == DocumentoTipo.Codigo.FACTURA_VENTA
    if es_factura and documento.resolucion is None:
        raise ErrorEmision("La factura no tiene resolución de facturación asociada.")
    if (
        codigo_tipo in (DocumentoTipo.Codigo.NOTA_CREDITO,
                        DocumentoTipo.Codigo.NOTA_DEBITO)
        and documento.documento_referencia is None
    ):
        raise ErrorEmision("La nota debe referenciar el documento que corrige.")

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
    documento.save(update_fields=["cufe_cude", "xml_archivo", "estado", "actualizado_en"])
    return xml_firmado


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
    else:
        respuesta = cliente.enviar_factura_sincrono(xml, nombre)

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
        *_CAMPOS_RESPUESTA, "track_id", "estado", "fecha_validacion", "actualizado_en",
    ])
    return respuesta


def consultar_estado(documento, *, cliente=None, ambiente=None, track_id=None, **cred):
    """Consulta (solo lectura) el estado del documento en la DIAN.

    NO modifica el documento; devuelve lo que responde la DIAN. Para aplicar el
    resultado al documento usa ``actualizar_estado``.

    Elige la operación según cómo se envió: si el identificador es un ZipKey del
    Set de Pruebas (distinto del CUFE, en habilitación) usa GetStatusZip; si es
    la clave del documento (CUFE, envíos SendBillSync) usa GetStatus.
    """
    ambiente = ambiente if ambiente is not None else settings.DIAN_ENVIRONMENT
    if cliente is None:
        cliente = construir_cliente(documento, ambiente, **cred)

    track_id = track_id or documento.track_id
    if not track_id:
        raise ErrorEmision("El documento no tiene track_id; envíelo a la DIAN primero.")

    # ZipKey del Set de Pruebas (≠ CUFE) → GetStatusZip; CUFE/trackId → GetStatus.
    es_zipkey = ambiente == 2 and track_id != documento.cufe_cude
    if es_zipkey:
        return cliente.consultar_estado_zip(track_id)
    return cliente.consultar_estado(track_id)


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

    respuesta = consultar_estado(documento, cliente=cliente, ambiente=ambiente, **cred)
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


# ---------------------------------------------------------------------------
# Set de Pruebas (habilitación)
# ---------------------------------------------------------------------------

class _Descartar(Exception):
    """Señal interna para deshacer los documentos de prueba."""


def _catalogo(Modelo, codigo, que):
    try:
        return Modelo.objects.get(codigo=codigo)
    except Modelo.DoesNotExist:
        raise ErrorEmision(
            f"Falta {que} (código {codigo}) en los catálogos. "
            f"Corre 'manage.py cargar_catalogos'."
        )


def _tipo_documento(codigo):
    try:
        return DocumentoTipo.objects.get(codigo=codigo)
    except DocumentoTipo.DoesNotExist:
        raise ErrorEmision(f"Falta el tipo de documento '{codigo}' en la base.")


# Resolución del Set de Pruebas. Es la misma para todo el que se habilita —la
# publica la DIAN con su clave técnica— y no tiene nada que ver con la
# numeración real del emisor, así que no se guarda: se arma al vuelo, numera los
# dos documentos de prueba, entra en el CUFE y se va con ellos.
RESOLUCION_PRUEBAS = {
    "numero_resolucion": "18760000001",
    "fecha_resolucion": date(2026, 6, 29),
    "prefijo": "SETP",
    "rango_desde": 990000000,
    "rango_hasta": 995000000,
    "clave_tecnica": "fc8eac422eba16e22ffd8c6f94b3f40a6e38162c",
    "vigente_desde": date(2019, 1, 19),
    "vigente_hasta": date(2030, 1, 19),
}


def _resolucion_de_pruebas(emisor):
    """La resolución de habilitación, dentro de lo que luego se deshace.

    Vive en la base solo mientras dura la emisión porque los documentos la
    referencian por clave ajena; al revertir la transacción desaparece con
    ellos. La numeración real del emisor no se toca.

    Se usa ``get_or_create`` porque el emisor puede tener ya esa misma
    resolución dada de alta (importada de la DIAN durante la habilitación):
    crearla otra vez chocaría con ``resolucion_unica_por_emisor`` y tumbaría el
    envío. Si ya está, esa vale —y su clave técnica es la suya de verdad.
    """
    from apps.catalogos.models import TipoFactura

    clave = {
        "emisor": emisor,
        "tipo_factura": _catalogo(TipoFactura, "01", "el tipo de factura 01"),
        "prefijo": RESOLUCION_PRUEBAS["prefijo"],
        "numero_resolucion": RESOLUCION_PRUEBAS["numero_resolucion"],
    }
    defaults = {c: v for c, v in RESOLUCION_PRUEBAS.items() if c not in clave}
    resolucion, _ = ResolucionFacturacion.objects.get_or_create(**clave, defaults=defaults)
    return resolucion


def _armar_documento(emisor, *, tipo, resolucion, consecutivo, referencia=None,
                     valor=Decimal("1000.00"), iva=Decimal("190.00")):
    """Crea en la base un documento de prueba completo (se deshace después)."""
    from apps.catalogos.models import Moneda, Tributo, UnidadMedida

    prefijo = resolucion.prefijo if referencia is None else ""
    documento = Documento.objects.create(
        documento_tipo=tipo,
        estado=_estado(DocumentoEstado.Nombre.BORRADOR),
        emisor=emisor,
        resolucion=resolucion if referencia is None else None,
        documento_referencia=referencia,
        moneda=_catalogo(Moneda, "COP", "la moneda COP"),
        prefijo=prefijo,
        consecutivo=consecutivo,
        numero=f"{prefijo}{consecutivo}",
        fecha_emision=timezone.localdate(),
        hora_emision=timezone.localtime().time(),
        observaciones="Documento del Set de Pruebas (habilitación).",
        valor_bruto=valor, total_impuestos=iva, total_a_pagar=valor + iva,
    )
    # El adquiriente del documento de prueba es el propio emisor: evita depender
    # de datos de un tercero y de códigos de catálogo que quizá no estén.
    Adquiriente.objects.create(
        documento=documento,
        razon_social=emisor.razon_social,
        tipo_identificacion=emisor.tipo_identificacion,
        numero_identificacion=emisor.numero_identificacion,
        digito_verificacion=emisor.digito_verificacion,
        tipo_organizacion=emisor.tipo_organizacion,
        pais=emisor.pais, departamento=emisor.departamento,
        municipio=emisor.municipio, direccion=emisor.direccion,
    )
    detalle = DocumentoDetalle.objects.create(
        documento=documento, numero_linea=1,
        descripcion="Servicio de prueba", cantidad=Decimal("1"),
        unidad_medida=_catalogo(UnidadMedida, "94", "la unidad de medida 94"),
        valor_unitario=valor, valor_total=valor,
    )
    DocumentoDetalleImpuesto.objects.create(
        detalle=detalle, tributo=_catalogo(Tributo, "01", "el tributo IVA"),
        tarifa=Decimal("19.00"), base_gravable=valor, valor=iva,
    )
    return documento


def _emitir_prueba(documento, *, software, firmador, cliente, ambiente):
    """Genera, firma y envía al Set de Pruebas. No guarda nada del documento."""
    constructor = ubl.constructor_para(
        documento,
        software=software,
        resolucion=documento.resolucion,
        ambiente=ambiente,
        clave_tecnica=documento.resolucion.clave_tecnica if documento.resolucion else "",
    )
    xml = constructor.generar_xml()
    documento.cufe_cude = constructor.cufe
    xml_firmado = firmador.firmar(xml)
    respuesta = cliente.enviar_set_pruebas(
        xml_firmado, f"{documento.numero}.xml", software.test_set_id
    )
    return {
        "numero": documento.numero,
        "cufe_cude": constructor.cufe,
        "track_id": respuesta.track_id,
        "es_valido": respuesta.es_valido,
        "codigo_estado": respuesta.codigo_estado,
        "descripcion_estado": respuesta.descripcion_estado,
        "errores": respuesta.errores,
    }


def emitir_set_pruebas(emisor, *, consecutivo=None, cliente=None, firmador=None,
                       ambiente=None, **cred):
    """Emite la factura y la nota crédito del Set de Pruebas, sin registrarlas.

    Los dos documentos existen solo para que la DIAN acepte la habilitación, así
    que no tienen por qué quedar en el histórico del emisor: se construyen en la
    base —el XML se arma leyendo las líneas y el adquiriente, que son filas
    relacionadas—, se firman, se envían con ``SendTestSetAsync`` y al final se
    deshace todo. Tampoco se sube nada a B2 ni se guardan los rechazos.

    Lo único que queda es lo que devuelve: número, CUFE/CUDE, ZipKey y el
    veredicto de la DIAN para cada uno.

    La resolución es la del Set de Pruebas (``RESOLUCION_PRUEBAS``), la misma
    para todos y con la clave técnica que publica la DIAN: no se guarda ni tiene
    que ver con la numeración real del emisor. El consecutivo arranca en el
    principio de su rango y la nota usa el siguiente; repetir la llamada reenvía
    los mismos números y la DIAN contesta "documento procesado anteriormente"
    (regla 90), así que para reenviar pásalos en ``consecutivo``.

    Lo único que persiste es ``emisor.habilitado_facturacion``, que queda en
    ``True`` al enviar: es lo que distingue al emisor recién dado de alta del
    que ya pasó por el Set de Pruebas.
    """
    ambiente = ambiente if ambiente is not None else settings.DIAN_ENVIRONMENT
    if not emisor.activo:
        raise ErrorEmision(MENSAJE_EMISOR_INACTIVO)

    software = _software_activo_emisor(emisor)
    if not software.test_set_id:
        raise ErrorEmision(
            "El software del emisor no tiene TestSetId; sin él no hay Set de "
            "Pruebas al que enviar."
        )
    if firmador is None:
        firmador = construir_firmador_emisor(emisor, **cred)
    if cliente is None:
        cliente = construir_cliente_emisor(emisor, ambiente, **cred)

    primero = consecutivo or RESOLUCION_PRUEBAS["rango_desde"]
    resultados = {}
    try:
        with transaction.atomic():
            resolucion = _resolucion_de_pruebas(emisor)
            factura = _armar_documento(
                emisor,
                tipo=_tipo_documento(DocumentoTipo.Codigo.FACTURA_VENTA),
                resolucion=resolucion, consecutivo=primero,
            )
            resultados["factura"] = _emitir_prueba(
                factura, software=software, firmador=firmador,
                cliente=cliente, ambiente=ambiente,
            )
            # La nota corrige a la factura recién emitida: la DIAN exige que la
            # referencia (número y CUFE) sea de un documento real.
            nota = _armar_documento(
                emisor,
                tipo=_tipo_documento(DocumentoTipo.Codigo.NOTA_CREDITO),
                resolucion=resolucion, consecutivo=primero + 1, referencia=factura,
            )
            resultados["nota_credito"] = _emitir_prueba(
                nota, software=software, firmador=firmador,
                cliente=cliente, ambiente=ambiente,
            )
            raise _Descartar
    except _Descartar:
        pass

    # Fuera de la transacción, que se deshizo: los documentos de prueba no
    # quedan, pero el hecho de que el emisor ya pasó por el Set de Pruebas sí.
    # Se marca por haberlos enviado, no por el veredicto: SendTestSetAsync es
    # asíncrono y el resultado se consulta después.
    if not emisor.habilitado_facturacion:
        emisor.habilitado_facturacion = True
        emisor.save(update_fields=["habilitado_facturacion", "actualizado_en"])
    return resultados
