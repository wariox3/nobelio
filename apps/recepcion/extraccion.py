"""Extracción de los documentos electrónicos que trae un correo.

Recorre los adjuntos —XML y PDF sueltos, ZIP (también anidados) y correos
adjuntos— y lee cada XML: el AttachedDocument que exige la DIAN, con el
documento incrustado y la validación, o un Invoice, CreditNote o DebitNote
suelto. Todo viene de terceros, así que el XML se lee sin DTD, entidades ni
red, y los ZIP tienen tope de profundidad, de archivos y de bytes.

No toca la base: devuelve lo que encontró y ``procesamiento`` decide.
"""
import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import PurePosixPath

from django.utils import timezone
from lxml import etree

from apps.documentos.models import DocumentoTipo

logger = logging.getLogger(__name__)

CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
NS = {"cbc": CBC, "cac": CAC}

# Topes contra un ZIP bomba o un correo armado para tumbar al worker.
MAXIMA_PROFUNDIDAD = 4
MAXIMOS_ARCHIVOS = 200
MAXIMOS_BYTES = 100 * 1024 * 1024

# Sin DTD, sin entidades y sin red: un XML de un tercero no puede leer archivos
# del servidor (XXE) ni hacer peticiones al abrirse.
_PARSER = etree.XMLParser(
    resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False,
    remove_comments=True,
)

# El tipo interno por raíz y código. Invoice con 01, 02, 03 o 04 es factura de
# venta (nacional, exportación, contingencia); otros códigos —el 05 del
# documento soporte, por ejemplo— no son una compra a un proveedor.
CODIGOS_FACTURA = {"", "01", "02", "03", "04"}
TIPO_POR_RAIZ = {
    "CreditNote": DocumentoTipo.Codigo.NOTA_CREDITO,
    "DebitNote": DocumentoTipo.Codigo.NOTA_DEBITO,
}
CODIGO_POR_RAIZ = {
    "Invoice": "InvoiceTypeCode",
    "CreditNote": "CreditNoteTypeCode",
    "DebitNote": "DebitNoteTypeCode",
}


class ContenidoExcesivo(Exception):
    """El correo pasa los topes de profundidad, archivos o bytes."""


@dataclass
class Archivo:
    nombre: str
    contenido: bytes

    @property
    def extension(self):
        return PurePosixPath(self.nombre.lower()).suffix

    @property
    def raiz_nombre(self):
        return PurePosixPath(self.nombre.lower()).stem


@dataclass
class DatosDocumento:
    """Lo que se lee de un documento electrónico, listo para guardarse."""

    tipo: str
    tipo_codigo_dian: str
    numero: str
    cufe_cude: str
    fecha_emision: date
    hora_emision: time | None
    moneda: str
    proveedor_numero_identificacion: str
    proveedor_digito_verificacion: str
    proveedor_razon_social: str
    # El schemeName del CompanyID (31 NIT, 13 cédula...) y el
    # AdditionalAccountID (1 jurídica, 2 natural). Los eventos RADIAN los
    # repiten en el ReceiverParty.
    proveedor_tipo_identificacion: str
    proveedor_tipo_organizacion: str
    receptor_numero_identificacion: str
    valor_bruto: Decimal | None
    total_impuestos: Decimal | None
    total_a_pagar: Decimal | None
    validacion_codigo: str = ""
    fecha_validacion: datetime | None = None
    # ProfileExecutionID: 1 producción, 2 habilitación. Es el ambiente donde
    # está registrado el documento, y contra el que se verifica.
    ambiente: int | None = None
    # Los archivos: el XML como llegó, el documento extraído (si venía en un
    # AttachedDocument) y el PDF que lo acompañaba.
    xml: Archivo | None = None
    xml_documento: bytes = b""
    pdf: Archivo | None = None


@dataclass
class _Conteo:
    archivos: int = 0
    bytes: int = 0

    def sumar(self, tamano):
        self.archivos += 1
        self.bytes += tamano
        if self.archivos > MAXIMOS_ARCHIVOS:
            raise ContenidoExcesivo(f"El correo trae más de {MAXIMOS_ARCHIVOS} archivos.")
        if self.bytes > MAXIMOS_BYTES:
            raise ContenidoExcesivo(
                f"El contenido descomprimido pasa de {MAXIMOS_BYTES // (1024 * 1024)} MB."
            )


@dataclass
class _Grupo:
    """Los archivos de un mismo contenedor (el correo o un ZIP): un PDF se
    empareja con un XML del mismo grupo."""

    xml: list = field(default_factory=list)
    pdf: list = field(default_factory=list)
    otros: list = field(default_factory=list)


@dataclass
class Extraccion:
    """Lo que trae el correo: sus documentos y **todos** sus archivos finales.

    ``archivos`` son los XML, PDF y demás adjuntos ya fuera de sus ZIP y
    correos adjuntos (los contenedores no van: su contenido sí). Los de cada
    documento están también aquí, como el mismo objeto.
    """

    documentos: list
    archivos: list


# --- Recorrido de adjuntos ----------------------------------------------------

def extraer(mensaje: EmailMessage) -> Extraccion:
    """Los documentos electrónicos del correo, con su PDF si se encontró, y
    todos sus archivos.

    Lanza ``ContenidoExcesivo`` si el correo pasa los topes. Un XML que no es un
    documento sigue siendo un archivo del correo; un ZIP dañado, también.
    """
    grupos = []
    _recorrer_mensaje(mensaje, grupos, _Conteo(), profundidad=0)
    archivos = [a for g in grupos for a in (*g.xml, *g.pdf, *g.otros)]
    return Extraccion(documentos=_documentos(grupos), archivos=archivos)


def extraer_archivo(nombre, contenido) -> Extraccion:
    """Como :func:`extraer`, pero de un archivo suelto (un ZIP o un XML) en
    vez de un correo: es la entrada de las cargas manuales.

    Mismos topes que un correo; lanza ``ContenidoExcesivo`` si los pasa.
    """
    grupo = _Grupo()
    grupos = [grupo]
    _agregar(Archivo(nombre, contenido), grupo, grupos, _Conteo(), profundidad=0)
    archivos = [a for g in grupos for a in (*g.xml, *g.pdf, *g.otros)]
    return Extraccion(documentos=_documentos(grupos), archivos=archivos)


def documentos_del_correo(mensaje: EmailMessage) -> list[DatosDocumento]:
    """Solo los documentos de :func:`extraer`."""
    return extraer(mensaje).documentos


def _documentos(grupos):
    documentos = []
    pdfs_sueltos = []
    for grupo in grupos:
        leidos = [d for d in (_leer_xml(a) for a in grupo.xml) if d is not None]
        _emparejar_pdf(leidos, grupo.pdf)
        documentos.extend(leidos)
        pdfs_sueltos.extend(p for p in grupo.pdf if not any(d.pdf is p for d in leidos))
    # El proveedor que manda el XML dentro de un ZIP y el PDF aparte, en el
    # correo: si hay un solo documento sin PDF y un solo PDF sin dueño, van juntos.
    sin_pdf = [d for d in documentos if d.pdf is None]
    if len(sin_pdf) == 1 and len(pdfs_sueltos) == 1:
        sin_pdf[0].pdf = pdfs_sueltos[0]
    return documentos


def _recorrer_mensaje(mensaje, grupos, conteo, profundidad):
    grupo = _Grupo()
    grupos.append(grupo)
    for parte in mensaje.walk():
        if parte.is_multipart():
            continue
        nombre = parte.get_filename() or ""
        tipo = parte.get_content_type()
        if tipo == "message/rfc822":
            # walk() ya entra en los correos adjuntos: sus adjuntos pasan por
            # aquí como los del correo de afuera.
            continue
        if not nombre and parte.get_content_disposition() != "attachment":
            # El cuerpo del correo (texto o HTML), no un adjunto.
            continue
        try:
            contenido = parte.get_payload(decode=True) or b""
        except Exception:
            continue
        _agregar(Archivo(nombre or "adjunto", contenido), grupo, grupos, conteo, profundidad)


def _agregar(archivo, grupo, grupos, conteo, profundidad):
    conteo.sumar(len(archivo.contenido))
    extension = archivo.extension
    # Por la firma solo si no trae extensión: un .docx o un .xlsx también son
    # ZIP por dentro, y no hay facturas ahí.
    if extension == ".zip" or (not extension and archivo.contenido[:4] == b"PK\x03\x04"):
        if not _recorrer_zip(archivo, grupos, conteo, profundidad + 1):
            # Dañado: no se abre, pero se guarda tal cual.
            grupo.otros.append(archivo)
    elif extension == ".eml":
        _recorrer_eml(archivo, grupos, conteo, profundidad + 1)
    elif extension == ".xml":
        grupo.xml.append(archivo)
    elif extension == ".pdf":
        grupo.pdf.append(archivo)
    else:
        grupo.otros.append(archivo)


def _recorrer_zip(archivo, grupos, conteo, profundidad):
    """Recorre el ZIP; ``False`` si está dañado y no se pudo abrir."""
    if profundidad > MAXIMA_PROFUNDIDAD:
        raise ContenidoExcesivo("El correo anida demasiados ZIP o correos adjuntos.")
    try:
        zip_ = zipfile.ZipFile(io.BytesIO(archivo.contenido))
    except zipfile.BadZipFile:
        logger.info("recepcion.zip_danado nombre=%s", archivo.nombre)
        return False
    grupo = _Grupo()
    grupos.append(grupo)
    with zip_:
        for info in zip_.infolist():
            if info.is_dir() or info.flag_bits & 0x1:
                # Carpeta o cifrado: nada que leer.
                continue
            # El tamaño de la cabecera puede mentir: se lee con tope.
            restante = MAXIMOS_BYTES - conteo.bytes
            try:
                with zip_.open(info) as entrada:
                    contenido = entrada.read(restante + 1)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError):
                continue
            nombre = PurePosixPath(info.filename).name
            _agregar(Archivo(nombre, contenido), grupo, grupos, conteo, profundidad)
    return True


def _recorrer_eml(archivo, grupos, conteo, profundidad):
    if profundidad > MAXIMA_PROFUNDIDAD:
        raise ContenidoExcesivo("El correo anida demasiados ZIP o correos adjuntos.")
    mensaje = BytesParser(policy=policy.default).parsebytes(archivo.contenido)
    _recorrer_mensaje(mensaje, grupos, conteo, profundidad)


def _emparejar_pdf(documentos, pdfs):
    """Al documento, el PDF de su mismo nombre; si el grupo trae uno de cada,
    esos dos."""
    libres = list(pdfs)
    for documento in documentos:
        mismo = next(
            (p for p in libres if p.raiz_nombre == documento.xml.raiz_nombre), None,
        )
        if mismo is not None:
            documento.pdf = mismo
            libres.remove(mismo)
    sin_pdf = [d for d in documentos if d.pdf is None]
    if len(sin_pdf) == 1 and len(libres) == 1:
        sin_pdf[0].pdf = libres[0]


# --- Lectura del XML ----------------------------------------------------------

def _leer_xml(archivo):
    """El documento que trae el XML, o ``None`` si no es uno."""
    raiz = _parsear(archivo.contenido)
    if raiz is None:
        return None
    nombre = etree.QName(raiz).localname
    if nombre == "AttachedDocument":
        return _leer_attached(raiz, archivo)
    datos = _leer_documento(raiz)
    if datos is not None:
        datos.xml = archivo
    return datos


def _leer_attached(raiz, archivo):
    texto = _texto(raiz, "cac:Attachment/cac:ExternalReference/cbc:Description")
    if not texto:
        return None
    contenido = texto.strip().encode("utf-8")
    documento = _parsear(contenido)
    if documento is None:
        return None
    datos = _leer_documento(documento)
    if datos is None:
        return None
    datos.xml = archivo
    datos.xml_documento = contenido
    verificacion = "cac:ParentDocumentLineReference/cac:DocumentReference/cac:ResultOfVerification/"
    datos.validacion_codigo = _texto(raiz, verificacion + "cbc:ValidationResultCode")[:10]
    datos.fecha_validacion = _fecha_hora(
        _texto(raiz, verificacion + "cbc:ValidationDate"),
        _texto(raiz, verificacion + "cbc:ValidationTime"),
    )
    return datos


def _leer_documento(raiz):
    """Los datos de un Invoice, CreditNote o DebitNote; ``None`` si es otra cosa
    o le falta lo indispensable."""
    nombre = etree.QName(raiz).localname
    if nombre not in CODIGO_POR_RAIZ:
        return None
    codigo = _texto(raiz, f"cbc:{CODIGO_POR_RAIZ[nombre]}")
    if nombre == "Invoice":
        if codigo not in CODIGOS_FACTURA:
            return None
        tipo = DocumentoTipo.Codigo.FACTURA_VENTA
    else:
        tipo = TIPO_POR_RAIZ[nombre]

    proveedor = raiz.find("cac:AccountingSupplierParty/cac:Party", NS)
    receptor = raiz.find("cac:AccountingCustomerParty/cac:Party", NS)
    nit_proveedor, dv_proveedor, tipo_proveedor = _nit(proveedor)
    nit_receptor, _, _ = _nit(receptor)
    numero = _texto(raiz, "cbc:ID")
    cufe = _texto(raiz, "cbc:UUID")
    fecha = _fecha(_texto(raiz, "cbc:IssueDate"))
    if not (numero and cufe and fecha and nit_proveedor and nit_receptor):
        return None

    totales = raiz.find("cac:LegalMonetaryTotal", NS)
    if totales is None:
        totales = raiz.find("cac:RequestedMonetaryTotal", NS)
    impuestos = [_decimal(t.text) for t in raiz.findall("cac:TaxTotal/cbc:TaxAmount", NS)]
    impuestos = [i for i in impuestos if i is not None]
    return DatosDocumento(
        tipo=tipo,
        tipo_codigo_dian=codigo[:2],
        numero=numero[:30],
        cufe_cude=cufe[:96],
        fecha_emision=fecha,
        hora_emision=_hora(_texto(raiz, "cbc:IssueTime")),
        moneda=_texto(raiz, "cbc:DocumentCurrencyCode")[:3],
        proveedor_numero_identificacion=nit_proveedor,
        proveedor_digito_verificacion=dv_proveedor,
        proveedor_razon_social=_razon_social(proveedor)[:450],
        proveedor_tipo_identificacion=tipo_proveedor,
        proveedor_tipo_organizacion=_texto(
            raiz, "cac:AccountingSupplierParty/cbc:AdditionalAccountID",
        )[:1],
        receptor_numero_identificacion=nit_receptor,
        valor_bruto=_decimal(_texto(totales, "cbc:LineExtensionAmount")),
        total_impuestos=sum(impuestos) if impuestos else None,
        total_a_pagar=_decimal(_texto(totales, "cbc:PayableAmount")),
        ambiente=_ambiente(_texto(raiz, "cbc:ProfileExecutionID")),
    )


def _ambiente(texto):
    return int(texto) if texto in ("1", "2") else None


def _parsear(contenido):
    try:
        return etree.fromstring(contenido, _PARSER)
    except (etree.XMLSyntaxError, ValueError):
        return None


def _texto(nodo, ruta):
    if nodo is None:
        return ""
    encontrado = nodo.find(ruta, NS)
    if encontrado is None or encontrado.text is None:
        return ""
    return encontrado.text.strip()


def _nit(parte):
    """El NIT (solo dígitos, sin DV), el DV y el tipo de identificación
    (``schemeName``) de una parte del documento."""
    for ruta in ("cac:PartyTaxScheme/cbc:CompanyID", "cac:PartyLegalEntity/cbc:CompanyID"):
        nodo = parte.find(ruta, NS) if parte is not None else None
        if nodo is not None and nodo.text:
            # Hay quien manda "900123456-7": el DV va después del guion.
            numero = re.sub(r"\D", "", nodo.text.split("-")[0])[:20]
            if numero:
                return (
                    numero, (nodo.get("schemeID") or "")[:1],
                    (nodo.get("schemeName") or "")[:2],
                )
    return "", "", ""


def _razon_social(parte):
    for ruta in (
        "cac:PartyTaxScheme/cbc:RegistrationName",
        "cac:PartyLegalEntity/cbc:RegistrationName",
        "cac:PartyName/cbc:Name",
    ):
        if valor := _texto(parte, ruta):
            return valor
    return ""


def _fecha(texto):
    try:
        return date.fromisoformat(texto[:10])
    except ValueError:
        return None


def _hora(texto):
    """``10:20:30-05:00`` → ``10:20:30``; la hora del XML es la de Colombia."""
    try:
        return time.fromisoformat(texto).replace(tzinfo=None, microsecond=0)
    except ValueError:
        return None


def _fecha_hora(fecha, hora):
    dia = _fecha(fecha)
    if dia is None:
        return None
    momento = datetime.combine(dia, _hora(hora) or time())
    return timezone.make_aware(momento)


def _decimal(texto):
    if texto is None:
        return None
    try:
        valor = Decimal(texto.strip())
    except (InvalidOperation, AttributeError):
        return None
    return valor if valor.is_finite() else None
