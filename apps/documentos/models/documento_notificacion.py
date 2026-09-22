"""Registro de las notificaciones de los documentos al adquiriente."""
from django.db import models

from .documento import Documento


class DocumentoNotificacion(models.Model):
    """Un envío del documento al adquiriente por correo, y cómo le fue.

    Una fila por cada vez que se intentó mandar por la pasarela, haya salido o
    no: `enviado` es que Zinc lo aceptó; `fallido`, que lo rechazó o no
    respondió, con el motivo en `error`. No dejan fila la descarga del paquete
    (``?descargar=1``), que no envía nada, ni lo que se rechaza antes de armar
    el paquete —documento sin firmar o sin aceptar, adquiriente sin correo—.

    Guarda los destinatarios, que son datos de un tercero: el log solo cuenta
    cuántos, pero la base es donde tiene que quedar a quién se le entregó.
    Las filas solo se crean: nadie las edita.
    """

    class Estado(models.TextChoices):
        ENVIADO = "enviado", "Enviado"
        FALLIDO = "fallido", "Fallido"

    estado = models.CharField("estado", max_length=20, choices=Estado.choices)
    # Tal como se le pasaron a Zinc: varios correos separados por `;`.
    destinatario = models.CharField("destinatario", max_length=500)
    # La copia del emisor en ese momento: puede cambiar después.
    copia = models.CharField("copia", max_length=255, blank=True)
    archivo = models.CharField("archivo", max_length=255)
    tamano = models.PositiveIntegerField("tamaño en bytes")
    contenido = models.JSONField("archivos del zip", default=list, blank=True)
    # Con lo que se rastrea el correo en la pasarela. Solo si salió.
    codigo_envio = models.CharField("código de envío", max_length=100, blank=True)
    error = models.TextField("error", blank=True)
    fecha = models.DateTimeField("fecha", auto_now_add=True)

    documento = models.ForeignKey(
        Documento, on_delete=models.CASCADE,
        related_name="notificaciones", verbose_name="documento",
    )

    class Meta:
        db_table = "doc_documento_notificacion"
        verbose_name = "notificación de documento"
        verbose_name_plural = "notificaciones de documento"
        ordering = ["fecha", "id"]

    def __str__(self):
        return f"{self.get_estado_display()} — {self.documento.numero}"
