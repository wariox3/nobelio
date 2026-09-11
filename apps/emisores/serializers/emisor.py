"""Serializer del emisor."""
from rest_framework import serializers

from apps.catalogos.models import (
    Departamento,
    Municipio,
    Pais,
    ResponsabilidadFiscal,
)
from apps.emisores.models import Emisor, ambiente_por_defecto
from apps.nucleo.models import Ambiente

from .resolucion import ResolucionSerializer


class CodigoDeCatalogo(serializers.SlugRelatedField):
    """Campo de catálogo que entra y sale por su ``codigo``, no por el ``id``.

    El id es un serial de la base y no significa nada fuera de ella: el mismo
    municipio tiene distinto id en desarrollo y en producción, según en qué
    orden se cargaron las listas. El código sí es estable y es el que el ERP
    conoce —ISO 3166 para el país, DANE para departamento (2 dígitos) y
    municipio (5)—, así que es lo que se recibe y lo que se devuelve.
    """

    default_error_messages = {
        "does_not_exist": "No existe en el catálogo el código '{value}'.",
        "invalid": "Se espera el código del catálogo, no el id.",
    }

    def __init__(self, **kwargs):
        kwargs.setdefault("slug_field", "codigo")
        super().__init__(**kwargs)


# El NIT es único en toda la plataforma (ver Emisor.Meta.constraints), así que
# el mensaje no necesita decir dónde: no hay más de un sitio posible.
MENSAJE_DUPLICADO = (
    "El emisor con identificación {numero} ya está dado de alta."
)

# La DIAN rechaza con la regla 92 ("El Emisor del Documento no se encuentra
# Habilitado"), un mensaje que hace buscar el error dentro de la nómina cuando
# lo que falta es el trámite. Mejor decirlo aquí, que es donde se decide.
MENSAJE_NOMINA_SIN_HABILITAR = (
    "El emisor no está habilitado para nómina electrónica, así que no puede "
    "emitirla en producción. La bandera se marca sola al cerrarse el Set de "
    "Pruebas de nómina y no se puede escribir por la API."
)


# Mismo caso que la nómina: sin la habilitación del documento equivalente, la
# DIAN rechaza en producción y el mensaje manda a buscar el error dentro del
# tiquete cuando lo que falta es el trámite.
MENSAJE_DOCUMENTO_EQUIVALENTE_SIN_HABILITAR = (
    "El emisor no está habilitado para documento equivalente, así que no puede "
    "emitirlo en producción. La bandera se marca sola al cerrarse su Set de "
    "Pruebas y no se puede escribir por la API."
)


class EmisorSerializer(serializers.ModelSerializer):
    resoluciones = ResolucionSerializer(many=True, read_only=True)
    # La ubicación llega por código; el serializer resuelve la fila y guarda su
    # llave, que es lo que la FK necesita.
    pais = CodigoDeCatalogo(queryset=Pais.objects.all())
    departamento = CodigoDeCatalogo(queryset=Departamento.objects.all())
    municipio = CodigoDeCatalogo(queryset=Municipio.objects.all())
    # Igual que la ubicación: por su código de la lista TipoResponsabilidad
    # ('O-13', 'O-15', 'O-23', 'O-47', 'R-99-PN'), que es lo que viaja en el
    # TaxLevelCode del XML y lo que el ERP conoce. Sin ninguna, el XML sale con
    # 'R-99-PN' (ver `SIN_RESPONSABILIDAD` en apps.dian.ubl).
    responsabilidades = CodigoDeCatalogo(
        queryset=ResponsabilidadFiscal.objects.all(), many=True, required=False,
    )
    # Las tres banderas de habilitación son de solo lectura. Ninguna es una
    # decisión de quien llama: son la constancia de un hecho que declara la
    # DIAN, y decir 'ya estoy habilitado' en el cuerpo de una petición no
    # habilita a nadie. Las marca `_marcar_habilitacion_superada`
    # (`apps/dian/servicios.py`) al cerrarse el Set de Pruebas de cada
    # operación —hay uno por operación, la nómina incluida—, y cada una se
    # habilita por separado.
    #
    # Que además condicionen el paso a producción (ver `validate`) es la razón
    # de peso: de escritura, cualquier cliente se saltaba la habilitación
    # entera marcando la bandera en el mismo PATCH que pone el ambiente en
    # producción. Si hay que ponerlas a mano —porque la DIAN habilitó por fuera
    # del automatismo, o porque cambió el texto que reconoce
    # `_set_pruebas_cerrado`—, se hace por backend.
    habilitado_facturacion = serializers.BooleanField(read_only=True)
    habilitado_nomina = serializers.BooleanField(read_only=True)
    habilitado_documento_equivalente = serializers.BooleanField(read_only=True)
    # Los ambientes sí son de escritura, y ahí está la diferencia con las
    # banderas: 'estoy habilitado' es un hecho que constata la DIAN, mientras
    # que 'emite contra producción' es una decisión nuestra sobre este emisor.
    # Es lo que permite pasar a uno a producción sin mover a los demás.

    def validate(self, attrs):
        """Comprueba que el emisor no esté ya dado de alta.

        El NIT no se contrasta con el RUES al dar de alta: es un registro ajeno
        y a veces caído, y no puede decidir si un alta entra o no. Quien quiera
        comprobarlo tiene ``GET /api/emisores/emisor/validar-nit/``, que además
        devuelve los datos para autocompletar el formulario.
        """
        def valor(campo):
            return attrs.get(campo, getattr(self.instance, campo, None))

        # En un alta que no trae `ambiente_nomina` el valor lo pone el default
        # del modelo, así que es el que hay que comprobar: con el servidor ya
        # en producción, un emisor nuevo nacería en producción sin haber hecho
        # el trámite y sin pasar por ninguna validación.
        ambiente_nomina = valor("ambiente_nomina")
        if ambiente_nomina is None:
            ambiente_nomina = ambiente_por_defecto()
        self.exigir_habilitacion_de_nomina(
            ambiente_nomina, valor("habilitado_nomina"),
        )

        # Lo mismo para el documento equivalente, y por el mismo motivo: su
        # habilitación es un trámite aparte y salir a producción sin ella es la
        # regla 92 otra vez.
        ambiente_de = valor("ambiente_documento_equivalente")
        if ambiente_de is None:
            ambiente_de = ambiente_por_defecto()
        self.exigir_habilitacion_de_documento_equivalente(
            ambiente_de, valor("habilitado_documento_equivalente"),
        )

        tipo = valor("tipo_identificacion")
        numero = valor("numero_identificacion")
        if not (tipo and numero):
            # Falta algo para poder comprobarlo: ya lo reportan los validadores
            # de campo.
            return attrs

        self.exigir_no_duplicado(tipo, numero)
        return attrs

    def exigir_habilitacion_de_nomina(self, ambiente, habilitado):
        """Producción de nómina solo después del trámite ante la DIAN.

        Es lo único que ata las dos parejas de campos: el ambiente decide contra
        qué servidor se emite y la bandera dice si la DIAN ya habilitó a este
        emisor para nómina. Sin el trámite, todo lo que salga a producción se
        rechaza, así que dejarlo pasar solo aplaza el fallo hasta la primera
        nómina y lo disfraza de error del documento.

        No existe la simétrica para facturación a propósito: no se ha pedido
        cerrar ese paso, y hacerlo ahora dejaría en habilitación a los emisores
        que ya están facturando en producción con la bandera sin marcar.
        """
        if ambiente == Ambiente.PRODUCCION and not habilitado:
            raise serializers.ValidationError(
                {"ambiente_nomina": MENSAJE_NOMINA_SIN_HABILITAR}
            )

    def exigir_habilitacion_de_documento_equivalente(self, ambiente, habilitado):
        """Producción de documento equivalente solo después de su habilitación.

        Gemela de ``exigir_habilitacion_de_nomina``: cada operación se habilita
        por separado, así que estar en producción para factura no dice nada del
        tiquete P.O.S.
        """
        if ambiente == Ambiente.PRODUCCION and not habilitado:
            raise serializers.ValidationError(
                {"ambiente_documento_equivalente":
                    MENSAJE_DOCUMENTO_EQUIVALENTE_SIN_HABILITAR}
            )

    def exigir_no_duplicado(self, tipo, numero):
        """La unicidad del emisor, que ahora es global.

        Sustituye al ``UniqueTogetherValidator`` que DRF genera solo, cuyo error
        aterriza en ``non_field_errors``, donde el formulario no lo puede
        señalar. Aquí cuelga de ``numero_identificacion``, que es el campo que
        hay que corregir.

        Ojo con lo que **no** dice el mensaje: si el NIT lo tiene dado de alta
        otra persona, no se revela quién. Sería filtrar quién usa la plataforma.
        """
        repetidos = Emisor.objects.filter(
            tipo_identificacion=tipo, numero_identificacion=numero
        )
        if self.instance is not None:
            repetidos = repetidos.exclude(pk=self.instance.pk)
        if repetidos.exists():
            raise serializers.ValidationError(
                {"numero_identificacion": MENSAJE_DUPLICADO.format(numero=numero)}
            )

    class Meta:
        model = Emisor
        fields = [
            "id", "usuario", "razon_social",
            "tipo_identificacion", "numero_identificacion", "digito_verificacion",
            "tipo_organizacion", "responsabilidades",
            "pais", "departamento", "municipio", "direccion", "codigo_postal",
            "correo_copia",
            "telefono", "correo", "activo",
            "habilitado_facturacion", "habilitado_nomina",
            "habilitado_documento_equivalente",
            "ambiente_facturacion", "ambiente_nomina",
            "ambiente_documento_equivalente",
            "resoluciones",
        ]
        # El dueño no se manda: lo pone la vista con quien hace la petición.
        # Aceptarlo del cuerpo dejaría dar de alta emisores a nombre de otro.
        read_only_fields = ["usuario"]
        # Vacío a propósito: desactiva el UniqueTogetherValidator automático de
        # DRF para que la unicidad la explique `validate()` con un mensaje útil.
        validators = []


# Campos del emisor que el listado no devuelve: la resolución solo interesa en
# el detalle, el municipio sale desglosado (id, nombre y código) y país y
# departamento no se muestran en la tabla.
FUERA_DEL_LISTADO = {"resoluciones", "municipio", "departamento", "pais"}


class EmisorListaSerializer(EmisorSerializer):
    """El emisor tal y como sale en el listado.

    Una resolución solo interesa al abrir el emisor concreto, y anidarlas en el
    listado infla la respuesta —y obliga a traerlas todas— sin que nadie las
    mire. ``GET /api/emisores/emisor/{id}/`` las sigue devolviendo.
    """

    # El listado se pinta en una tabla: necesita el nombre del municipio ya
    # resuelto, y su id para poder seleccionarlo en un formulario sin volver a
    # buscarlo en el catálogo. Sustituyen a `municipio`, que aquí solo repetía
    # el código; el detalle lo sigue devolviendo (y recibiendo) como siempre.
    municipio_id = serializers.IntegerField(read_only=True)
    municipio_nombre = serializers.CharField(source="municipio.nombre", read_only=True)
    municipio_codigo = serializers.CharField(source="municipio.codigo", read_only=True)

    class Meta(EmisorSerializer.Meta):
        fields = [
            campo
            for campo in EmisorSerializer.Meta.fields
            if campo not in FUERA_DEL_LISTADO
        ] + ["municipio_id", "municipio_nombre", "municipio_codigo"]
