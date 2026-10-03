"""Llave de API para clientes máquina (el ERP que envía documentos).

La credencial completa tiene el formato ``<prefijo>.<secreto>`` y se entrega
una sola vez al crearla. En la base de datos solo se guarda el ``prefijo``
(para localizar la fila) y el hash del secreto; el secreto en claro nunca se
almacena.

La llave está ligada a un **usuario** y actúa en su nombre: alcanza exactamente
los mismos emisores que él, ni más ni menos. Así el alcance se define una sola
vez en el proyecto (``apps.seguridad.alcance``) en vez de tener una regla para
personas y otra para integraciones, que es como acaban divergiendo.

La excepción es la llave de **alcance global** (``alcance_global``): alcanza
todos los emisores, como el staff. Es para un servidor de la plataforma —la
aplicación administrativa— que no se autentica contra nobelio con personas.
Solo se crea por CLI, su dueño tiene que ser staff y vence en 90 días como
mucho; ver :meth:`LlaveApi.problema_alcance_global`. No da administración:
usuarios y llaves siguen exigiendo a una persona staff.

Un ERP que factura para varios clientes opera con una sola credencial sobre
todos los emisores de su dueño. Un mismo usuario puede tener varias llaves vivas
a la vez: producción y habilitación, o la nueva y la vieja mientras dura una
rotación.
"""
import hashlib
from datetime import timedelta
from hmac import compare_digest

from django.contrib.auth.hashers import check_password
from django.conf import settings
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

# Vida máxima de una llave de alcance global. Una credencial que lo ve todo no
# puede quedarse viva para siempre en la configuración de un servidor.
DIAS_MAXIMOS_ALCANCE_GLOBAL = 90


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
    # Solo se enciende por CLI (`crear_llave_api --alcance-global`): la API lo
    # tiene de solo lectura, para que ninguna credencial pueda ascenderse.
    alcance_global = models.BooleanField(
        "alcance global", default=False,
        help_text="Alcanza todos los emisores, como el staff. Exige dueño "
        "staff y vencimiento de 90 días como máximo.",
    )

    # --- Relaciones ---
    # La llave actúa en nombre de una persona: alcanza exactamente lo que esa
    # persona alcanza, ni más ni menos. Así hay una sola definición de alcance
    # en el proyecto en vez de dos que puedan divergir.
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="llaves_api",
        verbose_name="usuario",
        help_text="Persona en cuyo nombre actúa la llave.",
    )

    class Meta:
        db_table = "seg_llave_api"
        verbose_name = "llave de API"
        verbose_name_plural = "llaves de API"
        ordering = ["-creado_en"]

    def __str__(self):
        return f"{self.nombre} ({self.prefijo})"

    @classmethod
    def generar(cls, *, usuario, nombre, activa=True, expira_en=None, alcance_global=False):
        """Crea una llave y devuelve ``(llave, clave_completa)``.

        ``clave_completa`` (``<prefijo>.<secreto>``) es lo único que sirve para
        autenticar y solo se conoce en este momento; guárdala donde el ERP la
        pueda leer, porque después no se puede recuperar.

        La llave alcanza exactamente lo mismo que ``usuario``, salvo con
        ``alcance_global``, que alcanza todo y lanza ``ValueError`` si no
        cumple sus condiciones.
        """
        problema = cls(
            usuario=usuario, expira_en=expira_en, alcance_global=alcance_global,
        ).problema_alcance_global()
        if problema:
            raise ValueError(problema)
        prefijo = get_random_string(LONGITUD_PREFIJO)
        while cls.objects.filter(prefijo=prefijo).exists():
            prefijo = get_random_string(LONGITUD_PREFIJO)
        secreto = get_random_string(LONGITUD_SECRETO)
        llave = cls.objects.create(
            usuario=usuario,
            nombre=nombre,
            prefijo=prefijo,
            clave_hash=_hash_secreto(secreto),
            activa=activa,
            expira_en=expira_en,
            alcance_global=alcance_global,
        )
        return llave, f"{prefijo}.{secreto}"

    def esta_vigente(self):
        """¿La llave puede usarse ahora (activa y no expirada)?"""
        if not self.activa:
            return False
        if self.expira_en and self.expira_en <= timezone.now():
            return False
        return True

    def problema_alcance_global(self, expira_en=None):
        """Por qué esta llave no puede tener alcance global, o ``None``.

        Se pregunta al crearla, al cambiarle el vencimiento y en **cada**
        petición: si el dueño deja de ser staff, la llave deja de servir al
        instante, sin depender de que alguien se acuerde de revocarla.
        ``expira_en`` permite validar un vencimiento nuevo antes de guardarlo.
        """
        if not self.alcance_global:
            return None
        if not self.usuario.is_staff:
            return "El dueño de una llave de alcance global tiene que ser staff."
        expira_en = expira_en or self.expira_en
        if expira_en is None:
            return "Una llave de alcance global tiene que tener vencimiento."
        tope = timezone.now() + timedelta(days=DIAS_MAXIMOS_ALCANCE_GLOBAL)
        if expira_en > tope:
            return (
                "Una llave de alcance global vence en "
                f"{DIAS_MAXIMOS_ALCANCE_GLOBAL} días como máximo."
            )
        return None

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
