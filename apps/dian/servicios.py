"""
Servicios de orquestación del ciclo de vida de un documento electrónico.

Encadena el pipeline completo:
    documento → XML UBL → CUFE → firma XAdES → envío a la DIAN → estado

Cada paso actualiza el estado del documento y guarda los artefactos (CUFE, XML
firmado, respuesta DIAN). Las credenciales (certificado) y el cliente SOAP se
pueden inyectar para facilitar las pruebas sin red ni .p12 reales.
"""
from __future__ import annotations

from django.conf import settings

from apps.dian import firma, soap, ubl
from apps.documentos.models import DocumentoElectronico


class ErrorEmision(Exception):
    """Error en el proceso de emisión de un documento."""


def _software_activo_emisor(emisor):
    software = emisor.softwares.filter(activo=True).first()
    if software is None:
        raise ErrorEmision("El emisor no tiene un software DIAN activo.")
    return software


def _certificado_activo_emisor(emisor):
    certificado = emisor.certificados.filter(activo=True).first()
    if certificado is None:
        raise ErrorEmision("El emisor no tiene un certificado digital activo.")
    return certificado


def _software_activo(documento):
    return _software_activo_emisor(documento.emisor)


def _certificado_activo(documento):
    return _certificado_activo_emisor(documento.emisor)


def construir_firmador(documento, *, llave=None, certificado=None, cadena=None):
    """Crea el FirmadorXAdES, cargando el .p12 del emisor si no se inyecta."""
    if llave is None or certificado is None:
        cert_modelo = _certificado_activo(documento)
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
    software = _software_activo(documento)

    es_factura = documento.tipo == DocumentoElectronico.Tipo.FACTURA_VENTA
    if es_factura and documento.resolucion is None:
        raise ErrorEmision("La factura no tiene resolución de facturación asociada.")
    if (
        documento.tipo in (DocumentoElectronico.Tipo.NOTA_CREDITO,
                            DocumentoElectronico.Tipo.NOTA_DEBITO)
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
    documento.estado = DocumentoElectronico.Estado.GENERADO

    if firmador is None:
        firmador = construir_firmador(documento, **cred)
    xml_firmado = firmador.firmar(xml)

    documento.xml_firmado = xml_firmado.decode("utf-8")
    documento.estado = DocumentoElectronico.Estado.FIRMADO
    documento.save(update_fields=["cufe_cude", "xml_firmado", "estado", "actualizado_en"])
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

    Usa el software DIAN activo del emisor (``id_proveedor`` = NIT del proveedor
    tecnológico, que en software propio es el propio emisor). Devuelve un
    ``soap.RespuestaRangos`` con el código/descripción de la DIAN y los rangos
    (cada uno con su clave técnica).
    """
    ambiente = ambiente if ambiente is not None else settings.DIAN_ENVIRONMENT
    software = software or _software_activo_emisor(emisor)
    if cliente is None:
        cliente = construir_cliente_emisor(emisor, ambiente, **cred)
    return cliente.consultar_rangos_numeracion(
        emisor.numero_identificacion,
        software.id_proveedor,
        software.identificador,
    )


def enviar_a_dian(documento, *, cliente=None, ambiente=None, **cred):
    """Empaqueta y envía el XML firmado a la DIAN; actualiza el estado.

    En habilitación (ambiente=2) usa SendTestSetAsync con el TestSetId del
    software; en producción usa SendBillSync.
    """
    ambiente = ambiente if ambiente is not None else settings.DIAN_ENVIRONMENT
    if not documento.xml_firmado:
        raise ErrorEmision("El documento no está firmado; ejecute generar_y_firmar primero.")

    software = _software_activo(documento)
    if cliente is None:
        cliente = construir_cliente(documento, ambiente, **cred)

    xml = documento.xml_firmado.encode("utf-8")
    nombre = f"{documento.numero}.xml"

    if ambiente == 2:
        respuesta = cliente.enviar_set_pruebas(xml, nombre, software.test_set_id)
    else:
        respuesta = cliente.enviar_factura_sincrono(xml, nombre)

    documento.respuesta_dian = respuesta.xml_crudo
    if respuesta.es_valido:
        documento.estado = DocumentoElectronico.Estado.ACEPTADO
    elif respuesta.errores:
        documento.estado = DocumentoElectronico.Estado.RECHAZADO
    else:
        documento.estado = DocumentoElectronico.Estado.ENVIADO
    documento.save(update_fields=["respuesta_dian", "estado", "actualizado_en"])
    return respuesta


def consultar_estado(documento, *, cliente=None, ambiente=None, track_id=None, **cred):
    """Consulta el estado de un documento en la DIAN (GetStatus)."""
    ambiente = ambiente if ambiente is not None else settings.DIAN_ENVIRONMENT
    if cliente is None:
        cliente = construir_cliente(documento, ambiente, **cred)

    respuesta = cliente.consultar_estado(track_id or "")
    documento.respuesta_dian = respuesta.xml_crudo
    if respuesta.es_valido:
        documento.estado = DocumentoElectronico.Estado.ACEPTADO
    elif respuesta.errores:
        documento.estado = DocumentoElectronico.Estado.RECHAZADO
    documento.save(update_fields=["respuesta_dian", "estado", "actualizado_en"])
    return respuesta
