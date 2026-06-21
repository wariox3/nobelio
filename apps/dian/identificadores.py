"""
Cálculo de los identificadores únicos DIAN: CUFE, CUDE y código de seguridad
del software.

Referencia: Anexo Técnico Factura Electrónica de Venta v1.9 (Res. 000165/2023),
secciones 11.2 (CUFE), 11.4 (CUDE) y 11.8 (SoftwareSecurityCode).
Resumen en ``docs/anexo-tecnico.md``.

Reglas de formato (críticas para que el hash coincida con el de la DIAN):
  - Algoritmo: SHA-384 sobre la concatenación de los campos en orden exacto.
  - Valores monetarios: punto decimal, exactamente 2 decimales TRUNCADOS
    (no redondeados), sin separador de miles ni símbolo de moneda.
  - NITs/identificaciones: sin puntos, sin guiones y SIN dígito de verificación.
  - Fecha: ``YYYY-MM-DD``. Hora: ``HH:MM:SS-05:00`` (incluyendo la zona horaria).
  - El CUFE usa la Clave Técnica del rango; el CUDE usa el PIN del software.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time
from decimal import ROUND_DOWN, Decimal, InvalidOperation

# Valores del atributo @schemeName de /cbc:UUID según el tipo de documento.
SCHEME_NAME_CUFE = "CUFE-SHA384"
SCHEME_NAME_CUDE = "CUDE-SHA384"

# Códigos fijos de impuesto usados en la composición (orden definido por la DIAN).
COD_IMPUESTO_IVA = "01"
COD_IMPUESTO_INC = "04"
COD_IMPUESTO_ICA = "03"

_DOS_DECIMALES = Decimal("0.01")


def formatear_valor(valor) -> str:
    """Formatea un valor monetario: 2 decimales truncados, con punto decimal.

    Acepta ``Decimal``, ``int``, ``float`` o ``str``. El truncamiento es hacia
    cero (``ROUND_DOWN``), tal como exige la DIAN: ``19.999`` -> ``"19.99"``.
    """
    if isinstance(valor, float):
        # Evita el ruido binario del float convirtiendo desde su repr decimal.
        decimal = Decimal(str(valor))
    else:
        try:
            decimal = Decimal(valor)
        except (InvalidOperation, TypeError) as exc:
            raise ValueError(f"Valor monetario inválido: {valor!r}") from exc

    truncado = decimal.quantize(_DOS_DECIMALES, rounding=ROUND_DOWN)
    return f"{truncado:.2f}"


def formatear_fecha(fecha) -> str:
    """Devuelve la fecha como ``YYYY-MM-DD``. Acepta ``date``/``datetime``/``str``."""
    if isinstance(fecha, (date, datetime)):
        return fecha.strftime("%Y-%m-%d")
    return str(fecha)


def formatear_hora(hora) -> str:
    """Devuelve la hora como ``HH:MM:SS-05:00`` (con zona horaria).

    Acepta ``time``/``datetime``/``str``. Si recibe un ``time``/``datetime``
    sin zona horaria, asume la hora de Colombia (``-05:00``).
    """
    if isinstance(hora, datetime):
        hora = hora.timetz()
    if isinstance(hora, time):
        base = hora.strftime("%H:%M:%S")
        if hora.tzinfo is not None:
            desfase = hora.strftime("%z")  # p. ej. -0500
            return f"{base}{desfase[:3]}:{desfase[3:]}"
        return f"{base}-05:00"
    return str(hora)


def _sha384_hex(cadena: str) -> str:
    """SHA-384 en hexadecimal (minúsculas) de una cadena UTF-8."""
    return hashlib.sha384(cadena.encode("utf-8")).hexdigest()


def _componer(
    *,
    numero_factura: str,
    fecha,
    hora,
    valor_sin_impuestos,
    valor_iva,
    valor_inc,
    valor_ica,
    valor_total,
    nit_emisor: str,
    id_adquirente: str,
    clave: str,
    tipo_ambiente,
) -> str:
    """Construye la cadena a hashear, común a CUFE y CUDE.

    ``clave`` es la Clave Técnica (CUFE) o el PIN del software (CUDE).
    """
    return (
        f"{numero_factura}"
        f"{formatear_fecha(fecha)}"
        f"{formatear_hora(hora)}"
        f"{formatear_valor(valor_sin_impuestos)}"
        f"{COD_IMPUESTO_IVA}{formatear_valor(valor_iva)}"
        f"{COD_IMPUESTO_INC}{formatear_valor(valor_inc)}"
        f"{COD_IMPUESTO_ICA}{formatear_valor(valor_ica)}"
        f"{formatear_valor(valor_total)}"
        f"{nit_emisor}"
        f"{id_adquirente}"
        f"{clave}"
        f"{tipo_ambiente}"
    )


def calcular_cufe(
    *,
    numero_factura: str,
    fecha,
    hora,
    valor_sin_impuestos,
    valor_total,
    nit_emisor: str,
    id_adquirente: str,
    clave_tecnica: str,
    tipo_ambiente,
    valor_iva=0,
    valor_inc=0,
    valor_ica=0,
) -> str:
    """Calcula el CUFE (Código Único de Factura Electrónica).

    Aplica a factura de venta, exportación y tipo 04. Devuelve el hash SHA-384
    en hexadecimal, que va en ``/Invoice/cbc:UUID`` con
    ``@schemeName="CUFE-SHA384"``.

    Los impuestos no referenciados se representan con ``0.00`` (valor por
    defecto ``0``).
    """
    composicion = _componer(
        numero_factura=numero_factura,
        fecha=fecha,
        hora=hora,
        valor_sin_impuestos=valor_sin_impuestos,
        valor_iva=valor_iva,
        valor_inc=valor_inc,
        valor_ica=valor_ica,
        valor_total=valor_total,
        nit_emisor=nit_emisor,
        id_adquirente=id_adquirente,
        clave=clave_tecnica,
        tipo_ambiente=tipo_ambiente,
    )
    return _sha384_hex(composicion)


def calcular_cude(
    *,
    numero_factura: str,
    fecha,
    hora,
    valor_sin_impuestos,
    valor_total,
    nit_emisor: str,
    id_adquirente: str,
    pin_software: str,
    tipo_ambiente,
    valor_iva=0,
    valor_inc=0,
    valor_ica=0,
) -> str:
    """Calcula el CUDE (Código Único de Documento Electrónico).

    Igual al CUFE pero usando el PIN del software en lugar de la Clave Técnica.
    Aplica a notas crédito/débito, documento soporte y ApplicationResponse.
    Va en ``/cbc:UUID`` con ``@schemeName="CUDE-SHA384"``.
    """
    composicion = _componer(
        numero_factura=numero_factura,
        fecha=fecha,
        hora=hora,
        valor_sin_impuestos=valor_sin_impuestos,
        valor_iva=valor_iva,
        valor_inc=valor_inc,
        valor_ica=valor_ica,
        valor_total=valor_total,
        nit_emisor=nit_emisor,
        id_adquirente=id_adquirente,
        clave=pin_software,
        tipo_ambiente=tipo_ambiente,
    )
    return _sha384_hex(composicion)


def calcular_codigo_seguridad_software(
    *, id_software: str, pin: str, numero_documento: str
) -> str:
    """Calcula el ``sts:SoftwareSecurityCode``.

    ``SoftwareSecurityCode = SHA-384(IdSoftware + Pin + NroDocumento)``.
    """
    return _sha384_hex(f"{id_software}{pin}{numero_documento}")
