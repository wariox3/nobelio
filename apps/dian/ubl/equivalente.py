"""Documento equivalente P.O.S. y sus notas de ajuste.

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
from apps.dian.ubl.factura import ConstructorNotaCredito, ConstructorNotaDebito
from apps.documentos.models import DocumentoTipo as _Tipo



class ConstructorDocumentoEquivalentePOS(ConstructorUBL):
    """Documento equivalente P.O.S. (Invoice tipo 20, CUDE).

    Tiquete de máquina registradora con sistema P.O.S., numeral 8.2 del Anexo
    Técnico de documento equivalente electrónico (Res. 000165/2023). Es un
    ``Invoice`` UBL 2.1 corriente —mismo XSD que la factura de venta, con el que
    la ejemplificación oficial valida sin un error—, así que lo único que lo
    distingue de una factura son cuatro señas de identidad:

    - el ``cbc:ProfileID`` propio (DEAD03),
    - el ``cbc:InvoiceTypeCode`` ``20`` (DEAD12b), que viene del catálogo,
    - el identificador, que es **CUDE** y no CUFE: se firma con el PIN del
      software en vez de con la clave técnica de la resolución,
    - y las tres extensiones propias, obligatorias por DEPD11 y DEPD21.

    Las tres extensiones las añade la fase siguiente; hasta entonces el XML es
    estructuralmente correcto pero la DIAN lo rechazaría por incompleto.

    El adquiriente no se fuerza aquí: el P.O.S. lo declara como cualquier otro
    tipo, y quien emite decide si es el comprador identificado o el
    ``Consumidor Final`` genérico (``222222222222``) de la ejemplificación. Esa
    elección entra en el CUDE por ``NumAdq``, así que es del documento y no del
    constructor.

    Referencia: ``docs/anexo-documento-equivalente.md``.
    """

    profile_id = PROFILE_ID_DOCUMENTO_EQUIVALENTE_POS
    scheme_name = ident.SCHEME_NAME_CUDE
    usa_cude = True
    nombre_tipo = NOMBRE_TIPO_DOCUMENTO_EQUIVALENTE_POS

    def _linea_extra(self, il, linea):
        """``cbc:FreeOfChargeIndicator``, como en la ejemplificación oficial.

        Repite la línea de ``ConstructorFacturaUBL`` en vez de heredar de él a
        propósito: el P.O.S. y la factura coinciden hoy, pero son documentos de
        anexos distintos y encadenarlos haría que un cambio en la factura se
        colara en el P.O.S. sin que nadie lo pidiera.
        """
        _sub(il, "cbc", "FreeOfChargeIndicator", "false")

    # -- Extensiones propias -------------------------------------------------

    def _extensiones(self, raiz, cude):
        """El ``sts:DianExtensions`` de siempre y las tres del P.O.S.

        Las reglas DEPD11 y DEPD21 —las dos de rechazo— exigen cinco nodos:
        ``sts:DianExtensions``, ``ds:Signature``,
        ``InformacionDelFabricanteDelSoftware``,
        ``InformacionBeneficiosComprador`` e ``InformacionCajaVenta``. No hay
        condicionales: un tiquete sin el programa de puntos se rechaza igual
        que uno sin firma.

        La firma la añade después ``apps/dian/firma``, que engancha su
        ``UBLExtension`` al final, así que el orden acaba siendo el de la
        ejemplificación oficial.
        """
        super()._extensiones(raiz, cude)
        extensiones = raiz.find(_q("ext", "UBLExtensions"))
        datos = self._datos_pos()

        self._extension_pares(
            extensiones, "FabricanteSoftware",
            "InformacionDelFabricanteDelSoftware",
            (
                ("NombreApellido", self._fabricante(
                    "fabricante_nombre", settings.DIAN_FABRICANTE_NOMBRE)),
                ("RazonSocial", self._fabricante(
                    "fabricante_razon_social", settings.DIAN_FABRICANTE_RAZON_SOCIAL)),
                ("NombreSoftware", self._fabricante(
                    "fabricante_nombre_software",
                    settings.DIAN_FABRICANTE_NOMBRE_SOFTWARE)),
            ),
        )
        self._extension_pares(
            extensiones, "BeneficiosComprador",
            "InformacionBeneficiosComprador",
            (
                ("Codigo", datos.comprador_codigo
                 or self.doc.adquiriente.numero_identificacion),
                ("NombresApellidos", datos.comprador_nombres
                 or self.doc.adquiriente.razon_social),
                ("Puntos", datos.comprador_puntos),
            ),
        )
        self._extension_pares(
            extensiones, "PuntoVenta", "InformacionCajaVenta",
            (
                ("PlacaCaja", datos.caja_placa),
                # Con tilde las dos: es el literal que compara la DIAN
                # (DEPD27 y DEPD33), no una errata.
                ("UbicaciónCaja", datos.caja_ubicacion),
                ("Cajero", datos.cajero),
                ("TipoCaja", datos.caja_tipo),
                ("CódigoVenta", datos.codigo_venta),
                ("SubTotal", _valor(
                    datos.subtotal if datos.subtotal is not None
                    else self.doc.valor_bruto
                )),
            ),
        )

    def _fabricante(self, campo, por_defecto):
        """Quién fabricó el software: el ajuste de la plataforma, o el del emisor.

        Lo normal es el de la plataforma: el fabricante es el mismo para todos
        los emisores, porque describe quién hizo el software y no quién emite.
        El campo de ``SoftwareDian`` es la excepción, para el obligado que use
        software propio; vacío —que es lo normal— no pisa nada.
        """
        return getattr(self.software, campo, "") or por_defecto

    def _datos_pos(self):
        """El satélite con la caja y el comprador, que aquí es obligatorio."""
        datos = getattr(self.doc, "pos", None)
        if datos is None:
            raise ValueError(
                "El documento equivalente P.O.S. no tiene datos de la venta en "
                "caja (`DocumentoPOS`): sin ellos no se pueden armar las "
                "extensiones que exigen las reglas DEPD11 y DEPD21."
            )
        return datos

    def _extension_pares(self, extensiones, envoltorio, grupo, pares):
        """Una ``UBLExtension`` con un grupo de pares ``Name``/``Value``.

        Las tres extensiones del P.O.S. tienen la misma forma y —cosa rara— van
        en el namespace de ``Invoice-2``, el mismo de la raíz, en vez de en uno
        propio como el ``sts:`` de la DIAN. Se reproduce tal cual la
        ejemplificación.
        """
        ns = NS_RAIZ[self.nombre_raiz]
        ext = _sub(extensiones, "ext", "UBLExtension")
        contenido = _sub(ext, "ext", "ExtensionContent")
        envuelto = etree.SubElement(contenido, etree.QName(ns, envoltorio))
        info = etree.SubElement(envuelto, etree.QName(ns, grupo))
        for nombre, valor in pares:
            etree.SubElement(info, etree.QName(ns, "Name")).text = nombre
            etree.SubElement(info, etree.QName(ns, "Value")).text = (
                "" if valor is None else str(valor)
            )


class ConstructorNotaAjusteDECredito(ConstructorNotaCredito):
    """Nota de ajuste crédito al documento equivalente (CreditNote, tipo 94).

    Numeral 8.12.1. A diferencia de la nota de ajuste de **nómina**, que
    reemplaza o elimina el documento entero, esta se comporta como una nota
    crédito de factura: corrige por diferencia, lleva su
    ``cac:DiscrepancyResponse`` y referencia el documento ajustado.

    Lo que la separa de la nota crédito de factura son tres cosas: el
    ``ProfileID``, el ``CustomizationID`` —que aquí dice a qué documento
    equivalente se refiere, no el tipo de operación— y que el UUID
    referenciado es un **CUDE**, no un CUFE.

    **No lleva las tres extensiones del P.O.S.**: sus ejemplificaciones traen
    solo ``sts:DianExtensions`` y la firma, y las reglas NAAA02 y NAAB03 no
    piden más. Por eso hereda de ``ConstructorNotaCredito`` y no del
    constructor del P.O.S.

    Referencia: ``docs/anexo-documento-equivalente.md``.
    """

    profile_id = PROFILE_ID_NOTA_AJUSTE_DE_CREDITO
    customization_id_default = CUSTOMIZATION_NOTA_AJUSTE_DE_POS
    scheme_name_referencia = ident.SCHEME_NAME_CUDE
    codigo_tipo_referencia = CODIGO_TIPO_REFERENCIA_POS


class ConstructorNotaAjusteDEDebito(ConstructorNotaDebito):
    """Nota de ajuste débito al documento equivalente (DebitNote, CUDE).

    Numeral 8.12.2. Igual que la de crédito, con la salvedad de siempre del
    UBL ``DebitNote``: no tiene elemento de tipo, así que su ``codigo_dian`` no
    se emite en ninguna parte.

    No emite ``DocumentTypeCode`` en la referencia: su ejemplificación
    (``NDADxml.xml``) no lo trae, al contrario que la de crédito. La asimetría
    es del anexo, no nuestra, y se reproduce tal cual.
    """

    profile_id = PROFILE_ID_NOTA_AJUSTE_DE_DEBITO
    customization_id_default = CUSTOMIZATION_NOTA_AJUSTE_DE_POS
    scheme_name_referencia = ident.SCHEME_NAME_CUDE
