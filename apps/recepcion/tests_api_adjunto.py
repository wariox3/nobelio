"""La API de los adjuntos: ``/api/recepcion/adjunto/`` y la lista del correo."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from apps.documentos.tests_utils import crear_catalogos_minimos, crear_usuario
from apps.emisores.models import Emisor
from apps.recepcion import adjuntos
from apps.recepcion.models import Adjunto, Correo
from apps.recepcion.tests_api import R2_CONFIGURADO
from apps.recepcion.tests_utils import crear_documento_recibido

Usuario = get_user_model()

URL = "/api/recepcion/adjunto/"


class AdjuntoApiTests(APITestCase):
    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.usuario = crear_usuario(nombre="Dueño")
        self.emisor = self.crear_emisor(self.usuario, "900000001")
        self.emisor_ajeno = self.crear_emisor(crear_usuario(nombre="Ajeno"), "900000003")
        self.correo = self.crear_correo(self.emisor, "a")
        # Un documento propio y otro de un emisor ajeno, en el mismo correo.
        self.documento = crear_documento_recibido(
            self.correo, self.emisor, "1", pdf=b"%PDF",
        )
        crear_documento_recibido(self.correo, self.emisor_ajeno, "2")
        self.otro = self.crear_adjunto(self.correo, b"imagen", "logo.png")
        self.correo_ajeno = self.crear_correo(self.emisor_ajeno, "b")
        self.de_correo_ajeno = self.crear_adjunto(self.correo_ajeno, b"x", "x.txt")
        self.client.force_authenticate(self.usuario)

    def crear_emisor(self, usuario, nit):
        c = self.cat
        return Emisor.objects.create(
            usuario=usuario, razon_social=f"Empresa {nit}",
            tipo_identificacion=c["nit"], numero_identificacion=nit,
            tipo_organizacion=c["juridica"], pais=c["colombia"],
            departamento=c["antioquia"], municipio=c["medellin"],
            direccion="Calle 1 # 2-3",
        )

    def crear_correo(self, emisor, huella):
        return Correo.objects.create(
            alias=emisor.numero_identificacion, emisor=emisor, sha256=huella * 64,
            raw_key=f"{huella}.eml", envelope_to="x@recepcion.rededoc.co",
        )

    def crear_adjunto(self, correo, contenido, nombre):
        return adjuntos.crear(
            correo, contenido, nombre=nombre, rol=Adjunto.Rol.OTRO, subidos=[],
        )

    def nombres(self, respuesta):
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        filas = respuesta.json()
        filas = filas["results"] if isinstance(filas, dict) else filas
        return sorted(f["nombre"] for f in filas)

    def test_lista_lo_que_alcanza(self):
        # Del documento ajeno no ve nada, aunque esté en su correo.
        self.assertEqual(
            self.nombres(self.client.get(URL)), ["FE-1.pdf", "ad-FE-1.xml", "logo.png"],
        )

    def test_el_staff_ve_todos(self):
        admin = Usuario.objects.create_superuser(email="admin@nobelio.co", password="Clave123456")
        self.client.force_authenticate(admin)

        self.assertEqual(len(self.nombres(self.client.get(URL))), 5)

    def test_filtros(self):
        self.assertEqual(self.nombres(self.client.get(URL, {"rol": "otro"})), ["logo.png"])
        self.assertEqual(
            self.nombres(self.client.get(URL, {"documento": self.documento.pk})),
            ["FE-1.pdf", "ad-FE-1.xml"],
        )
        self.assertEqual(
            self.nombres(self.client.get(URL, {"correo": self.correo_ajeno.pk})), [],
        )
        self.assertEqual(self.nombres(self.client.get(URL, {"search": "logo"})), ["logo.png"])

    def test_filtro_invalido_es_400(self):
        self.assertEqual(self.client.get(URL, {"documento": "no-es-uuid"}).status_code, 400)

    def test_detalle(self):
        fila = self.client.get(f"{URL}{self.otro.pk}/").json()

        self.assertEqual(fila["rol"], "otro")
        self.assertEqual(fila["correo"], self.correo.pk)
        self.assertIsNone(fila["documento"])
        self.assertEqual(fila["tipo_contenido"], "image/png")
        self.assertEqual(fila["tamano"], 6)
        self.assertNotIn("archivo", fila)

    def test_descarga_con_el_nombre_original(self):
        respuesta = self.client.get(f"{URL}{self.otro.pk}/descargar/")

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(b"".join(respuesta.streaming_content), b"imagen")
        self.assertEqual(respuesta["Content-Type"], "image/png")
        self.assertIn('attachment; filename="logo.png"', respuesta["Content-Disposition"])

    def test_el_ajeno_es_404(self):
        self.assertEqual(self.client.get(f"{URL}{self.de_correo_ajeno.pk}/").status_code, 404)
        self.assertEqual(
            self.client.get(f"{URL}{self.de_correo_ajeno.pk}/descargar/").status_code, 404,
        )

    def test_es_de_solo_lectura(self):
        self.assertEqual(self.client.post(URL, {}).status_code, 405)
        self.assertEqual(self.client.delete(f"{URL}{self.otro.pk}/").status_code, 405)

    def test_sin_autenticar_es_401(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)

    def test_los_adjuntos_del_correo(self):
        respuesta = self.client.get(f"/api/recepcion/correo/{self.correo.pk}/adjuntos/")

        self.assertEqual(self.nombres(respuesta), ["FE-1.pdf", "ad-FE-1.xml", "logo.png"])

    def test_los_adjuntos_de_un_correo_ajeno_es_404(self):
        respuesta = self.client.get(f"/api/recepcion/correo/{self.correo_ajeno.pk}/adjuntos/")

        self.assertEqual(respuesta.status_code, 404)

    @override_settings(**R2_CONFIGURADO)
    def test_eliminar_un_correo_sin_emisor_borra_sus_adjuntos_en_b2(self):
        admin = Usuario.objects.create_superuser(email="admin@nobelio.co", password="Clave123456")
        self.client.force_authenticate(admin)
        sin_emisor = Correo.objects.create(
            alias="desconocido", sha256="c" * 64, raw_key="c.eml",
            envelope_to="desconocido@recepcion.rededoc.co",
        )
        adjunto = self.crear_adjunto(sin_emisor, b"pdf", "suelto.pdf")
        archivo = adjunto.archivo

        with mock.patch("apps.recepcion.r2._cliente"):
            respuesta = self.client.delete(f"/api/recepcion/correo/{sin_emisor.pk}/")

        self.assertEqual(respuesta.status_code, 204)
        self.assertFalse(Adjunto.objects.filter(pk=adjunto.pk).exists())
        self.assertFalse(archivo.storage.exists(archivo.name))
