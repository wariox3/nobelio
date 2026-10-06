"""El comando ``reprocesar_correos`` y ``procesamiento.reprocesar``."""
from datetime import timedelta
from io import StringIO
from unittest import mock

from botocore.exceptions import EndpointConnectionError
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.documentos.tests_utils import crear_catalogos_minimos
from apps.emisores.models import Emisor
from apps.recepcion import procesamiento
from apps.recepcion.models import Adjunto, Correo, Documento
from apps.recepcion.tareas import procesar_correo
from apps.recepcion.tests_utils import PDF, correo_con, xml_attached, xml_documento, zip_con

NIT = "901192048"
# Una factura para NIT, que todavía no está registrado.
MIME = correo_con(("fe.zip", zip_con(fe__xml=xml_attached(), fe__pdf=PDF), "application/zip"))


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class ReprocesarCorreosTests(TestCase):
    def setUp(self):
        self.cat = crear_catalogos_minimos()
        parche = mock.patch("apps.recepcion.r2.descargar_mime", return_value=MIME)
        self.descargar = parche.start()
        self.addCleanup(parche.stop)

    def crear_emisor(self, nit=NIT):
        c = self.cat
        return Emisor.objects.create(
            usuario=c["usuario"], razon_social=f"Empresa {nit}",
            tipo_identificacion=c["nit"], numero_identificacion=nit,
            tipo_organizacion=c["juridica"], pais=c["colombia"],
            departamento=c["antioquia"], municipio=c["medellin"],
            direccion="Calle 1 # 2-3",
        )

    def crear_correo(self, *, huella="a", alias=NIT, estado=None, hace=timedelta(hours=1)):
        correo = Correo.objects.create(
            alias=alias, sha256=huella * 64, raw_key=f"2026-10-03/{huella}.eml",
            envelope_to=f"{alias}@recepcion.rededoc.co",
            recibido_en=timezone.now() - hace,
        )
        if estado is not None:
            Correo.objects.filter(pk=correo.pk).update(estado=estado)
        return correo

    def recibido_sin_emisor(self, **kwargs):
        """Un correo procesado antes de que registraran a su emisor."""
        correo = self.crear_correo(**kwargs)
        procesar_correo.delay(correo.pk)
        correo.refresh_from_db()
        self.assertEqual(correo.estado, Correo.Estado.EMPRESA_DESCONOCIDA)
        return correo

    def comando(self, *args):
        salida = StringIO()
        call_command("reprocesar_correos", *args, stdout=salida)
        return salida.getvalue()

    def test_empresa_desconocida_registrada_despues_guarda_el_documento(self):
        correo = self.recibido_sin_emisor()
        viejos = list(correo.adjuntos.all())
        self.assertEqual([a.rol for a in viejos], ["otro", "otro"])
        emisor = self.crear_emisor()

        salida = self.comando("--id", str(correo.pk))

        correo.refresh_from_db()
        self.assertEqual(correo.estado, Correo.Estado.PROCESADO)
        self.assertEqual(correo.emisor, emisor)
        self.assertEqual(Documento.objects.get().emisor, emisor)
        # El "otro" del primer intento se borró, de la base y del almacenamiento.
        self.assertEqual(
            sorted(a.rol for a in correo.adjuntos.all()), ["pdf", "xml", "xml_documento"],
        )
        for viejo in viejos:
            self.assertFalse(viejo.archivo.storage.exists(viejo.archivo.name))
        for adjunto in correo.adjuntos.all():
            self.assertTrue(adjunto.archivo.name.startswith(f"{emisor.pk}/recepcion/"))
        self.assertIn(f"correo {correo.pk}: procesado", salida)

    def test_toma_el_emisor_del_alias_aunque_el_xml_sea_de_otro(self):
        self.descargar.return_value = correo_con(
            ("fe.xml", xml_attached(xml_documento(nit_receptor="811000111")), "text/xml"),
        )
        correo = self.recibido_sin_emisor()
        emisor = self.crear_emisor()

        self.comando("--id", str(correo.pk))

        correo.refresh_from_db()
        self.assertEqual(correo.estado, Correo.Estado.EMPRESA_DESCONOCIDA)
        self.assertEqual(correo.emisor, emisor)
        self.assertEqual(correo.adjuntos.count(), 1)

    def test_un_correo_en_error_se_reprocesa(self):
        self.crear_emisor()
        correo = self.crear_correo(estado=Correo.Estado.ERROR)

        self.comando("--id", str(correo.pk))

        correo.refresh_from_db()
        self.assertEqual(correo.estado, Correo.Estado.PROCESADO)
        self.assertEqual(correo.error_detalle, "")

    def test_un_correo_procesado_no_se_reprocesa(self):
        self.crear_emisor()
        correo = self.crear_correo()
        procesar_correo.delay(correo.pk)

        with self.assertRaisesMessage(CommandError, "está procesado"):
            self.comando("--id", str(correo.pk))

        self.assertEqual(Documento.objects.count(), 1)
        self.assertEqual(self.descargar.call_count, 1)

    def test_un_correo_con_documentos_no_se_reprocesa(self):
        emisor = self.crear_emisor()
        correo = self.crear_correo()
        procesar_correo.delay(correo.pk)
        Correo.objects.filter(pk=correo.pk).update(estado=Correo.Estado.ERROR)

        with self.assertRaisesMessage(CommandError, "ya tiene documentos"):
            self.comando("--id", str(correo.pk))

        self.assertEqual(Documento.objects.get().emisor, emisor)
        self.assertEqual(correo.adjuntos.count(), 3)

    def test_un_id_que_no_existe(self):
        with self.assertRaisesMessage(CommandError, "No existe el correo 999"):
            self.comando("--id", "999")

    def test_si_r2_falla_queda_pendiente_y_sin_adjuntos(self):
        correo = self.recibido_sin_emisor()
        self.crear_emisor()
        self.descargar.side_effect = EndpointConnectionError(endpoint_url="r2")

        salida = self.comando("--id", str(correo.pk))

        correo.refresh_from_db()
        self.assertEqual(correo.estado, Correo.Estado.PENDIENTE)
        self.assertFalse(Adjunto.objects.exists())
        self.assertIn("queda pendiente", salida)
        # Y se puede volver a intentar.
        self.descargar.side_effect = None
        self.comando("--id", str(correo.pk))
        correo.refresh_from_db()
        self.assertEqual(correo.estado, Correo.Estado.PROCESADO)

    def test_todos_reprocesa_solo_los_reprocesables(self):
        desconocida = self.recibido_sin_emisor(huella="a")
        self.crear_emisor()
        error = self.crear_correo(huella="b", estado=Correo.Estado.ERROR)
        pendiente_viejo = self.crear_correo(huella="c")
        pendiente_nuevo = self.crear_correo(huella="d", hace=timedelta(minutes=5))
        procesado = self.crear_correo(huella="e", estado=Correo.Estado.SIN_DOCUMENTOS)

        salida = self.comando("--todos")

        estados = dict(Correo.objects.values_list("pk", "estado"))
        # Los tres traen la misma factura: el primero la guarda, los otros dos
        # la tienen repetida, y también quedan procesados.
        for correo in (desconocida, error, pendiente_viejo):
            self.assertEqual(estados[correo.pk], Correo.Estado.PROCESADO)
        self.assertEqual(estados[pendiente_nuevo.pk], Correo.Estado.PENDIENTE)
        self.assertEqual(estados[procesado.pk], Correo.Estado.SIN_DOCUMENTOS)
        self.assertEqual(Documento.objects.count(), 1)
        self.assertIn("3 correos reprocesados (procesado: 3)", salida)

    def test_todos_sin_nada_que_reprocesar(self):
        self.assertIn("No hay correos para reprocesar.", self.comando("--todos"))

    def test_pide_id_o_todos(self):
        with self.assertRaises(CommandError):
            self.comando()

    def test_reprocesables_excluye_los_pendientes_recientes(self):
        viejo = self.crear_correo(huella="a")
        self.crear_correo(huella="b", hace=procesamiento.ESPERA_PENDIENTE - timedelta(minutes=1))

        self.assertEqual(list(procesamiento.reprocesables()), [viejo])
