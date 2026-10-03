"""La API de consulta de los correos recibidos."""
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from botocore.exceptions import EndpointConnectionError

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from apps.documentos.tests_utils import crear_catalogos_minimos, crear_usuario
from apps.emisores.models import Emisor
from apps.recepcion.models import Correo

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

    def test_el_correo_ajeno_es_404(self):
        self.assertEqual(self.client.get(f"{URL}{self.ajeno.pk}/").status_code, 404)

    def test_muestra_solo_los_documentos_de_sus_emisores(self):
        from apps.documentos.models import DocumentoTipo
        from apps.recepcion.models import Documento

        def documento(emisor, cufe):
            return Documento.objects.create(
                numero="FE-1", cufe_cude=cufe, fecha_emision="2026-10-01",
                proveedor_numero_identificacion="800123456",
                receptor_numero_identificacion=emisor.numero_identificacion,
                xml_archivo="x.xml", emisor=emisor, correo=self.primero,
                documento_tipo=DocumentoTipo.objects.get(codigo="factura_venta"),
            )

        propio = documento(self.emisor, "1" * 96)
        documento(self.emisor_ajeno, "2" * 96)

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
