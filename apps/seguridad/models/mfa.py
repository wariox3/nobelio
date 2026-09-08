"""Modelos del segundo factor.

Cuatro piezas con vidas distintas: la **configuración** de la cuenta, el
**desafío** abierto entre los dos pasos de un inicio de sesión, los **códigos de
respaldo** y los **dispositivos recordados**.

Ninguna guarda nada en claro: el secreto TOTP va cifrado con
``MFA_ENCRYPTION_KEY`` y todo lo demás —códigos enviados, de respaldo y tokens de
dispositivo— solo como HMAC. Un volcado de la base no permite ni entrar ni
fabricar cookies válidas.
"""
from django.conf import settings
from django.db import models

from apps.nucleo.models import ModeloConFechas

METODO_TOTP = "totp"
METODO_CORREO = "correo"

# El orden es el que ve el usuario al elegir. Se sirve desde la API para que el
# front no lo repita: TOTP primero porque no depende de que llegue un correo.
METODOS = (
    (METODO_TOTP, "Aplicación autenticadora"),
    (METODO_CORREO, "Código por correo"),
)

# Los que mandan un código a algún lado, frente a TOTP, que se recalcula.
METODOS_ENVIADOS = {METODO_CORREO}


class MfaUsuario(ModeloConFechas):
    """Configuración del segundo factor de una cuenta.

    Una por usuario. Existe desde que empieza el enrolamiento, con ``activo`` en
    falso: el segundo factor no se enciende hasta que la persona demuestra que su
    app genera códigos válidos, para no dejarla fuera de su propia cuenta.
    """

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mfa",
        verbose_name="usuario",
    )
    metodo = models.CharField("método", max_length=20, choices=METODOS)
    # Solo TOTP lo usa. Cifrado con MFA_ENCRYPTION_KEY, nunca en claro.
    secreto = models.CharField("secreto cifrado", max_length=500, blank=True)
    activo = models.BooleanField("activo", default=False)
    # Última ventana de TOTP consumida. Es lo que impide reusar un código
    # interceptado durante los 90 s en que sigue siendo válido.
    ultimo_contador = models.BigIntegerField(
        "último contador TOTP", null=True, blank=True
    )

    class Meta:
        db_table = "seg_mfa_usuario"
        verbose_name = "segundo factor"
        verbose_name_plural = "segundos factores"

    def __str__(self):
        return f"MFA {self.metodo} de {self.usuario}"


class MfaDesafio(ModeloConFechas):
    """Segundo paso pendiente de un inicio de sesión cuya clave ya se validó.

    El conteo de intentos vive aquí y no en la caché a propósito: es el único
    punto donde se puede probar un código de seis dígitos, y un contador que se
    reinicia con cada despliegue —o que lleva cada worker por su lado— no frena
    nada. La caché sirve para moderar el tráfico; esto es un cerrojo.
    """

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="desafios_mfa",
        verbose_name="usuario",
    )
    metodo = models.CharField("método", max_length=20, choices=METODOS)
    # Nulo en TOTP: ahí no hay código que guardar, se recalcula del secreto.
    hash_codigo = models.CharField("hash del código", max_length=64, blank=True)
    intentos = models.PositiveSmallIntegerField("intentos", default=0)
    consumido = models.BooleanField("consumido", default=False)
    expira = models.DateTimeField("expira")
    ip = models.GenericIPAddressField("IP", null=True, blank=True)

    class Meta:
        db_table = "seg_mfa_desafio"
        verbose_name = "desafío de segundo factor"
        verbose_name_plural = "desafíos de segundo factor"
        ordering = ["-creado_en"]

    def __str__(self):
        return f"Desafío {self.metodo} de {self.usuario}"


class MfaCodigoRespaldo(ModeloConFechas):
    """Código de un solo uso para entrar cuando el método habitual no está.

    Solo se guarda el HMAC. Existen en claro una única vez, cuando se generan y
    se le muestran a la persona.
    """

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="codigos_respaldo",
        verbose_name="usuario",
    )
    hash_codigo = models.CharField("hash del código", max_length=64)
    usado_en = models.DateTimeField("usado en", null=True, blank=True)

    class Meta:
        db_table = "seg_mfa_codigo_respaldo"
        verbose_name = "código de respaldo"
        verbose_name_plural = "códigos de respaldo"
        indexes = [models.Index(fields=["usuario", "hash_codigo"])]


class MfaDispositivo(ModeloConFechas):
    """Navegador al que se le permite saltarse el segundo paso.

    Es una excepción concedida, no una sesión: sobrevive al cierre de sesión y se
    revoca desde el perfil, o sola cuando cambia cómo se protege la cuenta.
    """

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dispositivos_mfa",
        verbose_name="usuario",
    )
    hash_token = models.CharField("hash del token", max_length=64)
    agente = models.CharField("navegador", max_length=500, blank=True)
    ip = models.GenericIPAddressField("IP", null=True, blank=True)
    ultimo_uso = models.DateTimeField("último uso", null=True, blank=True)
    expira = models.DateTimeField("expira")

    class Meta:
        db_table = "seg_mfa_dispositivo"
        verbose_name = "dispositivo recordado"
        verbose_name_plural = "dispositivos recordados"
        ordering = ["-ultimo_uso"]
        indexes = [models.Index(fields=["usuario", "hash_token"])]
