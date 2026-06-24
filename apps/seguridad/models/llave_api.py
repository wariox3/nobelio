"""Llave de API para clientes máquina (el ERP que envía documentos).

La credencial completa tiene el formato ``<prefijo>.<secreto>`` y se entrega
una sola vez al crearla. En la base de datos solo se guarda el ``prefijo``
(para localizar la fila) y el hash del secreto; el secreto en claro nunca se
almacena. La llave está ligada a un emisor, de modo que delimita para qué
emisor puede operar el ERP.
"""
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone
from django.utils.crypto import get_random_string

from apps.nucleo.models import ModeloConFechas

LONGITUD_PREFIJO = 8
LONGITUD_SECRETO = 40


class LlaveApi(ModeloConFechas):
    """Credencial de larga duración para autenticar a un ERP por API Key."""

    emisor = models.ForeignKey(
        "emisores.Emisor",
        on_delete=models.CASCADE,
        related_name="llaves_api",
        verbose_name="emisor",
    )
    nombre = models.CharField(
        "nombre", max_length=150,
        help_text="Identifica la integración, p. ej. 'ERP producción'.",
    )
    prefijo = models.CharField(
        "prefijo", max_length=LONGITUD_PREFIJO, unique=True, editable=False,
        help_text="Identificador público de la llave (no es secreto).",
    )
    clave_hash = models.CharField("hash de la clave", max_length=128, editable=False)

    activa = models.BooleanField("activa", default=True)
    expira_en = models.DateTimeField("expira en", null=True, blank=True)
    ultimo_uso_en = models.DateTimeField("último uso en", null=True, blank=True)

    class Meta:
        verbose_name = "llave de API"
        verbose_name_plural = "llaves de API"
        ordering = ["-creado_en"]

    def __str__(self):
        return f"{self.nombre} ({self.prefijo})"

    @classmethod
    def generar(cls, *, emisor, nombre, activa=True, expira_en=None):
        """Crea una llave y devuelve ``(llave, clave_completa)``.

        ``clave_completa`` (``<prefijo>.<secreto>``) es lo único que sirve para
        autenticar y solo se conoce en este momento; guárdala donde el ERP la
        pueda leer, porque después no se puede recuperar.
        """
        prefijo = get_random_string(LONGITUD_PREFIJO)
        while cls.objects.filter(prefijo=prefijo).exists():
            prefijo = get_random_string(LONGITUD_PREFIJO)
        secreto = get_random_string(LONGITUD_SECRETO)
        llave = cls.objects.create(
            emisor=emisor,
            nombre=nombre,
            prefijo=prefijo,
            clave_hash=make_password(secreto),
            activa=activa,
            expira_en=expira_en,
        )
        return llave, f"{prefijo}.{secreto}"

    def esta_vigente(self):
        """¿La llave puede usarse ahora (activa y no expirada)?"""
        if not self.activa:
            return False
        if self.expira_en and self.expira_en <= timezone.now():
            return False
        return True

    def verificar_secreto(self, secreto):
        """Comprueba el secreto contra el hash almacenado."""
        return check_password(secreto, self.clave_hash)

    def registrar_uso(self):
        """Marca el instante del último uso (sin tocar el resto de campos)."""
        self.ultimo_uso_en = timezone.now()
        self.save(update_fields=["ultimo_uso_en"])
