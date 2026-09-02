"""Orquestación del ciclo de vida de la **nómina electrónica**.

Vivía al final de `apps/dian/servicios.py`, tras un separador de comentarios:
la mitad de un módulo de 1.069 líneas que no comparte pipeline con la otra
mitad. La nómina no es UBL, no lleva resolución de numeración, tiene su propio
Set de Pruebas y sale por `SendNominaSync`; lo único que comparte con el
documento es la máquina de estados.

Se reexporta desde `apps.dian.servicios`, así que
`servicios.generar_y_firmar_nomina` y sus vecinas siguen valiendo igual.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.dian import identificadores as ident, nomina as xml_nomina
from apps.dian.servicios import (
    ErrorEmision,
    MENSAJE_EMISOR_INACTIVO,
    _anotar_si_cerro_el_set,
    _estado,
    _marcar_habilitacion_superada,
    _parsear_error,
    _registrar_veredicto,
    _set_pruebas_cerrado,
    _software_activo_emisor,
    _ESTADOS_ACTUALIZABLES,
    _ya_procesado,
    construir_cliente_emisor,
    construir_firmador_emisor,
)
from apps.documentos.models import DocumentoEstado
from apps.emisores.models import SoftwareDian
from apps.nucleo.registro import campos

logger = logging.getLogger(__name__)


# ===========================================================================
# Nómina electrónica
# ===========================================================================
# Prefijos del nombre de archivo (numerales 3.3 a 3.5 del anexo de nómina).
PREFIJO_ARCHIVO_NOMINA = "nie"
PREFIJO_ARCHIVO_NOTA_AJUSTE_NOMINA = "niae"
PREFIJO_ARCHIVO_ZIP_NOMINA = "z"


def _software_activo_nomina(nomina):
    return _software_activo_emisor(nomina.emisor, SoftwareDian.Tipo.NOMINA)


def _nombres_archivo_nomina(nomina):
    """Nombres del XML y del ZIP de un envío, reservando su consecutivo.

    El consecutivo es de **archivos enviados** y se reinicia cada año, así que
    se pide al numerador del emisor en el momento de enviar y no antes: si se
    reservara al firmar, cada reintento gastaría uno.
    """
    from apps.nomina.models import ConsecutivoArchivo, Nomina

    anio = timezone.localdate().year
    consecutivo = ConsecutivoArchivo.siguiente(nomina.emisor, anio)
    prefijo = (
        PREFIJO_ARCHIVO_NOTA_AJUSTE_NOMINA
        if nomina.tipo_xml == Nomina.TipoXML.AJUSTE
        else PREFIJO_ARCHIVO_NOMINA
    )
    datos = {
        "nit": nomina.emisor.numero_identificacion,
        "anio": anio,
        "consecutivo": consecutivo,
    }
    return (
        f"{ident.nombre_archivo_nomina(prefijo, **datos)}.xml",
        f"{ident.nombre_archivo_nomina(PREFIJO_ARCHIVO_ZIP_NOMINA, **datos)}.zip",
    )


def _guardar_respuesta_nomina(nomina, respuesta):
    """Registra el resultado DIAN de una nómina: SOAP crudo y rechazos."""
    from apps.nomina.models import NominaError

    if respuesta.xml_crudo:
        nomina.respuesta_archivo.save(
            f"{nomina.numero}.dian.xml",
            ContentFile(respuesta.xml_crudo.encode("utf-8")),
            save=False,
        )
    nomina.errores.all().delete()
    filas = []
    for e in respuesta.errores:
        datos = _parsear_error(e, respaldo=respuesta.descripcion_estado)
        if datos["tipo"] == NominaError.Tipo.NOTIFICACION:
            continue
        filas.append(NominaError(nomina=nomina, **datos))
    NominaError.objects.bulk_create(filas)


def generar_y_firmar_nomina(nomina, *, firmador=None, ambiente=None, **cred):
    """Genera el XML de la nómina, calcula el CUNE y la firma.

    ``FechaGen``/``HoraGen`` se fijan aquí, en el momento de firmar. No son la
    fecha de liquidación —esa va en ``FechaLiquidacionInicio/Fin`` y no se toca—
    sino cuándo se generó el XML, y generarlo y firmarlo son el mismo acto: el
    ``SigningTime`` de la firma tiene que poder cuadrar con lo que el documento
    dice de sí mismo. Se refecha en vez de fallar como hace la factura (regla
    FAD09) porque aquí no hay consecutivo de resolución que se gaste, y generar
    hoy la nómina de un periodo pasado es lo normal.

    El CUNE se recalcula en cada firma: entra en él todo lo que se puede haber
    corregido tras un rechazo, y reutilizar el anterior dejaría el XML con un
    hash que no corresponde a su propio contenido.
    """
    from apps.nomina import models as nom
    from apps.dian import nomina as xml_nomina

    # El de la nómina, que lo heredó del ``ambiente_nomina`` de su emisor —la
    # DIAN habilita la nómina aparte de la facturación—. Antes se resolvía con
    # el ajuste global y se escribía encima del campo, así que una nómina
    # creada como de prueba se firmaba como producción en cuanto el despliegue
    # pasaba a producción.
    ambiente = ambiente if ambiente is not None else nomina.ambiente

    bloqueados = {
        DocumentoEstado.Nombre.FIRMADO: "La nómina ya está firmada.",
        DocumentoEstado.Nombre.ENVIADO: "La nómina ya fue enviada a la DIAN.",
        DocumentoEstado.Nombre.ACEPTADO: "La nómina ya fue aceptada por la DIAN.",
    }
    if nomina.estado_id and nomina.estado.nombre in bloqueados:
        raise ErrorEmision(bloqueados[nomina.estado.nombre])

    if not nomina.emisor.activo:
        raise ErrorEmision(MENSAJE_EMISOR_INACTIVO)

    # Antes se respetaba el par que trajera la nómina, y eso dejaba documentos
    # que declaraban haberse generado horas después de estar firmados.
    nomina.fecha_generacion = timezone.localdate()
    nomina.hora_generacion = timezone.localtime().time()

    software = _software_activo_nomina(nomina)
    nomina.cune = ""
    constructor = xml_nomina.constructor_nomina_para(
        nomina, software=software, ambiente=ambiente,
    )
    xml = constructor.generar_xml()
    nomina.cune = constructor.cune

    if firmador is None:
        # El Description de la política es el de nómina, que el anexo fija
        # aparte del de factura (numeral 7.10).
        firmador = construir_firmador_emisor(
            nomina.emisor, policy_name=settings.DIAN_POLICY_NAME_NOMINA, **cred
        )
    xml_firmado = firmador.firmar(xml)

    nomina.xml_archivo.save(
        f"{nomina.numero}.xml", ContentFile(xml_firmado), save=False
    )
    nomina.ambiente = ambiente
    nomina.estado = _estado(DocumentoEstado.Nombre.FIRMADO)
    nomina.save(update_fields=[
        "cune", "fecha_generacion", "hora_generacion", "xml_archivo", "ambiente",
        "estado", "actualizado_en",
    ])
    logger.info("nomina.firmada %s", campos(
        nomina=nomina.pk,
        emisor=nomina.emisor_id,
        numero=nomina.numero,
        ambiente=ambiente,
        cune=nomina.cune,
    ))
    return xml_firmado


def enviar_nomina_a_dian(nomina, *, cliente=None, ambiente=None, **cred):
    """Envía la nómina firmada a la DIAN y actualiza su estado.

    Igual que la factura, elige entre dos operaciones: ``SendTestSetAsync`` con
    el ``TestSetId`` del software mientras el emisor está en habilitación y su
    Set de Pruebas de nómina no ha sido aceptado, y ``SendNominaSync`` después.
    La nómina tiene su propio Set de Pruebas, con su ``TestSetId`` y su software,
    separados de los de facturación.

    Si la DIAN responde que el Set ya está aceptado, se marcan las banderas y la
    nómina sale por ``SendNominaSync`` en el mismo envío.
    """
    from apps.nomina.models import Nomina

    # El mismo con el que se firmó: es el que declara el XML y el que entró en
    # el CUNE.
    ambiente = ambiente if ambiente is not None else nomina.ambiente
    if nomina.estado_id and nomina.estado.nombre == DocumentoEstado.Nombre.ACEPTADO:
        raise ErrorEmision("La nómina ya fue aceptada por la DIAN.")
    if not nomina.xml_archivo:
        raise ErrorEmision(
            "La nómina no está firmada; ejecute generar_y_firmar_nomina primero."
        )

    software = _software_activo_nomina(nomina)
    if cliente is None:
        cliente = construir_cliente_emisor(nomina.emisor, ambiente, **cred)

    xml = nomina.leer_xml()
    nombre_xml, nombre_zip = _nombres_archivo_nomina(nomina)

    usar_set_pruebas = ambiente == 2 and not software.set_pruebas_aceptado
    if usar_set_pruebas:
        # Sin TestSetId el envío sale con el campo vacío y la DIAN lo rechaza
        # con un mensaje que no habla de esto. Es un dato del portal, así que
        # es más útil pararlo aquí y decir dónde se consigue.
        if not software.test_set_id:
            raise ErrorEmision(
                "El software de nómina no tiene TestSetId, y sin él no se puede "
                "enviar al Set de Pruebas. Cópielo del portal de la DIAN "
                "(modo de operación de nómina electrónica) y guárdelo en el "
                "software antes de enviar."
            )
        respuesta = cliente.enviar_set_pruebas(
            xml, nombre_xml, software.test_set_id, nombre_zip=nombre_zip,
        )
        # La DIAN cerró la habilitación entre un envío y otro: se anota y se
        # reenvía por el camino que ya corresponde.
        if _set_pruebas_cerrado(respuesta):
            _marcar_habilitacion_superada(software, nomina.emisor)
            usar_set_pruebas = False
            respuesta = cliente.enviar_nomina_sincrono(xml, nombre_xml)
    else:
        respuesta = cliente.enviar_nomina_sincrono(xml, nombre_xml)

    # Queda anotado con qué operación salió: es lo que decide cómo se consulta
    # después, porque el Set de Pruebas es asíncrono y se pregunta por ZipKey.
    nomina.envio = (
        Nomina.Envio.SET_PRUEBAS if usar_set_pruebas else Nomina.Envio.SINCRONO
    )

    _guardar_respuesta_nomina(nomina, respuesta)
    if respuesta.track_id:
        nomina.track_id = respuesta.track_id
    # "Documento procesado anteriormente" (regla 90), igual que en la factura:
    # la DIAN ya tiene ese CUNE de un envío previo, así que en la práctica está
    # aceptado y no es un rechazo. Faltaba aquí, de modo que un reenvío —el
    # caso normal tras un corte de red— dejaba la nómina en `rechazado` con un
    # error que no habla de su contenido.
    if respuesta.es_valido or _ya_procesado(respuesta):
        nomina.estado = _estado(DocumentoEstado.Nombre.ACEPTADO)
        if not nomina.fecha_validacion:
            nomina.fecha_validacion = respuesta.fecha_validacion or timezone.now()
    elif respuesta.errores:
        nomina.estado = _estado(DocumentoEstado.Nombre.RECHAZADO)
    else:
        nomina.estado = _estado(DocumentoEstado.Nombre.ENVIADO)
    nomina.save(update_fields=[
        "respuesta_archivo", "track_id", "envio", "estado", "fecha_validacion",
        "actualizado_en",
    ])
    _registrar_veredicto("nomina.enviada", nomina, respuesta, campos(
        numero=nomina.numero,
        ambiente=ambiente,
        envio=nomina.envio,
        archivo=nombre_xml,
    ), etiqueta="nomina")
    return respuesta


def consultar_estado_nomina(nomina, *, cliente=None, ambiente=None, **cred):
    """GetStatus por el CUNE. Solo lectura: no toca la nómina."""
    ambiente = ambiente if ambiente is not None else nomina.ambiente
    if cliente is None:
        cliente = construir_cliente_emisor(nomina.emisor, ambiente, **cred)
    if not nomina.cune:
        raise ErrorEmision("La nómina no tiene CUNE; emítala primero.")
    return cliente.consultar_estado(nomina.cune)


def consultar_estado_zip_nomina(nomina, *, cliente=None, ambiente=None,
                                zip_key=None, **cred):
    """GetStatusZip: pregunta por la **entrega**, por su ZipKey.

    Es la consulta de lo que se mandó al Set de Pruebas, que es asíncrono y
    devuelve un ZipKey en vez de un veredicto. Responde por esa entrega
    concreta, no por la nómina.
    """
    ambiente = ambiente if ambiente is not None else nomina.ambiente
    if cliente is None:
        cliente = construir_cliente_emisor(nomina.emisor, ambiente, **cred)
    clave = zip_key or nomina.track_id
    if not clave:
        raise ErrorEmision(
            "La nómina no tiene track_id; envíela a la DIAN primero."
        )
    return cliente.consultar_estado_zip(clave)


def consultar_segun_envio_nomina(nomina, *, cliente=None, ambiente=None, **cred):
    """La consulta que corresponde a cómo se envió la nómina.

    Lo dice ``nomina.envio``. Las enviadas antes de que la nómina tuviera Set de
    Pruebas lo tienen en ``nomina_sync`` o vacío, y para ellas la consulta por
    CUNE sigue siendo la correcta.
    """
    from apps.nomina.models import Nomina

    consulta = (
        consultar_estado_zip_nomina
        if nomina.envio == Nomina.Envio.SET_PRUEBAS
        else consultar_estado_nomina
    )
    return consulta(nomina, cliente=cliente, ambiente=ambiente, **cred)


def actualizar_estado_nomina(nomina, *, cliente=None, ambiente=None, **cred):
    """Consulta la DIAN y **aplica** el resultado a la nómina.

    El gemelo de ``actualizar_estado`` para documentos, y hace falta por lo
    mismo: el envío al Set de Pruebas es asíncrono y solo devuelve un ZipKey, de
    modo que en ese momento no hay veredicto que guardar. El rechazo aparece
    después, al consultar, y las funciones de consulta son de solo lectura a
    propósito. Sin esto, una nómina rechazada se queda en ``enviado`` y con cero
    errores para siempre, aunque la DIAN ya la haya rechazado.

    Solo desde ``enviado`` o ``rechazado``: un ``aceptado`` es terminal y una
    que no se ha enviado no tiene nada que consultar.
    """
    codigo_actual = nomina.estado.nombre if nomina.estado_id else ""
    if codigo_actual not in _ESTADOS_ACTUALIZABLES:
        raise ErrorEmision(
            "Solo se puede actualizar el estado de nóminas enviadas o "
            "rechazadas (no aceptadas ni en borrador)."
        )

    respuesta = consultar_segun_envio_nomina(
        nomina, cliente=cliente, ambiente=ambiente, **cred
    )
    _guardar_respuesta_nomina(nomina, respuesta)
    _anotar_si_cerro_el_set(respuesta, _software_activo_nomina(nomina), nomina.emisor)
    if respuesta.es_valido or _ya_procesado(respuesta):
        nomina.estado = _estado(DocumentoEstado.Nombre.ACEPTADO)
        if not nomina.fecha_validacion:
            nomina.fecha_validacion = respuesta.fecha_validacion or timezone.now()
    elif respuesta.errores:
        nomina.estado = _estado(DocumentoEstado.Nombre.RECHAZADO)
    nomina.save(update_fields=[
        "respuesta_archivo", "estado", "fecha_validacion", "actualizado_en",
    ])
    _registrar_veredicto("nomina.estado_actualizado", nomina, respuesta, campos(
        numero=nomina.numero,
        desde=codigo_actual,
        envio=nomina.envio,
    ), etiqueta="nomina")
    return respuesta
