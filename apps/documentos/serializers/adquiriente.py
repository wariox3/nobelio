"""Serializer del adquiriente: va anidado dentro del documento."""
from rest_framework import serializers

from apps.catalogos.memoria import RelacionDeCatalogo
from apps.documentos.models import Adquiriente
from apps.nucleo.serializers import EstructuraEstricta

# Lista TipoOrganizacion de la DIAN.
CODIGO_PERSONA_JURIDICA = "1"
CODIGO_PERSONA_NATURAL = "2"
CODIGO_PAIS_COLOMBIA = "CO"

CAMPOS_NOMBRE = ("primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido")
# Los segundos no: no todo el mundo tiene segundo nombre o segundo apellido.
OBLIGATORIOS_PERSONA_NATURAL = {
    "primer_nombre": "su primer nombre",
    "primer_apellido": "su primer apellido",
}
# La dirección colombiana del XML: `cbc:ID` y `CityName` salen del municipio,
# `CountrySubentity` del departamento y `AddressLine` de la dirección.
OBLIGATORIOS_EN_COLOMBIA = {
    "departamento": "el departamento",
    "municipio": "el municipio",
    "direccion": "la dirección",
}
# Son catálogos colombianos: fuera de Colombia no significan nada.
SOLO_EN_COLOMBIA = ("departamento", "municipio")


def mensaje_falta_en_persona_natural(campo):
    """Mensaje para una persona natural a la que le falta parte del nombre."""
    return f"Una persona natural debe informar {OBLIGATORIOS_PERSONA_NATURAL[campo]}."


def mensaje_falta_en_colombia(campo):
    """Mensaje para un adquiriente en Colombia al que le falta la ubicación."""
    return f"Un adquiriente en Colombia debe informar {OBLIGATORIOS_EN_COLOMBIA[campo]}."


def mensaje_municipio_de_otro_departamento(municipio, departamento):
    """Mensaje para un municipio que no es del departamento informado."""
    return (
        f"El municipio {municipio.nombre} ({municipio.codigo}) no pertenece al "
        f"departamento {departamento.nombre} ({departamento.codigo})."
    )


def _pertenece(municipio, departamento):
    """¿Es el municipio de ese departamento?

    Manda la relación del catálogo. Como es nullable, cuando no está cargada se
    compara lo que la sustituye: el código DANE del municipio empieza por el
    del departamento (05001 es de 05, Antioquia).
    """
    if municipio.departamento_id is not None:
        return municipio.departamento_id == departamento.pk
    return municipio.codigo[:2] == departamento.codigo


class AdquirienteSerializer(EstructuraEstricta, serializers.ModelSerializer):
    """Datos del receptor. No tiene endpoint propio: se piden en el documento."""

    # Tipo de identificación, organización, país, departamento, municipio y
    # responsabilidades: todos catálogos.
    serializer_related_field = RelacionDeCatalogo

    class Meta:
        model = Adquiriente
        fields = [
            "razon_social",
            "primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido",
            "tipo_identificacion", "numero_identificacion",
            "digito_verificacion", "tipo_organizacion", "responsabilidades",
            "pais", "departamento", "municipio", "direccion", "codigo_postal",
            "telefono", "correo",
        ]
        # El código postal se exige siempre, aunque el modelo lo admita vacío:
        # es el `cbc:PostalZone` del XML y no hay forma de completarlo después
        # —un documento firmado ya no se edita—, así que se pide al crear y no
        # cuando la DIAN lo rechace con el consecutivo ya gastado.
        #
        # El dígito de verificación es de solo lectura: lo calcula
        # `Adquiriente.save` a partir del NIT, y el que mandara el ERP se
        # sobrescribía sin avisar. Mandarlo responde 400; en la lectura sale el
        # calculado.
        #
        # `responsabilidades` es obligatoria como clave. Admite la lista vacía,
        # que el XML emite como `R-99-PN` («No responsable»).
        #
        # El correo también, y con valor, en todos los tipos de documento: es a
        # donde `notificar` entrega el documento, y sin él el fallo aparecía
        # después, al notificar, con el documento ya emitido.
        extra_kwargs = {
            "codigo_postal": {"required": True, "allow_blank": False},
            "digito_verificacion": {"read_only": True},
            "responsabilidades": {"required": True},
            "correo": {"required": True, "allow_blank": False},
        }

    def validate(self, attrs):
        """Reglas que dependen de otro dato del propio adquiriente.

        Viven aquí y no en el documento porque solo miran al adquiriente, y
        valen igual en todos los tipos de documento, el soporte incluido. Los
        errores de las dos se informan juntos.
        """
        errores = {**self._validar_nombre(attrs), **self._validar_ubicacion(attrs)}
        if errores:
            raise serializers.ValidationError(errores)
        return attrs

    def _validar_nombre(self, attrs):
        """El nombre desglosado depende del tipo de organización.

        Antes no dependía de nada: el XML emite ``cac:Person`` con que venga
        cualquier parte del nombre, así que una persona natural salía sin él y
        una empresa con nombres salía como si fuera una persona.

        - **Persona natural:** primer nombre y primer apellido obligatorios.
        - **Persona jurídica:** los nombres se descartan, por decisión de
          MarioA, en vez de rechazar el documento.
        """
        codigo = getattr(attrs.get("tipo_organizacion"), "codigo", None)
        if codigo == CODIGO_PERSONA_NATURAL:
            return {
                campo: mensaje_falta_en_persona_natural(campo)
                for campo in OBLIGATORIOS_PERSONA_NATURAL
                if not attrs.get(campo)
            }
        if codigo == CODIGO_PERSONA_JURIDICA:
            for campo in CAMPOS_NOMBRE:
                attrs[campo] = ""
        return {}

    def _validar_ubicacion(self, attrs):
        """La ubicación depende del país.

        - **Colombia:** departamento, municipio y dirección obligatorios, y el
          municipio tiene que ser de ese departamento: los dos van en la misma
          dirección del XML (``CityName`` y ``CountrySubentity``), y antes
          Medellín con Cundinamarca pasaba. Sin municipio el XML no emitía
          ``PhysicalLocation`` y la ``RegistrationAddress`` salía solo con el
          país.
        - **Otro país:** departamento y municipio se descartan: son catálogos
          colombianos. La dirección se conserva.

        Sustituye a la regla que solo tenía el documento soporte residente
        (municipio, dirección y código postal): el código postal ya lo exige la
        estructura, y el resto vale ahora para todos los tipos. Aquella regla
        tenía un motivo que sigue en pie: en el documento soporte el país del
        vendedor decide el ``CustomizationID`` —Colombia ``10``, fuera ``11``—, y
        con ``10`` la DIAN rechaza (DSAJ08a) el grupo ``cac:PhysicalLocation``
        incompleto, cuando el consecutivo ya está reservado.
        """
        codigo = getattr(attrs.get("pais"), "codigo", None)
        if codigo == CODIGO_PAIS_COLOMBIA:
            errores = {
                campo: mensaje_falta_en_colombia(campo)
                for campo in OBLIGATORIOS_EN_COLOMBIA
                if not attrs.get(campo)
            }
            departamento, municipio = attrs.get("departamento"), attrs.get("municipio")
            if departamento and municipio and not _pertenece(municipio, departamento):
                errores["municipio"] = mensaje_municipio_de_otro_departamento(
                    municipio, departamento,
                )
            return errores
        if codigo is not None:
            for campo in SOLO_EN_COLOMBIA:
                attrs[campo] = None
        return {}
