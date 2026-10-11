"""El entorno del despliegue (``ENV``) y lo que prohíbe fuera de producción.

``ENV`` dice dónde corre nobelio —``dev``, ``test`` o ``prod``— y es distinto
del ambiente DIAN, que es de cada emisor. La regla es una sola y va por encima
de cualquier otra: **fuera de ``prod`` no se crea ningún documento en el
ambiente 1 ni se le envía nada a la DIAN de producción**, diga lo que diga el
emisor, el documento o quien llame.

Se comprueba en los dos extremos y no en las vistas, para que ningún camino
(API, comando, tarea, shell) la esquive: al guardar un documento o una nómina
nuevos, y en el cliente SOAP justo antes de cada envío.
"""
from urllib.parse import urlsplit

from django.conf import settings

from .api import ErrorSolicitud
from .models import Ambiente

ENTORNO_PRODUCCION = "prod"

# El servidor de producción de la DIAN. Fijo y no leído de `DIAN_WSDL`: lo que
# se protege es ese servidor, apunte a donde apunte la configuración.
HOST_DIAN_PRODUCCION = "vpfe.dian.gov.co"


class AmbienteBloqueado(ErrorSolicitud):
    """Se intentó usar el ambiente 1 de la DIAN fuera de ``ENV=prod``."""

    default_code = "ambiente_bloqueado"


def es_produccion() -> bool:
    return settings.ENV == ENTORNO_PRODUCCION


def ambiente_segun_habilitacion(habilitado) -> int:
    """El ambiente DIAN que corresponde a una operación habilitada o no.

    Producción solo si ya está habilitada **y** el despliegue es ``prod``; en
    ``dev`` y ``test`` siempre pruebas, esté como esté la habilitación.
    """
    if habilitado and es_produccion():
        return Ambiente.PRODUCCION
    return Ambiente.PRUEBAS


def exigir_ambiente_permitido(ambiente, accion):
    """Lanza ``AmbienteBloqueado`` si ``ambiente`` es producción y ``ENV`` no."""
    if es_produccion() or ambiente is None:
        return
    if int(ambiente) == Ambiente.PRODUCCION:
        raise AmbienteBloqueado(
            f"ENV={settings.ENV}: no se permite {accion} en el ambiente 1 "
            f"(producción) de la DIAN. Solo se puede con ENV=prod."
        )


def validar_ambiente_de_creacion(serializer, attrs, emisor, operacion):
    """Las reglas del ``ambiente`` que trae un documento o nómina al crearse.

    Común a los dos serializers de creación: el ambiente se fija al crear y no
    cambia después —entra en el CUFE/CUNE y decide el servidor de envío—, fuera
    de ``ENV=prod`` no puede ser producción, y a producción solo sale la
    operación que la DIAN ya habilitó. Esta última no se exige a la
    facturación: hay emisores que ya facturan en producción con la bandera sin
    marcar.
    """
    from rest_framework import serializers

    instancia = serializer.instance
    ambiente = attrs.get("ambiente")
    if instancia is not None:
        if ambiente is not None and ambiente != instancia.ambiente:
            raise serializers.ValidationError({
                "ambiente": "El ambiente se fija al crear y no se puede cambiar."
            })
        return
    try:
        exigir_ambiente_permitido(ambiente, "crear documentos")
    except AmbienteBloqueado as exc:
        raise serializers.ValidationError(
            {"ambiente": serializers.ErrorDetail(str(exc.detail), exc.default_code)}
        )
    if (
        ambiente == Ambiente.PRODUCCION
        and operacion != "facturacion"
        and emisor is not None
        and not emisor.habilitado_para(operacion)
    ):
        # La DIAN rechaza con la regla 92 ("El Emisor del Documento no se
        # encuentra Habilitado"), que manda a buscar el error dentro del
        # documento cuando lo que falta es el trámite.
        etiqueta = operacion.replace("_", " ")
        raise serializers.ValidationError({
            "ambiente": f"El emisor no está habilitado para {etiqueta}, así "
            f"que no puede emitir en producción (ambiente 1)."
        })


def exigir_envio_permitido(url, ambiente=None):
    """Lo mismo para un envío, mirando además a qué servidor va de verdad."""
    exigir_ambiente_permitido(ambiente, "enviar")
    if es_produccion():
        return
    if (urlsplit(url).hostname or "").lower() == HOST_DIAN_PRODUCCION:
        raise AmbienteBloqueado(
            f"ENV={settings.ENV}: no se permite enviar a la DIAN de producción "
            f"({HOST_DIAN_PRODUCCION}). Solo se puede con ENV=prod."
        )
