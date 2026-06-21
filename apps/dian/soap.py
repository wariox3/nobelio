"""
Cliente de los Web Services de la DIAN (WcfDianCustomerServices).

Implementa:
  - Empaquetado del XML firmado en ZIP + Base64.
  - Construcción del sobre SOAP 1.2 con WS-Addressing y WS-Security (firma del
    Timestamp y el Body con C14N exclusiva, RSA-SHA256, BinarySecurityToken).
  - Envío por HTTP y parseo de la respuesta de la DIAN.

Operaciones soportadas:
  - SendTestSetAsync : envío al Set de Pruebas (habilitación) -> devuelve ZipKey/trackId.
  - SendBillSync     : envío síncrono (producción) -> DianResponse.
  - GetStatus        : consulta de estado por trackId -> DianResponse.

La firma del sobre (WS-Security) y el empaquetado son independientes del envío
HTTP, de modo que pueden probarse sin conexión.
"""
from __future__ import annotations

import base64
import hashlib
import io
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import Encoding
from lxml import etree

# --- Namespaces -------------------------------------------------------------
NS = {
    "soap": "http://www.w3.org/2003/05/soap-envelope",
    "wcf": "http://wcf.dian.colombia",
    "wsa": "http://www.w3.org/2005/08/addressing",
    "wsse": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd",
    "wsu": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
}

# Algoritmos WS-Security.
ALG_EXC_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
ALG_FIRMA_RSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
ALG_DIGEST_SHA256 = "http://www.w3.org/2001/04/xmlenc#sha256"

VALUE_TYPE_X509 = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-x509-token-profile-1.0#X509v3"
)
ENCODING_BASE64 = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-soap-message-security-1.0#Base64Binary"
)

ACCION_BASE = "http://wcf.dian.colombia/IWcfDianCustomerServices"
TZ_UTC = timezone.utc


def _q(prefijo, etiqueta):
    return etree.QName(NS[prefijo], etiqueta)


def _sub(padre, prefijo, etiqueta, texto=None, **atributos):
    elem = etree.SubElement(padre, _q(prefijo, etiqueta))
    if texto is not None:
        elem.text = str(texto)
    for clave, valor in atributos.items():
        elem.set(clave, str(valor))
    return elem


def _exc_c14n(elemento) -> bytes:
    """Canonicalización C14N exclusiva (la que usa WS-Security)."""
    return etree.tostring(elemento, method="c14n", exclusive=True, with_comments=False)


def _digest_b64(datos: bytes) -> str:
    return base64.b64encode(hashlib.sha256(datos).digest()).decode()


# ===========================================================================
# Empaquetado ZIP
# ===========================================================================
def empaquetar_zip(nombre_xml: str, contenido_xml: bytes) -> bytes:
    """Comprime el XML firmado en un ZIP y devuelve los bytes del ZIP."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(nombre_xml, contenido_xml)
    return buffer.getvalue()


def empaquetar_base64(nombre_xml: str, contenido_xml: bytes) -> str:
    """Devuelve el ZIP del XML firmado codificado en Base64 (contentFile)."""
    return base64.b64encode(empaquetar_zip(nombre_xml, contenido_xml)).decode()


# ===========================================================================
# Respuesta DIAN
# ===========================================================================
@dataclass
class RespuestaDian:
    """Resultado normalizado de una respuesta de la DIAN."""

    track_id: str = ""
    es_valido: bool = False
    codigo_estado: str = ""
    descripcion_estado: str = ""
    errores: list[str] = field(default_factory=list)
    xml_crudo: str = ""

    @classmethod
    def desde_xml(cls, xml: bytes | str) -> "RespuestaDian":
        """Parsea la respuesta SOAP de la DIAN (DianResponse / ZipKey)."""
        if isinstance(xml, str):
            xml = xml.encode()
        raiz = etree.fromstring(xml)

        def texto(local_name):
            nodos = raiz.xpath(
                f"//*[local-name()='{local_name}']"
            )
            return nodos[0].text.strip() if nodos and nodos[0].text else ""

        es_valido = texto("IsValid").lower() == "true"
        errores = [
            n.text.strip()
            for n in raiz.xpath("//*[local-name()='ErrorMessage']/*[local-name()='string']")
            if n.text
        ]
        # SendTestSetAsync devuelve ZipKey; los síncronos no.
        track_id = texto("ZipKey") or texto("TrackId") or texto("XmlDocumentKey")

        return cls(
            track_id=track_id,
            es_valido=es_valido,
            codigo_estado=texto("StatusCode"),
            descripcion_estado=texto("StatusDescription") or texto("Message"),
            errores=errores,
            xml_crudo=xml.decode(errors="replace"),
        )


# ===========================================================================
# Firma WS-Security del sobre SOAP
# ===========================================================================
class FirmanteWSSecurity:
    """Firma un sobre SOAP con WS-Security (Timestamp + Body)."""

    def __init__(self, llave_privada, certificado, *, vigencia_segundos: int = 60):
        self.llave = llave_privada
        self.cert = certificado
        self.vigencia = vigencia_segundos
        sid = uuid.uuid4().hex
        self.id_timestamp = f"TS-{sid}"
        self.id_body = f"id-{sid}"
        self.id_bst = f"X509-{sid}"
        self.id_sig = f"SIG-{sid}"

    def firmar(self, envelope) -> None:
        """Añade Header WS-Security firmado al envelope (modifica in situ)."""
        header = envelope.find(_q("soap", "Header"))
        body = envelope.find(_q("soap", "Body"))
        body.set(_q("wsu", "Id"), self.id_body)

        seguridad = _sub(header, "wsse", "Security")
        seguridad.set(_q("soap", "mustUnderstand"), "1")

        # Timestamp
        timestamp = _sub(seguridad, "wsu", "Timestamp")
        timestamp.set(_q("wsu", "Id"), self.id_timestamp)
        ahora = datetime.now(TZ_UTC)
        _sub(timestamp, "wsu", "Created", _instante(ahora))
        _sub(timestamp, "wsu", "Expires", _instante(ahora + timedelta(seconds=self.vigencia)))

        # BinarySecurityToken (certificado)
        bst = _sub(
            seguridad, "wsse", "BinarySecurityToken",
            base64.b64encode(self.cert.public_bytes(Encoding.DER)).decode(),
            EncodingType=ENCODING_BASE64, ValueType=VALUE_TYPE_X509,
        )
        bst.set(_q("wsu", "Id"), self.id_bst)

        firma = self._construir_firma(seguridad, timestamp, body)

    def _construir_firma(self, seguridad, timestamp, body):
        firma = _sub(seguridad, "ds", "Signature", Id=self.id_sig)
        signed_info = _sub(firma, "ds", "SignedInfo")
        _sub(signed_info, "ds", "CanonicalizationMethod", Algorithm=ALG_EXC_C14N)
        _sub(signed_info, "ds", "SignatureMethod", Algorithm=ALG_FIRMA_RSA_SHA256)

        for elemento, ref_id in [(timestamp, self.id_timestamp), (body, self.id_body)]:
            self._referencia(signed_info, elemento, ref_id)

        sig_value = _sub(firma, "ds", "SignatureValue")
        sig_value.text = base64.b64encode(
            self.llave.sign(_exc_c14n(signed_info), padding.PKCS1v15(), hashes.SHA256())
        ).decode()

        self._key_info(firma)
        return firma

    def _referencia(self, signed_info, elemento, ref_id):
        ref = _sub(signed_info, "ds", "Reference", URI=f"#{ref_id}")
        transforms = _sub(ref, "ds", "Transforms")
        _sub(transforms, "ds", "Transform", Algorithm=ALG_EXC_C14N)
        _sub(ref, "ds", "DigestMethod", Algorithm=ALG_DIGEST_SHA256)
        _sub(ref, "ds", "DigestValue", _digest_b64(_exc_c14n(elemento)))

    def _key_info(self, firma):
        key_info = _sub(firma, "ds", "KeyInfo")
        str_ref = _sub(key_info, "wsse", "SecurityTokenReference")
        _sub(str_ref, "wsse", "Reference", URI=f"#{self.id_bst}", ValueType=VALUE_TYPE_X509)


def _instante(momento: datetime) -> str:
    """Formato WS-Security: ISO 8601 UTC con milisegundos y 'Z'."""
    return momento.strftime("%Y-%m-%dT%H:%M:%S.") + f"{momento.microsecond // 1000:03d}Z"


# ===========================================================================
# Cliente DIAN
# ===========================================================================
class ClienteDian:
    """Cliente HTTP para los Web Services de la DIAN."""

    def __init__(self, url: str, llave_privada, certificado, *, timeout: int = 60):
        self.url = url
        self.firmante = FirmanteWSSecurity(llave_privada, certificado)
        self.llave = llave_privada
        self.cert = certificado
        self.timeout = timeout

    # -- Operaciones --------------------------------------------------------

    def enviar_set_pruebas(self, xml_firmado: bytes, nombre_archivo: str,
                           test_set_id: str) -> RespuestaDian:
        """SendTestSetAsync: envía al Set de Pruebas (habilitación)."""
        contenido = empaquetar_base64(nombre_archivo, xml_firmado)
        cuerpo = {
            "fileName": nombre_archivo,
            "contentFile": contenido,
            "testSetId": test_set_id,
        }
        return self._invocar("SendTestSetAsync", cuerpo)

    def enviar_factura_sincrono(self, xml_firmado: bytes, nombre_archivo: str) -> RespuestaDian:
        """SendBillSync: envío síncrono (producción)."""
        contenido = empaquetar_base64(nombre_archivo, xml_firmado)
        cuerpo = {"fileName": nombre_archivo, "contentFile": contenido}
        return self._invocar("SendBillSync", cuerpo)

    def consultar_estado(self, track_id: str) -> RespuestaDian:
        """GetStatus: consulta el estado de un documento por su trackId."""
        return self._invocar("GetStatus", {"trackId": track_id})

    # -- Internos -----------------------------------------------------------

    def construir_sobre(self, operacion: str, parametros: dict) -> bytes:
        """Construye y firma el sobre SOAP para una operación."""
        envelope = etree.Element(_q("soap", "Envelope"), nsmap=NS)
        header = _sub(envelope, "soap", "Header")
        _sub(header, "wsa", "Action", f"{ACCION_BASE}/{operacion}")
        _sub(header, "wsa", "To", self.url)

        body = _sub(envelope, "soap", "Body")
        op = _sub(body, "wcf", operacion)
        for clave, valor in parametros.items():
            _sub(op, "wcf", clave, valor)

        self.firmante.firmar(envelope)
        return etree.tostring(envelope, xml_declaration=True, encoding="UTF-8")

    def _invocar(self, operacion: str, parametros: dict) -> RespuestaDian:
        sobre = self.construir_sobre(operacion, parametros)
        accion = f"{ACCION_BASE}/{operacion}"
        respuesta = self._post(sobre, accion)
        return RespuestaDian.desde_xml(respuesta)

    def _post(self, sobre: bytes, accion: str) -> bytes:
        """Realiza el POST HTTP. Aislado para facilitar las pruebas."""
        headers = {
            "Content-Type": f'application/soap+xml;charset=UTF-8;action="{accion}"',
        }
        resp = requests.post(self.url, data=sobre, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.content
