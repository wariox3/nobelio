"""La llave de API de alcance global: ve todos los emisores, como el staff,
pero solo con dueño staff, con vencimiento corto y sin administración."""
from datetime import timedelta
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.documentos.tests_utils import crear_catalogos_minimos, crear_usuario
from apps.emisores.models import Emisor
from apps.seguridad.models import LlaveApi

URL_EMISORES = "/api/emisores/emisor/"


def en_dias(dias):
    return timezone.now() + timedelta(days=dias)


class LlaveGlobalApiTests(APITestCase):
    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.tecnico = crear_usuario(nombre="App admin", is_staff=True)
        self.emisor_a = self.crear_emisor(crear_usuario(nombre="Cliente A"), "900000001")
        self.emisor_b = self.crear_emisor(crear_usuario(nombre="Cliente B"), "900000002")
        self.llave, clave = LlaveApi.generar(
            usuario=self.tecnico, nombre="App administrativa",
            expira_en=en_dias(30), alcance_global=True,
        )
        self.cabecera = {"HTTP_AUTHORIZATION": f"Api-Key {clave}"}

    def crear_emisor(self, usuario, nit):
        c = self.cat
        return Emisor.objects.create(
            usuario=usuario, razon_social=f"Empresa {nit}",
            tipo_identificacion=c["nit"], numero_identificacion=nit,
            tipo_organizacion=c["juridica"], pais=c["colombia"],
            departamento=c["antioquia"], municipio=c["medellin"],
            direccion="Calle 1 # 2-3",
        )

    def ids_de_emisores(self, **extra):
        respuesta = self.client.get(URL_EMISORES, **self.cabecera, **extra)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return {fila["id"] for fila in respuesta.json()["results"]}

    def test_ve_los_emisores_de_todos(self):
        self.assertEqual(self.ids_de_emisores(), {self.emisor_a.pk, self.emisor_b.pk})

    def test_una_llave_normal_del_mismo_staff_no_ve_nada(self):
        _, clave = LlaveApi.generar(usuario=self.tecnico, nombre="Normal")
        self.cabecera = {"HTTP_AUTHORIZATION": f"Api-Key {clave}"}

        self.assertEqual(self.ids_de_emisores(), set())

    def test_no_administra_usuarios(self):
        respuesta = self.client.get("/api/seguridad/usuario/", **self.cabecera)

        self.assertEqual(respuesta.status_code, 403)

    def test_si_el_dueno_deja_de_ser_staff_deja_de_servir(self):
        self.tecnico.is_staff = False
        self.tecnico.save(update_fields=["is_staff"])

        respuesta = self.client.get(URL_EMISORES, **self.cabecera)

        self.assertEqual(respuesta.status_code, 401)

    def test_vencida_deja_de_servir(self):
        LlaveApi.objects.filter(pk=self.llave.pk).update(expira_en=en_dias(-1))

        self.assertEqual(self.client.get(URL_EMISORES, **self.cabecera).status_code, 401)

    def test_sin_vencimiento_deja_de_servir(self):
        LlaveApi.objects.filter(pk=self.llave.pk).update(expira_en=None)

        self.assertEqual(self.client.get(URL_EMISORES, **self.cabecera).status_code, 401)

    def test_registra_cada_uso_con_el_actor(self):
        with self.assertLogs("apps.seguridad.autenticacion", "INFO") as registro:
            self.ids_de_emisores(HTTP_X_ACTOR="maria@empresa.co\nfalso=1")

        linea = registro.output[-1]
        self.assertIn(f"llave={self.llave.prefijo}", linea)
        self.assertIn("alcance_global=1", linea)
        self.assertIn("ruta=/api/emisores/emisor/", linea)
        self.assertIn("actor=maria@empresa.cofalso=1", linea)


class GenerarLlaveGlobalTests(TestCase):
    def setUp(self):
        self.tecnico = crear_usuario(nombre="App admin", is_staff=True)

    def test_exige_dueno_staff(self):
        with self.assertRaisesMessage(ValueError, "tiene que ser staff"):
            LlaveApi.generar(
                usuario=crear_usuario(nombre="Normal"), nombre="x",
                expira_en=en_dias(30), alcance_global=True,
            )

    def test_exige_vencimiento(self):
        with self.assertRaisesMessage(ValueError, "tiene que tener vencimiento"):
            LlaveApi.generar(usuario=self.tecnico, nombre="x", alcance_global=True)

    def test_no_vence_en_mas_de_90_dias(self):
        with self.assertRaisesMessage(ValueError, "90 días como máximo"):
            LlaveApi.generar(
                usuario=self.tecnico, nombre="x",
                expira_en=en_dias(91), alcance_global=True,
            )


class LlaveGlobalPorApiTests(APITestCase):
    URL = "/api/seguridad/llave-api/"

    def setUp(self):
        self.tecnico = crear_usuario(nombre="App admin", is_staff=True)
        self.client.force_authenticate(self.tecnico)

    def test_no_se_puede_crear_por_la_api(self):
        respuesta = self.client.post(
            self.URL, {"nombre": "x", "alcance_global": True}, format="json",
        )

        self.assertEqual(respuesta.status_code, 201)
        self.assertFalse(LlaveApi.objects.get().alcance_global)

    def test_no_se_puede_quitar_ni_alargar_el_vencimiento(self):
        llave, _ = LlaveApi.generar(
            usuario=self.tecnico, nombre="x", expira_en=en_dias(30), alcance_global=True,
        )
        url = f"{self.URL}{llave.pk}/"

        sin_vencer = self.client.patch(url, {"expira_en": None}, format="json")
        muy_largo = self.client.patch(url, {"expira_en": en_dias(200)}, format="json")
        valido = self.client.patch(url, {"expira_en": en_dias(60)}, format="json")

        self.assertEqual(sin_vencer.status_code, 400)
        self.assertEqual(muy_largo.status_code, 400)
        self.assertEqual(valido.status_code, 200)


class ComandoCrearLlaveGlobalTests(TestCase):
    def test_crea_la_llave_global_con_vencimiento(self):
        crear_usuario(nombre="App admin", email="admin-app@rededoc.co", is_staff=True)
        salida = StringIO()

        call_command(
            "crear_llave_api", "--usuario", "admin-app@rededoc.co",
            "--nombre", "App administrativa", "--alcance-global", "--dias", "30",
            stdout=salida,
        )

        llave = LlaveApi.objects.get()
        self.assertTrue(llave.alcance_global)
        self.assertLess(llave.expira_en, en_dias(31))
        self.assertIn("GLOBAL", salida.getvalue())

    def test_rechaza_un_usuario_que_no_es_staff(self):
        crear_usuario(nombre="Normal", email="normal@rededoc.co")

        with self.assertRaisesMessage(CommandError, "tiene que ser staff"):
            call_command(
                "crear_llave_api", "--usuario", "normal@rededoc.co",
                "--nombre", "x", "--alcance-global", stdout=StringIO(),
            )
        self.assertFalse(LlaveApi.objects.exists())
