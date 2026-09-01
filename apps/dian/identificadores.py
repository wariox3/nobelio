"""
Cálculo de los identificadores únicos DIAN: CUFE, CUDE, CUDS y código de
seguridad del software.

Referencia: Anexo Técnico Factura Electrónica de Venta v1.9 (Res. 000165/2023),
secciones 11.2 (CUFE), 11.4 (CUDE) y 11.8 (SoftwareSecurityCode).
Resumen en ``docs/anexo-tecnico.md``.

El CUDS es del otro anexo —Documento Soporte v1.1 (Res. 000167/2021), numeral
14.1.1, resumen en ``docs/anexo-documento-soporte.md``— y no comparte
composición con los anteriores: ver ``calcular_cuds``.

El CUNE es de un tercer anexo —Nómina Electrónica v1.0 (Res. 000013/2021),
numeral 8.1, resumen en ``docs/anexo-nomina.md``— y tampoco: ver
``calcular_cune``.

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
SCHEME_NAME_CUDS = "CUDS-SHA384"
# En la nómina no es un @schemeName sino el atributo @EncripCUNE, pero cumple lo
# mismo: declarar con qué algoritmo se calculó el identificador.
SCHEME_NAME_CUNE = "CUNE-SHA384"

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
        # Se pregunta por el desfase, no por el tzinfo: una zona como
        # ZoneInfo("America/Bogota") pegada a un `time` no puede resolverlo
        # (necesita una fecha) y devolvía "" -> "HH:MM:SS:", que no es una hora.
        if hora.utcoffset() is not None:
            desfase = hora.strftime("%z")  # p. ej. -0500
            return f"{base}{desfase[:3]}:{desfase[3:]}"
        return f"{base}-05:00"
    return str(hora)


def nombre_archivo_dian(prefijo: str, *, nit, consecutivo) -> str:
    """Compone el nombre de un archivo según la convención DIAN, sin extensión.

    ``prefijo`` + NIT del emisor a 10 dígitos + consecutivo a 8, rellenando con
    ceros a la izquierda. Los prefijos son ``z`` para el zip de entrega y ``ad``
    para el AttachedDocument.
    """
    return f"{prefijo}{str(nit).zfill(10)}{str(consecutivo).zfill(8)}"


def nombre_archivo_nomina(prefijo: str, *, nit, anio, consecutivo) -> str:
    """Compone el nombre de un archivo de nómina, sin extensión.

    Otra convención que la de factura (``nombre_archivo_dian``): lleva los dos
    últimos dígitos del año entre el NIT y el consecutivo, y el consecutivo va
    en **hexadecimal** de ocho dígitos, no en decimal. Los prefijos son ``nie``
    para la nómina, ``niae`` para la nota de ajuste y ``z`` para el zip.

    Numerales 3.3 a 3.5 del anexo de nómina.
    """
    return (
        f"{prefijo}{str(nit).zfill(10)}{str(anio)[-2:]}"
        f"{format(int(consecutivo), 'X').zfill(8)}"
    )


def nombre_archivo_documento_equivalente(
    prefijo: str, *, nit, codigo_pt, anio, consecutivo
) -> str:
    """Compone el nombre de un archivo de documento equivalente, sin extensión.

    Tercera convención, distinta de las otras dos: es la de nómina más el
    ``ppp``, el código de tres dígitos que la DIAN asigna al proveedor
    tecnológico.

        prefijo + NIT(10) + ppp(3) + aa(2) + consecutivo hexadecimal(8)

    Los prefijos son ``ds`` para el documento equivalente, ``ncs`` para su nota
    de ajuste, ``ars`` para el ApplicationResponse soporte y ``z`` para el zip.
    El consecutivo es de **archivos enviados** y se reinicia cada 1 de enero.

    Ejemplo del anexo: ``ds08001972680002000000001.xml``.

    Numeral 8.13.5 del Anexo Técnico de documento equivalente electrónico v1.0;
    resumen en ``docs/anexo-documento-equivalente.md``.
    """
    return (
        f"{prefijo}{str(nit).zfill(10)}{str(codigo_pt or '').zfill(3)}"
        f"{str(anio)[-2:]}{format(int(consecutivo), 'X').zfill(8)}"
    )


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


def calcular_cuds(
    *,
    numero_documento: str,
    fecha,
    hora,
    valor_sin_impuestos,
    valor_total,
    nit_vendedor: str,
    nit_adquiriente: str,
    pin_software: str,
    tipo_ambiente,
    valor_iva=0,
) -> str:
    """Calcula el CUDS (Código Único de Documento Soporte).

    **No es el CUDE.** Comparte el algoritmo (SHA-384) y el PIN del software,
    pero la composición es más corta: lleva un solo impuesto —el IVA— donde el
    CUFE/CUDE lleva tres pares (IVA, INC, ICA). Pasar por ``calcular_cude`` un
    documento soporte produce un hash que la DIAN rechaza.

    El orden de los dos identificadores también es propio: primero el del
    vendedor (el sujeto no obligado, que en el UBL va como *supplier*) y después
    el del adquiriente que emite y firma (que va como *customer*). Es el reverso
    de la factura, donde el que firma va primero.

    Aplica al documento soporte (Invoice tipo 05) y a su nota de ajuste
    (CreditNote tipo 95). Va en ``/cbc:UUID`` con ``@schemeName="CUDS-SHA384"``.

    Referencia: Anexo Técnico Documento Soporte v1.1 (Res. 000167/2021),
    numeral 14.1.1. Resumen en ``docs/anexo-documento-soporte.md``.
    """
    composicion = (
        f"{numero_documento}"
        f"{formatear_fecha(fecha)}"
        f"{formatear_hora(hora)}"
        f"{formatear_valor(valor_sin_impuestos)}"
        f"{COD_IMPUESTO_IVA}{formatear_valor(valor_iva)}"
        f"{formatear_valor(valor_total)}"
        f"{nit_vendedor}"
        f"{nit_adquiriente}"
        f"{pin_software}"
        f"{tipo_ambiente}"
    )
    return _sha384_hex(composicion)


def calcular_cune(
    *,
    numero_documento: str,
    fecha,
    hora,
    valor_devengado,
    valor_deduccion,
    valor_total,
    nit_empleador: str,
    documento_empleado: str,
    tipo_xml: str,
    pin_software: str,
    tipo_ambiente,
) -> str:
    """Calcula el CUNE (Código Único de Nómina Electrónica).

    No comparte composición con el CUFE, el CUDE ni el CUDS: no hay impuestos
    sino los dos totales de la nómina, y entra el tipo de XML (``102`` nómina,
    ``103`` nota de ajuste), que en los otros documentos no existe.

    Aplica al documento soporte de pago de nómina y a su nota de ajuste. Va en
    ``@CUNE``, con ``@EncripCUNE = "CUNE-SHA384"``.

    ⚠️ El ejemplo del anexo (numeral 8.1.1.3) **no reproduce su propio hash**:
    ni con la composición documentada ni con ninguna variante razonable. Los
    ejemplos del CUFE, el CUDE y el CUDS sí cuadran, así que aquí no hay vector
    de prueba oficial con el que confirmar esta función; solo la DIAN.

    Referencia: Anexo Técnico Nómina Electrónica v1.0, numeral 8.1.1.1.
    """
    composicion = (
        f"{numero_documento}"
        f"{formatear_fecha(fecha)}"
        f"{formatear_hora(hora)}"
        f"{formatear_valor(valor_devengado)}"
        f"{formatear_valor(valor_deduccion)}"
        f"{formatear_valor(valor_total)}"
        f"{nit_empleador}"
        f"{documento_empleado}"
        f"{tipo_xml}"
        f"{pin_software}"
        f"{tipo_ambiente}"
    )
    return _sha384_hex(composicion)


def calcular_codigo_seguridad_software(
    *, id_software: str, pin: str, numero_documento: str
) -> str:
    """Calcula el ``sts:SoftwareSecurityCode``.

    ``SoftwareSecurityCode = SHA-384(IdSoftware + Pin + NroDocumento)``.
    """
    return _sha384_hex(f"{id_software}{pin}{numero_documento}")
