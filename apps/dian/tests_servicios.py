"""Pruebas del servicio de orquestación (pipeline completo)."""
from django.test import TestCase

from apps.dian import servicios, soap
from apps.dian.tests_firma import _generar_certificado
from apps.documentos.models import DocumentoElectronico
from apps.documentos.tests_utils import crear_documento_factura


class FakeCliente:
    """Cliente SOAP falso para probar el envío sin red."""

    def __init__(self, respuesta):
        self.respuesta = respuesta
        self.llamadas = []

    def enviar_set_pruebas(self, xml, nombre, test_set_id):
        self.llamadas.append(("set_pruebas", nombre, test_set_id))
        return self.respuesta

    def enviar_factura_sincrono(self, xml, nombre):
        self.llamadas.append(("sincrono", nombre))
        return self.respuesta


class GenerarYFirmarTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        datos = crear_documento_factura()
        cls.documento = datos["documento"]
        cls.llave, cls.cert = _generar_certificado()

    def test_genera_firma_y_guarda(self):
        from apps.dian import firma

        firmador = firma.FirmadorXAdES(
            self.llave, self.cert, policy_id="x", policy_hash="aGFzaA==",
        )
        servicios.generar_y_firmar(self.documento, firmador=firmador, ambiente=1)

        self.documento.refresh_from_db()
        self.assertEqual(self.documento.estado, DocumentoElectronico.Estado.FIRMADO)
        # CUFE oficial (ambiente=1).
        self.assertEqual(
            self.documento.cufe_cude,
            "8bb918b19ba22a694f1da11c643b5e9de39adf60311cf179179e9b33"
            "381030bcd4c3c3f156c506ed5908f9276f5bd9b4",
        )
        self.assertIn("<ds:Signature", self.documento.xml_firmado)


class EnviarADianTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.documento = crear_documento_factura()["documento"]
        cls.documento.xml_firmado = "<Invoice/>"
        cls.documento.cufe_cude = "ABC"
        cls.documento.save()

    def test_set_pruebas_acepta(self):
        respuesta = soap.RespuestaDian(track_id="track-1", es_valido=True, codigo_estado="00")
        cliente = FakeCliente(respuesta)
        r = servicios.enviar_a_dian(self.documento, cliente=cliente, ambiente=2)

        self.documento.refresh_from_db()
        self.assertEqual(self.documento.estado, DocumentoElectronico.Estado.ACEPTADO)
        self.assertEqual(r.track_id, "track-1")
        self.assertEqual(cliente.llamadas[0][0], "set_pruebas")

    def test_rechazo_marca_estado(self):
        respuesta = soap.RespuestaDian(es_valido=False, errores=["Error X"])
        cliente = FakeCliente(respuesta)
        servicios.enviar_a_dian(self.documento, cliente=cliente, ambiente=2)

        self.documento.refresh_from_db()
        self.assertEqual(self.documento.estado, DocumentoElectronico.Estado.RECHAZADO)

    def test_sin_firmar_falla(self):
        self.documento.xml_firmado = ""
        self.documento.save()
        with self.assertRaises(servicios.ErrorEmision):
            servicios.enviar_a_dian(self.documento, cliente=FakeCliente(None), ambiente=2)
