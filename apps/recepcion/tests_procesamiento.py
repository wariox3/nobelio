"""La tarea ``procesar_correo``: cabeceras del MIME, confirmación de Gmail,
reintentos ante fallos de R2 y registro de los documentos."""
from decimal import Decimal
from unittest import mock

from botocore.exceptions import ClientError, EndpointConnectionError
from django.test import TestCase, override_settings

from apps.documentos.tests_utils import crear_catalogos_minimos
from apps.emisores.models import Emisor
from apps.recepcion import extraccion, r2
from apps.recepcion.models import Correo, Documento
from apps.recepcion.tareas import procesar_correo
from apps.recepcion.tests_utils import (
    CUFE, PDF, correo_con, xml_attached, xml_documento, zip_con,
)

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
        self.assertEqual(self.correo.estado, Correo.Estado.SIN_DOCUMENTOS)
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

        self.assertEqual(self.correo.estado, Correo.Estado.SIN_DOCUMENTOS)
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

        self.assertEqual(self.correo.estado, Correo.Estado.SIN_DOCUMENTOS)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class RegistrarDocumentosTests(TestCase):
    """Los documentos del correo: a qué emisor van, repetidos y estado final."""

    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.emisor = self.crear_emisor("901192048")
        self.otro = self.crear_emisor("900555666")
        parche = mock.patch("apps.recepcion.r2.descargar_mime")
        self.descargar = parche.start()
        self.addCleanup(parche.stop)

    def crear_emisor(self, nit):
        c = self.cat
        return Emisor.objects.create(
            usuario=c["usuario"], razon_social=f"Empresa {nit}",
            tipo_identificacion=c["nit"], numero_identificacion=nit,
            tipo_organizacion=c["juridica"], pais=c["colombia"],
            departamento=c["antioquia"], municipio=c["medellin"],
            direccion="Calle 1 # 2-3",
        )

    def procesar(self, mime, *, emisor=None, huella="a"):
        correo = Correo.objects.create(
            alias=emisor.numero_identificacion if emisor else "desconocido",
            emisor=emisor, sha256=huella * 64, raw_key=f"2026-10-03/{huella}.eml",
            envelope_to="x@recepcion.rededoc.co",
        )
        self.descargar.return_value = mime
        procesar_correo.delay(correo.pk)
        correo.refresh_from_db()
        return correo

    def test_guarda_el_documento_con_sus_archivos(self):
        mime = correo_con(("fe.zip", zip_con(fe__xml=xml_attached(), fe__pdf=PDF), "application/zip"))

        correo = self.procesar(mime, emisor=self.emisor)

        self.assertEqual(correo.estado, Correo.Estado.PROCESADO)
        doc = Documento.objects.get()
        self.assertEqual(doc.emisor, self.emisor)
        self.assertEqual(doc.correo, correo)
        self.assertEqual(doc.cufe_cude, CUFE)
        self.assertEqual(doc.documento_tipo.codigo, "factura_venta")
        self.assertEqual(doc.moneda, self.cat["cop"])
        self.assertEqual(doc.total_a_pagar, Decimal("119800.00"))
        self.assertEqual(doc.validacion_codigo, "02")
        self.assertTrue(doc.xml_archivo.name.startswith(f"{self.emisor.pk}/recepcion/2026/10/FE-100-"))
        self.assertEqual(doc.xml_archivo.read(), xml_attached())
        self.assertTrue(doc.xml_factura_archivo.read().startswith(b"<?xml"))
        self.assertEqual(doc.pdf_archivo.read(), PDF)

    def test_el_emisor_lo_decide_el_nit_receptor_del_xml(self):
        mime = correo_con(("fe.xml", xml_attached(xml_documento(nit_receptor="900555666")), "text/xml"))

        self.procesar(mime, emisor=self.emisor)

        self.assertEqual(Documento.objects.get().emisor, self.otro)

    def test_un_correo_sin_emisor_toma_el_del_xml(self):
        mime = correo_con(("fe.xml", xml_attached(), "text/xml"))

        correo = self.procesar(mime)

        self.assertEqual(correo.emisor, self.emisor)
        self.assertEqual(correo.estado, Correo.Estado.PROCESADO)

    def test_receptor_desconocido_no_guarda_nada(self):
        mime = correo_con(("fe.xml", xml_attached(xml_documento(nit_receptor="811000111")), "text/xml"))

        correo = self.procesar(mime)

        self.assertEqual(correo.estado, Correo.Estado.EMPRESA_DESCONOCIDA)
        self.assertIsNone(correo.emisor)
        self.assertFalse(Documento.objects.exists())

    def test_un_cufe_repetido_se_ignora(self):
        mime = correo_con(("fe.xml", xml_attached(), "text/xml"))
        self.procesar(mime, emisor=self.emisor, huella="a")

        segundo = self.procesar(mime + b" ", emisor=self.emisor, huella="b")

        self.assertEqual(segundo.estado, Correo.Estado.PROCESADO)
        self.assertEqual(Documento.objects.count(), 1)
        self.assertFalse(segundo.documentos.exists())

    def test_el_mismo_documento_dos_veces_en_un_correo_se_guarda_una(self):
        mime = correo_con(
            ("ad.xml", xml_attached(), "text/xml"),
            ("factura.xml", xml_documento(), "text/xml"),
        )

        self.procesar(mime, emisor=self.emisor)

        self.assertEqual(Documento.objects.count(), 1)

    def test_contenido_excesivo_queda_en_error(self):
        mime = correo_con(("fe.xml", xml_attached(), "text/xml"))

        with mock.patch.object(extraccion, "MAXIMOS_BYTES", 10):
            correo = self.procesar(mime, emisor=self.emisor)

        self.assertEqual(correo.estado, Correo.Estado.ERROR)
        self.assertFalse(Documento.objects.exists())

    def test_si_b2_falla_no_deja_el_documento_a_medias(self):
        mime = correo_con(("fe.zip", zip_con(fe__xml=xml_attached(), fe__pdf=PDF), "application/zip"))
        falla = EndpointConnectionError(endpoint_url="b2")
        with mock.patch("django.db.models.fields.files.FieldFile.save", side_effect=falla):
            correo = self.procesar(mime, emisor=self.emisor)

        # Agotados los reintentos queda en error, sin documentos ni archivos.
        self.assertEqual(correo.estado, Correo.Estado.ERROR)
        self.assertIn("B2", correo.error_detalle)
        self.assertFalse(Documento.objects.exists())
