"""Piezas de serializer compartidas por las apps.

``EstructuraEstricta`` rechaza con 400 las claves que el serializer no puede
escribir. DRF descarta sin avisar lo que no conoce, y también lo que conoce pero
es de solo lectura. En la recepción de un documento eso es un error silencioso:
un campo con el nombre mal escrito no llega a la base, la petición responde 201
y el fallo solo aparece cuando la DIAN rechaza el documento —con el consecutivo
ya gastado—. Pasó el 2026-09-01 con un P.O.S. cuyos impuestos llegaron con otro
nombre y se guardaron en cero.

Va delante de la clase de DRF (``class X(EstructuraEstricta, ModelSerializer)``)
y hay que ponerlo también en cada serializer anidado: DRF valida un anidado
llamando a **su** ``to_internal_value``, así que el padre no ve lo que trae
dentro, pero sí anida el error bajo su campo
(``{"detalles": [{"valor_totl": [...]}]}``).

Los sobrantes se informan junto con los errores de los campos, no en lugar de
ellos: quien integra corrige todo en una vuelta. Lo que no llega a correr con un
sobrante es el ``validate()`` del objeto, que DRF ejecuta después; con la
estructura mal, sus reglas cruzadas tampoco significarían mucho.
"""
from collections.abc import Mapping

from rest_framework import serializers

MENSAJE_CAMPO_DESCONOCIDO = (
    "Campo desconocido: no existe en esta petición. Revise el nombre; tal como "
    "viene no se guardaría."
)
MENSAJE_CAMPO_SOLO_LECTURA = (
    "Es de solo lectura: lo pone el sistema y no se acepta en la petición."
)


class EstructuraEstricta:
    # Sin docstring a propósito: drf-spectacular describe cada serializer con la
    # primera docstring que encuentra en su MRO, y los anidados que no tienen la
    # suya publicarían esta nota interna en `schema.yml`. La explicación está en
    # la del módulo.

    def to_internal_value(self, data):
        sobrantes = self._campos_sobrantes(data)
        try:
            valores = super().to_internal_value(data)
        except serializers.ValidationError as exc:
            # Sin sobrantes el error es el de siempre, del tipo que sea. Con
            # ellos, `data` era un dict, y DRF solo lanza aquí errores por campo.
            if not sobrantes:
                raise
            raise serializers.ValidationError({**exc.detail, **sobrantes})
        if sobrantes:
            raise serializers.ValidationError(sobrantes)
        return valores

    def _campos_sobrantes(self, data):
        """Las claves de ``data`` que no son un campo escribible, con su motivo."""
        if not isinstance(data, Mapping):
            # Lo que no es un objeto ya lo rechaza DRF con su propio mensaje.
            return {}
        errores = {}
        for clave in data:
            campo = self.fields.get(clave)
            if campo is None:
                errores[clave] = [MENSAJE_CAMPO_DESCONOCIDO]
            elif campo.read_only:
                errores[clave] = [MENSAJE_CAMPO_SOLO_LECTURA]
        return errores
