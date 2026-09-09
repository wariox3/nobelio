"""Lo que el generador de OpenAPI no puede deducir solo.

Dos cosas, y las dos por el mismo motivo: `drf-spectacular` lee el código, y
aquí hay piezas propias que no se parecen a las de fábrica.

**Las autenticaciones.** El proyecto no usa ninguna clase estándar: el ERP entra
con `Authorization: Api-Key <prefijo>.<secreto>` y el navegador con una cookie
`httpOnly` que nadie manda a mano. Sin las extensiones de aquí, el esquema diría
que la API es abierta y el botón «Authorize» de Swagger no serviría de nada, que
es la forma más rápida de que alguien concluya que la documentación miente.

**El cuerpo de error.** `apps.nucleo.api.exception_handler` devuelve siempre la
misma forma —`detail` y `errores`—, y eso es justo lo que un integrador necesita
saber para escribir su manejo de errores una sola vez. Como no sale de ningún
serializer, hay que declararlo.

Y una tercera, de otra naturaleza: **qué se publica**. El esquema es público, y
`/api/seguridad/` no entra en él. Ver `excluir_seguridad`.
"""
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework import serializers


class LlaveApiEsquema(OpenApiAuthenticationExtension):
    """`Authorization: Api-Key <prefijo>.<secreto>` — la vía del ERP."""

    target_class = "apps.seguridad.autenticacion.LlaveApiAuthentication"
    name = "LlaveApi"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": (
                "Llave de API de una integración. El valor completo es "
                "`Api-Key <prefijo>.<secreto>`, tal como lo imprime "
                "`manage.py crear_llave_api`. La llave alcanza exactamente los "
                "mismos emisores que la persona a cuyo nombre se creó."
            ),
        }


class JwtDeCookieEsquema(OpenApiAuthenticationExtension):
    """Cookie `access_token`, la sesión del navegador.

    Se declara aunque **no se publique**: sin ella, spectacular no sabría
    resolver la clase y avisaría en cada generación, y ese aviso hace fallar la
    suite (`--fail-on-warn`). Del esquema la quita `solo_llave_api`, que es
    donde está escrito el porqué.
    """

    target_class = "apps.seguridad.autenticacion.JwtDeCookie"
    name = "SesionCookie"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "cookie",
            "name": "access_token",
            "description": (
                "La emite `POST /api/seguridad/token/` y viaja sola: es "
                "`httpOnly`, así que el JavaScript de la página no la lee ni la "
                "escribe. Desde Swagger UI no hay nada que pegar aquí; basta con "
                "haber iniciado sesión en el mismo navegador."
            ),
        }


class ErrorSerializer(serializers.Serializer):
    """El cuerpo que devuelve cualquier error de la API.

    No se usa para validar nada: existe para que el esquema pueda nombrar la
    forma de los 4xx en vez de dejarlos sin describir.
    """

    detail = serializers.CharField(
        help_text="Mensaje para mostrar a la persona.",
    )
    errores = serializers.DictField(
        help_text=(
            "Fallos por campo, cuando el error es de validación: la clave es el "
            "campo y el valor la lista de mensajes. Vacío en los demás casos."
        ),
        child=serializers.ListField(child=serializers.CharField()),
    )


class DetalleSerializer(serializers.Serializer):
    """Respuesta de las operaciones que solo confirman que algo pasó.

    Muchas rutas —cerrar sesión, confirmar el correo, restablecer la clave— no
    tienen nada que devolver salvo el mensaje. Tenerlas todas apuntando aquí
    evita repetir el mismo componente con quince nombres distintos.
    """

    detail = serializers.CharField(
        help_text="Mensaje para mostrar a la persona.",
    )


# --------------------------------------------------------------------------- #
# Qué se publica
# --------------------------------------------------------------------------- #

PREFIJOS_SIN_PUBLICAR = ("/api/seguridad/",)


def excluir_seguridad(endpoints, **kwargs):
    """Deja fuera del esquema público todo lo de `/api/seguridad/`.

    El esquema se sirve abierto porque su destinatario es quien integra un ERP,
    y lo que necesita es el dominio fiscal: emisores, documentos, nómina,
    catálogos. Ingreso, registro, segundo factor y recuperación de contraseña no
    le hacen falta para nada, y son justo las rutas donde un listado completo
    —formatos exactos, códigos de respuesta, campos opcionales— le ahorra trabajo
    a quien las quiera atacar.

    **Esto no las esconde ni las protege.** Las rutas siguen existiendo,
    respondiendo y siendo descubribles: lo que se decide aquí es qué publicamos,
    no qué exponemos. Lo que de verdad las defiende es lo de siempre y sigue en
    su sitio: los topes por IP y por destinatario, el segundo factor, las
    respuestas que no distinguen si una cuenta existe.

    Quien las tenga que usar —el frontend propio— tiene su documentación en
    `docs/autenticacion.md`, que además explica el porqué de cada decisión, cosa
    que un esquema generado no hace.

    Se filtra por prefijo de ruta y no vista por vista a propósito: así una ruta
    nueva en `apps/seguridad/urls.py` nace fuera del esquema sin que nadie tenga
    que acordarse de excluirla.
    """
    return [
        endpoint for endpoint in endpoints
        if not endpoint[0].startswith(PREFIJOS_SIN_PUBLICAR)
    ]


def solo_llave_api(result, generator, request, public):
    """Deja `Api-Key` como única credencial del esquema publicado.

    La API tiene dos autenticaciones, pero solo una es para quien lee esto. El
    destinatario del esquema es quien integra un ERP, y su credencial es la
    llave: `Authorization: Api-Key <prefijo>.<secreto>`. La sesión en cookies es
    del navegador, la emiten rutas que no publicamos (`excluir_seguridad`) y no
    hay forma de obtenerla siguiendo este documento, así que anunciarla solo
    servía para mandar a alguien a un callejón sin salida.

    Como en el resto de este módulo: no la desactiva. `JwtDeCookie` sigue en
    `DEFAULT_AUTHENTICATION_CLASSES` y el front sigue entrando por ahí. Lo que
    se decide aquí es qué contamos.
    """
    esquemas = result.get("components", {}).get("securitySchemes", {})
    esquemas.pop("SesionCookie", None)

    for operaciones in result.get("paths", {}).values():
        for metodo, operacion in operaciones.items():
            if metodo not in ("get", "post", "put", "patch", "delete"):
                continue
            if "security" not in operacion:
                continue
            # Se conserva el `{}` de las rutas anónimas: dice que no piden
            # credencial, que no es lo mismo que no tener ninguna declarada.
            operacion["security"] = [
                requisito for requisito in operacion["security"]
                if "SesionCookie" not in requisito
            ]

    return result


def documentar_errores(result, generator, request, public):
    """Cuelga el cuerpo de error común de las operaciones publicadas.

    El `ErrorSerializer` de arriba lo declaraban las vistas de `seguridad`, que
    ya no se publican, así que sin esto el esquema se quedaba prometiendo en su
    descripción un formato de error que no describía en ninguna parte —y quien
    genere un cliente no tendría de dónde sacar el tipo—.

    Se hace en un gancho y no vista por vista porque el formato no es una
    decisión de cada endpoint: lo impone `apps.nucleo.api.exception_handler`
    para toda la API. Repetirlo en 94 operaciones sería copiar noventa y cuatro
    veces la misma verdad.

    Qué se añade y dónde, que es mecánico a propósito:

    - **401** en todas: el permiso por defecto es `IsAuthenticated`.
    - **429** en todas: hay topes de peticiones globales.
    - **400** donde hay cuerpo que validar.
    - **404** en las rutas de detalle, que además es lo que responde un recurso
      ajeno: no se distingue de uno inexistente para no revelar que existe.

    Nunca pisa una respuesta ya declarada por la vista.
    """
    componentes = result.setdefault("components", {}).setdefault("schemas", {})
    componentes["Error"] = {
        "type": "object",
        "description": (
            "Cuerpo común de los errores de la API. Lo produce "
            "`apps.nucleo.api.exception_handler`, así que tiene esta forma "
            "venga de donde venga el fallo."
        ),
        "properties": {
            "detail": {
                "type": "string",
                "description": "Mensaje para mostrar a la persona.",
            },
            "errores": {
                "type": "object",
                "additionalProperties": {
                    "type": "array", "items": {"type": "string"},
                },
                "description": (
                    "Fallos por campo cuando el error es de validación: la "
                    "clave es el campo y el valor la lista de mensajes. Vacío "
                    "en los demás casos."
                ),
            },
        },
        "required": ["detail", "errores"],
    }

    referencia = {
        "description": "Error",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
    }

    for ruta, operaciones in result.get("paths", {}).items():
        es_detalle = "{" in ruta
        for metodo, operacion in operaciones.items():
            if metodo not in ("get", "post", "put", "patch", "delete"):
                continue
            respuestas = operacion.setdefault("responses", {})
            codigos = ["401", "429"]
            if "requestBody" in operacion:
                codigos.append("400")
            if es_detalle:
                codigos.append("404")
            for codigo in codigos:
                respuestas.setdefault(codigo, referencia)

    return result
