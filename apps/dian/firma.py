"""
Firma digital XAdES-EPES para los documentos electrónicos DIAN.

Implementa una firma XML envuelta (enveloped) con propiedades XAdES-EPES tal
como exige el Anexo Técnico v1.9: tres ``ds:Reference`` (documento, KeyInfo y
SignedProperties), ``SigningCertificate``, ``SignaturePolicyIdentifier`` y
``SignerRole``. La firma se inserta en una segunda ``ext:UBLExtension``.

La canonicalización es C14N inclusiva 1.0 (la que usa la DIAN) y los digests y
la firma usan SHA-256 / RSA-SHA256.

Referencia de estructura: ``Ejemplificaciones/.../Generica.xml`` (ds:Signature)
y ``docs/anexo-tecnico.md`` (sección 6, firma).
"""
from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import Encoding, pkcs12
from lxml import etree

from apps.dian.ubl import NS, _q, _sub

# --- Algoritmos (URIs estándar XML-DSig) -----------------------------------
ALG_C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
ALG_FIRMA_RSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
ALG_DIGEST_SHA256 = "http://www.w3.org/2001/04/xmlenc#sha256"
ALG_ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
TIPO_SIGNED_PROPERTIES = "http://uri.etsi.org/01903#SignedProperties"

# Zona horaria de Colombia (-05:00).
TZ_COLOMBIA = timezone(timedelta(hours=-5))


def calcular_hash_politica(ruta_pdf) -> str:
    """SHA-256 en base64 del PDF de la política de firma (para SigPolicyHash)."""
    with open(ruta_pdf, "rb") as fh:
        return base64.b64encode(hashlib.sha256(fh.read()).digest()).decode()


def cargar_pkcs12(datos: bytes, clave: str):
    """Carga un .p12/.pfx y devuelve (llave_privada, certificado, cadena)."""
    llave, certificado, cadena = pkcs12.load_key_and_certificates(
        datos, clave.encode() if clave else None
    )
    return llave, certificado, list(cadena or [])


def _c14n(elemento) -> bytes:
    """Canonicalización C14N inclusiva 1.0 (sin comentarios) de un elemento.

    Incluye los namespaces heredados de los ancestros (C14N inclusiva), por eso
    el elemento debe estar insertado en el árbol antes de canonicalizarlo.
    """
    return etree.tostring(elemento, method="c14n", exclusive=False, with_comments=False)


def _digest_sha256_b64(datos: bytes) -> str:
    return base64.b64encode(hashlib.sha256(datos).digest()).decode()


class FirmadorXAdES:
    """Firma un documento UBL con XAdES-EPES e inserta la firma in situ."""

    def __init__(
        self,
        llave_privada,
        certificado,
        *,
        cadena=None,
        policy_id: str,
        policy_hash: str,
        policy_name: str = "",
        signing_time: datetime | None = None,
        rol: str = "supplier",
    ):
        self.llave = llave_privada
        self.cert = certificado
        self.cadena = cadena or []
        self.policy_id = policy_id
        self.policy_hash = policy_hash
        self.policy_name = policy_name
        self.signing_time = signing_time or datetime.now(TZ_COLOMBIA)
        self.rol = rol

        sid = uuid.uuid4()
        self.id_firma = f"xmldsig-{sid}"
        self.id_signed_info_ref = f"{self.id_firma}-ref0"
        self.id_keyinfo = f"{self.id_firma}-keyinfo"
        self.id_signed_props = f"{self.id_firma}-signedprops"
        self.id_sig_value = f"{self.id_firma}-sigvalue"

    # -- API pública --------------------------------------------------------

    def firmar(self, xml) -> bytes:
        """Firma el XML (bytes o Element) y devuelve los bytes firmados."""
        raiz = xml if etree.iselement(xml) else etree.fromstring(xml)

        contenido = self._segunda_extension(raiz)
        firma = self._construir_firma(raiz, contenido)

        return etree.tostring(
            raiz, xml_declaration=True, encoding="UTF-8", standalone=False
        )

    # -- Construcción -------------------------------------------------------

    def _segunda_extension(self, raiz):
        """Crea la 2ª UBLExtension/ExtensionContent que alojará la firma."""
        extensiones = raiz.find(_q("ext", "UBLExtensions"))
        if extensiones is None:
            raise ValueError("El XML no contiene ext:UBLExtensions")
        ext = _sub(extensiones, "ext", "UBLExtension")
        return _sub(ext, "ext", "ExtensionContent")

    def _construir_firma(self, raiz, contenido):
        firma = _sub(contenido, "ds", "Signature", Id=self.id_firma)

        signed_info = self._signed_info(firma)
        sig_value = _sub(firma, "ds", "SignatureValue", Id=self.id_sig_value)
        key_info = self._key_info(firma)
        objeto = self._objeto_xades(firma)

        # Con todo el árbol en su sitio, calculamos los tres digests.
        signed_props = objeto.find(f".//{{{NS['xades']}}}SignedProperties")
        digest_doc = self._digest_documento(raiz, firma)
        digest_keyinfo = _digest_sha256_b64(_c14n(key_info))
        digest_props = _digest_sha256_b64(_c14n(signed_props))

        refs = signed_info.findall(_q("ds", "Reference"))
        self._fijar_digest(refs[0], digest_doc)
        self._fijar_digest(refs[1], digest_keyinfo)
        self._fijar_digest(refs[2], digest_props)

        # Firmar el SignedInfo canonicalizado.
        firma_valor = self.llave.sign(
            _c14n(signed_info), padding.PKCS1v15(), hashes.SHA256()
        )
        sig_value.text = base64.b64encode(firma_valor).decode()
        return firma

    def _signed_info(self, firma):
        si = _sub(firma, "ds", "SignedInfo")
        _sub(si, "ds", "CanonicalizationMethod", Algorithm=ALG_C14N)
        _sub(si, "ds", "SignatureMethod", Algorithm=ALG_FIRMA_RSA_SHA256)

        # Reference 0: documento completo (enveloped).
        ref0 = _sub(si, "ds", "Reference", Id=self.id_signed_info_ref, URI="")
        transforms = _sub(ref0, "ds", "Transforms")
        _sub(transforms, "ds", "Transform", Algorithm=ALG_ENVELOPED)
        _sub(ref0, "ds", "DigestMethod", Algorithm=ALG_DIGEST_SHA256)
        _sub(ref0, "ds", "DigestValue")

        # Reference 1: KeyInfo.
        ref1 = _sub(si, "ds", "Reference", URI=f"#{self.id_keyinfo}")
        _sub(ref1, "ds", "DigestMethod", Algorithm=ALG_DIGEST_SHA256)
        _sub(ref1, "ds", "DigestValue")

        # Reference 2: SignedProperties.
        ref2 = _sub(
            si, "ds", "Reference",
            URI=f"#{self.id_signed_props}", Type=TIPO_SIGNED_PROPERTIES,
        )
        _sub(ref2, "ds", "DigestMethod", Algorithm=ALG_DIGEST_SHA256)
        _sub(ref2, "ds", "DigestValue")
        return si

    def _key_info(self, firma):
        ki = _sub(firma, "ds", "KeyInfo", Id=self.id_keyinfo)
        x509_data = _sub(ki, "ds", "X509Data")
        _sub(x509_data, "ds", "X509Certificate", self._cert_b64(self.cert))
        return ki

    def _objeto_xades(self, firma):
        objeto = _sub(firma, "ds", "Object")
        qp = _sub(objeto, "xades", "QualifyingProperties", Target=f"#{self.id_firma}")
        sp = _sub(qp, "xades", "SignedProperties", Id=self.id_signed_props)
        ssp = _sub(sp, "xades", "SignedSignatureProperties")

        _sub(ssp, "xades", "SigningTime", self._signing_time_str())

        signing_cert = _sub(ssp, "xades", "SigningCertificate")
        for cert in [self.cert, *self.cadena]:
            self._cert_xades(signing_cert, cert)

        self._politica_firma(ssp)

        rol = _sub(ssp, "xades", "SignerRole")
        roles = _sub(rol, "xades", "ClaimedRoles")
        _sub(roles, "xades", "ClaimedRole", self.rol)
        return objeto

    def _cert_xades(self, signing_cert, cert):
        nodo = _sub(signing_cert, "xades", "Cert")
        cert_digest = _sub(nodo, "xades", "CertDigest")
        _sub(cert_digest, "ds", "DigestMethod", Algorithm=ALG_DIGEST_SHA256)
        digest = base64.b64encode(
            hashlib.sha256(cert.public_bytes(Encoding.DER)).digest()
        ).decode()
        _sub(cert_digest, "ds", "DigestValue", digest)

        issuer_serial = _sub(nodo, "xades", "IssuerSerial")
        _sub(issuer_serial, "ds", "X509IssuerName", cert.issuer.rfc4514_string())
        _sub(issuer_serial, "ds", "X509SerialNumber", str(cert.serial_number))

    def _politica_firma(self, ssp):
        spi = _sub(ssp, "xades", "SignaturePolicyIdentifier")
        spid = _sub(spi, "xades", "SignaturePolicyId")
        sid = _sub(spid, "xades", "SigPolicyId")
        _sub(sid, "xades", "Identifier", self.policy_id)
        if self.policy_name:
            _sub(sid, "xades", "Description", self.policy_name)
        sph = _sub(spid, "xades", "SigPolicyHash")
        _sub(sph, "ds", "DigestMethod", Algorithm=ALG_DIGEST_SHA256)
        _sub(sph, "ds", "DigestValue", self.policy_hash)

    # -- Digests / utilidades ----------------------------------------------

    def _digest_documento(self, raiz, firma):
        """Digest del documento sin la firma (transformada enveloped)."""
        padre = firma.getparent()
        indice = padre.index(firma)
        padre.remove(firma)
        try:
            canonico = _c14n(raiz)
        finally:
            padre.insert(indice, firma)
        return _digest_sha256_b64(canonico)

    def _fijar_digest(self, referencia, valor):
        referencia.find(_q("ds", "DigestValue")).text = valor

    def _cert_b64(self, cert) -> str:
        return base64.b64encode(cert.public_bytes(Encoding.DER)).decode()

    def _signing_time_str(self) -> str:
        t = self.signing_time
        base = t.strftime("%Y-%m-%dT%H:%M:%S")
        ms = f".{t.microsecond // 1000:03d}"
        desfase = t.strftime("%z") or "-0500"
        return f"{base}{ms}{desfase[:3]}:{desfase[3:]}"
