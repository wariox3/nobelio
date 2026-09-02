"""Generación del XML UBL 2.1 para la DIAN.

Construye los documentos electrónicos conforme al Anexo Técnico v1.9, incluidas
las extensiones DIAN (``sts:DianExtensions``). El XML que sale **no está
firmado**: la firma XAdES-EPES la añade ``apps/dian/firma`` en una segunda
``UBLExtension``.

Una clase base, ``ConstructorUBL``, concentra lo común; cada subclase parametriza
las diferencias de su tipo (raíz, código, nombre de la línea, total monetario,
CUFE o CUDE, y las referencias al documento que corrige).

Esto era un solo módulo de 1.340 líneas con once constructores dentro, que se
había convertido en el sitio donde había que buscarlo todo. Ahora está partido
**por familia de documento**, que es como se leen los anexos y como se trabaja:

- ``base``        — namespaces, constantes, ayudantes y ``ConstructorUBL``
- ``factura``     — factura de venta y sus notas crédito y débito
- ``soporte``     — documento soporte y su nota de ajuste
- ``adjunto``     — el ``AttachedDocument`` que se entrega al comprador
- ``equivalente`` — documento equivalente P.O.S. y sus notas de ajuste

La superficie pública **no cambió**: todo lo que antes colgaba de
``apps.dian.ubl`` se reexporta aquí, así que ``from apps.dian import ubl`` y
``ubl.NS``, ``ubl.constructor_para`` o ``ubl.CONSTRUCTORES`` siguen valiendo
igual. Referencia: ``docs/anexo-tecnico.md``.
"""
from apps.dian.ubl.base import *  # noqa: F401,F403
from apps.dian.ubl.base import (  # noqa: F401
    NS,
    NS_RAIZ,
    NS_RAIZ_ADJUNTO,
    ConstructorUBL,
    agrupar_impuestos,
    # Los ayudantes con guion bajo no vienen en el `*` y sí los usa quien está
    # fuera del paquete: `apps.dian.firma` construye la extensión de la firma
    # con `_q` y `_sub`, y `apps.dian.nomina` reutiliza el resto.
    _cantidad,
    _nsmap,
    _q,
    _sub,
    _valor,
    _valor_por_tributo,
)
from apps.dian.ubl.factura import (  # noqa: F401
    ConstructorFacturaUBL,
    ConstructorNotaCredito,
    ConstructorNotaDebito,
)
from apps.dian.ubl.soporte import (  # noqa: F401
    ConstructorDocumentoSoporte,
    ConstructorNotaAjuste,
)
from apps.dian.ubl.adjunto import (  # noqa: F401
    CODIGO_VALIDACION_ACEPTADO,
    PERFILES_ADJUNTO,
    VALIDADOR_DIAN,
    ConstructorAttachedDocument,
)
from apps.dian.ubl.equivalente import (  # noqa: F401
    ConstructorDocumentoEquivalentePOS,
    ConstructorNotaAjusteDECredito,
    ConstructorNotaAjusteDEDebito,
)
from apps.documentos.models import DocumentoTipo as _Tipo


CONSTRUCTORES = {
    _Tipo.Codigo.FACTURA_VENTA: ConstructorFacturaUBL,
    _Tipo.Codigo.NOTA_CREDITO: ConstructorNotaCredito,
    _Tipo.Codigo.NOTA_DEBITO: ConstructorNotaDebito,
    _Tipo.Codigo.DOCUMENTO_SOPORTE: ConstructorDocumentoSoporte,
    _Tipo.Codigo.NOTA_AJUSTE: ConstructorNotaAjuste,
    _Tipo.Codigo.DOCUMENTO_EQUIVALENTE_POS: ConstructorDocumentoEquivalentePOS,
    _Tipo.Codigo.NOTA_AJUSTE_DE_CREDITO: ConstructorNotaAjusteDECredito,
    _Tipo.Codigo.NOTA_AJUSTE_DE_DEBITO: ConstructorNotaAjusteDEDebito,
}


def constructor_para(documento, **kwargs) -> ConstructorUBL:
    """Devuelve el constructor adecuado según el código del tipo de documento."""
    codigo = documento.documento_tipo.codigo
    try:
        clase = CONSTRUCTORES[codigo]
    except KeyError:
        raise ValueError(f"Tipo de documento no soportado para UBL: {codigo}")
    # El concepto de corrección es del documento; el argumento explícito sigue
    # ganando para que las pruebas puedan forzar uno sin tocar la fila.
    if documento.concepto_correccion:
        kwargs.setdefault("concepto", documento.concepto_correccion)
    return clase(documento, **kwargs)


def generar_xml_factura(documento, *, software, resolucion, ambiente, clave_tecnica,
                        customization_id="10") -> bytes:
    """Genera el XML UBL 2.1 (sin firmar) de una factura de venta."""
    return ConstructorFacturaUBL(
        documento, software=software, resolucion=resolucion, ambiente=ambiente,
        clave_tecnica=clave_tecnica, customization_id=customization_id,
    ).generar_xml()
