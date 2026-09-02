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

La cuenta es el único alcance posible: un emisor nunca se conecta por su lado.
Quien vaya a emitir directamente se da de alta como su propia cuenta, con su
emisor y su llave.
"""
import hashlib
from datetime import timedelta
from hmac import compare_digest

from django.contrib.auth.hashers import check_password
from django.db import models
from django.utils import timezone
from django.utils.crypto import get_random_string

from apps.nucleo.models import ModeloConFechas

LONGITUD_PREFIJO = 8
LONGITUD_SECRETO = 40

# El secreto se guarda como SHA-256 y no con el hasher de contraseñas de Django.
#
# El PBKDF2 que había antes existe para proteger **contraseñas humanas**: son
# cortas, se repiten entre sitios y se adivinan, así que se encarece cada intento
# para que probarlas en masa no salga a cuenta. Aquí no hay nada de eso: el
# secreto lo genera el servidor con 40 caracteres de un alfabeto de 62, unos 238
# bits. No se puede adivinar por fuerza bruta a ningún coste por intento, así que
# las ~600.000 iteraciones solo las paga el ERP legítimo, en **cada petición**.
#
# Lo que sí importa es comparar en tiempo constante, y de eso se encarga
# `compare_digest`.
PREFIJO_HASH = "sha256$"

# Cada cuánto se refresca `ultimo_uso_en`. Es un dato para saber si una
# integración sigue viva, no una auditoría: al minuto sobra.
INTERVALO_REGISTRO_USO = timedelta(minutes=5)


def _hash_secreto(secreto: str) -> str:
    return PREFIJO_HASH + hashlib.sha256(secreto.encode("utf-8")).hexdigest()


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

    class Meta:
        db_table = "seg_llave_api"
        verbose_name = "llave de API"
        verbose_name_plural = "llaves de API"
        ordering = ["-creado_en"]

    def __str__(self):
        return f"{self.nombre} ({self.prefijo})"

    @classmethod
    def generar(cls, *, cuenta, nombre, activa=True, expira_en=None):
        """Crea una llave y devuelve ``(llave, clave_completa)``.

        ``clave_completa`` (``<prefijo>.<secreto>``) es lo único que sirve para
        autenticar y solo se conoce en este momento; guárdala donde el ERP la
        pueda leer, porque después no se puede recuperar.

        La llave alcanza a todos los emisores de ``cuenta``.
        """
        prefijo = get_random_string(LONGITUD_PREFIJO)
        while cls.objects.filter(prefijo=prefijo).exists():
            prefijo = get_random_string(LONGITUD_PREFIJO)
        secreto = get_random_string(LONGITUD_SECRETO)
        llave = cls.objects.create(
            cuenta=cuenta,
            nombre=nombre,
            prefijo=prefijo,
            clave_hash=_hash_secreto(secreto),
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
        """Comprueba el secreto contra el hash almacenado.

        Migración perezosa: una llave creada antes del cambio sigue guardando un
        hash de Django (``pbkdf2_...``). Se verifica con el verificador de
        siempre y, si es correcto, se reescribe en el formato nuevo. Así ninguna
        integración tiene que rotar su llave y el coste viejo se paga una última
        vez por llave, no en cada petición.
        """
        if not self.clave_hash.startswith(PREFIJO_HASH):
            if not check_password(secreto, self.clave_hash):
                return False
            self.clave_hash = _hash_secreto(secreto)
            self.save(update_fields=["clave_hash", "actualizado_en"])
            return True
        return compare_digest(self.clave_hash, _hash_secreto(secreto))

    def registrar_uso(self):
        """Marca el instante del último uso, como mucho una vez cada intervalo.

        El campo es informativo —sirve para ver qué integraciones siguen vivas—,
        así que no hace falta al segundo. Antes se escribía en **cada** petición
        autenticada: un `UPDATE` por llamada, sobre la misma fila, que en un
        punto de venta con varias cajas es contención pura a cambio de una
        precisión que nadie usa.
        """
        ahora = timezone.now()
        if (
            self.ultimo_uso_en
            and (ahora - self.ultimo_uso_en) < INTERVALO_REGISTRO_USO
        ):
            return
        self.ultimo_uso_en = ahora
        self.save(update_fields=["ultimo_uso_en"])
