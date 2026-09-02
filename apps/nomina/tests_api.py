"""El ciclo de vida de una nómina por la API: emitir, enviar y consultar.

No hay red: el envío y la consulta usan un cliente SOAP falso, igual que en
`apps.dian.tests_servicios`. Lo que se prueba es que la vista encadene bien el
pipeline y que el estado acabe donde debe, no que la DIAN conteste.
"""
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from rest_framework import status
from rest_framework.test import APITestCase

from apps.dian import soap
from apps.dian.tests_firma import _generar_certificado
from apps.documentos.models import DocumentoEstado
from apps.emisores.models import Certificado
from apps.nomina.models import Nomina
from apps.nomina.tests_utils import crear_emisor_de_nomina, crear_nomina


# El parcheo apunta a `servicios_nomina` y no a `servicios`: el pipeline de
# nómina vive en su propio módulo desde que se partió el de 1.069 líneas, y
# `mock.patch` sustituye el nombre **donde se busca**, no donde se define.
class ClienteNominaFalso:
    """El cliente SOAP de nómina, sin red. Registra con qué se le llamó."""

    def __init__(self, respuesta):
        self.respuesta = respuesta
        self.llamadas = []

    def enviar_set_pruebas(self, xml, nombre, test_set_id, nombre_zip=None):
        self.llamadas.append(("set_pruebas", nombre, test_set_id, nombre_zip))
        return self.respuesta

    def enviar_nomina_sincrono(self, xml, nombre):
        self.llamadas.append(("sincrono", nombre))
        return self.respuesta

    def consultar_estado(self, clave):
        self.llamadas.append(("estado", clave))
        return self.respuesta

    def consultar_estado_zip(self, clave):
        self.llamadas.append(("estado_zip", clave))
        return self.respuesta


def _respuesta(*, es_valido=True, errores=(), track_id="ZIPKEY-1"):
    return soap.RespuestaDian(
        es_valido=es_valido,
        codigo_estado="00" if es_valido else "99",
        descripcion_estado="Procesado Correctamente" if es_valido else "Rechazo",
        errores=list(errores),
        track_id=track_id,
        xml_crudo="<ApplicationResponse/>",
    )


class NominaAPIBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.base = crear_emisor_de_nomina()
        cls.emisor = cls.base["emisor"]
        cls.nomina, _ = crear_nomina(cls.base)

        # Un .p12 de verdad, para que `emitir` firme de verdad.
        llave, cert = _generar_certificado()
        p12 = pkcs12.serialize_key_and_certificates(
            b"alias", llave, cert, None, BestAvailableEncryption(b"clave123")
        )
        Certificado.objects.filter(emisor=cls.emisor).delete()
        certificado = Certificado(emisor=cls.emisor, clave="clave123", alias="test")
        certificado.archivo.save("test.p12", ContentFile(p12), save=True)

        cls.usuario = get_user_model().objects.create_user(
            email="nomina@example.com", password="x"
        )
        cls.usuario.emisores.add(cls.emisor)

    def setUp(self):
        self.client.force_authenticate(self.usuario)

    def _url(self, sufijo="", nomina=None):
        return f"/api/nomina/nomina/{(nomina or self.nomina).id}/{sufijo}"

    def _emitir(self, nomina=None):
        return self.client.post(self._url("emitir/", nomina))


class NominaCicloTests(NominaAPIBase):
    def test_emitir_firma_y_calcula_el_cune(self):
        resp = self._emitir()
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data["estado"], DocumentoEstado.Nombre.FIRMADO)
        self.assertEqual(len(resp.data["cune"]), 96)

        self.nomina.refresh_from_db()
        self.assertTrue(self.nomina.xml_archivo)
        # El ambiente queda sellado en la nómina: lo que entra en el CUNE tiene
        # que ser lo mismo que después decide a qué servidor se envía.
        self.assertEqual(self.nomina.ambiente, 2)

    def test_no_se_emite_dos_veces(self):
        self._emitir()
        resp = self._emitir()
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("firmada", resp.data["detail"])

    def test_no_se_envia_sin_firmar(self):
        resp = self.client.post(self._url("enviar/"))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("no está firmada", resp.data["detail"])

    def test_enviar_aceptado_deja_la_nomina_aceptada(self):
        from unittest.mock import patch

        self._emitir()
        cliente = ClienteNominaFalso(_respuesta())
        with patch("apps.dian.servicios_nomina.construir_cliente_emisor", return_value=cliente):
            resp = self.client.post(self._url("enviar/"))

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertTrue(resp.data["es_valido"])
        self.nomina.refresh_from_db()
        self.assertEqual(self.nomina.estado.nombre, DocumentoEstado.Nombre.ACEPTADO)
        self.assertIsNotNone(self.nomina.fecha_validacion)

    def test_en_habilitacion_sale_por_el_set_de_pruebas(self):
        """Con el Set sin aceptar y ambiente 2, va por ``SendTestSetAsync``.

        Y queda anotado en ``envio``, que es lo que decide cómo se consulta
        después: el Set de Pruebas es asíncrono y se pregunta por ZipKey.
        """
        from unittest.mock import patch

        self._emitir()
        cliente = ClienteNominaFalso(_respuesta())
        with patch("apps.dian.servicios_nomina.construir_cliente_emisor", return_value=cliente):
            self.client.post(self._url("enviar/"))

        self.assertEqual(cliente.llamadas[0][0], "set_pruebas")
        self.assertEqual(cliente.llamadas[0][2], "set-de-pruebas-nomina")
        self.nomina.refresh_from_db()
        self.assertEqual(self.nomina.envio, Nomina.Envio.SET_PRUEBAS)

    def test_con_el_set_aceptado_sale_por_el_sincrono(self):
        from unittest.mock import patch

        self.base["software"].set_pruebas_aceptado = True
        self.base["software"].save(update_fields=["set_pruebas_aceptado"])
        self._emitir()
        cliente = ClienteNominaFalso(_respuesta())
        with patch("apps.dian.servicios_nomina.construir_cliente_emisor", return_value=cliente):
            self.client.post(self._url("enviar/"))

        self.assertEqual(cliente.llamadas[0][0], "sincrono")
        self.nomina.refresh_from_db()
        self.assertEqual(self.nomina.envio, Nomina.Envio.SINCRONO)

    def test_el_rechazo_guarda_los_errores(self):
        from unittest.mock import patch

        self._emitir()
        cliente = ClienteNominaFalso(_respuesta(
            es_valido=False,
            errores=["Regla: NIE001, Rechazo: El CUNE no corresponde."],
        ))
        with patch("apps.dian.servicios_nomina.construir_cliente_emisor", return_value=cliente):
            resp = self.client.post(self._url("enviar/"))

        self.assertFalse(resp.data["es_valido"])
        self.nomina.refresh_from_db()
        self.assertEqual(self.nomina.estado.nombre, DocumentoEstado.Nombre.RECHAZADO)
        error = self.nomina.errores.get()
        self.assertEqual(error.regla, "NIE001")

    def test_consultar_aplica_el_veredicto_que_el_envio_no_trajo(self):
        """El envío asíncrono solo devuelve un ZipKey; el rechazo llega al consultar.

        Sin esto una nómina rechazada se quedaría en ``enviado`` y con cero
        errores para siempre.
        """
        from unittest.mock import patch

        self._emitir()
        # Envío al Set de Pruebas: sin veredicto (ni válido ni con errores).
        sin_veredicto = _respuesta(es_valido=False, errores=[])
        cliente = ClienteNominaFalso(sin_veredicto)
        with patch("apps.dian.servicios_nomina.construir_cliente_emisor", return_value=cliente):
            self.client.post(self._url("enviar/"))
        self.nomina.refresh_from_db()
        self.assertEqual(self.nomina.estado.nombre, DocumentoEstado.Nombre.ENVIADO)

        # Y al consultar, la DIAN ya tiene el resultado.
        cliente = ClienteNominaFalso(_respuesta())
        with patch("apps.dian.servicios_nomina.construir_cliente_emisor", return_value=cliente):
            resp = self.client.get(self._url("consultar/"))

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.nomina.refresh_from_db()
        self.assertEqual(self.nomina.estado.nombre, DocumentoEstado.Nombre.ACEPTADO)
        # Salió por el Set de Pruebas, así que se pregunta por el ZipKey.
        self.assertEqual(cliente.llamadas[0][0], "estado_zip")


class NominaAlcanceTests(NominaAPIBase):
    """Una cuenta no ve las nóminas de otra."""

    def test_la_nomina_de_otro_emisor_no_existe(self):
        otra_base = crear_emisor_de_nomina(self.base["catalogos"], nit="800199436")
        ajena, _ = crear_nomina(otra_base)

        resp = self.client.get(self._url(nomina=ajena))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_el_listado_solo_trae_las_suyas(self):
        otra_base = crear_emisor_de_nomina(self.base["catalogos"], nit="800199436")
        crear_nomina(otra_base)

        resp = self.client.get("/api/nomina/nomina/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        emisores = {fila["emisor"] for fila in resp.data["results"]}
        self.assertEqual(emisores, {self.emisor.id})
