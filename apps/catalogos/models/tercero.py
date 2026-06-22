"""Catálogos relativos a la identificación de terceros (emisor/adquirente)."""
from .base import ElementoCatalogo


class TipoIdentificacion(ElementoCatalogo):
    """Tipo de identificación fiscal (cédula, NIT, etc.). Lista TipoIdFiscal."""

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "tipo de identificación"
        verbose_name_plural = "tipos de identificación"


class TipoOrganizacion(ElementoCatalogo):
    """Tipo de organización (persona natural / jurídica). Lista TipoOrganizacion."""

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "tipo de organización"
        verbose_name_plural = "tipos de organización"


class ResponsabilidadFiscal(ElementoCatalogo):
    """Responsabilidad tributaria (cbc:TaxLevelCode). Lista TipoResponsabilidad.

    El código puede ser alfanumérico (p. ej. ``O-13``, ``R-99-PN``).
    """

    class Meta(ElementoCatalogo.Meta):
        verbose_name = "responsabilidad fiscal"
        verbose_name_plural = "responsabilidades fiscales"
