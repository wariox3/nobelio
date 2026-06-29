"""Software de facturación registrado ante la DIAN."""
from django.db import models

from apps.nucleo.models import ModeloConFechas

from .emisor import Emisor


class SoftwareDian(ModeloConFechas):
    """Software de facturación registrado por el emisor ante la DIAN.

    El ``pin`` no se incluye en el XML; se usa para el CUDE y el
    ``SoftwareSecurityCode``.
    """

    # --- Atributos ---
    identificador = models.CharField(
        "ID del software", max_length=100,
        help_text="SoftwareID asignado por la DIAN.",
    )
    pin = models.CharField("PIN del software", max_length=100)
    id_proveedor = models.CharField(
        "ID del proveedor", max_length=20,
        help_text="ProviderID: NIT del proveedor del software (sin DV).",
    )
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

    def __str__(self):
        return f"Software {self.identificador} ({self.emisor})"
