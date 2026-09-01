"""Serializer de los datos de la venta en caja (documento equivalente P.O.S.)."""
from rest_framework import serializers

from apps.documentos import models


class DocumentoPOSSerializer(serializers.ModelSerializer):
    """Va anidado en el documento, como el adquiriente.

    Alimenta dos de las tres extensiones obligatorias del P.O.S. (DEPD11 y
    DEPD21). La tercera, la del fabricante del software, sale de
    ``SoftwareDian`` y no se pide aquí: es constante para todos los tiquetes.

    La caja llega aquí y no por id de un maestro: el punto de venta conoce su
    placa, no nuestros identificadores internos, y así no hace falta darla de
    alta antes de vender.

    Los campos del comprador y el subtotal pueden omitirse; el constructor los
    toma del propio documento. No es que la DIAN los admita vacíos —los tres
    pares son de rechazo—, es que su valor ya está en el documento y pedirlo dos
    veces solo daría ocasión de que no coincidan.
    """

    class Meta:
        model = models.DocumentoPOS
        fields = [
            "caja_placa", "caja_ubicacion", "caja_tipo",
            "cajero", "codigo_venta", "subtotal",
            "comprador_codigo", "comprador_nombres", "comprador_puntos",
        ]
