"""Catálogo: tipo de organización."""
from .base import ElementoCatalogo


class TipoOrganizacion(ElementoCatalogo):
    """Tipo de organización (persona natural / jurídica). Lista TipoOrganizacion."""

    class Meta(ElementoCatalogo.Meta):
        db_table = "cat_tipo_organizacion"
        verbose_name = "tipo de organización"
        verbose_name_plural = "tipos de organización"
