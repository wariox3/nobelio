"""Llave de API para clientes máquina (el ERP que envía documentos).

La credencial completa tiene el formato ``<prefijo>.<secreto>`` y se entrega
una sola vez al crearla. En la base de datos solo se guarda el ``prefijo``
(para localizar la fila) y el hash del secreto; el secreto en claro nunca se
almacena.

La llave está ligada a una **cuenta**, que es el propietario de los datos: una
integración (p. ej. un ERP que factura para muchos de sus clientes) opera con
una sola credencial sobre todos los emisores de su cuenta. Una cuenta puede
tener varias llaves vivas a la vez: producción y habilitación, o la nueva y la
vieja mientras dura una rotación.

Opcionalmente la llave se puede *estrechar* a un único emisor (``emisor`` no
nulo) para casos en los que un cliente concreto se conecta por su cuenta sin
pasar por la integración principal.
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

    # --- Atributos ---
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

    # --- Relaciones ---
    cuenta = models.ForeignKey(
        "cuentas.Cuenta",
        on_delete=models.CASCADE,
        related_name="llaves_api",
        verbose_name="cuenta",
        help_text="Alcance de la llave: todos los emisores de esta cuenta.",
    )
    emisor = models.ForeignKey(
        "emisores.Emisor",
        on_delete=models.CASCADE,
        related_name="llaves_api",
        verbose_name="emisor",
        null=True,
        blank=True,
        help_text="Opcional: restringe la llave a un único emisor de la cuenta.",
    )

    class Meta:
        db_table = "seg_llave_api"
        verbose_name = "llave de API"
        verbose_name_plural = "llaves de API"
        ordering = ["-creado_en"]

    def __str__(self):
        return f"{self.nombre} ({self.prefijo})"

    def clean(self):
        """Un emisor estrecho tiene que pertenecer a la cuenta de la llave."""
        from django.core.exceptions import ValidationError

        if self.emisor_id and self.emisor.cuenta_id != self.cuenta_id:
            raise ValidationError(
                {"emisor": "El emisor no pertenece a la cuenta de la llave."}
            )

    @classmethod
    def generar(cls, *, cuenta, nombre, emisor=None, activa=True, expira_en=None):
        """Crea una llave y devuelve ``(llave, clave_completa)``.

        ``clave_completa`` (``<prefijo>.<secreto>``) es lo único que sirve para
        autenticar y solo se conoce en este momento; guárdala donde el ERP la
        pueda leer, porque después no se puede recuperar.

        Con ``emisor`` la llave queda restringida a ese emisor; sin él alcanza a
        todos los emisores de ``cuenta``.
        """
        prefijo = get_random_string(LONGITUD_PREFIJO)
        while cls.objects.filter(prefijo=prefijo).exists():
            prefijo = get_random_string(LONGITUD_PREFIJO)
        secreto = get_random_string(LONGITUD_SECRETO)
        llave = cls.objects.create(
            cuenta=cuenta,
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
