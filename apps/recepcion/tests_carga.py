"""La carga manual de documentos recibidos: ``POST /api/recepcion/documento/cargar/``."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient, APITestCase

from apps.documentos.tests_utils import crear_catalogos_minimos, crear_usuario
from apps.emisores.models import Emisor
from apps.recepcion.models import Adjunto, Correo, Documento
from apps.recepcion.serializers import carga
from apps.recepcion.tests_utils import PDF, xml_attached, xml_documento, zip_con

URL = "/api/recepcion/documento/cargar/"
NIT = "901192048"  # el receptor por defecto de `xml_documento`


class CargaDocumentoTests(APITestCase):
    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.usuario = crear_usuario(nombre="Dueño")
        self.emisor = self.crear_emisor(self.usuario, NIT)
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

    def cargar(self, nombre, contenido, emisor=None):
        return self.client.post(URL, {
            "emisor": (emisor or self.emisor).pk,
            "archivo": SimpleUploadedFile(nombre, contenido),
        }, format="multipart")

    def zip_del_proveedor(self, **datos):
        return zip_con(ad_FE__xml=xml_attached(xml_documento(**datos)), FE__pdf=PDF)

    # --- Lo que se registra ---------------------------------------------------

    def test_registra_el_zip_del_proveedor(self):
        respuesta = self.cargar("factura.zip", self.zip_del_proveedor())

        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertEqual(len(respuesta.data["creados"]), 1)
        documento = Documento.objects.get()
        self.assertEqual(respuesta.data["creados"][0]["id"], str(documento.pk))
        self.assertEqual(documento.emisor, self.emisor)
        self.assertEqual(documento.numero, "FE-100")
        self.assertEqual(
            set(documento.adjuntos.values_list("rol", flat=True)),
            {Adjunto.Rol.XML, Adjunto.Rol.XML_DOCUMENTO, Adjunto.Rol.PDF},
        )

    def test_la_carga_queda_registrada_con_quien_la_subio(self):
        self.cargar("factura.zip", self.zip_del_proveedor())

        entrada = Correo.objects.get()
        self.assertEqual(entrada.origen, Correo.Origen.CARGA)
        self.assertEqual(entrada.usuario, self.usuario)
        self.assertEqual(entrada.emisor, self.emisor)
        self.assertEqual(entrada.asunto, "factura.zip")
        self.assertEqual(entrada.estado, Correo.Estado.PROCESADO)
        self.assertIsNone(entrada.sha256)
        self.assertEqual(Documento.objects.get().correo, entrada)

    def test_registra_el_xml_suelto(self):
        respuesta = self.cargar("FE-100.xml", xml_documento())

        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertEqual(Documento.objects.count(), 1)

    def test_sale_en_los_correos_filtrando_por_origen(self):
        self.cargar("factura.zip", self.zip_del_proveedor())

        filas = self.client.get("/api/recepcion/correo/", {"origen": "carga"}).json()["results"]
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["usuario"], self.usuario.email)
        self.assertEqual(len(filas[0]["documentos"]), 1)
        correos = self.client.get("/api/recepcion/correo/", {"origen": "correo"}).json()
        self.assertEqual(correos["count"], 0)

    # --- El receptor tiene que ser el emisor elegido --------------------------

    def test_rechaza_el_documento_de_otro_receptor_y_no_guarda_nada(self):
        # Ni siquiera si ese otro NIT es un emisor de la plataforma.
        self.crear_emisor(crear_usuario(nombre="Otro"), "800555666")

        respuesta = self.cargar("factura.zip", self.zip_del_proveedor(nit_receptor="800555666"))

        self.assertEqual(respuesta.status_code, 400, respuesta.data)
        self.assertIn("800555666", respuesta.data["detail"])
        self.assertFalse(Documento.objects.exists())
        self.assertFalse(Correo.objects.exists())
        self.assertFalse(Adjunto.objects.exists())

    def test_de_un_zip_mezclado_registra_el_suyo_y_rechaza_el_otro(self):
        contenido = zip_con(
            FE_100__xml=xml_documento(),
            FE_101__xml=xml_documento(numero="FE-101", cufe="b" * 96, nit_receptor="800555666"),
        )

        respuesta = self.cargar("dos.zip", contenido)

        self.assertEqual(respuesta.status_code, 201, respuesta.data)
        self.assertEqual(len(respuesta.data["creados"]), 1)
        self.assertEqual(respuesta.data["rechazados"][0]["numero"], "FE-101")
        self.assertEqual(Documento.objects.get().numero, "FE-100")

    def test_un_emisor_fuera_del_alcance_es_400(self):
        ajeno = self.crear_emisor(crear_usuario(nombre="Ajeno"), "800777888")

        respuesta = self.cargar("factura.zip", self.zip_del_proveedor(), emisor=ajeno)

        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(Documento.objects.exists())

    # --- Repetidos --------------------------------------------------------------

    def test_subirlo_dos_veces_no_lo_duplica(self):
        primero = self.cargar("factura.zip", self.zip_del_proveedor())

        respuesta = self.cargar("factura.zip", self.zip_del_proveedor())

        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        self.assertEqual(respuesta.data["creados"], [])
        self.assertEqual(respuesta.data["repetidos"][0]["id"], primero.data["creados"][0]["id"])
        # La segunda no deja ni su fila ni sus archivos.
        self.assertEqual(Correo.objects.count(), 1)
        self.assertEqual(Documento.objects.count(), 1)
        self.assertEqual(Adjunto.objects.count(), 3)

    # --- Archivos que no sirven -------------------------------------------------

    def test_sin_documentos_es_400(self):
        respuesta = self.cargar("cosas.zip", zip_con(leeme__txt=b"hola", FE__pdf=PDF))

        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(Correo.objects.exists())

    def test_un_zip_danado_es_400(self):
        self.assertEqual(self.cargar("factura.zip", b"PK\x03\x04no es un zip").status_code, 400)

    def test_solo_zip_o_xml(self):
        respuesta = self.cargar("factura.pdf", PDF)

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("archivo", respuesta.data["errores"][0]["mensaje"])

    def test_rechaza_un_archivo_demasiado_grande(self):
        with mock.patch.object(carga, "MAXIMO_CARGA", 10):
            respuesta = self.cargar("factura.zip", self.zip_del_proveedor())

        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(Documento.objects.exists())

    def test_sin_autenticar_es_401(self):
        self.client = APIClient()
        self.assertEqual(self.cargar("factura.zip", self.zip_del_proveedor()).status_code, 401)

    # --- Borrado ----------------------------------------------------------------

    def test_el_staff_la_elimina_sin_tocar_r2(self):
        """Una carga no tiene MIME en R2: borrarla no lo necesita."""
        self.cargar("factura.zip", self.zip_del_proveedor())
        entrada = Correo.objects.get()
        admin = get_user_model().objects.create_superuser(
            email="admin@nobelio.co", password="Clave123456",
        )
        self.client.force_authenticate(admin)

        with mock.patch("apps.recepcion.r2.borrar_mime") as borrar_mime, \
                mock.patch("apps.recepcion.r2.r2_habilitado", return_value=False):
            respuesta = self.client.delete(f"/api/recepcion/correo/{entrada.pk}/eliminar-admin/")

        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        borrar_mime.assert_not_called()
        self.assertFalse(Documento.objects.exists())
