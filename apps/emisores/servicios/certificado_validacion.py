"""Validación de un certificado .p12/.pfx antes de almacenarlo.

Comprueba lo que la firma XAdES de la DIAN necesita de verdad, de modo que un
certificado inservible se rechace al cargarlo y no al emitir una factura:

1. Integridad + clave: el archivo abre y descifra con la clave dada.
2. Contenido: trae a la vez llave privada y certificado.
3. Vigencia: no está vencido ni es aún futuro.
4. Algoritmo: la llave es RSA y de tamaño suficiente (la firma usa RSA-SHA256).
5. Titularidad: el NIT del certificado coincide con el del emisor.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509 import ObjectIdentifier
from cryptography.x509.oid import NameOID

from apps.dian.firma import cargar_pkcs12

# La firma DIAN usa RSA-SHA256; rechazamos llaves débiles.
TAM_MINIMO_RSA = 2048
# organizationIdentifier (perfil ETSI/eIDAS): suele traer "NIT-901192048-8".
OID_ORG_ID = ObjectIdentifier("2.5.4.97")


class CertificadoInvalido(ValueError):
    """El .p12/.pfx no sirve para firmar (clave, vigencia, NIT, etc.)."""


def _solo_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def _identificadores_del_certificado(cert) -> list[str]:
    """Los números que aparecen en el subject, cada uno por separado.

    Se miran los campos donde una CA colombiana pone el NIT del titular
    —``serialNumber`` y ``organizationIdentifier``, que suele traer
    ``"NIT-901192048-8"``— y, como respaldo, el subject entero.

    Devuelve una **lista de rachas de dígitos** y no una sola cadena. Antes se
    concatenaba todo y se preguntaba con ``in``, que es una comparación por
    subcadena: el NIT ``900123`` daba por bueno un certificado emitido a
    ``8900123456``. Separándolo, cada número se compara entero.
    """
    partes = []
    for oid in (NameOID.SERIAL_NUMBER, OID_ORG_ID):
        partes += [attr.value for attr in cert.subject.get_attributes_for_oid(oid)]
    partes.append(cert.subject.rfc4514_string())
    numeros = []
    for parte in partes:
        numeros += re.findall(r"\d+", parte or "")
    return numeros


def _corresponde_al_emisor(cert, emisor) -> bool:
    """¿Alguno de los números del certificado es el NIT del emisor?

    Se acepta el NIT solo y el NIT con su dígito de verificación pegado, que es
    como lo escribe el perfil ETSI (``NIT-901192048-8`` deja ``901192048`` y
    ``8`` como rachas separadas, pero alguna CA lo emite junto). Nada más: una
    coincidencia parcial ya no vale.
    """
    nit = _solo_digitos(emisor.numero_identificacion)
    if not nit:
        return True  # sin NIT que comparar, no hay nada que comprobar
    dv = _solo_digitos(emisor.digito_verificacion)
    aceptados = {nit, nit + dv} if dv else {nit}
    return any(numero in aceptados for numero in _identificadores_del_certificado(cert))


def validar_pkcs12(datos: bytes, clave: str, emisor) -> dict:
    """Valida el certificado y devuelve metadatos para guardar.

    Lanza :class:`CertificadoInvalido` (con un mensaje apto para el usuario) si
    alguna comprobación falla. Si todo va bien, devuelve ``vigente_desde`` y
    ``vigente_hasta`` tomados del propio certificado.
    """
    # 1. Integridad + clave correcta.
    try:
        llave, cert, _ = cargar_pkcs12(datos, clave)
    except (ValueError, TypeError):
        raise CertificadoInvalido(
            "No se pudo abrir el certificado: el archivo no es un .p12/.pfx "
            "válido o la clave es incorrecta."
        )

    # 2. Debe traer llave privada y certificado.
    if llave is None or cert is None:
        raise CertificadoInvalido(
            "El archivo no contiene a la vez una llave privada y un "
            "certificado; no sirve para firmar."
        )

    # 4. La llave debe ser RSA y de tamaño suficiente.
    if not isinstance(llave, rsa.RSAPrivateKey):
        raise CertificadoInvalido(
            "La llave del certificado no es RSA; la firma DIAN exige RSA."
        )
    if llave.key_size < TAM_MINIMO_RSA:
        raise CertificadoInvalido(
            f"La llave RSA es de {llave.key_size} bits; se requieren al menos "
            f"{TAM_MINIMO_RSA}."
        )

    # 3. Vigencia temporal.
    ahora = datetime.now(timezone.utc)
    if ahora < cert.not_valid_before_utc:
        raise CertificadoInvalido(
            "El certificado aún no es vigente (válido desde "
            f"{cert.not_valid_before_utc:%Y-%m-%d})."
        )
    if ahora > cert.not_valid_after_utc:
        raise CertificadoInvalido(
            "El certificado está vencido (venció el "
            f"{cert.not_valid_after_utc:%Y-%m-%d})."
        )

    # 5. El NIT del certificado debe coincidir con el del emisor.
    if not _corresponde_al_emisor(cert, emisor):
        raise CertificadoInvalido(
            "El certificado no corresponde al emisor: no se encontró su NIT "
            f"({emisor.numero_identificacion}) en los datos del certificado."
        )

    return {
        "vigente_desde": cert.not_valid_before_utc.date(),
        "vigente_hasta": cert.not_valid_after_utc.date(),
    }
