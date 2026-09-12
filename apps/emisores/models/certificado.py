"""Certificado digital del emisor para la firma XAdES."""
from django.db import models

from apps.nucleo.models import ModeloConFechas
from apps.utilidades.almacenamiento import almacenamiento_backblaze
from apps.utilidades.cifrado import ClaveCifradaField

from .emisor import Emisor


def ruta_certificado(instance, filename):
    """Ruta del .p12 dentro del bucket: ``<id_emisor>/certificados/<archivo>``.

    Se usa el id del emisor para aislar los certificados de cada uno en su
    propia carpeta. ``instance.emisor_id`` evita una consulta extra a la BD.
    """
    return f"{instance.emisor_id}/certificados/{filename}"


class Certificado(ModeloConFechas):
    """Certificado digital (.p12/.pfx) del emisor para la firma XAdES.

    El archivo y la clave son sensibles y se guardan separados: el .p12 vive en
    Backblaze B2 (nunca en el repositorio ni en el disco de la aplicación) y la
    clave va cifrada en la base con ``CERT_ENCRYPTION_KEY``, que está en el
    entorno. Ninguna de las dos mitades sirve sin la otra.
    """

    # --- Atributos ---
    alias = models.CharField("alias", max_length=150, blank=True)
    archivo = models.FileField(
        "archivo .p12",
        upload_to=ruta_certificado,
        storage=almacenamiento_backblaze,
    )
    # Se guarda cifrada; en Python se lee y se escribe en claro. El 512 es para
    # el token Fernet, que abulta bastante más que la clave que envuelve.
    clave = ClaveCifradaField("clave del certificado", max_length=512)
    vigente_desde = models.DateField("vigente desde", null=True, blank=True)
    vigente_hasta = models.DateField("vigente hasta", null=True, blank=True)

    # --- Relaciones ---
    # Uno por emisor, y lo impone la base. Antes era una ForeignKey con un
    # campo `activo`: cargar uno nuevo jubilaba el anterior (`activo=False`) y
    # lo dejaba ahí para siempre. Ese histórico no servía a nadie —nada lo lee,
    # y firmar con él es justo lo que el flag impedía— y en cambio conservaba
    # un .p12 vivo en el bucket por cada renovación. La firma XAdES incrusta el
    # certificado en el propio XML, así que el pasado ya está guardado donde
    # importa: en los documentos emitidos, no aquí.
    emisor = models.OneToOneField(
        Emisor,
        on_delete=models.CASCADE,
        related_name="certificado",
        verbose_name="emisor",
    )

    class Meta:
        db_table = "emi_certificado"
        verbose_name = "certificado digital"
        verbose_name_plural = "certificados digitales"
        # Solo ordena el listado de varios emisores (staff): cada emisor tiene
        # como mucho uno.
        ordering = ["-creado_en"]

    def __str__(self):
        return f"Certificado {self.alias or self.pk} ({self.emisor})"

    # --- Resumen en el emisor ---
    # `Emisor.certificado_activo` y `Emisor.certificado_vence` son una copia de
    # lo que hay aquí. Se mantienen desde este lado —y no desde la vista— para
    # que valga por cualquier camino: la API, el admin, el shell, una carga de
    # datos. Una copia que solo se actualizara en `cargar` se queda desfasada
    # el primer día que alguien borre un certificado desde el admin, y una
    # bandera desfasada es peor que no tenerla.
    #
    # El único hueco es `Certificado.objects.filter(...).delete()`, que Django
    # resuelve en SQL sin pasar por aquí. No lo hace nadie en la aplicación
    # —la baja va por `perform_destroy`, que borra la instancia— y si el emisor
    # entero se borra, el resumen se va con él. Si algún día hiciera falta
    # cerrarlo del todo, es una señal `post_delete`.

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self._sincronizar_resumen()

    def delete(self, *args, **kwargs):
        resultado = super().delete(*args, **kwargs)
        self._sincronizar_resumen()
        return resultado

    def _sincronizar_resumen(self):
        """Refresca en el emisor el resumen de su certificado.

        Se relee de la base en vez de usar ``self``: después de un ``delete``
        el objeto sigue en memoria pero la fila ya no está, y lo que hay que
        escribir es lo que queda, no lo que se acaba de borrar.

        Va por ``update`` y no por ``emisor.save()`` a propósito: es una sola
        sentencia, no pisa lo que otra petición esté escribiendo en el emisor y
        no mueve ``actualizado_en`` —el emisor no ha cambiado; ha cambiado su
        certificado—.
        """
        vigente = type(self).objects.filter(emisor_id=self.emisor_id).first()
        Emisor.objects.filter(pk=self.emisor_id).update(
            certificado_activo=vigente is not None,
            certificado_vence=vigente.vigente_hasta if vigente else None,
        )
