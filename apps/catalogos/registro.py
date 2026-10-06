"""Ficha de cada catálogo: qué es, de dónde sale y dónde lo usa la API.

Es lo que publican `GET /api/catalogos/` y la descripción de cada catálogo en el
esquema OpenAPI, y lo que el sitio de documentación enlaza desde la referencia:
quien lee que `detalles[].unidad_medida` es un id necesita saber de qué lista.

Los `usos` se escriben a mano porque dicen algo que el código no dice solo —si
el campo va por id o por código, y en qué ruta—, pero no pueden quedarse atrás:
`RegistroTests` los compara con los serializers reales y falla si un campo de
catálogo nuevo no está aquí o si uno de aquí ya no existe.
"""
from dataclasses import dataclass, field

from apps.catalogos import models


@dataclass(frozen=True)
class Anexo:
    """El anexo técnico DIAN del que sale una lista."""

    documento: str
    version: str
    resolucion: str


@dataclass(frozen=True)
class Uso:
    """Un campo de la API que recibe valores del catálogo.

    ``valor`` es ``"id"`` —el `id` de la fila en este catálogo, fijo por
    código en todos los entornos (columna `id` del `.gc`)— o ``"codigo"``
    —el código DIAN—.
    """

    ruta: str
    campo: str
    valor: str


@dataclass(frozen=True)
class Catalogo:
    nombre: str
    modelo: type
    descripcion: str
    lista_dian: str
    anexos: tuple
    usos: tuple = field(default=())


FACTURA = Anexo(
    "Factura Electrónica de Venta", "1.9", "Resolución 000165 de 2023"
)
SOPORTE = Anexo("Documento Soporte", "1.1", "Resolución 000167 de 2021")
EQUIVALENTE = Anexo(
    "Documento Equivalente Electrónico", "1.0", "Resolución 000165 de 2023"
)
NOMINA = Anexo("Nómina Electrónica", "1.0", "Resolución 000013 de 2021")
RADIAN = Anexo("RADIAN", "1.1", "Resolución 000085 de 2022")

DOCUMENTO = "/api/documentos/documento/"
EMISOR = "/api/emisores/emisor/"
RESOLUCION = "/api/emisores/resolucion/"
EMPLEADO = "/api/nomina/empleado/"
NOMINA_RUTA = "/api/nomina/nomina/"


def _id(ruta, *campos):
    return tuple(Uso(ruta, campo, "id") for campo in campos)


def _codigo(ruta, *campos):
    return tuple(Uso(ruta, campo, "codigo") for campo in campos)


# En el orden de las rutas de `apps/catalogos/urls.py`.
CATALOGOS = (
    Catalogo(
        "tipo-factura", models.TipoFactura,
        "Tipo de documento electrónico (`cbc:InvoiceTypeCode`): factura de "
        "venta, de exportación, de contingencia, notas… Incluye los códigos 05 "
        "y 95 del documento soporte y su nota de ajuste, y el 20 del documento "
        "equivalente P.O.S.",
        "TipoDocumento", (FACTURA, SOPORTE, EQUIVALENTE),
        _id(RESOLUCION, "tipo_factura"),
    ),
    Catalogo(
        "tipo-identificacion", models.TipoIdentificacion,
        "Tipo de documento de identificación: cédula, NIT, pasaporte, "
        "documento extranjero… El `id` es el propio código DIAN (13 cédula = "
        "13, 31 NIT = 31).",
        "TipoIdentificacion", (FACTURA,),
        _id(DOCUMENTO, "adquiriente.tipo_identificacion")
        + _id(EMISOR, "tipo_identificacion")
        + _id(EMPLEADO, "tipo_identificacion")
        + _id(NOMINA_RUTA, "empleado.tipo_identificacion"),
    ),
    Catalogo(
        "tipo-organizacion", models.TipoOrganizacion,
        "Tipo de organización jurídica: persona natural o persona jurídica.",
        "TipoOrganizacion", (FACTURA,),
        _id(DOCUMENTO, "adquiriente.tipo_organizacion")
        + _id(EMISOR, "tipo_organizacion"),
    ),
    Catalogo(
        "responsabilidad-fiscal", models.ResponsabilidadFiscal,
        "Responsabilidad tributaria (`cbc:TaxLevelCode`). Los códigos son "
        "alfanuméricos: `O-13`, `O-15`, `O-23`, `O-47`, `R-99-PN`.",
        "TipoResponsabilidad", (FACTURA,),
        _id(DOCUMENTO, "adquiriente.responsabilidades[]")
        + _codigo(EMISOR, "responsabilidades[]"),
    ),
    Catalogo(
        "tributo", models.Tributo,
        "Tributo o impuesto (`cac:TaxScheme`): IVA (01), ICA (03), INC (04)… "
        "Los códigos 05 a 08 son retenciones (ReteIVA, ReteFuente, ReteICA, "
        "ReteCREE): no suman al total a pagar.",
        "TipoImpuesto", (FACTURA,),
        _id(DOCUMENTO, "detalles[].impuestos[].tributo"),
    ),
    Catalogo(
        "unidad-medida", models.UnidadMedida,
        "Unidad de medida de cada línea (`cbc:unitCode`), del estándar UN/ECE "
        "Rec 20: `94` unidad, `KGM` kilogramo, `HUR` hora…",
        "UnidadesMedida", (FACTURA,),
        _id(DOCUMENTO, "detalles[].unidad_medida"),
    ),
    Catalogo(
        "forma-pago", models.FormaPago,
        "Forma de pago: contado (1) o crédito (2).",
        "FormasPago", (FACTURA,),
        _id(DOCUMENTO, "forma_pago")
        + _id(EMPLEADO, "forma_pago")
        + _id(NOMINA_RUTA, "forma_pago", "empleado.forma_pago"),
    ),
    Catalogo(
        "medio-pago", models.MedioPago,
        "Medio de pago (`cbc:PaymentMeansCode`): efectivo, transferencia, "
        "tarjeta, cheque…",
        "MediosPago", (FACTURA,),
        _id(DOCUMENTO, "medio_pago")
        + _id(EMPLEADO, "medio_pago")
        + _id(NOMINA_RUTA, "medio_pago", "empleado.medio_pago"),
    ),
    Catalogo(
        "moneda", models.Moneda,
        "Moneda del documento (`cbc:DocumentCurrencyCode`), ISO 4217.",
        "TipoMoneda", (FACTURA,),
        _id(DOCUMENTO, "moneda") + _id(NOMINA_RUTA, "moneda"),
    ),
    Catalogo(
        "pais", models.Pais,
        "País (`cac:Country`), ISO 3166-1 alfa-2.",
        "Paises", (FACTURA,),
        _id(DOCUMENTO, "adquiriente.pais")
        + _codigo(EMISOR, "pais")
        + _id(EMPLEADO, "pais")
        + _id(NOMINA_RUTA, "empleado.pais", "lugar_trabajo_pais"),
    ),
    Catalogo(
        "departamento", models.Departamento,
        "Departamento de Colombia, código DANE de 2 dígitos. El `id` es la "
        "posición del código en orden (05 Antioquia = 1 … 99 Vichada = 33) y es "
        "el mismo en todos los entornos.",
        "Departamentos", (FACTURA,),
        _id(DOCUMENTO, "adquiriente.departamento")
        + _codigo(EMISOR, "departamento")
        + _id(EMPLEADO, "departamento")
        + _id(NOMINA_RUTA, "empleado.departamento", "lugar_trabajo_departamento"),
    ),
    Catalogo(
        "municipio", models.Municipio,
        "Municipio de Colombia, código DANE de 5 dígitos, con su departamento. "
        "El `id` es la posición del código en orden y es el mismo en todos los "
        "entornos. `codigo_postal` no es de la lista DIAN: es el de la cabecera "
        "municipal, del dataset de 4-72 en datos.gov.co.",
        "Municipio", (FACTURA,),
        _id(DOCUMENTO, "adquiriente.municipio")
        + _codigo(EMISOR, "municipio")
        + _id(EMPLEADO, "municipio")
        + _id(NOMINA_RUTA, "empleado.municipio", "lugar_trabajo_municipio"),
    ),
    Catalogo(
        "periodo-nomina", models.PeriodoNomina,
        "Periodicidad del pago de nómina: semanal, decenal, catorcenal, "
        "quincenal, mensual… (anexo de nómina, numeral 5.5.1).",
        "PeriodoNomina", (NOMINA,),
        _id(NOMINA_RUTA, "periodo_nomina"),
    ),
    Catalogo(
        "tipo-contrato", models.TipoContrato,
        "Tipo de contrato del trabajador: término fijo, indefinido, obra o "
        "labor, aprendizaje, prácticas (anexo de nómina, numeral 5.5.2).",
        "TipoContrato", (NOMINA,),
        _id(EMPLEADO, "tipo_contrato")
        + _id(NOMINA_RUTA, "tipo_contrato", "empleado.tipo_contrato"),
    ),
    Catalogo(
        "tipo-trabajador", models.TipoTrabajador,
        "Tipo de cotizante ante la seguridad social (anexo de nómina, numeral "
        "5.5.3).",
        "TipoTrabajador", (NOMINA,),
        _id(EMPLEADO, "tipo_trabajador")
        + _id(NOMINA_RUTA, "tipo_trabajador", "empleado.tipo_trabajador"),
    ),
    Catalogo(
        "subtipo-trabajador", models.SubTipoTrabajador,
        "Subtipo de cotizante; `00` (no aplica) en la mayoría de los casos "
        "(anexo de nómina, numeral 5.5.4).",
        "SubTipoTrabajador", (NOMINA,),
        _id(EMPLEADO, "subtipo_trabajador")
        + _id(NOMINA_RUTA, "subtipo_trabajador", "empleado.subtipo_trabajador"),
    ),
    Catalogo(
        "evento-radian", models.EventoRadian,
        "Evento que el adquiriente registra en RADIAN sobre una factura "
        "recibida: acuse de recibo (030), reclamo (031), recibo del bien o "
        "servicio (032) y aceptación expresa (033). La DIAN no lo publica en "
        "Genericode: sale de los ejemplos del anexo RADIAN.",
        "EventoRadian", (RADIAN,),
    ),
    Catalogo(
        "concepto-reclamo", models.ConceptoReclamo,
        "Por qué se reclama una factura en el evento 031: inconsistencias, "
        "mercancía no entregada total o parcialmente, servicio no prestado.",
        "ConceptoReclamo", (FACTURA, RADIAN),
    ),
)

POR_NOMBRE = {catalogo.nombre: catalogo for catalogo in CATALOGOS}
POR_MODELO = {catalogo.modelo: catalogo for catalogo in CATALOGOS}
