"""Cliente del RUES (Registro Único Empresarial y Social — Confecámaras).

Consulta el registro mercantil por NIT. Es un servicio transversal: cualquier
app puede usarlo (p. ej. ``emisores`` al dar de alta un emisor, o ``documentos``
para validar un adquirente).

Expone dos niveles:
  - :func:`consultar_nit`: una sola llamada (búsqueda). Devuelve existencia +
    datos básicos (razón social, estado). Útil para *validar* que el NIT existe.
  - :func:`consultar_detalle`: dos llamadas (búsqueda → detalle). Añade correo,
    dirección, teléfono y actividad económica (CIIU). Útil para *autocompletar*.

Notas sobre la API pública de RUES (sin token):
  - Exige el header ``Origin`` del portal público; sin él responde 403, y sin un
    ``User-Agent`` de navegador el WAF también bloquea.
  - La búsqueda es difusa: puede devolver coincidencias cuyo ``nit`` NO es el
    consultado. Por eso filtramos los registros por coincidencia EXACTA de NIT.
  - Un mismo NIT puede traer varios registros (uno por cámara de comercio); se
    usa el principal (o, en su defecto, el primero que coincida).
  - El detalle suele traer correo/dirección/teléfono VACÍOS: RUES no publica
    esos datos de todas las empresas. Se devuelven tal cual (cadena vacía).

Los endpoints son de Confecámaras; no cambian entre ambientes, así que se
definen como constantes del servicio (no en settings).
"""
from __future__ import annotations

from dataclasses import dataclass

import requests

# Búsqueda en el Registro Mercantil (POST con cuerpo JSON). Devuelve el id_rm.
RUES_URL = "https://elasticprd.rues.org.co/api/ConsultasRUES/BusquedaAvanzadaRM"
# Detalle de una matrícula por id_rm (GET). Trae correo, dirección, etc.
RUES_DETALLE_URL = "https://ruesapi.rues.org.co/WEB2/api/Expediente/DetalleRM/{id_rm}"
# El RUES exige el Origin del portal público; sin él responde 403.
RUES_ORIGIN = "https://www.rues.org.co"
RUES_TIMEOUT = 15  # segundos

# El RUES bloquea peticiones sin un User-Agent de navegador (WAF).
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class RuesError(Exception):
    """Error genérico al consultar el RUES."""


class RuesNoDisponible(RuesError):
    """El servicio RUES no respondió correctamente (red, timeout, 4xx/5xx)."""


@dataclass(frozen=True)
class EmpresaRues:
    """Datos básicos de una empresa (endpoint de búsqueda)."""

    nit: str
    digito_verificacion: str
    razon_social: str
    estado_matricula: str
    organizacion_juridica: str
    camara_comercio: str
    categoria: str
    tipo_documento: str

    @property
    def activa(self) -> bool:
        """¿La matrícula mercantil está activa?"""
        return self.estado_matricula.strip().upper() == "ACTIVA"

    @classmethod
    def desde_registro(cls, registro: dict) -> "EmpresaRues":
        return cls(
            nit=str(registro.get("nit", "")).strip(),
            digito_verificacion=str(registro.get("dv", "")).strip(),
            razon_social=str(registro.get("razon_social", "")).strip(),
            estado_matricula=str(registro.get("estado_matricula", "")).strip(),
            organizacion_juridica=str(registro.get("organizacion_juridica", "")).strip(),
            camara_comercio=str(registro.get("nom_camara", "")).strip(),
            categoria=str(registro.get("categoria", "")).strip(),
            tipo_documento=str(registro.get("tipo_documento", "")).strip(),
        )


@dataclass(frozen=True)
class DetalleEmpresaRues:
    """Datos ampliados de una empresa (endpoint de detalle).

    Los campos ``correo``, ``direccion`` y ``telefono`` pueden venir vacíos:
    RUES no publica esa información de todas las empresas.
    """

    nit: str
    digito_verificacion: str
    razon_social: str
    estado_matricula: str
    organizacion_juridica: str
    camara_comercio: str
    correo: str  # el primero no vacío entre comercial y fiscal
    correo_comercial: str
    correo_fiscal: str
    direccion: str  # comercial preferida, con respaldo en la fiscal
    telefono: str  # comercial preferido, con respaldo en el fiscal
    actividad_ciiu: str
    actividad_ciiu_descripcion: str

    @property
    def activa(self) -> bool:
        return self.estado_matricula.strip().upper() == "ACTIVA"

    @classmethod
    def desde_registro(cls, r: dict) -> "DetalleEmpresaRues":
        correo_com = str(r.get("email_com", "")).strip()
        correo_fis = str(r.get("email_fiscal", "")).strip()
        direccion = str(r.get("dir_comercial", "")).strip() or str(r.get("dir_fiscal", "")).strip()
        telefono = str(r.get("tel_com_1", "")).strip() or str(r.get("tel_fiscal_1", "")).strip()
        return cls(
            nit=str(r.get("numero_identificacion", "")).strip(),
            digito_verificacion=str(r.get("dv", "")).strip(),
            razon_social=str(r.get("razon_social", "")).strip(),
            estado_matricula=str(r.get("estado", "")).strip(),
            organizacion_juridica=str(r.get("organizacion_juridica", "")).strip(),
            camara_comercio=str(r.get("camara", "")).strip(),
            correo=correo_com or correo_fis,
            correo_comercial=correo_com,
            correo_fiscal=correo_fis,
            direccion=direccion,
            telefono=telefono,
            actividad_ciiu=str(r.get("cod_ciiu_act_econ_pri", "")).strip(),
            actividad_ciiu_descripcion=str(r.get("desc_ciiu_act_econ_pri", "")).strip(),
        )


def _headers(*, con_json: bool = False) -> dict:
    cabeceras = {
        "User-Agent": _USER_AGENT,
        "Origin": RUES_ORIGIN,
        "Referer": RUES_ORIGIN + "/",
    }
    if con_json:
        cabeceras["Content-Type"] = "application/json"
    return cabeceras


def _es_principal(registro: dict) -> bool:
    """Heurística para distinguir el registro principal de las sucursales."""
    return "PRINCIPAL" in str(registro.get("categoria", "")).upper()


def _buscar_principal(nit: str) -> dict | None:
    """Busca el NIT y devuelve el registro principal (dict crudo) o None.

    Filtra por coincidencia EXACTA de NIT (la búsqueda del RUES es difusa).
    """
    try:
        respuesta = requests.post(
            RUES_URL,
            json={"nit": nit, "tipo": "RM"},
            headers=_headers(con_json=True),
            timeout=RUES_TIMEOUT,
        )
        respuesta.raise_for_status()
        datos = respuesta.json()
    except (requests.RequestException, ValueError) as exc:
        raise RuesNoDisponible(f"No se pudo consultar el RUES: {exc}") from exc

    registros = datos.get("registros") or []
    exactos = [r for r in registros if str(r.get("nit", "")).strip() == nit]
    if not exactos:
        return None
    return next((r for r in exactos if _es_principal(r)), exactos[0])


def consultar_nit(nit: str) -> EmpresaRues | None:
    """Consulta un NIT en el RUES (una llamada).

    Devuelve una :class:`EmpresaRues` si el NIT existe en el registro, o
    ``None`` si no se encuentra. Lanza :class:`RuesNoDisponible` si el servicio
    no está accesible (para distinguir "no existe" de "no pude verificar").

    ``nit`` debe ir sin puntos, sin guiones y sin dígito de verificación.
    """
    nit = (nit or "").strip()
    if not nit:
        return None
    principal = _buscar_principal(nit)
    if principal is None:
        return None
    return EmpresaRues.desde_registro(principal)


def consultar_detalle(nit: str) -> DetalleEmpresaRues | None:
    """Consulta un NIT y trae su detalle (dos llamadas: búsqueda + detalle).

    Añade correo, dirección, teléfono y actividad económica respecto a
    :func:`consultar_nit`. Devuelve ``None`` si el NIT no existe; lanza
    :class:`RuesNoDisponible` si el servicio no está accesible.
    """
    nit = (nit or "").strip()
    if not nit:
        return None
    principal = _buscar_principal(nit)
    if principal is None:
        return None
    id_rm = str(principal.get("id_rm", "")).strip()
    if not id_rm:
        return None

    try:
        respuesta = requests.get(
            RUES_DETALLE_URL.format(id_rm=id_rm),
            headers=_headers(),
            timeout=RUES_TIMEOUT,
        )
        respuesta.raise_for_status()
        datos = respuesta.json()
    except (requests.RequestException, ValueError) as exc:
        raise RuesNoDisponible(f"No se pudo consultar el detalle en el RUES: {exc}") from exc

    registro = datos.get("registros") or {}
    if not registro:
        return None
    return DetalleEmpresaRues.desde_registro(registro)
