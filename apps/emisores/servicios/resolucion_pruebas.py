"""La resolución de numeración del Set de Pruebas, por tipo de software.

Registrar un software es el primer paso de una habilitación, y sin numeración
no hay nada que emitir después. Sembrarla aquí evita el hueco entre los dos
pasos —un emisor con software y sin resolución no puede crear ni un documento
de prueba— y deja el dato en un solo sitio en vez de repetido en cada vista que
lo necesite.

Lo que se siembra es **del sandbox de la DIAN**, no del emisor: la resolución
18760000001 con prefijo SETP y su clave técnica son públicas y las mismas para
todos. Por eso solo se siembra en ambiente de pruebas; ver
`sembrar_resolucion_de_pruebas`.
"""
import logging
from datetime import date

from apps.nucleo.models import Ambiente

logger = logging.getLogger(__name__)

# Los datos que la DIAN publica para su Set de Pruebas de facturación.
RESOLUCION_SET_PRUEBAS = {
    "prefijo": "SETP",
    "numero_resolucion": "18760000001",
    "fecha_resolucion": date(2026, 6, 29),
    "rango_desde": 990000000,
    "rango_hasta": 995000000,
    "clave_tecnica": "fc8eac422eba16e22ffd8c6f94b3f40a6e38162c",
    "vigente_desde": date(2019, 1, 19),
    "vigente_hasta": date(2030, 1, 19),
}

# Qué resolución de pruebas le toca a cada tipo de software, y contra qué
# ambiente del emisor se decide (cada operación tiene el suyo y pueden no
# coincidir: se puede estar en producción para factura y en pruebas para POS).
#
# Las tres operaciones no están en el mismo punto, y es a propósito:
#
# - `facturacion`: la de arriba, sobre el tipo de factura 01.
#
# - `nomina`: **no lleva resolución de numeración**. La nómina no está en
#   `DocumentoTipo.CODIGOS_CON_RESOLUCION` y el modelo `Nomina` numera con su
#   propio prefijo y consecutivo, sin `sts:InvoiceControl`. No es que falte el
#   dato: es que no existe, así que sembrar algo aquí sería inventarlo.
#
# - `documento_equivalente`: falta el dato. La DIAN da al P.O.S. una
#   numeración propia (reglas DEAB05a y DEAB05b comprueban su
#   `sts:InvoiceControl`), pero no tenemos todavía la resolución de su Set de
#   Pruebas —ver `docs/anexo-documento-equivalente.md`, "Falta"—, y el catálogo
#   TipoFactura tampoco trae aún el código con el que registrarla. Inventar un
#   número aquí es lo peor que se puede hacer: sería numerar con una
#   autorización que no es del emisor. Cuando llegue el dato, se añade una
#   entrada a este diccionario y no hay que tocar nada más.
RESOLUCION_POR_TIPO_DE_SOFTWARE = {
    "facturacion": {
        "codigo_tipo_factura": "01",
        "campo_ambiente": "ambiente_facturacion",
        "datos": RESOLUCION_SET_PRUEBAS,
    },
}


def sembrar_resolucion_de_pruebas(emisor, tipo_software):
    """Siembra la resolución del Set de Pruebas que le toca a ese software.

    Devuelve ``(resolucion, creada)``, o ``(None, False)`` cuando no hay nada
    que sembrar: porque esa operación no se numera con resolución (nómina),
    porque todavía no se conoce la suya (documento equivalente) o porque el
    emisor ya está en producción para ella.

    **Solo en ambiente de pruebas.** Lo que escribe son datos del sandbox de la
    DIAN: una resolución que no es del emisor y una clave técnica pública. En
    un emisor que ya emite en producción eso significa numerar con un rango que
    no le pertenece, y los consecutivos que se gasten por el camino no se
    recuperan. No es motivo para rechazar el alta del software —registrarlo en
    producción es normal—, así que se calla y no siembra.

    Es idempotente: la resolución se identifica por (emisor, tipo de factura,
    prefijo, número), que es su índice único, así que repetir la llamada la
    actualiza en vez de duplicarla.
    """
    from apps.catalogos.models import TipoFactura
    from apps.emisores.models import Resolucion

    receta = RESOLUCION_POR_TIPO_DE_SOFTWARE.get(str(tipo_software))
    if receta is None:
        return None, False

    if getattr(emisor, receta["campo_ambiente"]) != Ambiente.PRUEBAS:
        return None, False

    try:
        tipo_factura = TipoFactura.objects.get(codigo=receta["codigo_tipo_factura"])
    except TipoFactura.DoesNotExist:
        # El catálogo no está cargado (`manage.py cargar_catalogos`). Registrar
        # el software sí tiene sentido sin él, así que no se revienta el alta;
        # pero tampoco se calla: sin resolución, el emisor no podrá emitir y el
        # motivo no se ve por ninguna parte.
        logger.warning(
            "No se sembró la resolución de pruebas del emisor %s: falta el "
            "tipo de factura '%s' en el catálogo (¿cargar_catalogos?).",
            emisor.pk, receta["codigo_tipo_factura"],
        )
        return None, False

    datos = receta["datos"]
    return Resolucion.objects.update_or_create(
        emisor=emisor,
        tipo_factura=tipo_factura,
        prefijo=datos["prefijo"],
        numero_resolucion=datos["numero_resolucion"],
        defaults={
            **{
                k: v for k, v in datos.items()
                if k not in ("prefijo", "numero_resolucion")
            },
            "activa": True,
        },
    )
