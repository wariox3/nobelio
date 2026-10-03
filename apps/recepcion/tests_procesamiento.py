"""La tarea ``procesar_correo``: cabeceras del MIME, confirmación de Gmail y
reintentos ante fallos de R2."""
from unittest import mock

from botocore.exceptions import ClientError, EndpointConnectionError
from django.test import TestCase, override_settings

from apps.recepcion import r2
from apps.recepcion.models import Correo
from apps.recepcion.tareas import procesar_correo

MIME = (
    "From: Proveedor <facturas@proveedor.example>\r\n"
    "To: 901192048@recepcion.rededoc.co\r\n"
    "Subject: =?utf-8?q?Factura_electr=C3=B3nica_FE-100?=\r\n"
    "Message-ID: <abc123@proveedor.example>\r\n"
    "\r\n"
    "Adjunto la factura.\r\n"
).encode()

MIME_GMAIL = (
    "From: Equipo de Gmail <forwarding-noreply@google.com>\r\n"
    "To: 901192048@recepcion.rededoc.co\r\n"
    "Subject: (#123456789) =?utf-8?q?Confirmaci=C3=B3n_de_reenv=C3=ADo?=\r\n"
    "Message-ID: <gmail1@google.com>\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "\r\n"
    "Para permitir el reenvio, haz clic en el enlace:\r\n"
    "https://mail-settings.google.com/mail/vf-%5BANGjdJ%5D-abc.\r\n"
).encode()


def error_r2(codigo):
    return ClientError({"Error": {"Code": codigo}}, "GetObject")


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class ProcesarCorreoTests(TestCase):
    def setUp(self):
        self.correo = Correo.objects.create(
            alias="901192048", sha256="a" * 64, raw_key="2026-10-03/x.eml",
            envelope_to="901192048@recepcion.rededoc.co",
        )
        parche = mock.patch("apps.recepcion.r2.descargar_mime", return_value=MIME)
        self.descargar = parche.start()
        self.addCleanup(parche.stop)

    def procesar(self):
        procesar_correo.delay(self.correo.pk)
        self.correo.refresh_from_db()

    def test_guarda_asunto_y_message_id(self):
        self.procesar()

        self.descargar.assert_called_once_with("2026-10-03/x.eml")
        self.assertEqual(self.correo.estado, Correo.Estado.PROCESADO)
        self.assertEqual(self.correo.asunto, "Factura electrónica FE-100")
        self.assertEqual(self.correo.message_id, "<abc123@proveedor.example>")
        self.assertEqual(self.correo.intentos, 1)

    def test_reconoce_la_confirmacion_de_reenvio_de_gmail(self):
        self.descargar.return_value = MIME_GMAIL

        self.procesar()

        self.assertEqual(self.correo.estado, Correo.Estado.CONFIRMACION_REENVIO)
        self.assertEqual(self.correo.asunto, "(#123456789) Confirmación de reenvío")
        self.assertEqual(
            self.correo.confirmacion_reenvio,
            "codigo: 123456789\n"
            "enlace: https://mail-settings.google.com/mail/vf-%5BANGjdJ%5D-abc",
        )

    def test_lo_ya_procesado_no_se_vuelve_a_procesar(self):
        self.procesar()
        self.procesar()

        self.assertEqual(self.descargar.call_count, 1)
        self.assertEqual(self.correo.intentos, 1)

    def test_un_correo_eliminado_no_falla(self):
        pk = self.correo.pk
        self.correo.delete()

        procesar_correo.delay(pk)

        self.descargar.assert_not_called()

    def test_sin_el_mime_en_r2_queda_en_error_sin_reintentar(self):
        self.descargar.side_effect = error_r2("NoSuchKey")

        self.procesar()

        self.assertEqual(self.correo.estado, Correo.Estado.ERROR)
        self.assertIn("no está en R2", self.correo.error_detalle)
        self.assertEqual(self.correo.intentos, 1)

    def test_credenciales_rechazadas_queda_en_error_sin_reintentar(self):
        self.descargar.side_effect = error_r2("AccessDenied")

        self.procesar()

        self.assertEqual(self.correo.estado, Correo.Estado.ERROR)
        self.assertEqual(self.correo.intentos, 1)

    def test_sin_r2_configurado_queda_en_error(self):
        self.descargar.side_effect = r2.R2NoConfigurado

        self.procesar()

        self.assertEqual(self.correo.estado, Correo.Estado.ERROR)
        self.assertIn("R2_", self.correo.error_detalle)

    def test_un_fallo_transitorio_se_reintenta_y_luego_se_procesa(self):
        self.descargar.side_effect = [EndpointConnectionError(endpoint_url="r2"), MIME]

        self.procesar()

        self.assertEqual(self.correo.estado, Correo.Estado.PROCESADO)
        self.assertEqual(self.correo.intentos, 2)
        self.assertEqual(self.correo.error_detalle, "")

    def test_agotados_los_reintentos_queda_en_error(self):
        self.descargar.side_effect = EndpointConnectionError(endpoint_url="r2")

        self.procesar()

        self.assertEqual(self.correo.estado, Correo.Estado.ERROR)
        self.assertIn("No se pudo conectar con R2", self.correo.error_detalle)
        # El primero más los cinco reintentos.
        self.assertEqual(self.correo.intentos, 6)

    def test_un_correo_en_error_se_puede_reprocesar(self):
        Correo.objects.filter(pk=self.correo.pk).update(estado=Correo.Estado.ERROR)

        self.procesar()

        self.assertEqual(self.correo.estado, Correo.Estado.PROCESADO)
