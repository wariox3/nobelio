"""Piezas de serializer compartidas por las apps.

``EstructuraEstricta`` valida la **estructura** de la petición antes que sus
datos: que no venga ninguna clave que el serializer no pueda escribir y que no
falte ninguna obligatoria.

Lo que sobra era el problema de origen. DRF descarta sin avisar lo que no
conoce, y también lo que conoce pero es de solo lectura; en la recepción de un
documento eso es un error silencioso: un campo con el nombre mal escrito no
llega a la base, la petición responde 201 y el fallo solo aparece cuando la
DIAN rechaza el documento —con el consecutivo ya gastado—. Pasó el 2026-09-01
con un P.O.S. cuyos impuestos llegaron con otro nombre y se guardaron en cero.

**La estructura se valida primero y sola**, por decisión de MarioA. Antes de
mirar ningún dato se recorre la petición entera, anidados incluidos, y si sobra
o falta alguna clave la respuesta solo trae eso. Una estructura mal dice que
quien integra está leyendo otro contrato, y mezclarlo con errores de datos —un
emisor que no existe, una fecha que no es la de hoy— los hace parecer del mismo
tipo cuando no lo son.

Por eso el recorrido lo hace la raíz y no cada nivel por su cuenta: DRF valida
un anidado llamando a **su** ``to_internal_value`` en medio de la validación de
los campos del padre, así que para cuando un anidado encontrara su error el
padre ya habría validado el resto. El mixin va delante de la clase de DRF
(``class X(EstructuraEstricta, ModelSerializer)``) y hay que ponerlo también en
cada anidado: el recorrido solo baja a los que lo llevan.

"Falta" es la clave ausente de un campo que el serializer declara obligatorio
siempre. Los obligatorios que dependen de otro dato —la resolución según el
tipo de documento, la referencia de una nota, el bloque ``pos``, los conceptos
según el tipo de nota— son reglas de negocio y siguen en ``validate()``. Y un
valor presente pero vacío, nulo o de otro tipo es un error de datos, no de
estructura.
"""
from collections.abc import Mapping

from django.utils.encoding import force_str
from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail

# Los códigos que DRF ya define se usan tal cual —el que falta es `required`,
# lo detecte este recorrido o la validación de campos—; los propios del
# proyecto van en español, como `solicitud_invalida`.
CODIGO_CAMPO_DESCONOCIDO = "campo_desconocido"
CODIGO_CAMPO_SOLO_LECTURA = "campo_solo_lectura"
CODIGO_OBLIGATORIO = "required"

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
        errores = self.errores_de_estructura(data)
        if errores:
            raise serializers.ValidationError(errores)
        return super().to_internal_value(data)

    def errores_de_estructura(self, data):
        """Las claves que sobran o faltan en ``data`` y en sus anidados.

        Devuelve los errores con la misma forma que DRF da a los de campo —un
        dict por objeto, una lista con un dict por elemento—, para que el
        cliente los lea igual. Vacío si la estructura está bien.

        Lo que no tiene la forma esperada (un texto donde va un objeto, un
        objeto donde va una lista) no se recorre: no hay claves que comparar, y
        el error de tipo lo da la validación de campos.
        """
        if not isinstance(data, Mapping):
            return {}
        errores = {}
        for clave, valor in data.items():
            campo = self.fields.get(clave)
            if campo is None:
                errores[clave] = [
                    ErrorDetail(MENSAJE_CAMPO_DESCONOCIDO, CODIGO_CAMPO_DESCONOCIDO)
                ]
            elif campo.read_only:
                errores[clave] = [
                    ErrorDetail(MENSAJE_CAMPO_SOLO_LECTURA, CODIGO_CAMPO_SOLO_LECTURA)
                ]
            elif anidados := _errores_del_anidado(campo, valor):
                errores[clave] = anidados
        # En una actualización parcial lo ausente es "no lo toques". Hoy ninguna
        # ruta de documentos la tiene, pero el mixin no debe romperla si llega.
        if not getattr(self.root, "partial", False):
            for nombre, campo in self.fields.items():
                if campo.required and nombre not in data:
                    errores[nombre] = [ErrorDetail(
                        force_str(campo.error_messages["required"]), CODIGO_OBLIGATORIO,
                    )]
        return errores


def _errores_del_anidado(campo, valor):
    """Los errores de estructura de un campo anidado, o vacío si no lo es."""
    if isinstance(campo, serializers.ListSerializer):
        hijo = campo.child
        if not isinstance(hijo, EstructuraEstricta) or not isinstance(valor, list):
            return []
        por_elemento = [hijo.errores_de_estructura(elemento) for elemento in valor]
        return por_elemento if any(por_elemento) else []
    if isinstance(campo, EstructuraEstricta):
        return campo.errores_de_estructura(valor)
    return {}
