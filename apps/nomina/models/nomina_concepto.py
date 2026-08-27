"""Cada devengado o deducción de una nómina, en una sola tabla."""
from django.db import models

from apps.nucleo.models import ModeloConFechas


class NominaConcepto(ModeloConFechas):
    """Un concepto liquidado: una hora extra, la salud, una incapacidad…

    ``Devengados`` y ``Deducciones`` del XML no son entidades distintas sino un
    catálogo de conceptos —26 y 18— que se emiten cada uno con su propio
    elemento. Modelarlos como 44 tablas (o 44 columnas) daría un esquema que hay
    que migrar cada vez que la DIAN añade un concepto; aquí cada concepto es una
    fila y ``concepto`` dice cuál es.

    Los atributos son la unión de los que piden todos los conceptos, y por eso
    casi todos son opcionales: un ``basico`` usa ``cantidad`` (días) y ``valor``;
    una hora extra añade ``hora_inicio``, ``hora_fin`` y ``porcentaje``; una
    incapacidad, las fechas y su tipo; ``salud`` solo ``porcentaje`` y ``valor``.

    El paso de fila a elemento XML lo hará el constructor de la fase 3.
    """

    class Grupo(models.TextChoices):
        DEVENGADO = "devengado", "Devengado"
        DEDUCCION = "deduccion", "Deducción"

    class Concepto(models.TextChoices):
        """Discriminador interno; no es un código de la DIAN.

        La DIAN no numera los conceptos: los distingue por el nombre del
        elemento XML. Estos códigos son nuestros y se mapean a ese nombre al
        generar el XML.
        """

        # --- Devengados ---
        BASICO = "basico", "Salario básico"
        AUXILIO_TRANSPORTE = "auxilio_transporte", "Auxilio de transporte"
        VIATICOS = "viaticos", "Viáticos de manutención y alojamiento"
        HED = "hed", "Hora extra diurna"
        HEN = "hen", "Hora extra nocturna"
        HRN = "hrn", "Hora recargo nocturno"
        HEDDF = "heddf", "Hora extra diurna dominical o festiva"
        HRDDF = "hrddf", "Hora recargo diurno dominical o festivo"
        HENDF = "hendf", "Hora extra nocturna dominical o festiva"
        HRNDF = "hrndf", "Hora recargo nocturno dominical o festivo"
        VACACIONES_COMUNES = "vacaciones_comunes", "Vacaciones comunes"
        VACACIONES_COMPENSADAS = "vacaciones_compensadas", "Vacaciones compensadas"
        PRIMA = "prima", "Prima"
        CESANTIAS = "cesantias", "Cesantías"
        INTERESES_CESANTIAS = "intereses_cesantias", "Intereses de cesantías"
        INCAPACIDAD = "incapacidad", "Incapacidad"
        LICENCIA_MP = "licencia_mp", "Licencia de maternidad o paternidad"
        LICENCIA_REMUNERADA = "licencia_remunerada", "Licencia remunerada"
        LICENCIA_NO_REMUNERADA = "licencia_no_remunerada", "Licencia no remunerada"
        BONIFICACION = "bonificacion", "Bonificación"
        AUXILIO = "auxilio", "Auxilio"
        HUELGA_LEGAL = "huelga_legal", "Huelga legal"
        OTRO_CONCEPTO = "otro_concepto", "Otro concepto"
        COMPENSACION_ORDINARIA = "compensacion_ordinaria", "Compensación ordinaria"
        COMPENSACION_EXTRAORDINARIA = (
            "compensacion_extraordinaria", "Compensación extraordinaria")
        BONO_EPCTV = "bono_epctv", "Bono o pago EPCTV"
        BONO_EPCTV_ALIMENTACION = (
            "bono_epctv_alimentacion", "Bono EPCTV de alimentación")
        COMISION = "comision", "Comisión"
        DOTACION = "dotacion", "Dotación"
        APOYO_SOSTENIMIENTO = "apoyo_sostenimiento", "Apoyo de sostenimiento"
        TELETRABAJO = "teletrabajo", "Teletrabajo"
        BONIFICACION_RETIRO = "bonificacion_retiro", "Bonificación por retiro"
        INDEMNIZACION = "indemnizacion", "Indemnización"

        # --- Deducciones ---
        SALUD = "salud", "Salud"
        FONDO_PENSION = "fondo_pension", "Fondo de pensión"
        FONDO_SP = "fondo_sp", "Fondo de solidaridad pensional"
        FONDO_SP_SUBSISTENCIA = "fondo_sp_subsistencia", "Fondo de subsistencia"
        SINDICATO = "sindicato", "Sindicato"
        SANCION_PUBLICA = "sancion_publica", "Sanción pública"
        SANCION_PRIVADA = "sancion_privada", "Sanción privada"
        LIBRANZA = "libranza", "Libranza"
        OTRA_DEDUCCION = "otra_deduccion", "Otra deducción"
        PENSION_VOLUNTARIA = "pension_voluntaria", "Pensión voluntaria"
        RETENCION_FUENTE = "retencion_fuente", "Retención en la fuente"
        AFC = "afc", "AFC"
        COOPERATIVA = "cooperativa", "Cooperativa"
        EMBARGO_FISCAL = "embargo_fiscal", "Embargo fiscal"
        PLAN_COMPLEMENTARIOS = "plan_complementarios", "Planes complementarios"
        EDUCACION = "educacion", "Educación"
        DEUDA = "deuda", "Deuda"

        # --- En los dos grupos ---
        # La DIAN los define igual en Devengados y en Deducciones; lo que los
        # separa es `grupo`, no el concepto.
        PAGO_TERCERO = "pago_tercero", "Pago a tercero"
        ANTICIPO = "anticipo", "Anticipo"
        REINTEGRO = "reintegro", "Reintegro"

    class TipoIncapacidad(models.TextChoices):
        COMUN = "1", "Común"
        PROFESIONAL = "2", "Profesional"
        LABORAL = "3", "Laboral"

    grupo = models.CharField("grupo", max_length=10, choices=Grupo.choices)
    concepto = models.CharField("concepto", max_length=30, choices=Concepto.choices)

    # Cantidad de días, de horas o de veces, según el concepto.
    cantidad = models.DecimalField(
        "cantidad", max_digits=10, decimal_places=2, null=True, blank=True,
    )
    porcentaje = models.DecimalField(
        "porcentaje", max_digits=6, decimal_places=2, null=True, blank=True,
        help_text="Recargo de la hora extra, o porcentaje de la deducción.",
    )
    valor = models.DecimalField("valor", max_digits=15, decimal_places=2, default=0)
    # Varios conceptos se pagan partidos en una parte que constituye salario y
    # otra que no (bonificaciones, auxilios, primas, viáticos, bonos EPCTV).
    valor_no_salarial = models.DecimalField(
        "valor no salarial", max_digits=15, decimal_places=2, null=True, blank=True,
    )

    fecha_inicio = models.DateField("fecha de inicio", null=True, blank=True)
    fecha_fin = models.DateField("fecha de fin", null=True, blank=True)
    # Las horas extra se informan con fecha y hora completas.
    hora_inicio = models.DateTimeField("hora de inicio", null=True, blank=True)
    hora_fin = models.DateTimeField("hora de fin", null=True, blank=True)

    descripcion = models.CharField(
        "descripción", max_length=255, blank=True,
        help_text="La piden 'otro concepto' y las libranzas.",
    )
    tipo_incapacidad = models.CharField(
        "tipo de incapacidad", max_length=1,
        choices=TipoIncapacidad.choices, blank=True,
    )

    nomina = models.ForeignKey(
        "nomina.Nomina", on_delete=models.CASCADE,
        related_name="conceptos", verbose_name="nómina",
    )

    class Meta:
        db_table = "nom_nomina_concepto"
        verbose_name = "concepto de nómina"
        verbose_name_plural = "conceptos de nómina"
        ordering = ["grupo", "concepto", "id"]

    def __str__(self):
        return f"{self.get_concepto_display()}: {self.valor}"
