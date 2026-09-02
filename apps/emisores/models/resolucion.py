"""Resolución de numeración (autorización de rango y clave técnica)."""
from django.db import models

from apps.nucleo.models import ModeloConFechas

from .emisor import Emisor


class Resolucion(ModeloConFechas):
    """Resolución de numeración (autorización de rango y clave técnica).

    La ``clave_tecnica`` es la que se usa para calcular el CUFE y NO viaja en
    el XML. Se obtiene de la consulta del rango de numeración ante la DIAN.
    """

    # --- Atributos ---
    numero_resolucion = models.CharField("número de resolución", max_length=50)
    fecha_resolucion = models.DateField("fecha de la resolución")

    prefijo = models.CharField("prefijo", max_length=10, blank=True)
    rango_desde = models.PositiveBigIntegerField("rango desde")
    rango_hasta = models.PositiveBigIntegerField("rango hasta")

    clave_tecnica = models.CharField("clave técnica", max_length=255, blank=True)

    vigente_desde = models.DateField("vigente desde")
    vigente_hasta = models.DateField("vigente hasta")

    activa = models.BooleanField("activa", default=True)

    # --- Relaciones ---
    emisor = models.ForeignKey(
        Emisor,
        on_delete=models.CASCADE,
        related_name="resoluciones",
        verbose_name="emisor",
    )
    tipo_factura = models.ForeignKey(
        "catalogos.TipoFactura",
        on_delete=models.PROTECT,
        related_name="resoluciones",
        verbose_name="tipo de factura",
    )

    class Meta:
        db_table = "emi_resolucion"
        verbose_name = "resolución"
        verbose_name_plural = "resoluciones"
        ordering = ["-fecha_resolucion"]
        constraints = [
            models.UniqueConstraint(
                fields=["emisor", "tipo_factura", "prefijo", "numero_resolucion"],
                name="resolucion_unica_por_emisor",
            )
        ]

    def __str__(self):
        return f"Res. {self.numero_resolucion} {self.prefijo} ({self.emisor})"


def resolucion_activa_en_otra_cuenta(emisor, prefijo, numero_resolucion, excluir_pk=None):
    """Devuelve la resolución activa que ya tiene este NIT en otra cuenta, o ``None``.

    Un NIT puede estar dado de alta en varias cuentas (una por integración),
    pero la numeración que autoriza la DIAN es una sola: si dos filas emitieran
    con el mismo prefijo y resolución, cada una numeraría por su cuenta y la
    DIAN rechazaría los números repetidos —consecutivos que además ya no se
    recuperan.

    Se mira solo entre las **activas** a propósito: cuando un cliente migra de
    integración se desactiva la resolución en la cuenta que deja, y con eso
    queda libre para registrarla en la nueva.
    """
    consulta = Resolucion.objects.filter(
        activa=True,
        prefijo=prefijo,
        numero_resolucion=numero_resolucion,
        emisor__tipo_identificacion_id=emisor.tipo_identificacion_id,
        emisor__numero_identificacion=emisor.numero_identificacion,
    ).exclude(emisor_id=emisor.pk).select_related("emisor__cuenta")
    if excluir_pk is not None:
        consulta = consulta.exclude(pk=excluir_pk)
    return consulta.first()


def mensaje_resolucion_ocupada(resolucion):
    """Explica el choque de numeración y cómo resolverlo."""
    return (
        f"La resolución {resolucion.numero_resolucion} con prefijo "
        f"'{resolucion.prefijo}' ya está activa para este NIT en la cuenta "
        f"'{resolucion.emisor.cuenta}'. Dos cuentas no pueden numerar con la "
        f"misma resolución a la vez: desactívala allí antes de registrarla aquí."
    )
