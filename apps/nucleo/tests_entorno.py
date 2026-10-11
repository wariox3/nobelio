"""Pruebas del seguro de `ENV`: fuera de `prod` no existe el ambiente 1."""
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from apps.dian import soap
from apps.dian.tests_firma import _generar_certificado
from apps.documentos.models import Documento
from apps.documentos.tests_utils import crear_documento_factura
from apps.nomina.models import Nomina
from apps.nomina.tests_utils import crear_nomina
from apps.nucleo.entorno import AmbienteBloqueado
from apps.nucleo.models import Ambiente

URL_PRODUCCION = "https://vpfe.dian.gov.co/WcfDianCustomerServices.svc"
URL_HABILITACION = "https://vpfe-hab.dian.gov.co/WcfDianCustomerServices.svc"


def _copia(documento, **cambios):
    """Los datos de ``documento`` para crear otro igual con otro consecutivo."""
    datos = {
        campo.attname: getattr(documento, campo.attname)
        for campo in Documento._meta.concrete_fields
        if campo.attname not in ("id", "numero", "ambiente", "creado_en", "actualizado_en")
    }
    datos["consecutivo"] = documento.consecutivo + 1
    datos.update(cambios)
    return datos


class CrearEnAmbienteDeProduccionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.documento = crear_documento_factura()["documento"]

    def test_no_crea_un_documento_con_ambiente_1(self):
        for entorno in ("dev", "test"):
            with self.subTest(entorno=entorno), override_settings(ENV=entorno):
                with self.assertRaises(AmbienteBloqueado):
                    Documento.objects.create(
                        **_copia(self.documento, ambiente=Ambiente.PRODUCCION)
                    )
        self.assertEqual(Documento.objects.count(), 1)

    def test_sin_ambiente_no_se_crea(self):
        # Ya no se hereda del emisor: tiene que venir dicho.
        with self.assertRaises(ValueError):
            Documento.objects.create(**_copia(self.documento))

    def test_en_prod_si_lo_crea(self):
        with override_settings(ENV="prod"):
            documento = Documento.objects.create(
                **_copia(self.documento, ambiente=Ambiente.PRODUCCION)
            )

        self.assertEqual(documento.ambiente, Ambiente.PRODUCCION)

    def test_un_documento_que_ya_existe_se_puede_seguir_guardando(self):
        # Una base copiada de producción trae documentos del ambiente 1: se
        # pueden leer y editar; lo que no se puede es enviarlos.
        Documento.objects.filter(pk=self.documento.pk).update(
            ambiente=Ambiente.PRODUCCION
        )
        self.documento.refresh_from_db()

        self.documento.save()


class CrearNominaEnAmbienteDeProduccionTests(TestCase):
    def test_no_crea_una_nomina_con_ambiente_1(self):
        nomina, _ = crear_nomina()
        otra = Nomina.objects.get(pk=nomina.pk)
        otra.pk = None
        otra._state.adding = True
        otra.numero = ""
        otra.consecutivo += 1
        otra.ambiente = Ambiente.PRODUCCION

        with self.assertRaises(AmbienteBloqueado):
            otra.save()
        self.assertEqual(Nomina.objects.count(), 1)


class EnviarAProduccionTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.llave, cls.cert = _generar_certificado()

    def _cliente(self, url, **kwargs):
        return soap.ClienteDian(url, self.llave, self.cert, **kwargs)

    def _envios(self, cliente):
        return [
            lambda: cliente.enviar_set_pruebas(b"<x/>", "fac.xml", "abc"),
            lambda: cliente.enviar_factura_sincrono(b"<x/>", "fac.xml"),
            lambda: cliente.enviar_nomina_sincrono(b"<x/>", "nom.xml"),
            lambda: cliente.enviar_evento(b"<x/>", "ev.xml"),
        ]

    def test_ningun_envio_sale_hacia_el_servidor_de_produccion(self):
        cliente = self._cliente(URL_PRODUCCION)
        with patch.object(soap.ClienteDian, "_post") as post:
            for enviar in self._envios(cliente):
                with self.assertRaises(AmbienteBloqueado):
                    enviar()
        post.assert_not_called()

    def test_ni_con_ambiente_1_aunque_la_url_sea_otra(self):
        cliente = self._cliente(URL_HABILITACION, ambiente=Ambiente.PRODUCCION)
        with patch.object(soap.ClienteDian, "_post") as post:
            for enviar in self._envios(cliente):
                with self.assertRaises(AmbienteBloqueado):
                    enviar()
        post.assert_not_called()

    def test_a_habilitacion_si_envia(self):
        cliente = self._cliente(URL_HABILITACION, ambiente=Ambiente.PRUEBAS)
        with patch.object(soap.ClienteDian, "_post", return_value=b"<r/>") as post:
            cliente.enviar_factura_sincrono(b"<x/>", "fac.xml")
        post.assert_called_once()

    @override_settings(ENV="prod")
    def test_en_prod_si_envia_a_produccion(self):
        cliente = self._cliente(URL_PRODUCCION, ambiente=Ambiente.PRODUCCION)
        with patch.object(soap.ClienteDian, "_post", return_value=b"<r/>") as post:
            cliente.enviar_factura_sincrono(b"<x/>", "fac.xml")
        post.assert_called_once()

    def test_las_consultas_a_produccion_siguen_permitidas(self):
        # Leer no emite nada: es lo que deja importar la resolución real.
        cliente = self._cliente(URL_PRODUCCION, ambiente=Ambiente.PRODUCCION)
        with patch.object(soap.ClienteDian, "_post", return_value=b"<r/>") as post:
            cliente.consultar_rangos_numeracion("901192048", "901192048", "sw")
        post.assert_called_once()
