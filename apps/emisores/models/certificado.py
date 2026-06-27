"""Certificado digital del emisor para la firma XAdES."""
from django.db import models

from apps.nucleo.models import ModeloConFechas
from apps.utilidades.almacenamiento import almacenamiento_backblaze

from .emisor import Emisor


def ruta_certificado(instance, filename):
    """Ruta del .p12 dentro del bucket: ``<id_emisor>/certificados/<archivo>``.

    Se usa el id del emisor para aislar los certificados de cada uno en su
    propia carpeta. ``instance.emisor_id`` evita una consulta extra a la BD.
    """
    return f"{instance.emisor_id}/certificados/{filename}"


class Certificado(ModeloConFechas):
    """Certificado digital (.p12/.pfx) del emisor para la firma XAdES.

    El archivo y la clave son sensibles: el .p12 está fuera del control de
    versiones (ver .gitignore) y la clave debería cifrarse en producción.
    """

    # --- Atributos ---
    alias = models.CharField("alias", max_length=150, blank=True)
    archivo = models.FileField(
        "archivo .p12",
        upload_to=ruta_certificado,
        storage=almacenamiento_backblaze,
    )
    clave = models.CharField("clave del certificado", max_length=255)
    vigente_desde = models.DateField("vigente desde", null=True, blank=True)
    vigente_hasta = models.DateField("vigente hasta", null=True, blank=True)
    activo = models.BooleanField("activo", default=True)

    # --- Relaciones ---
    emisor = models.ForeignKey(
        Emisor,
        on_delete=models.CASCADE,
        related_name="certificados",
        verbose_name="emisor",
    )

    class Meta:
        db_table = "emi_certificado"
        verbose_name = "certificado digital"
        verbose_name_plural = "certificados digitales"

    def __str__(self):
        return f"Certificado {self.alias or self.pk} ({self.emisor})"
