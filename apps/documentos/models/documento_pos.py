"""Lo que el documento equivalente P.O.S. lleva y los demás documentos no."""
from django.db import models

from apps.nucleo.models import ModeloConFechas


class DocumentoPOS(ModeloConFechas):
    """Datos de la venta en caja, para las extensiones propias del P.O.S.

    Satélite y no columnas de ``Documento`` a propósito: son ocho campos que
    solo tienen sentido en un tiquete de caja, y meterlos en la tabla común
    obligaría a cada factura de venta a cargar con el nombre del cajero.

    **La caja no tiene maestro**: sus tres datos son columnas de aquí. Se
    valoró tenerlo —una registradora es un equipo que dura y emite miles de
    tiquetes— y se descartó: la DIAN no la contrasta contra ningún registro,
    solo quiere tres cadenas en la extensión, y un maestro obligaba al punto de
    venta a conocer un id nuestro y a darse de alta antes de vender.

    Tiene además una ventaja que el maestro no daba gratis: al ser columnas del
    documento, lo que se guarda es **lo que se emitió**. Si la caja se muda de
    pasillo, los tiquetes de ayer siguen diciendo dónde estaba ayer. Es el mismo
    criterio que ``Nomina`` aplica a las condiciones del periodo.

    Alimenta dos de las tres extensiones obligatorias (DEPD11 y DEPD21):
    ``InformacionCajaVenta`` e ``InformacionBeneficiosComprador``. La tercera,
    la del fabricante del software, sale de ``SoftwareDian``, que es donde ese
    dato es constante.

    Los campos del comprador y el subtotal admiten quedar vacíos: el
    constructor los rellena desde el propio documento (ver
    ``ConstructorDocumentoEquivalentePOS``). No es que sean opcionales para la
    DIAN —los tres pares son de rechazo—, es que su valor ya está en el
    documento y pedirlo dos veces solo daría ocasión de que no coincidan.

    Referencia: Anexo Técnico de documento equivalente electrónico v1.0,
    numerales 8.2.1.1 y 8.2.1.2.
    """

    documento = models.OneToOneField(
        "documentos.Documento", on_delete=models.CASCADE,
        related_name="pos", verbose_name="documento",
    )
    # --- La caja registradora, tal como estaba en esta venta ---
    caja_placa = models.CharField(
        "placa de la caja", max_length=50,
        help_text="Placa de inventario de la caja (par `PlacaCaja`).",
    )
    caja_ubicacion = models.CharField(
        "ubicación de la caja", max_length=255,
        help_text="Dónde estaba la caja (par `UbicaciónCaja`, con tilde: es el "
        "literal que compara la DIAN).",
    )
    caja_tipo = models.CharField(
        "tipo de caja", max_length=100,
        help_text="Par `TipoCaja`. El anexo no da lista de valores; la "
        "ejemplificación oficial usa texto libre ('Caja de apoyo').",
    )

    # --- De esta venta en concreto ---
    cajero = models.CharField(
        "cajero", max_length=200,
        help_text="Nombres y apellidos del cajero o vendedor (par `Cajero`).",
    )
    codigo_venta = models.CharField(
        "código de la venta", max_length=50,
        help_text="Identificador interno de la transacción (par `CódigoVenta`, "
        "con tilde).",
    )
    subtotal = models.DecimalField(
        "subtotal de la venta", max_digits=15, decimal_places=2,
        null=True, blank=True,
        help_text="Par `SubTotal`. Si se deja vacío se emite el `valor_bruto` "
        "del documento. Se admite informarlo aparte porque la ejemplificación "
        "oficial trae ahí un importe distinto del LineExtensionAmount y el "
        "anexo no aclara la diferencia.",
    )

    # --- Datos y beneficios del comprador (numeral 8.2.1.1) ---
    # El nombre de la extensión engaña: no es solo el programa de puntos. La
    # regla DEPD16 dice que el `Codigo` es "el documento de identidad" del
    # comprador, así que esto identifica al comprador y de paso lleva sus
    # puntos.
    comprador_codigo = models.CharField(
        "código del comprador", max_length=50, blank=True,
        help_text="Documento de identidad del comprador. Vacío emite el del "
        "adquiriente del documento.",
    )
    comprador_nombres = models.CharField(
        "nombres del comprador", max_length=200, blank=True,
        help_text="Vacío emite el nombre del adquiriente del documento.",
    )
    comprador_puntos = models.PositiveIntegerField(
        "puntos acumulados", default=0,
        help_text="Puntos del programa de fidelización. Cero si no hay "
        "programa: el par es obligatorio y omitirlo es un rechazo.",
    )

    class Meta:
        db_table = "doc_documento_pos"
        verbose_name = "datos P.O.S. del documento"
        verbose_name_plural = "datos P.O.S. de los documentos"

    def __str__(self):
        return f"P.O.S. {self.documento_id} — caja {self.caja_placa}"
