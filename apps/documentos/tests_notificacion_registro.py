"""El registro de las notificaciones del documento (`doc_documento_notificacion`)."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from apps.documentos.models import DocumentoNotificacion
from apps.documentos.servicios import (
    ErrorEnvioCorreo, ErrorNotificacion, empaquetar_notificacion, enviar_notificacion,
)
from apps.documentos.servicios.notificacion import Paquete
from apps.documentos.tests_utils import crear_documento_factura
from apps.nucleo.tests_utils import errores_por_campo
from apps.utilidades.zinc import ZincNoDisponible

EMPAQUETAR = "apps.documentos.servicios.notificacion.empaquetar_notificacion"


def _paquete():
    # Armar el de verdad exige el XML firmado en B2; aquí lo que se prueba es
    # el registro, no el zip.
    return Paquete(
        nombre="z0901192048000260000001.zip", contenido=b"x" * 42,
        tipo="application/zip", destinatario="compras@cliente.co; pagos@cliente.co",
        archivos=["ad0901192048000260000001.xml", "z0901192048000260000001.pdf"],
    )


class RegistroTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        datos = crear_documento_factura()
        cls.documento = datos["documento"]
        cls.documento.emisor.correo_copia = "archivo@emisor.co"
        cls.documento.emisor.save(update_fields=["correo_copia"])

    def _enviar(self, zinc):
        with mock.patch(EMPAQUETAR, return_value=_paquete()):
            return enviar_notificacion(self.documento, zinc=zinc)

    def test_un_envio_que_sale_deja_su_fila(self):
        zinc = mock.Mock()
        zinc.correo_html.return_value = {"error": False, "codigoEnvio": 777}

        self._enviar(zinc)

        fila = DocumentoNotificacion.objects.get()
        self.assertEqual(fila.estado, DocumentoNotificacion.Estado.ENVIADO)
        self.assertEqual(fila.destinatario, "compras@cliente.co;pagos@cliente.co")
        self.assertEqual(fila.copia, "archivo@emisor.co")
        self.assertEqual(fila.archivo, "z0901192048000260000001.zip")
        self.assertEqual(fila.tamano, 42)
        self.assertEqual(fila.contenido, _paquete().archivos)
        self.assertEqual(fila.codigo_envio, "777")
        self.assertEqual(fila.error, "")
        self.documento.refresh_from_db()
        self.assertTrue(self.documento.notificado)

    def test_un_rechazo_de_zinc_deja_fila_fallida(self):
        zinc = mock.Mock()
        zinc.correo_html.return_value = {"error": True, "errorMensaje": "correo bloqueado"}

        with self.assertRaises(ErrorEnvioCorreo):
            self._enviar(zinc)

        fila = DocumentoNotificacion.objects.get()
        self.assertEqual(fila.estado, DocumentoNotificacion.Estado.FALLIDO)
        self.assertEqual(fila.error, "correo bloqueado")
        self.assertEqual(fila.codigo_envio, "")
        self.documento.refresh_from_db()
        self.assertFalse(self.documento.notificado)

    def test_zinc_caido_deja_fila_fallida(self):
        zinc = mock.Mock()
        zinc.correo_html.side_effect = ZincNoDisponible("sin red")

        with self.assertRaises(ZincNoDisponible):
            self._enviar(zinc)

        fila = DocumentoNotificacion.objects.get()
        self.assertEqual(fila.estado, DocumentoNotificacion.Estado.FALLIDO)
        self.assertIn("sin red", fila.error)

    def test_lo_que_no_se_puede_armar_no_deja_fila(self):
        zinc = mock.Mock()

        with mock.patch(EMPAQUETAR, side_effect=ErrorNotificacion("sin correo")), \
                self.assertRaises(ErrorNotificacion):
            enviar_notificacion(self.documento, zinc=zinc)

        zinc.correo_html.assert_not_called()
        self.assertFalse(DocumentoNotificacion.objects.exists())

    def test_cada_intento_es_una_fila(self):
        zinc = mock.Mock()
        zinc.correo_html.side_effect = [
            ZincNoDisponible("sin red"), {"error": False, "codigoEnvio": 1},
        ]

        with self.assertRaises(ZincNoDisponible):
            self._enviar(zinc)
        self._enviar(zinc)

        self.assertEqual(
            list(DocumentoNotificacion.objects.values_list("estado", flat=True)),
            ["fallido", "enviado"],
        )


class CorreoTests(TestCase):
    """El `correo` opcional de la notificación: reemplaza el del adquiriente."""

    @classmethod
    def setUpTestData(cls):
        cls.documento = crear_documento_factura()["documento"]
        cls.documento.adquiriente.correo = "viejo@cliente.co"
        cls.documento.adquiriente.save(update_fields=["correo"])

    def _correo(self):
        self.documento.adquiriente.refresh_from_db()
        return self.documento.adquiriente.correo

    def _enviar(self, zinc, **kwargs):
        with mock.patch(EMPAQUETAR, return_value=_paquete()) as empaquetar:
            try:
                enviar_notificacion(self.documento, zinc=zinc, **kwargs)
            finally:
                self.empaquetar = empaquetar

    def _zinc(self, **kwargs):
        zinc = mock.Mock()
        zinc.correo_html.return_value = {"error": False, "codigoEnvio": 1}
        zinc.correo_html.configure_mock(**kwargs)
        return zinc

    def test_el_correo_que_viene_reemplaza_al_del_adquiriente(self):
        self._enviar(self._zinc(), correo="nuevo@cliente.co")

        self.assertEqual(self._correo(), "nuevo@cliente.co")
        self.assertEqual(self.empaquetar.call_args.kwargs["correo"], "nuevo@cliente.co")

    def test_sin_correo_o_vacio_se_queda_el_que_habia(self):
        for correo in (None, "", "  "):
            with self.subTest(correo=correo):
                self._enviar(self._zinc(), correo=correo)

                self.assertEqual(self._correo(), "viejo@cliente.co")

    def test_se_guarda_aunque_la_pasarela_falle(self):
        # Es el dato bueno: el reintento tiene que usarlo.
        with self.assertRaises(ZincNoDisponible):
            self._enviar(self._zinc(side_effect=ZincNoDisponible("sin red")), correo="nuevo@cliente.co")

        self.assertEqual(self._correo(), "nuevo@cliente.co")

    def test_si_el_paquete_no_se_arma_no_se_guarda(self):
        with mock.patch(EMPAQUETAR, side_effect=ErrorNotificacion("sin aceptar")), \
                self.assertRaises(ErrorNotificacion):
            enviar_notificacion(self.documento, zinc=mock.Mock(), correo="nuevo@cliente.co")

        self.assertEqual(self._correo(), "viejo@cliente.co")

    def test_el_paquete_va_al_correo_que_viene(self):
        # Sin tocar el zip, que necesita el XML firmado: basta con saltarse las
        # comprobaciones previas y el contenedor.
        self.documento.xml_archivo = "x.xml"
        self.documento.estado_id = None
        with mock.patch(
            "apps.documentos.servicios.notificacion._attached_document", return_value=b"<xml/>",
        ):
            paquete = empaquetar_notificacion(self.documento, correo=" nuevo@cliente.co ")

        self.assertEqual(paquete.destinatario, "nuevo@cliente.co")
        # Armar no guarda: eso es de enviar.
        self.assertEqual(self._correo(), "viejo@cliente.co")


class NotificarCorreoAPITests(APITestCase):
    ENVIAR = "apps.documentos.views.documento.enviar_notificacion"

    @classmethod
    def setUpTestData(cls):
        datos = crear_documento_factura()
        cls.documento = datos["documento"]
        cls.usuario = get_user_model().objects.create_user(email="correo@nobelio.co", password="x")
        cls.usuario.emisores.add(datos["emisor"])

    def setUp(self):
        self.client.force_authenticate(self.usuario)

    def _notificar(self, datos, query=""):
        return self.client.post(
            f"/api/documentos/documento/{self.documento.pk}/notificar/{query}",
            datos, format="multipart",
        )

    def test_pasa_el_correo_al_envio(self):
        with mock.patch(self.ENVIAR, return_value=(_paquete(), {"codigoEnvio": 1})) as enviar:
            resp = self._notificar({"correo": "nuevo@cliente.co"})

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(enviar.call_args.kwargs["correo"], "nuevo@cliente.co")

    def test_un_correo_invalido_es_400_y_no_envia(self):
        with mock.patch(self.ENVIAR) as enviar:
            resp = self._notificar({"correo": "no-es-correo"})

        self.assertEqual(resp.status_code, 400)
        self.assertIn("correo", errores_por_campo(resp))
        enviar.assert_not_called()

    def test_descargar_no_guarda_el_correo(self):
        with mock.patch(
            "apps.documentos.views.documento.empaquetar_notificacion", return_value=_paquete(),
        ) as empaquetar, mock.patch(self.ENVIAR) as enviar:
            resp = self._notificar({"correo": "nuevo@cliente.co"}, query="?descargar=1")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(empaquetar.call_args.kwargs["correo"], "nuevo@cliente.co")
        enviar.assert_not_called()
        self.documento.adquiriente.refresh_from_db()
        self.assertNotEqual(self.documento.adquiriente.correo, "nuevo@cliente.co")


class DocumentoNotificacionAPITests(APITestCase):
    URL = "/api/documentos/documento-notificacion/"

    @classmethod
    def setUpTestData(cls):
        datos = crear_documento_factura()
        cls.documento = datos["documento"]
        cls.usuario = get_user_model().objects.create_user(email="notif@nobelio.co", password="x")
        cls.usuario.emisores.add(datos["emisor"])
        for estado in ("fallido", "enviado"):
            DocumentoNotificacion.objects.create(
                documento=cls.documento, estado=estado, destinatario="a@b.co",
                archivo="z.zip", tamano=1,
            )

    def test_lista_las_del_documento_y_filtra_por_estado(self):
        self.client.force_authenticate(self.usuario)

        todas = self.client.get(self.URL, {"documento": str(self.documento.pk)})
        enviadas = self.client.get(self.URL, {"estado": "enviado"})

        self.assertEqual(todas.status_code, 200, todas.data)
        self.assertEqual([f["estado"] for f in todas.data["results"]], ["fallido", "enviado"])
        self.assertEqual(len(enviadas.data["results"]), 1)

    def test_otra_cuenta_no_las_ve(self):
        otro = get_user_model().objects.create_user(email="otro@nobelio.co", password="x")
        self.client.force_authenticate(otro)

        self.assertEqual(self.client.get(self.URL).data["results"], [])

    def test_es_de_solo_lectura(self):
        self.client.force_authenticate(self.usuario)

        self.assertEqual(self.client.post(self.URL, {}, format="json").status_code, 405)
