"""La API de consulta de los correos recibidos."""
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from botocore.exceptions import EndpointConnectionError
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from apps.documentos.tests_utils import crear_catalogos_minimos, crear_usuario
from apps.emisores.models import Emisor
from apps.recepcion.models import Adjunto, Correo, Documento
from apps.recepcion.tests_utils import crear_documento_recibido
from apps.seguridad.models import LlaveApi

Usuario = get_user_model()

URL = "/api/recepcion/correo/"
BOGOTA = ZoneInfo("America/Bogota")


class CorreosBase(APITestCase):
    """Cuatro correos: dos de emisores del usuario, uno ajeno y uno sin emisor."""

    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.usuario = crear_usuario(nombre="Dueño")
        self.emisor = self.crear_emisor(self.usuario, "900000001")
        self.hermano = self.crear_emisor(self.usuario, "900000002")
        self.emisor_ajeno = self.crear_emisor(crear_usuario(nombre="Ajeno"), "900000003")

        self.primero = self.crear_correo(self.emisor, "1", datetime(2026, 10, 1, 23, 30))
        self.segundo = self.crear_correo(
            self.hermano, "2", datetime(2026, 10, 2, 8), estado=Correo.Estado.ERROR,
        )
        self.ajeno = self.crear_correo(self.emisor_ajeno, "3", datetime(2026, 10, 2, 9))
        self.sin_emisor = self.crear_correo(None, "4", datetime(2026, 10, 2, 10))

        self.client.force_authenticate(self.usuario)

    def crear_emisor(self, usuario, nit):
        c = self.cat
        return Emisor.objects.create(
            usuario=usuario,
            razon_social=f"Empresa {nit}",
            tipo_identificacion=c["nit"],
            numero_identificacion=nit,
            tipo_organizacion=c["juridica"],
            pais=c["colombia"],
            departamento=c["antioquia"],
            municipio=c["medellin"],
            direccion="Calle 1 # 2-3",
        )

    def crear_correo(self, emisor, huella, recibido_en, **extra):
        alias = emisor.numero_identificacion if emisor else "desconocido"
        return Correo.objects.create(
            emisor=emisor,
            alias=alias,
            sha256=huella * 64,
            raw_key=f"2026-10-02/{huella}.eml",
            envelope_to=f"{alias}@recepcion.rededoc.co",
            envelope_from=f"facturas{huella}@proveedor.example",
            asunto=f"Factura FE-{huella}",
            recibido_en=recibido_en.replace(tzinfo=BOGOTA),
            **extra,
        )



class CorreoApiTests(CorreosBase):
    def ids(self, respuesta):
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return {fila["id"] for fila in respuesta.json()["results"]}

    def test_lista_solo_los_correos_de_sus_emisores(self):
        self.assertEqual(self.ids(self.client.get(URL)), {self.primero.pk, self.segundo.pk})

    def test_el_staff_ve_todos_incluso_sin_emisor(self):
        admin = Usuario.objects.create_superuser(email="admin@nobelio.co", password="Clave123456")
        self.client.force_authenticate(admin)

        self.assertEqual(len(self.ids(self.client.get(URL))), 4)

    def test_expone_la_clave_de_r2_pero_no_la_huella(self):
        fila = self.client.get(f"{URL}{self.primero.pk}/").json()

        self.assertEqual(fila["emisor"], self.emisor.pk)
        self.assertEqual(fila["asunto"], "Factura FE-1")
        self.assertNotIn("sha256", fila)
        self.assertEqual(fila["raw_key"], "2026-10-02/1.eml")

    def test_pagina_igual_que_los_documentos(self):
        """25 por defecto, `?page_size=` hasta 100 y orden estable con empates."""
        momento = datetime(2026, 10, 3, 9)
        for huella in "abcdefghijklmnopqrstuvwxyzABCD":
            self.crear_correo(self.emisor, huella, momento)

        cuerpo = self.client.get(URL).json()
        self.assertEqual(cuerpo["count"], 32)
        self.assertEqual(len(cuerpo["results"]), 25)

        vistos = []
        for pagina in range(1, 5):
            respuesta = self.client.get(URL, {"page": pagina, "page_size": 10, "ordering": "estado"})
            if respuesta.status_code == 404:
                break
            vistos += [fila["id"] for fila in respuesta.json()["results"]]
        self.assertEqual(len(vistos), 32)
        self.assertEqual(len(set(vistos)), 32)

    def test_el_correo_ajeno_es_404(self):
        self.assertEqual(self.client.get(f"{URL}{self.ajeno.pk}/").status_code, 404)

    def test_muestra_solo_los_documentos_de_sus_emisores(self):
        propio = crear_documento_recibido(self.primero, self.emisor, "1")
        crear_documento_recibido(self.primero, self.emisor_ajeno, "2")

        fila = self.client.get(f"{URL}{self.primero.pk}/").json()

        self.assertEqual([d["id"] for d in fila["documentos"]], [str(propio.pk)])
        self.assertEqual(fila["documentos"][0]["documento_tipo"], "factura_venta")

    def test_filtra_por_emisor(self):
        respuesta = self.client.get(URL, {"emisor": self.hermano.pk})

        self.assertEqual(self.ids(respuesta), {self.segundo.pk})

    def test_el_filtro_por_emisor_no_amplia_el_alcance(self):
        respuesta = self.client.get(URL, {"emisor": self.emisor_ajeno.pk})

        self.assertEqual(self.ids(respuesta), set())

    def test_filtra_por_estado(self):
        respuesta = self.client.get(URL, {"estado": "error"})

        self.assertEqual(self.ids(respuesta), {self.segundo.pk})

    def test_filtra_por_fecha_en_hora_de_colombia(self):
        # 23:30 del 1 en Bogotá ya es el 2 en UTC: tiene que contar como el 1.
        self.assertEqual(
            self.ids(self.client.get(URL, {"desde": "2026-10-01", "hasta": "2026-10-01"})),
            {self.primero.pk},
        )
        self.assertEqual(
            self.ids(self.client.get(URL, {"desde": "2026-10-02"})), {self.segundo.pk},
        )

    def test_busca_por_remitente(self):
        respuesta = self.client.get(URL, {"search": "facturas2@"})

        self.assertEqual(self.ids(respuesta), {self.segundo.pk})

    def test_ordena_del_mas_reciente_al_mas_antiguo(self):
        filas = self.client.get(URL).json()["results"]

        self.assertEqual([f["id"] for f in filas], [self.segundo.pk, self.primero.pk])

    def test_fecha_invalida_es_400(self):
        self.assertEqual(self.client.get(URL, {"desde": "02/10/2026"}).status_code, 400)

    def test_emisor_invalido_es_400(self):
        self.assertEqual(self.client.get(URL, {"emisor": "abc"}).status_code, 400)

    def test_no_se_crea_ni_edita_por_la_api(self):
        self.assertEqual(self.client.post(URL, {}).status_code, 405)
        self.assertEqual(self.client.patch(f"{URL}{self.primero.pk}/", {}).status_code, 405)

    def test_sin_autenticar_es_401(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)


R2_CONFIGURADO = dict(
    R2_HABILITADO=True, R2_BUCKET="nobelio-inbound-raw",
    R2_ACCOUNT_ID="cuenta", R2_ACCESS_KEY_ID="id", R2_SECRET_ACCESS_KEY="secreto",
)


@override_settings(**R2_CONFIGURADO)
class EliminarCorreoTests(CorreosBase):
    """``DELETE`` de un correo sin emisor: la fila y su MIME en R2, o nada."""

    def setUp(self):
        super().setUp()
        self.admin = Usuario.objects.create_superuser(
            email="admin@nobelio.co", password="Clave123456",
        )
        self.client.force_authenticate(self.admin)
        parche = mock.patch("apps.recepcion.r2._cliente")
        self.r2 = parche.start()()
        self.addCleanup(parche.stop)

    def eliminar(self, correo):
        return self.client.delete(f"{URL}{correo.pk}/")

    def test_borra_la_fila_y_el_mime(self):
        respuesta = self.eliminar(self.sin_emisor)

        self.assertEqual(respuesta.status_code, 204)
        self.assertFalse(Correo.objects.filter(pk=self.sin_emisor.pk).exists())
        self.r2.delete_object.assert_called_once_with(
            Bucket="nobelio-inbound-raw", Key="2026-10-02/4.eml",
        )

    def test_un_correo_con_emisor_no_se_borra(self):
        respuesta = self.eliminar(self.primero)

        self.assertEqual(respuesta.status_code, 400)
        self.assertTrue(Correo.objects.filter(pk=self.primero.pk).exists())
        self.r2.delete_object.assert_not_called()

    def test_si_r2_falla_la_fila_se_queda(self):
        self.r2.delete_object.side_effect = EndpointConnectionError(endpoint_url="r2")

        respuesta = self.eliminar(self.sin_emisor)

        self.assertEqual(respuesta.status_code, 502)
        self.assertTrue(Correo.objects.filter(pk=self.sin_emisor.pk).exists())

    @override_settings(R2_HABILITADO=False)
    def test_sin_r2_configurado_no_borra_nada(self):
        respuesta = self.eliminar(self.sin_emisor)

        self.assertEqual(respuesta.status_code, 503)
        self.assertTrue(Correo.objects.filter(pk=self.sin_emisor.pk).exists())

    def test_quien_no_es_staff_no_alcanza_los_correos_sin_emisor(self):
        self.client.force_authenticate(self.usuario)

        self.assertEqual(self.eliminar(self.sin_emisor).status_code, 404)
        self.assertEqual(self.eliminar(self.primero).status_code, 400)


@override_settings(**R2_CONFIGURADO)
class EliminarAdminTests(CorreosBase):
    """``DELETE eliminar-admin/``: el correo con sus documentos y archivos, solo
    para el staff o una llave de alcance global."""

    def setUp(self):
        super().setUp()
        tecnico = crear_usuario(nombre="App admin", is_staff=True)
        _, clave = LlaveApi.generar(
            usuario=tecnico, nombre="App administrativa",
            expira_en=timezone.now() + timedelta(days=30), alcance_global=True,
        )
        self.llave_global = {"HTTP_AUTHORIZATION": f"Api-Key {clave}"}
        parche = mock.patch("apps.recepcion.r2._cliente")
        self.r2 = parche.start()()
        self.addCleanup(parche.stop)
        self.documento = self.crear_documento(self.primero, self.emisor, "1")
        # Un documento de otro emisor en el mismo correo también se va.
        self.documento_ajeno = self.crear_documento(self.primero, self.emisor_ajeno, "2")

    def crear_documento(self, correo, emisor, cufe):
        return crear_documento_recibido(correo, emisor, cufe, pdf=b"%PDF")

    def eliminar(self, correo, **cabeceras):
        # Con credencial en la cabecera, un cliente sin la sesión forzada del setUp.
        cliente = APIClient() if cabeceras else self.client
        return cliente.delete(f"{URL}{correo.pk}/eliminar-admin/", **cabeceras)

    def test_la_llave_global_elimina_el_correo_con_todo(self):
        archivos = [a.archivo for a in Adjunto.objects.filter(correo=self.primero)]
        respuesta = self.eliminar(self.primero, **self.llave_global)

        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(
            respuesta.json(), {"correo": self.primero.pk, "documentos": 2, "archivos": 4},
        )
        self.assertFalse(Correo.objects.filter(pk=self.primero.pk).exists())
        self.assertFalse(Documento.objects.exists())
        self.assertFalse(Adjunto.objects.exists())
        for archivo in archivos:
            self.assertFalse(archivo.storage.exists(archivo.name))
        self.r2.delete_object.assert_called_once_with(
            Bucket="nobelio-inbound-raw", Key="2026-10-02/1.eml",
        )

    def test_el_staff_tambien_puede(self):
        admin = Usuario.objects.create_superuser(email="admin@nobelio.co", password="Clave123456")
        self.client.force_authenticate(admin)

        self.assertEqual(self.eliminar(self.primero).status_code, 200)

    def test_un_correo_sin_documentos_ni_emisor(self):
        respuesta = self.eliminar(self.sin_emisor, **self.llave_global)

        self.assertEqual(respuesta.json()["documentos"], 0)
        self.assertFalse(Correo.objects.filter(pk=self.sin_emisor.pk).exists())

    def test_el_dueno_del_emisor_no_puede(self):
        respuesta = self.eliminar(self.primero)

        self.assertEqual(respuesta.status_code, 403)
        self.assertTrue(Correo.objects.filter(pk=self.primero.pk).exists())
        self.assertEqual(Documento.objects.count(), 2)

    def test_una_llave_normal_no_puede(self):
        _, clave = LlaveApi.generar(usuario=self.usuario, nombre="ERP")
        respuesta = self.eliminar(self.primero, HTTP_AUTHORIZATION=f"Api-Key {clave}")

        self.assertEqual(respuesta.status_code, 403)

    @override_settings(R2_HABILITADO=False)
    def test_sin_r2_no_toca_nada(self):
        respuesta = self.eliminar(self.primero, **self.llave_global)

        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(Documento.objects.count(), 2)
        archivo = Adjunto.objects.filter(correo=self.primero).first().archivo
        self.assertTrue(archivo.storage.exists(archivo.name))

    def test_si_r2_falla_las_filas_vuelven(self):
        self.r2.delete_object.side_effect = EndpointConnectionError(endpoint_url="r2")
        respuesta = self.eliminar(self.primero, **self.llave_global)

        self.assertEqual(respuesta.status_code, 502)
        self.assertTrue(Correo.objects.filter(pk=self.primero.pk).exists())
        self.assertEqual(Documento.objects.count(), 2)

    def test_repetir_despues_de_un_fallo_termina_el_trabajo(self):
        self.r2.delete_object.side_effect = [EndpointConnectionError(endpoint_url="r2"), None]
        self.eliminar(self.primero, **self.llave_global)
        respuesta = self.eliminar(self.primero, **self.llave_global)

        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(Documento.objects.exists())
