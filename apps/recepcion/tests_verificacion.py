"""La verificación de los documentos recibidos contra la DIAN (GetStatus por CUFE)."""
import io
from unittest import mock

import requests
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from apps.dian.soap import RespuestaDian
from apps.documentos.tests_utils import crear_catalogos_minimos, crear_usuario
from apps.emisores.models import Emisor
from apps.recepcion import extraccion, tareas, verificacion
from apps.recepcion.models import Correo, Documento, EstadoVerificacion
from apps.recepcion.tests_utils import crear_documento_recibido, xml_documento

URL = "/api/recepcion/documento/"


class ClienteFalso:
    """Un ``ClienteDian`` que responde ``respuesta`` (o lanza ``error``)."""

    def __init__(self, respuesta=None, error=None):
        self.respuesta = respuesta
        self.error = error
        self.consultados = []

    def consultar_estado(self, track_id):
        self.consultados.append(track_id)
        if self.error:
            raise self.error
        return self.respuesta


VALIDO = RespuestaDian(es_valido=True, codigo_estado="00", descripcion_estado="Procesado Correctamente.")
NO_EXISTE = RespuestaDian(
    es_valido=False, codigo_estado="66",
    descripcion_estado="TrackId no existe en los registros de la DIAN.",
)


class VerificacionTests(APITestCase):
    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.usuario = crear_usuario(nombre="Dueño")
        c = self.cat
        self.emisor = Emisor.objects.create(
            usuario=self.usuario, razon_social="Empresa", tipo_identificacion=c["nit"],
            numero_identificacion="901192048", tipo_organizacion=c["juridica"],
            pais=c["colombia"], departamento=c["antioquia"], municipio=c["medellin"],
            direccion="Calle 1 # 2-3",
        )
        self.correo = Correo.objects.create(
            alias="901192048", emisor=self.emisor, sha256="a" * 64,
            raw_key="k.eml", envelope_to="901192048@recepcion.rededoc.co",
        )
        self.documento = crear_documento_recibido(self.correo, self.emisor, "1")
        self.client.force_authenticate(self.usuario)

    def con_cliente(self, cliente):
        """Simula un emisor con certificado vigente y la DIAN detrás de ``cliente``."""
        construir = mock.patch.object(verificacion, "construir_cliente_emisor", return_value=cliente)
        motivo = mock.patch.object(verificacion, "motivo_no_puede_emitir", return_value=None)
        self.construir = construir.start()
        motivo.start()
        self.addCleanup(construir.stop)
        self.addCleanup(motivo.stop)

    # --- El servicio --------------------------------------------------------

    def test_la_dian_lo_tiene_valido(self):
        cliente = ClienteFalso(VALIDO)

        verificacion.verificar(self.documento, cliente=cliente)

        self.documento.refresh_from_db()
        self.assertEqual(cliente.consultados, [self.documento.cufe_cude])
        self.assertEqual(self.documento.verificacion_estado, EstadoVerificacion.VALIDO)
        self.assertEqual(self.documento.verificacion_codigo, "00")
        self.assertIsNotNone(self.documento.verificado_en)

    def test_la_dian_no_lo_reconoce(self):
        verificacion.verificar(self.documento, cliente=ClienteFalso(NO_EXISTE))

        self.documento.refresh_from_db()
        self.assertEqual(self.documento.verificacion_estado, EstadoVerificacion.INVALIDO)
        self.assertEqual(self.documento.verificacion_codigo, "66")
        self.assertIn("no existe", self.documento.verificacion_descripcion)

    def test_sin_certificado_no_es_verificable(self):
        # El emisor de la prueba no tiene certificado.
        verificacion.verificar(self.documento)

        self.documento.refresh_from_db()
        self.assertEqual(self.documento.verificacion_estado, EstadoVerificacion.NO_VERIFICABLE)
        self.assertIn("certificado", self.documento.verificacion_descripcion.lower())

    def test_si_la_dian_no_responde_es_transitorio_y_no_toca_el_documento(self):
        cliente = ClienteFalso(error=requests.ConnectionError("sin red"))

        with self.assertRaises(verificacion.ErrorTransitorio):
            verificacion.verificar(self.documento, cliente=cliente)

        self.documento.refresh_from_db()
        self.assertEqual(self.documento.verificacion_estado, EstadoVerificacion.PENDIENTE)

    def test_consulta_en_el_ambiente_del_documento(self):
        self.con_cliente(ClienteFalso(VALIDO))
        Documento.objects.filter(pk=self.documento.pk).update(ambiente=2)
        self.documento.refresh_from_db()

        verificacion.verificar(self.documento)

        self.construir.assert_called_once_with(self.emisor, 2)

    def test_sin_ambiente_consulta_en_produccion(self):
        self.con_cliente(ClienteFalso(VALIDO))

        verificacion.verificar(self.documento)

        self.construir.assert_called_once_with(self.emisor, 1)

    # --- La lectura del ambiente -----------------------------------------------

    def test_lee_el_ambiente_del_xml(self):
        datos = extraccion.extraer_archivo("f.xml", xml_documento(ambiente="2")).documentos[0]
        self.assertEqual(datos.ambiente, 2)
        datos = extraccion.extraer_archivo("f.xml", xml_documento()).documentos[0]
        self.assertIsNone(datos.ambiente)

    # --- La tarea -------------------------------------------------------------

    def test_la_tarea_verifica(self):
        self.con_cliente(ClienteFalso(VALIDO))

        tareas.verificar_documento.apply(args=[str(self.documento.pk)])

        self.documento.refresh_from_db()
        self.assertEqual(self.documento.verificacion_estado, EstadoVerificacion.VALIDO)

    def test_la_tarea_agotados_los_reintentos_deja_error(self):
        self.con_cliente(ClienteFalso(error=requests.Timeout("lenta")))

        tareas.verificar_documento.apply(args=[str(self.documento.pk)])

        self.documento.refresh_from_db()
        self.assertEqual(self.documento.verificacion_estado, EstadoVerificacion.ERROR)
        self.assertIn("lenta", self.documento.verificacion_descripcion)

    def test_registrar_un_documento_encola_su_verificacion(self):
        with mock.patch("apps.recepcion.procesamiento.encolar") as encolar:
            respuesta = self.client.post(f"{URL}cargar/", {
                "emisor": self.emisor.pk,
                "archivo": SimpleUploadedFile("f.xml", xml_documento(cufe="c" * 96)),
            }, format="multipart")

        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        encolar.assert_called_once_with(
            tareas.verificar_documento, respuesta.data["creados"][0]["id"],
        )

    # --- La API -----------------------------------------------------------------

    def test_verificar_ahora(self):
        self.con_cliente(ClienteFalso(VALIDO))

        respuesta = self.client.post(f"{URL}{self.documento.pk}/verificar/")

        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertEqual(respuesta.data["verificacion_estado"], "valido")

    def test_verificar_ahora_con_la_dian_caida_deja_error(self):
        self.con_cliente(ClienteFalso(error=requests.ConnectionError("sin red")))

        respuesta = self.client.post(f"{URL}{self.documento.pk}/verificar/")

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data["verificacion_estado"], "error")

    def test_filtra_por_estado_de_verificacion(self):
        otro = crear_documento_recibido(self.correo, self.emisor, "2")
        Documento.objects.filter(pk=otro.pk).update(verificacion_estado=EstadoVerificacion.VALIDO)

        filas = self.client.get(URL, {"verificacion_estado": "valido"}).json()["results"]

        self.assertEqual([f["id"] for f in filas], [str(otro.pk)])

    # --- El comando ---------------------------------------------------------------

    def test_el_comando_verifica_los_pendientes(self):
        self.con_cliente(ClienteFalso(VALIDO))
        valido = crear_documento_recibido(self.correo, self.emisor, "2")
        Documento.objects.filter(pk=valido.pk).update(verificacion_estado=EstadoVerificacion.INVALIDO)
        salida = io.StringIO()

        call_command("verificar_documentos", "--todos", stdout=salida)

        self.documento.refresh_from_db()
        self.assertEqual(self.documento.verificacion_estado, EstadoVerificacion.VALIDO)
        # Uno que la DIAN ya dio por no válido no se vuelve a consultar.
        self.assertEqual(Documento.objects.get(pk=valido.pk).verificacion_estado, "invalido")
        self.assertIn("Válido: 1", salida.getvalue())
