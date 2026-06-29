"""Pruebas del servicio de orquestación (pipeline completo)."""
from django.test import TestCase

from apps.dian import servicios, soap
from apps.dian.tests_firma import _generar_certificado
from apps.documentos.models import Documento
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

    def consultar_rangos_numeracion(self, nit_emisor, nit_proveedor, software_id):
        self.llamadas.append(("rangos", nit_emisor, nit_proveedor, software_id))
        return self.respuesta

    def consultar_estado(self, track_id):
        self.llamadas.append(("estado", track_id))
        return self.respuesta

    def consultar_estado_zip(self, track_id):
        self.llamadas.append(("estado_zip", track_id))
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
        self.assertEqual(self.documento.estado, Documento.Estado.FIRMADO)
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
        self.assertEqual(self.documento.estado, Documento.Estado.ACEPTADO)
        self.assertEqual(r.track_id, "track-1")
        self.assertEqual(cliente.llamadas[0][0], "set_pruebas")
        # El track_id (ZipKey) se persiste para consultar el estado luego.
        self.assertEqual(self.documento.track_id, "track-1")

    def test_set_aceptado_usa_sendbillsync(self):
        # Con el Set de Pruebas ya aceptado, aun en habilitación se usa SendBillSync.
        sw = self.documento.emisor.softwares.filter(activo=True).first()
        sw.set_pruebas_aceptado = True
        sw.save(update_fields=["set_pruebas_aceptado"])
        cliente = FakeCliente(soap.RespuestaDian(es_valido=True, codigo_estado="00"))
        servicios.enviar_a_dian(self.documento, cliente=cliente, ambiente=2)
        self.assertEqual(cliente.llamadas[0][0], "sincrono")

    def test_rechazo_marca_estado(self):
        respuesta = soap.RespuestaDian(es_valido=False, errores=["Error X"])
        cliente = FakeCliente(respuesta)
        servicios.enviar_a_dian(self.documento, cliente=cliente, ambiente=2)

        self.documento.refresh_from_db()
        self.assertEqual(self.documento.estado, Documento.Estado.RECHAZADO)

    def test_sin_firmar_falla(self):
        self.documento.xml_firmado = ""
        self.documento.save()
        with self.assertRaises(servicios.ErrorEmision):
            servicios.enviar_a_dian(self.documento, cliente=FakeCliente(None), ambiente=2)


class ConsultarEstadoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.documento = crear_documento_factura()["documento"]
        cls.documento.track_id = "zip-123"
        cls.documento.save()

    def test_habilitacion_usa_getstatuszip(self):
        respuesta = soap.RespuestaDian(es_valido=True, codigo_estado="00")
        cliente = FakeCliente(respuesta)
        servicios.consultar_estado(self.documento, cliente=cliente, ambiente=2)

        self.documento.refresh_from_db()
        self.assertEqual(self.documento.estado, Documento.Estado.ACEPTADO)
        self.assertEqual(cliente.llamadas[0], ("estado_zip", "zip-123"))

    def test_produccion_usa_getstatus(self):
        cliente = FakeCliente(soap.RespuestaDian(es_valido=False, errores=["X"]))
        servicios.consultar_estado(self.documento, cliente=cliente, ambiente=1)
        self.assertEqual(cliente.llamadas[0], ("estado", "zip-123"))

    def test_sin_track_id_falla(self):
        self.documento.track_id = ""
        self.documento.save()
        with self.assertRaises(servicios.ErrorEmision):
            servicios.consultar_estado(self.documento, cliente=FakeCliente(None), ambiente=2)


class ConsultarRangosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emisor = crear_documento_factura()["documento"].emisor

    def test_consulta_usa_nit_y_software_del_emisor(self):
        respuesta = soap.RespuestaRangos(
            codigo="100", descripcion="OK",
            rangos=[soap.RangoNumeracion(prefijo="SETP", clave_tecnica="abc")],
        )
        cliente = FakeCliente(respuesta)
        resultado = servicios.consultar_rangos_numeracion(
            self.emisor, cliente=cliente, ambiente=2,
        )

        self.assertEqual(resultado, respuesta)
        clase, nit, proveedor, software_id = cliente.llamadas[0]
        self.assertEqual(clase, "rangos")
        self.assertEqual(nit, self.emisor.numero_identificacion)
        # Software propio: el proveedor es el propio NIT del emisor.
        self.assertEqual(proveedor, "700085371")
        self.assertEqual(software_id, "56f2ae4e-9812-4fad-9255-08fcfcd5ccb0")

    def test_sin_software_activo_falla(self):
        self.emisor.softwares.update(activo=False)
        with self.assertRaises(servicios.ErrorEmision):
            servicios.consultar_rangos_numeracion(
                self.emisor, cliente=FakeCliente([]), ambiente=2,
            )
