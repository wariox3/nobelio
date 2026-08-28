"""Documento soporte de pago de nómina electrónica (``NominaIndividual``)."""
from django.conf import settings
from django.db import models

from apps.nucleo.models import Ambiente as AmbienteDian, ModeloConFechas, ModeloUUID
from apps.utilidades.almacenamiento import almacenamiento_backblaze


def ambiente_por_defecto():
    """El ambiente DIAN configurado en el servidor.

    Ya no es el default del campo: la nómina hereda el ambiente de su emisor
    (ver ``save``), que lo tiene aparte del de facturación. Se conserva porque
    las migraciones ya hechas la referencian por su ruta de import.
    """
    return settings.DIAN_ENVIRONMENT


def _ruta_artefacto(instance, filename):
    """Ruta en el bucket: ``<emisor_id>/nomina/<aaaa>/<mm>/<archivo>``.

    Mismo criterio que los documentos electrónicos: aislado por emisor y
    agrupado por año/mes, pero en su propia carpeta para no mezclar los dos
    flujos.
    """
    fecha = instance.fecha_generacion
    return f"{instance.emisor_id}/nomina/{fecha:%Y/%m}/{filename}"


class Nomina(ModeloUUID, ModeloConFechas):
    """Un comprobante de nómina de un empleado en un periodo.

    No hereda de ``Documento`` ni comparte tabla con él a propósito: el XML de
    nómina no es UBL —otra raíz, otro namespace, la información en atributos y
    sin resolución, impuestos ni adquiriente—, así que lo único que tienen en
    común son el emisor, el software, el certificado y el ciclo de estados. Esos
    sí se reutilizan (ver ``estado``).

    Los totales se calculan desde los conceptos y se guardan porque entran en el
    CUNE: recalcularlos después de firmar cambiaría el hash del documento ya
    emitido. Ver ``docs/anexo-nomina.md`` §3.
    """

    # La misma lista que usa el documento electrónico (``apps.nucleo``): los
    # dos valores los define la DIAN y son los mismos para las dos operaciones.
    Ambiente = AmbienteDian

    class TipoXML(models.TextChoices):
        NOMINA = "102", "Documento soporte de pago de nómina electrónica"
        AJUSTE = "103", "Nota de ajuste de documento soporte de pago de nómina"

    class Envio(models.TextChoices):
        """Con qué operación salió a la DIAN.

        Hoy solo hay una: la nómina no tiene Set de Pruebas, así que no hay que
        elegir entre dos caminos como en la factura. Se guarda igual —y con el
        mismo nombre que en `Documento`— porque es parte del registro de qué se
        hizo con el documento, y porque el día que la DIAN añada una operación
        asíncrona el campo ya está.
        """

        SINCRONO = "nomina_sync", "Síncrono (SendNominaSync)"

    class TipoNota(models.TextChoices):
        """Qué le hace la nota al documento anterior (numeral 5.5.8).

        No hay "corrección parcial" como en las notas de factura: o se
        reemplaza el documento entero, o se elimina.
        """

        REEMPLAZAR = "1", "Reemplazar"
        ELIMINAR = "2", "Eliminar"

    # --- Identificación del documento ---
    prefijo = models.CharField(
        "prefijo", max_length=10, blank=True,
        help_text="Lo elige el emisor: la nómina no se numera con resolución "
                  "de la DIAN (regla NIE010).",
    )
    consecutivo = models.PositiveBigIntegerField("consecutivo")
    numero = models.CharField(
        "número", max_length=30, blank=True,
        help_text="Número del documento. Si se omite, se arma como prefijo + "
                  "consecutivo.",
    )

    # --- Periodo que se liquida ---
    fecha_liquidacion_inicio = models.DateField("inicio de la liquidación")
    fecha_liquidacion_fin = models.DateField("fin de la liquidación")
    tiempo_laborado = models.PositiveIntegerField(
        "tiempo laborado", help_text="Días laborados en el periodo (numeral 8.3.1).",
    )
    fecha_generacion = models.DateField("fecha de generación")
    hora_generacion = models.TimeField("hora de generación")
    # El XML admite varias (``FechasPagos`` es una lista), pero el caso normal
    # es una: se guarda una y el constructor emite ese único ``FechaPago``.
    fecha_pago = models.DateField("fecha de pago")

    # --- Condiciones del trabajador en este periodo ---
    # Copia de lo que decía el maestro al crear la nómina. No es duplicación
    # ociosa: el sueldo de agosto es un hecho de agosto, y con un solo valor
    # mutable en `Empleado` no habría forma de representarlo —al firmar se
    # emitiría el de hoy—. El maestro guarda lo vigente; el documento, lo que
    # se emitió. Los datos de identidad (documento y nombres) no se copian:
    # ahí un cambio es una corrección y debe propagarse a todo.
    codigo_trabajador = models.CharField(
        "código del trabajador", max_length=20, blank=True,
        help_text="Va en NumeroSecuenciaXML, que identifica el documento.",
    )
    alto_riesgo_pension = models.BooleanField("alto riesgo de pensión", default=False)
    salario_integral = models.BooleanField("salario integral", default=False)
    sueldo = models.DecimalField("sueldo", max_digits=15, decimal_places=2)
    # Nula mientras el trabajador siga vinculado; en la liquidación es un dato
    # de ese documento. La de ingreso no cambia y se queda en el maestro.
    fecha_retiro = models.DateField("fecha de retiro", null=True, blank=True)
    lugar_trabajo_direccion = models.CharField(
        "dirección del lugar de trabajo", max_length=255,
    )
    banco = models.CharField("banco", max_length=100, blank=True)
    tipo_cuenta = models.CharField(
        "tipo de cuenta", max_length=10, blank=True,
        choices=[("ahorros", "Ahorros"), ("corriente", "Corriente")],
    )
    numero_cuenta = models.CharField("número de cuenta", max_length=30, blank=True)

    # --- Identificadores DIAN ---
    ambiente = models.PositiveSmallIntegerField(
        "ambiente DIAN", choices=Ambiente.choices,
        help_text="Se hereda del ``ambiente_nomina`` del emisor al crear y se "
        "sella al firmar: la DIAN habilita la nómina aparte de la facturación, "
        "así que un emisor puede estar en producción para una y en "
        "habilitación para la otra.",
    )
    tipo_xml = models.CharField(
        "tipo de XML", max_length=3, choices=TipoXML.choices,
        default=TipoXML.NOMINA,
    )
    cune = models.CharField(
        "CUNE", max_length=96, blank=True,
        help_text="Código Único de Nómina Electrónica (SHA-384). Se calcula al firmar.",
    )
    envio = models.CharField(
        "operación de envío", max_length=20, choices=Envio.choices, blank=True,
        help_text="Con qué operación se envió a la DIAN. Vacío mientras no se "
        "haya enviado.",
    )
    # `Novedad`: marca que el documento recoge un cambio contractual y remite al
    # CUNE del documento donde estaba el dato anterior.
    novedad = models.BooleanField("novedad contractual", default=False)
    cune_novedad = models.CharField("CUNE de la novedad", max_length=96, blank=True)

    # --- Solo en la nota de ajuste ---
    tipo_nota = models.CharField(
        "tipo de nota", max_length=1, choices=TipoNota.choices, blank=True,
        help_text="Vacío en la nómina; obligatorio en la nota de ajuste.",
    )

    notas = models.TextField("notas", blank=True)
    trm = models.DecimalField(
        "TRM", max_digits=15, decimal_places=2, null=True, blank=True,
        help_text="Solo cuando la moneda no es COP.",
    )

    # --- Totales (entran en el CUNE) ---
    total_devengados = models.DecimalField(
        "total devengados", max_digits=15, decimal_places=2, default=0,
    )
    total_deducciones = models.DecimalField(
        "total deducciones", max_digits=15, decimal_places=2, default=0,
    )
    redondeo = models.DecimalField(
        "redondeo", max_digits=15, decimal_places=2, default=0,
    )
    total_comprobante = models.DecimalField(
        "total del comprobante", max_digits=15, decimal_places=2, default=0,
        help_text="Devengados menos deducciones, más el redondeo.",
    )

    fecha_validacion = models.DateTimeField(
        "fecha de validación DIAN", null=True, blank=True,
    )

    # --- Artefactos ---
    xml_archivo = models.FileField(
        "XML firmado", upload_to=_ruta_artefacto,
        storage=almacenamiento_backblaze, blank=True,
    )
    respuesta_archivo = models.FileField(
        "respuesta DIAN (cruda)", upload_to=_ruta_artefacto,
        storage=almacenamiento_backblaze, blank=True,
    )

    # --- Relaciones ---
    emisor = models.ForeignKey(
        "emisores.Emisor", on_delete=models.PROTECT,
        related_name="nominas", verbose_name="emisor",
    )
    empleado = models.ForeignKey(
        "nomina.Empleado", on_delete=models.PROTECT,
        related_name="nominas", verbose_name="empleado",
    )
    periodo_nomina = models.ForeignKey(
        "catalogos.PeriodoNomina", on_delete=models.PROTECT,
        related_name="nominas", verbose_name="periodo de nómina",
    )
    # Las demás condiciones del periodo, las que sí son catálogo.
    tipo_trabajador = models.ForeignKey(
        "catalogos.TipoTrabajador", on_delete=models.PROTECT,
        related_name="nominas", verbose_name="tipo de trabajador",
    )
    subtipo_trabajador = models.ForeignKey(
        "catalogos.SubTipoTrabajador", on_delete=models.PROTECT,
        related_name="nominas", verbose_name="subtipo de trabajador",
    )
    tipo_contrato = models.ForeignKey(
        "catalogos.TipoContrato", on_delete=models.PROTECT,
        related_name="nominas", verbose_name="tipo de contrato",
    )
    lugar_trabajo_pais = models.ForeignKey(
        "catalogos.Pais", on_delete=models.PROTECT,
        related_name="nominas", verbose_name="país del lugar de trabajo",
    )
    lugar_trabajo_departamento = models.ForeignKey(
        "catalogos.Departamento", on_delete=models.PROTECT,
        related_name="nominas", verbose_name="departamento del lugar de trabajo",
    )
    lugar_trabajo_municipio = models.ForeignKey(
        "catalogos.Municipio", on_delete=models.PROTECT,
        related_name="nominas", verbose_name="municipio del lugar de trabajo",
    )
    forma_pago = models.ForeignKey(
        "catalogos.FormaPago", on_delete=models.PROTECT,
        related_name="nominas", verbose_name="forma de pago",
    )
    medio_pago = models.ForeignKey(
        "catalogos.MedioPago", on_delete=models.PROTECT,
        related_name="nominas", verbose_name="medio de pago",
    )
    moneda = models.ForeignKey(
        "catalogos.Moneda", on_delete=models.PROTECT,
        related_name="nominas", verbose_name="moneda",
    )
    # El ciclo de vida es el mismo que el de los documentos electrónicos
    # (borrador → firmado → enviado → aceptado/rechazado) y la tabla de estados
    # ya existe: duplicarla solo daría dos catálogos que se desincronizan.
    estado = models.ForeignKey(
        "documentos.DocumentoEstado", on_delete=models.PROTECT,
        related_name="nominas", verbose_name="estado",
    )
    # La nómina que ajusta esta nota. De ella salen los tres datos del
    # `ReemplazandoPredecesor`/`EliminandoPredecesor`: número, CUNE y fecha.
    nomina_predecesora = models.ForeignKey(
        "nomina.Nomina", on_delete=models.PROTECT, null=True, blank=True,
        related_name="ajustes", verbose_name="nómina que ajusta",
    )

    class Meta:
        db_table = "nom_nomina"
        verbose_name = "nómina electrónica"
        verbose_name_plural = "nóminas electrónicas"
        ordering = ["-fecha_generacion", "-consecutivo"]
        constraints = [
            models.UniqueConstraint(
                fields=["emisor", "prefijo", "consecutivo"],
                name="nomina_numero_unico_por_emisor",
            )
        ]

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = f"{self.prefijo}{self.consecutivo}"
        # Del emisor y de su ambiente **de nómina**, que la DIAN habilita
        # aparte del de facturación. Se resuelve al crear y la nómina se lo
        # queda: entra en el CUNE y decide el servidor de envío, así que
        # cambiarlo después descuadraría uno con el otro.
        if self.ambiente is None:
            self.ambiente = self.emisor.ambiente_nomina
        if not self.estado_id:
            from apps.documentos.models import DocumentoEstado
            self.estado = DocumentoEstado.objects.get(
                nombre=DocumentoEstado.Nombre.BORRADOR
            )
        super().save(*args, **kwargs)

    @property
    def es_borrador(self) -> bool:
        """Aún no se ha firmado, así que sus datos todavía se pueden cambiar."""
        from apps.documentos.models import DocumentoEstado

        if not self.estado_id:
            return True
        return self.estado.nombre in (
            DocumentoEstado.Nombre.BORRADOR, DocumentoEstado.Nombre.GENERADO,
        )

    def leer_xml(self) -> bytes:
        """Devuelve los bytes del XML firmado desde el storage (B2/local)."""
        if not self.xml_archivo:
            return b""
        with self.xml_archivo.open("rb") as fh:
            return fh.read()

    def __str__(self):
        return f"Nómina {self.numero} — {self.empleado}"
