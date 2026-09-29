"""Los catálogos en el esquema OpenAPI.

Dos cosas, las dos para que la referencia pueda enlazar un campo con su lista:

- **Cada catálogo se describe con su ficha** (`registro.py`): qué es, de qué
  lista y anexo técnico sale y qué campos de la API reciben sus valores.
- **Cada campo que recibe un catálogo lleva `x-catalogo`** —el nombre de la
  ruta, `municipio`— y `x-catalogo-valor` —`id` o `codigo`—. Son extensiones de
  OpenAPI: los generadores de clientes las ignoran, y el sitio de
  documentación las usa para poner el enlace.
"""
from drf_spectacular.extensions import OpenApiSerializerFieldExtension
from drf_spectacular.utils import extend_schema, extend_schema_view

from apps.catalogos.registro import POR_MODELO


def _marcar(esquema, campo, valor):
    catalogo = POR_MODELO.get(campo.queryset.model)
    if catalogo is None:
        return esquema
    return {**esquema, "x-catalogo": catalogo.nombre, "x-catalogo-valor": valor}


class RelacionDeCatalogoEsquema(OpenApiSerializerFieldExtension):
    """Los campos que reciben el `id` de la fila."""

    target_class = "apps.catalogos.memoria.RelacionDeCatalogo"

    def map_serializer_field(self, auto_schema, direction):
        esquema = auto_schema._map_serializer_field(
            self.target, direction, bypass_extensions=True
        )
        return _marcar(esquema, self.target, "id")


class CodigoDeCatalogoEsquema(OpenApiSerializerFieldExtension):
    """Los campos que reciben el código DIAN (los del emisor)."""

    target_class = "apps.emisores.serializers.emisor.CodigoDeCatalogo"

    def map_serializer_field(self, auto_schema, direction):
        esquema = auto_schema._map_serializer_field(
            self.target, direction, bypass_extensions=True
        )
        return _marcar(esquema, self.target, "codigo")


def _ficha(catalogo):
    """La descripción común: qué es, de dónde sale y quién lo usa."""
    anexos = ", ".join(
        f"{a.documento} v{a.version} ({a.resolucion})" for a in catalogo.anexos
    )
    partes = [
        catalogo.descripcion,
        f"**Lista DIAN:** `{catalogo.lista_dian}`. **Anexo técnico:** {anexos}.",
    ]
    if catalogo.usos:
        filas = "\n".join(
            f"| `{u.ruta}` | `{u.campo}` | `{u.valor}` |" for u in catalogo.usos
        )
        partes.append(
            "**Campos que reciben sus valores:**\n\n"
            "| Ruta | Campo | Se envía |\n|---|---|---|\n" + filas
        )
    return "\n\n".join(partes)


def documentar(vista):
    """Describe las operaciones de un ViewSet de catálogo con su ficha."""
    catalogo = POR_MODELO[vista.queryset.model]
    opciones = vista.queryset.model._meta
    ficha = _ficha(catalogo)
    return extend_schema_view(
        list=extend_schema(
            summary=f"Listar {opciones.verbose_name_plural}",
            description=f"{ficha}\n\nPaginado; `search` busca por código y nombre.",
        ),
        retrieve=extend_schema(
            summary=f"Consultar {opciones.verbose_name}", description=ficha,
        ),
        exportar=extend_schema(
            summary=f"Exportar {opciones.verbose_name_plural}",
            description=(
                f"{ficha}\n\nEl catálogo entero en una respuesta, sin paginar y "
                "con los mismos campos que el detalle. Admite `search`."
            ),
            responses=vista.serializer_class(many=True),
        ),
    )(vista)
