"""La API de consulta de los documentos recibidos."""
from datetime import date

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from apps.documentos.models import DocumentoTipo
from apps.documentos.tests_utils import crear_catalogos_minimos, crear_usuario
from apps.emisores.models import Emisor
from apps.recepcion.models import Correo, Documento

Usuario = get_user_model()

URL = "/api/recepcion/documento/"


class DocumentoRecibidoApiTests(APITestCase):
    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.usuario = crear_usuario(nombre="Dueño")
        self.emisor = self.crear_emisor(self.usuario, "900000001")
        self.emisor_ajeno = self.crear_emisor(crear_usuario(nombre="Ajeno"), "900000003")
        self.correo = Correo.objects.create(
            alias="900000001", emisor=self.emisor, sha256="a" * 64,
            raw_key="k.eml", envelope_to="900000001@recepcion.rededoc.co",
        )
        self.factura = self.crear_documento(
            self.emisor, "1", fecha="2026-10-01", proveedor="800111222",
            razon_social="Papelería Central", xml_factura=True, pdf=True,
        )
        self.nota = self.crear_documento(
            self.emisor, "2", tipo="nota_credito", fecha="2026-10-05",
            proveedor="800333444", razon_social="Ferretería Sur",
        )
        self.ajeno = self.crear_documento(self.emisor_ajeno, "3", fecha="2026-10-02")
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

    def crear_documento(
        self, emisor, cufe, *, tipo="factura_venta", fecha, proveedor="800999999",
        razon_social="Proveedor", xml_factura=False, pdf=False,
    ):
        documento = Documento(
            numero=f"FE-{cufe}", cufe_cude=cufe * 96, fecha_emision=date.fromisoformat(fecha),
            proveedor_numero_identificacion=proveedor, proveedor_razon_social=razon_social,
            receptor_numero_identificacion=emisor.numero_identificacion,
            total_a_pagar="1000.00", emisor=emisor, correo=self.correo,
            documento_tipo=DocumentoTipo.objects.get(codigo=tipo), moneda=self.cat["cop"],
        )
        documento.xml_archivo.save("ad.xml", ContentFile(b"<AttachedDocument/>"), save=False)
        if xml_factura:
            documento.xml_factura_archivo.save("fe.xml", ContentFile(b"<Invoice/>"), save=False)
        if pdf:
            documento.pdf_archivo.save("fe.pdf", ContentFile(b"%PDF"), save=False)
        documento.save()
        return documento

    def ids(self, **filtros):
        respuesta = self.client.get(URL, filtros)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return {fila["id"] for fila in respuesta.json()["results"]}

    def descargar(self, documento, ruta):
        respuesta = self.client.get(f"{URL}{documento.pk}/{ruta}/")
        contenido = b"".join(respuesta.streaming_content) if respuesta.status_code == 200 else b""
        return respuesta, contenido

    def test_lista_solo_los_de_sus_emisores(self):
        self.assertEqual(self.ids(), {str(self.factura.pk), str(self.nota.pk)})

    def test_la_llave_global_y_el_staff_ven_todos(self):
        admin = Usuario.objects.create_superuser(email="admin@nobelio.co", password="Clave123456")
        self.client.force_authenticate(admin)

        self.assertEqual(len(self.ids()), 3)

    def test_detalle(self):
        fila = self.client.get(f"{URL}{self.factura.pk}/").json()

        self.assertEqual(fila["documento_tipo"], "factura_venta")
        self.assertEqual(fila["moneda"], "COP")
        self.assertEqual(fila["correo"], self.correo.pk)
        self.assertTrue(fila["tiene_pdf"])
        self.assertTrue(fila["tiene_xml_factura"])
        self.assertNotIn("xml_archivo", fila)

    def test_el_ajeno_es_404(self):
        self.assertEqual(self.client.get(f"{URL}{self.ajeno.pk}/").status_code, 404)
        self.assertEqual(self.descargar(self.ajeno, "xml")[0].status_code, 404)

    def test_filtros(self):
        self.assertEqual(self.ids(documento_tipo="nota_credito"), {str(self.nota.pk)})
        self.assertEqual(self.ids(proveedor="800111222"), {str(self.factura.pk)})
        self.assertEqual(self.ids(desde="2026-10-02"), {str(self.nota.pk)})
        self.assertEqual(self.ids(hasta="2026-10-01"), {str(self.factura.pk)})
        self.assertEqual(self.ids(correo=self.correo.pk), {str(self.factura.pk), str(self.nota.pk)})
        self.assertEqual(self.ids(emisor=self.emisor_ajeno.pk), set())

    def test_busca_por_razon_social_del_proveedor(self):
        self.assertEqual(self.ids(search="ferreter"), {str(self.nota.pk)})

    def test_filtro_invalido_es_400(self):
        self.assertEqual(self.client.get(URL, {"desde": "ayer"}).status_code, 400)

    def test_descarga_el_xml_recibido(self):
        respuesta, contenido = self.descargar(self.factura, "xml")

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta["Content-Type"], "application/xml")
        self.assertIn('filename="FE-1.xml"', respuesta["Content-Disposition"])
        self.assertEqual(contenido, b"<AttachedDocument/>")

    def test_descarga_el_xml_de_la_factura(self):
        self.assertEqual(self.descargar(self.factura, "xml-factura")[1], b"<Invoice/>")

    def test_sin_attached_el_xml_de_la_factura_es_el_recibido(self):
        self.assertEqual(self.descargar(self.nota, "xml-factura")[1], b"<AttachedDocument/>")

    def test_descarga_el_pdf(self):
        respuesta, contenido = self.descargar(self.factura, "pdf")

        self.assertEqual(respuesta["Content-Type"], "application/pdf")
        self.assertEqual(contenido, b"%PDF")

    def test_sin_pdf_es_400(self):
        self.assertEqual(self.descargar(self.nota, "pdf")[0].status_code, 400)

    def test_es_de_solo_lectura(self):
        self.assertEqual(self.client.post(URL, {}).status_code, 405)
        self.assertEqual(self.client.delete(f"{URL}{self.factura.pk}/").status_code, 405)

    def test_sin_autenticar_es_401(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)

    def test_no_pisa_la_ruta_de_los_documentos_emitidos(self):
        self.assertEqual(
            reverse("documento-detail", args=["x"]), "/api/documentos/documento/x/",
        )
