"""Software de facturación registrado ante la DIAN."""
from django.db import models

from apps.nucleo.models import ModeloConFechas

from .emisor import Emisor


class SoftwareDian(ModeloConFechas):
    """Software de facturación registrado por el emisor ante la DIAN.

    Solo guarda lo que la DIAN entrega y no se puede deducir. El ``ProviderID``
    del XML no está aquí a propósito: en modalidad **software propio** el
    proveedor tecnológico es el propio emisor, así que se toma de su NIT
    (guardarlo aparte solo abría la puerta a que se desincronizara).

    El ``pin`` no se incluye en el XML; se usa para el CUDE y el
    ``SoftwareSecurityCode``.
    """

    class Tipo(models.TextChoices):
        """Qué operación habilita el software.

        La DIAN habilita facturación, nómina electrónica y documento
        equivalente por separado, cada una con su propio SoftwareID y su PIN,
        así que un emisor puede tener registrados tres softwares a la vez y hay
        que saber cuál usar en cada documento.
        """

        FACTURACION = "facturacion", "Facturación electrónica"
        NOMINA = "nomina", "Nómina electrónica"
        DOCUMENTO_EQUIVALENTE = (
            "documento_equivalente", "Documento equivalente electrónico"
        )

    # --- Atributos ---
    tipo = models.CharField(
        "tipo de software", max_length=25, choices=Tipo.choices,
        help_text="Operación que habilita: facturación, nómina electrónica o "
        "documento equivalente.",
    )
    identificador = models.CharField(
        "ID del software", max_length=100,
        help_text="SoftwareID asignado por la DIAN.",
    )
    pin = models.CharField("PIN del software", max_length=100)
    test_set_id = models.CharField(
        "ID del set de pruebas", max_length=100, blank=True,
        help_text="TestSetId entregado por la DIAN para la habilitación.",
    )
    set_pruebas_aceptado = models.BooleanField(
        "set de pruebas aceptado", default=False,
        help_text="Cuando la DIAN acepta el Set de Pruebas, los envíos pasan a "
        "SendBillSync (síncrono) en vez de SendTestSetAsync.",
    )
    activo = models.BooleanField("activo", default=True)

    # --- Fabricante del software (solo documento equivalente) ---
    # El P.O.S. exige una extensión `InformacionDelFabricanteDelSoftware` con
    # estos tres pares Name/Value, y las reglas DEAB41 a DEAB46 la rechazan si
    # faltan.
    #
    # Lo normal es dejarlos **vacíos**: quien fabricó el software es la
    # plataforma, igual para todos los emisores, y sus valores están en
    # `DIAN_FABRICANTE_*` (ver `config/settings/base.py`). Estos campos son la
    # excepción, para el obligado que emita con software propio en vez de con
    # el de un proveedor tecnológico; informado, gana sobre el ajuste global.
    #
    # No confundir con la razón social del emisor: son cosas distintas y solo
    # coinciden cuando el fabricante se factura a sí mismo.
    codigo_proveedor_tecnologico = models.CharField(
        "código del PT", max_length=3, blank=True,
        help_text="Los tres dígitos que la DIAN asigna al proveedor "
        "tecnológico. Van en el nombre de todos los archivos del documento "
        "equivalente (numeral 8.13.5); con software propio suele ser 000.",
    )
    fabricante_nombre = models.CharField(
        "nombre y apellido del fabricante", max_length=200, blank=True,
        help_text="Par `NombreApellido`. Vacío usa DIAN_FABRICANTE_NOMBRE.",
    )
    fabricante_razon_social = models.CharField(
        "razón social del fabricante", max_length=200, blank=True,
        help_text="Par `RazonSocial`. Vacío usa DIAN_FABRICANTE_RAZON_SOCIAL. "
        "No es la razón social del emisor.",
    )
    fabricante_nombre_software = models.CharField(
        "nombre del software", max_length=200, blank=True,
        help_text="Par `NombreSoftware`. Vacío usa "
        "DIAN_FABRICANTE_NOMBRE_SOFTWARE.",
    )

    # --- Relaciones ---
    emisor = models.ForeignKey(
        Emisor,
        on_delete=models.CASCADE,
        related_name="softwares",
        verbose_name="emisor",
    )

    class Meta:
        db_table = "emi_software"
        verbose_name = "software DIAN"
        verbose_name_plural = "softwares DIAN"
        ordering = ["-creado_en"]

    def __str__(self):
        return f"Software {self.identificador} ({self.emisor})"
