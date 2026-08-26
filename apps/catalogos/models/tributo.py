"""Catálogo: tributo / impuesto."""
from .base import ElementoCatalogo


class Tributo(ElementoCatalogo):
    """Tributo / impuesto (cac:TaxScheme). Lista TipoImpuesto.

    Códigos relevantes para el CUFE: 01 IVA, 03 ICA, 04 INC.
    """

    # Retenciones: van en cac:WithholdingTaxTotal y no en cac:TaxTotal, y no
    # suman al total a pagar. La lista TipoImpuesto de la DIAN no las distingue
    # —trae todos los tributos en la misma tabla, sin columna que diga cuál
    # retiene—, así que la clasificación va aquí, por código, y no en un campo
    # que `cargar_catalogos` no sabría rellenar.
    CODIGOS_RETENCION = frozenset({"05", "06", "07", "08"})

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_tributo"
        verbose_name = "tributo"
        verbose_name_plural = "tributos"

    @property
    def es_retencion(self) -> bool:
        """ReteIVA (05), ReteFuente (06), ReteICA (07) o ReteCREE (08)."""
        return self.codigo in self.CODIGOS_RETENCION
