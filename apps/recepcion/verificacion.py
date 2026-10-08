"""Verificación de los documentos recibidos contra la DIAN.

Lo que trae el XML del proveedor no prueba nada: el AttachedDocument dice que
la DIAN lo validó, pero lo escribe el mismo proveedor, y por carga manual se
puede subir un XML inventado. La prueba es preguntarle a la DIAN por el CUFE
con ``GetStatus``, que responde si lo tiene registrado y válido.

- Se consulta con el **certificado del emisor que recibe** (el WS exige
  WS-Security firmado) y en el **ambiente del documento** (su
  ``ProfileExecutionID``; producción si no lo traía).
- Un emisor sin certificado vigente no puede consultar: el documento queda
  ``no_verificable``, con el motivo, y se verifica cuando cargue uno.
- La DIAN caída, un fallo de red o de B2 al leer el certificado son
  transitorios: la tarea reintenta y, agotados los reintentos, queda ``error``.

⚠ Pendiente de confirmar con un documento real en producción que ``GetStatus``
responde por un CUFE que emitió otro (el proveedor) y no solo por los propios.
"""
import logging

import requests
from botocore.exceptions import BotoCoreError, ClientError
from django.utils import timezone
from lxml import etree

from apps.dian.servicios import construir_cliente_emisor
from apps.emisores.servicios import motivo_no_puede_emitir
from apps.nucleo.models import Ambiente
from apps.nucleo.registro import campos
from apps.recepcion.models import EstadoVerificacion

logger = logging.getLogger(__name__)

LARGO_CODIGO = 10


class ErrorTransitorio(Exception):
    """No se pudo consultar a la DIAN, pero puede funcionar más tarde."""


def verificar(documento, *, cliente=None):
    """Consulta el CUFE de ``documento`` en la DIAN y guarda el resultado.

    Devuelve el documento actualizado. Lanza ``ErrorTransitorio`` si la
    consulta no se pudo hacer; en ese caso no toca el documento.
    ``cliente`` es para las pruebas: un ``ClienteDian`` ya armado.
    """
    if cliente is None:
        motivo = motivo_no_puede_emitir(documento.emisor)
        if motivo:
            return _guardar(documento, EstadoVerificacion.NO_VERIFICABLE, "", motivo)
        ambiente = documento.ambiente or Ambiente.PRODUCCION
        try:
            cliente = construir_cliente_emisor(documento.emisor, ambiente)
        except (BotoCoreError, ClientError) as error:
            raise ErrorTransitorio(f"No se pudo leer el certificado del emisor: {error}.")
    try:
        respuesta = cliente.consultar_estado(documento.cufe_cude)
    except (requests.RequestException, etree.XMLSyntaxError) as error:
        raise ErrorTransitorio(f"La DIAN no respondió: {error}.")
    estado = EstadoVerificacion.VALIDO if respuesta.es_valido else EstadoVerificacion.INVALIDO
    detalle = " ".join(filter(None, [respuesta.descripcion_estado, *respuesta.errores]))
    return _guardar(documento, estado, respuesta.codigo_estado, detalle)


def marcar_error(documento, detalle):
    """Deja constancia de que no se pudo consultar, tras agotar los reintentos."""
    return _guardar(documento, EstadoVerificacion.ERROR, "", detalle)


def _guardar(documento, estado, codigo, descripcion):
    documento.verificacion_estado = estado
    documento.verificacion_codigo = codigo[:LARGO_CODIGO]
    documento.verificacion_descripcion = descripcion
    documento.verificado_en = timezone.now()
    documento.save(update_fields=[
        "verificacion_estado", "verificacion_codigo", "verificacion_descripcion",
        "verificado_en", "actualizado_en",
    ])
    logger.info("recepcion.documento_verificado %s", campos(
        documento=documento.pk, estado=estado, codigo=codigo,
    ))
    return documento
