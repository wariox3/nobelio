"""Serializer del software DIAN del emisor."""
from rest_framework import serializers

from apps.emisores.models import SoftwareDian
from apps.emisores.servicios import motivo_no_puede_emitir


class SoftwareDianSerializer(serializers.ModelSerializer):
    class Meta:
        model = SoftwareDian
        fields = [
            "id", "emisor", "identificador", "pin",
            "test_set_id", "set_pruebas_aceptado", "activo",
        ]
        extra_kwargs = {
            # El PIN es sensible: se acepta al crear/editar pero nunca se devuelve.
            "pin": {"write_only": True},
        }

    def validate(self, attrs):
        """El emisor tiene que traer ya su certificado digital.

        El certificado va antes que el software en el flujo: es lo que firma la
        consulta de numeración y los documentos del Set de Pruebas, que son los
        dos pasos que siguen. Registrar el software sin él deja al emisor a
        medio habilitar, sin poder avanzar y sin que nada lo diga.
        """
        emisor = attrs.get("emisor") or getattr(self.instance, "emisor", None)
        if emisor is None:
            return attrs
        motivo = motivo_no_puede_emitir(emisor)
        if motivo:
            raise serializers.ValidationError({"emisor": motivo})
        return attrs


class HabilitarSerializer(SoftwareDianSerializer):
    """El software que se registra al habilitar: aquí el TestSetId es obligatorio.

    En el modelo ``test_set_id`` es opcional a propósito —un software registrado
    ya en producción no tiene ninguno—, pero ``habilitar/`` es justo el caso en
    que se necesita: es el set al que se envían la factura y la nota de prueba.
    Sin exigirlo aquí, la llamada respondía 201 con el fallo escondido en
    ``set_pruebas.error``, después de haber jubilado el software anterior.
    """

    test_set_id = serializers.CharField(max_length=100)

    def validate(self, attrs):
        # Antes que la comprobación del padre (el certificado del emisor): un
        # dato de la propia petición no debe quedar tapado por uno de
        # configuración de la cuenta.
        self._exigir_identificador_libre(attrs)
        return super().validate(attrs)

    def _exigir_identificador_libre(self, attrs):
        """El SoftwareID que asigna la DIAN se registra una sola vez.

        Se miran también los jubilados (``activo=False``): la fila existe, y
        volver a darlo de alta duplicaría el mismo software de la DIAN en dos
        registros con historiales distintos.
        """
        identificador = attrs.get("identificador")
        if not identificador:
            return
        emisor = attrs.get("emisor") or getattr(self.instance, "emisor", None)
        ya_existe = SoftwareDian.objects.filter(identificador=identificador).first()
        if ya_existe is None:
            return
        if emisor is not None and ya_existe.emisor_id == emisor.pk:
            mensaje = (
                f"El software '{identificador}' ya está registrado para este "
                f"emisor. Dé de baja el registro antes de volver a crearlo."
            )
        else:
            # Sin decir de quién es: el SoftwareID identifica a un tercero.
            mensaje = (
                f"El software '{identificador}' ya está registrado en el "
                f"sistema para otro emisor."
            )
        raise serializers.ValidationError({"identificador": mensaje})
