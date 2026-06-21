"""
Parser de listas de valores DIAN en formato Genericode (.gc).

Las listas oficiales de la DIAN (TipoDocumento, Municipio, Paises, etc.) se
distribuyen como archivos XML Genericode 1.0. Este módulo las lee y las
devuelve como estructuras de datos de Python, sin depender de la base de datos.

Estructura del archivo .gc:

    <gc:CodeList>
        <Identification>
            <ShortName>TipoDocumento</ShortName>
            <LongName>Tipo de Documento</LongName>
            ...
        </Identification>
        <ColumnSet>
            <Column Id="code">...</Column>
            <Column Id="name">...</Column>
        </ColumnSet>
        <SimpleCodeList>
            <Row>
                <Value ColumnRef="code"><SimpleValue>01</SimpleValue></Value>
                <Value ColumnRef="name"><SimpleValue>Factura...</SimpleValue></Value>
            </Row>
            ...
        </SimpleCodeList>
    </gc:CodeList>

Nota: algunas filas incluyen columnas (p. ej. ``description``) que no están
declaradas en el ``ColumnSet``; el parser las conserva de todos modos.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

GC_NS = "http://docs.oasis-open.org/codelist/ns/genericode/1.0/"


@dataclass
class Columna:
    """Definición de una columna del ColumnSet."""

    id: str
    nombre_corto: str = ""
    nombre_largo: str = ""
    tipo_dato: str = ""
    requerida: bool = False


@dataclass
class ListaCodigos:
    """Una lista de valores DIAN ya parseada."""

    nombre_corto: str
    nombre_largo: str = ""
    version: str = ""
    uri_canonica: str = ""
    columnas: list[Columna] = field(default_factory=list)
    filas: list[dict[str, str]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.filas)

    def codigos(self, columna_codigo: str = "code") -> list[str]:
        """Devuelve la lista de valores de la columna de código."""
        return [fila[columna_codigo] for fila in self.filas if columna_codigo in fila]

    def como_diccionario(
        self, clave: str = "code", valor: str = "name"
    ) -> dict[str, str]:
        """Devuelve un mapeo {código: nombre} a partir de las filas."""
        return {
            fila[clave]: fila.get(valor, "")
            for fila in self.filas
            if clave in fila
        }


def _texto(elemento: etree._Element | None) -> str:
    """Texto normalizado (sin espacios sobrantes) de un elemento, o ''."""
    if elemento is None or elemento.text is None:
        return ""
    return elemento.text.strip()


def parsear_archivo(ruta: str | Path) -> ListaCodigos:
    """Parsea un archivo .gc y devuelve una :class:`ListaCodigos`.

    Lanza ``FileNotFoundError`` si la ruta no existe y ``etree.XMLSyntaxError``
    si el XML está mal formado.
    """
    ruta = Path(ruta)
    arbol = etree.parse(str(ruta))
    raiz = arbol.getroot()

    # Los elementos internos no llevan prefijo de namespace (espacio nulo),
    # solo la raíz gc:CodeList está en el namespace de Genericode.
    identificacion = raiz.find("Identification")
    nombre_corto = _texto(identificacion.find("ShortName")) if identificacion is not None else ""
    nombre_largo = _texto(identificacion.find("LongName")) if identificacion is not None else ""
    version = _texto(identificacion.find("Version")) if identificacion is not None else ""
    uri = _texto(identificacion.find("CanonicalVersionUri")) if identificacion is not None else ""

    columnas: list[Columna] = []
    columnset = raiz.find("ColumnSet")
    if columnset is not None:
        for col in columnset.findall("Column"):
            columnas.append(
                Columna(
                    id=col.get("Id", ""),
                    nombre_corto=_texto(col.find("ShortName")),
                    nombre_largo=_texto(col.find("LongName")),
                    tipo_dato=(col.find("Data").get("Type", "") if col.find("Data") is not None else ""),
                    requerida=col.get("Use") == "required",
                )
            )

    filas: list[dict[str, str]] = []
    simple = raiz.find("SimpleCodeList")
    if simple is not None:
        for row in simple.findall("Row"):
            fila: dict[str, str] = {}
            for value in row.findall("Value"):
                ref = value.get("ColumnRef")
                if not ref:
                    continue
                fila[ref] = _texto(value.find("SimpleValue"))
            filas.append(fila)

    return ListaCodigos(
        nombre_corto=nombre_corto or ruta.stem,
        nombre_largo=nombre_largo,
        version=version,
        uri_canonica=uri,
        columnas=columnas,
        filas=filas,
    )


def _directorio_listas() -> Path:
    """Directorio configurado con las listas .gc."""
    from django.conf import settings

    return Path(settings.CATALOGOS_LISTAS_DIR)


def listar_archivos(directorio: str | Path | None = None) -> list[Path]:
    """Lista los archivos .gc disponibles en el directorio de listas."""
    base = Path(directorio) if directorio else _directorio_listas()
    return sorted(base.glob("*.gc"))


def cargar(nombre: str, directorio: str | Path | None = None) -> ListaCodigos:
    """Carga una lista por nombre de archivo (con o sin extensión .gc).

    Acepta el nombre exacto del archivo (``TipoDocumento-2.1``) o una
    coincidencia por prefijo insensible a mayúsculas (``tipodocumento``).
    """
    base = Path(directorio) if directorio else _directorio_listas()

    # Coincidencia exacta primero.
    candidato = base / (nombre if nombre.endswith(".gc") else f"{nombre}.gc")
    if candidato.exists():
        return parsear_archivo(candidato)

    # Coincidencia por prefijo, insensible a mayúsculas.
    objetivo = nombre.lower().removesuffix(".gc")
    for archivo in listar_archivos(base):
        if archivo.stem.lower().startswith(objetivo):
            return parsear_archivo(archivo)

    raise FileNotFoundError(
        f"No se encontró ninguna lista de valores para '{nombre}' en {base}"
    )
