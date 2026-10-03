"""Archivo que llegó en un correo de recepción."""
import re
import uuid
from pathlib import PurePosixPath

from django.db import models
from django.utils import timezone

from apps.nucleo.models import ModeloUUID
from apps.utilidades.almacenamiento import almacenamiento_backblaze


def _ruta(instance, filename):
    """``<emisor o sin-emisor>/recepcion/<aaaa>/<mm>/<uuid>.<ext>``.

    El emisor es el del documento si el adjunto es de uno, y si no el del
    correo. En el bucket el nombre es un uuid: el que puso el proveedor (que
    puede traer ``../`` o lo que sea) solo se guarda en ``nombre``.
    """
    if instance.documento_id is not None:
        dueno = instance.documento.emisor_id
    else:
        dueno = instance.correo.emisor_id or "sin-emisor"
    extension = PurePosixPath(filename.lower()).suffix
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", extension):
        extension = ""
    return f"{dueno}/recepcion/{timezone.now():%Y/%m}/{uuid.uuid4()}{extension}"


class Adjunto(ModeloUUID):
    """Un archivo del correo, guardado en B2.

    Todo lo que trajo el correo, ya fuera de sus ZIP: el XML y el PDF de cada
    documento y cualquier otro adjunto. También el documento extraído del
    AttachedDocument (``xml_documento``), que no venía como archivo propio.

    ``correo`` va siempre, aunque el documento ya lo diga: así todos los
    archivos de un correo salen con un solo filtro, que es lo que usan la lista
    y el borrado. Los dos los pone el mismo servicio.

    Se borran solo con el correo, por el servicio que borra también B2: por eso
    ``PROTECT`` hacia el correo y el documento.
    """

    class Rol(models.TextChoices):
        XML = "xml", "XML recibido"
        XML_DOCUMENTO = "xml_documento", "XML del documento"
        PDF = "pdf", "PDF del documento"
        OTRO = "otro", "Otro adjunto"

    correo = models.ForeignKey(
        "recepcion.Correo", on_delete=models.PROTECT,
        related_name="adjuntos", verbose_name="correo",
    )
    documento = models.ForeignKey(
        "recepcion.Documento", on_delete=models.PROTECT, null=True, blank=True,
        related_name="adjuntos", verbose_name="documento",
    )
    rol = models.CharField("rol", max_length=20, choices=Rol.choices)

    archivo = models.FileField(
        "archivo", upload_to=_ruta, storage=almacenamiento_backblaze, max_length=300,
    )
    nombre = models.CharField("nombre original", max_length=255)
    tipo_contenido = models.CharField("tipo de contenido", max_length=100)
    tamano = models.PositiveBigIntegerField("tamaño en bytes")
    sha256 = models.CharField("SHA-256", max_length=64)
    creado_en = models.DateTimeField("creado en", auto_now_add=True)

    class Meta:
        db_table = "rec_adjunto"
        verbose_name = "adjunto"
        verbose_name_plural = "adjuntos"
        ordering = ["creado_en"]
        constraints = [
            # Un solo XML, un solo XML del documento y un solo PDF por documento.
            models.UniqueConstraint(
                fields=["documento", "rol"],
                condition=models.Q(documento__isnull=False),
                name="rec_adjunto_rol_unico_por_documento",
            ),
            # Con documento es uno de sus archivos; sin documento, "otro".
            models.CheckConstraint(
                condition=(
                    models.Q(documento__isnull=True, rol="otro")
                    | (models.Q(documento__isnull=False) & ~models.Q(rol="otro"))
                ),
                name="rec_adjunto_rol_coherente",
            ),
        ]

    def __str__(self):
        return f"{self.nombre} ({self.rol})"
