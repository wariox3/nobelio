"""Documento soporte en adquisiciones a no obligados, y su nota de ajuste.

Parte del paquete `apps.dian.ubl`. La superficie pública no cambió al partirlo:
`from apps.dian import ubl` sigue dando acceso a todo desde `__init__`.
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from lxml import etree

from apps.dian import identificadores as ident
from apps.dian.ubl.base import *  # noqa: F401,F403  (constantes y perfiles)
from apps.dian.ubl.base import (  # los ayudantes privados no vienen en el `*`
    ConstructorUBL,
    _cantidad,
    _nsmap,
    _q,
    _sub,
    _valor,
    _valor_por_tributo,
    agrupar_impuestos,
)
from apps.dian.ubl.factura import _ConstructorNotaUBL
from apps.documentos.models import DocumentoTipo as _Tipo



class ConstructorDocumentoSoporte(ConstructorUBL):
    """Documento soporte en adquisiciones a no obligados (Invoice tipo 05, CUDS).

    Aquí el que emite y firma es el comprador, así que los roles UBL van al
    revés que en la factura. La DIAN los llama:

    - **SNO**: el sujeto no obligado a facturar, es decir el *vendedor*. Va en
      ``cac:AccountingSupplierParty``, y sus datos son los del ``adquiriente``
      del documento —que en este tipo es la contraparte, no el receptor—.
    - **ABS**: el adquiriente de bienes y servicios, el obligado que genera el
      documento. Va en ``cac:AccountingCustomerParty``, y es nuestro ``emisor``.

    El identificador es el CUDS, que no es el CUDE (ver ``calcular_cuds``), y
    las retenciones van en su propio ``cac:WithholdingTaxTotal``.

    Referencia: Anexo Técnico Documento Soporte v1.1, resumen en
    ``docs/anexo-documento-soporte.md``.
    """

    profile_id = PROFILE_ID_DOCUMENTO_SOPORTE
    scheme_name = ident.SCHEME_NAME_CUDS
    emite_retenciones = True

    @property
    def customization_id_default(self) -> str:
        """Procedencia del vendedor (DSAD02): 10 residente, 11 no residente.

        Se deduce del país del vendedor en vez de pedirse aparte: es el mismo
        dato dicho dos veces, y un documento cuyo CustomizationID no case con la
        dirección que lleva dentro es un rechazo.
        """
        pais = self.doc.adquiriente.pais
        if pais is not None and pais.codigo == COD_PAIS_COLOMBIA:
            return CUSTOMIZATION_DS_RESIDENTE
        return CUSTOMIZATION_DS_NO_RESIDENTE

    def calcular_identificador(self) -> str:
        """CUDS: composición propia, y el vendedor antes que el adquiriente."""
        return ident.calcular_cuds(
            numero_documento=self.doc.numero,
            fecha=self.doc.fecha_emision,
            hora=self.doc.hora_emision,
            valor_sin_impuestos=self.doc.valor_bruto,
            valor_iva=_valor_por_tributo(self.impuestos, COD_IVA),
            valor_total=self.doc.total_a_pagar,
            nit_vendedor=self.doc.adquiriente.numero_identificacion,
            nit_adquiriente=self.doc.emisor.numero_identificacion,
            pin_software=self.pin,
            tipo_ambiente=self.ambiente,
        )

    def _periodo_linea(self, il, linea):
        """El DS exige la **fecha de compra** en cada línea (regla DSFC02a).

        Va en ``cac:InvoicePeriod/cbc:StartDate``. Si la línea no trae periodo
        propio se usa la fecha de emisión: la compra que soporta el documento es
        la que se está documentando, y omitir el elemento es un rechazo.
        """
        if linea.periodo_desde or linea.periodo_hasta:
            return super()._periodo_linea(il, linea)
        periodo = _sub(il, "cac", "InvoicePeriod")
        _sub(periodo, "cbc", "StartDate", self.doc.fecha_emision.isoformat())
        if linea.periodo_descripcion_codigo:
            _sub(periodo, "cbc", "DescriptionCode", linea.periodo_descripcion_codigo)
        if linea.periodo_descripcion:
            _sub(periodo, "cbc", "Description", linea.periodo_descripcion)

    def _parte_emisor(self, raiz):
        """``cac:AccountingSupplierParty``, que aquí es el **vendedor** (SNO).

        El nombre del método es el del hueco que deja la base —``construir`` lo
        llama en el orden que fija el XSD—; lo que se emite dentro no es el
        emisor sino la contraparte.
        """
        self._parte_soporte(
            raiz, "AccountingSupplierParty", self.doc.adquiriente,
            tributo=TRIBUTO_NO_APLICA, con_direccion=True,
        )

    def _parte_adquirente(self, raiz):
        """``cac:AccountingCustomerParty``: el adquiriente obligado (ABS).

        Es nuestro ``emisor``: el que firma. No lleva dirección física; el anexo
        solo le pide el grupo tributario.
        """
        self._parte_soporte(
            raiz, "AccountingCustomerParty", self.doc.emisor,
            tributo=TRIBUTO_IVA, con_direccion=False,
        )

    def _parte_soporte(self, raiz, etiqueta, entidad, *, tributo, con_direccion):
        """Bloque de parte del documento soporte: bastante más corto que el de factura.

        No lleva ``PartyName``, ``PartyIdentification``, ``PartyLegalEntity``,
        ``Contact`` ni ``Person`` —el anexo del DS no los define— y su
        ``PartyTaxScheme`` va sin ``RegistrationAddress``. Comprobado contra
        ``apps/dian/datos/ejemplos/documento-soporte/documento-soporte-residente.xml``.
        """
        parte = _sub(raiz, "cac", etiqueta)
        _sub(parte, "cbc", "AdditionalAccountID", self._codigo_organizacion(entidad))
        party = _sub(parte, "cac", "Party")
        if con_direccion:
            self._direccion_fisica(party, entidad)
        pts = _sub(party, "cac", "PartyTaxScheme")
        _sub(pts, "cbc", "RegistrationName", entidad.razon_social)
        _sub(pts, "cbc", "CompanyID", entidad.numero_identificacion,
             schemeAgencyID="195", schemeAgencyName=AGENCIA_DIAN,
             schemeID=self._scheme_id_identificacion(entidad),
             schemeName=self._scheme_name_identificacion(entidad))
        _sub(pts, "cbc", "TaxLevelCode", self._responsabilidades(entidad))
        esquema = _sub(pts, "cac", "TaxScheme")
        _sub(esquema, "cbc", "ID", tributo[0])
        _sub(esquema, "cbc", "Name", tributo[1])

    def _scheme_name_identificacion(self, entidad) -> str:
        """``@schemeName`` del CompanyID: ``31`` (NIT) mientras el SNO sea residente.

        La regla DSAJ25a lo compara con el literal ``31`` aunque la parte se
        identifique con cédula —así aparece en la ejemplificación oficial, con un
        vendedor persona natural—. Con un vendedor no residente
        (``CustomizationID`` = 11) la identificación sale de la lista
        ``TipoIdFiscal`` y se emite la del propio tipo de identificación.
        """
        if self.customization_id == CUSTOMIZATION_DS_RESIDENTE:
            return SCHEME_NAME_NIT
        return entidad.tipo_identificacion.codigo

    def _scheme_id_identificacion(self, entidad) -> str:
        """``@schemeID``: el dígito de verificación del número.

        Va de la mano de `_scheme_name_identificacion`: si la parte se declara
        como NIT (``31``), la DIAN comprueba el dígito contra el número. El
        modelo solo guarda DV para los NIT —una cédula con DV inventado la
        rechaza la DIAN—, así que para el vendedor residente se calcula aquí; la
        ejemplificación oficial hace lo mismo con un SNO persona natural.
        """
        if entidad.digito_verificacion:
            return entidad.digito_verificacion
        if self.customization_id == CUSTOMIZATION_DS_RESIDENTE:
            return digito_verificacion(entidad.numero_identificacion) or "0"
        return "0"


class ConstructorNotaAjuste(ConstructorDocumentoSoporte, _ConstructorNotaUBL):
    """Nota de ajuste al documento soporte (CreditNote, tipo 95, CUDS).

    Es un documento soporte por dentro y una nota por fuera, y hereda de los dos
    en ese orden: del ``ConstructorDocumentoSoporte`` toma las partes invertidas
    (el vendedor como *supplier*), el CUDS, las retenciones aparte y la fecha de
    compra de la línea; de ``_ConstructorNotaUBL``, la raíz de nota con su
    ``cac:DiscrepancyResponse`` y su ``cac:BillingReference``, que no lleva
    resolución ni vencimiento.

    El documento corregido es un DS, así que su UUID se referencia como
    ``CUDS-SHA384`` y no como el CUFE de una nota de factura.

    Referencia: Anexo Técnico Documento Soporte v1.1, numeral 14.1.1.2; resumen
    en ``docs/anexo-documento-soporte.md`` §9.
    """

    profile_id = PROFILE_ID_NOTA_AJUSTE
    nombre_raiz = "CreditNote"
    etiqueta_tipo = "CreditNoteTypeCode"
    etiqueta_linea = "CreditNoteLine"
    etiqueta_cantidad = "CreditedQuantity"
    etiqueta_total = "LegalMonetaryTotal"
    scheme_name_referencia = ident.SCHEME_NAME_CUDS


# Mapeo código de tipo de documento -> constructor.

# Los códigos de retención, para repartir los impuestos entre cac:TaxTotal y
# cac:WithholdingTaxTotal. Va aquí abajo, con el otro import de modelos, por la
# misma razón: los métodos que lo usan solo lo resuelven al ejecutarse.
from apps.catalogos.models import Tributo as _Tributo  # noqa: E402

# ---------------------------------------------------------------------------
# AttachedDocument: el "sobre" con el que se entrega el documento al adquiriente
# ---------------------------------------------------------------------------
# Literales del contenedor, por tipo de documento envuelto. La DIAN no valida el
# AttachedDocument (no se le envía: es para el receptor), así que estos textos
# siguen la convención de los proveedores tecnológicos.
