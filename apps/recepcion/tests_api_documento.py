"""La API de consulta de los documentos recibidos."""
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from apps.documentos.tests_utils import crear_catalogos_minimos, crear_usuario
from apps.emisores.models import Emisor
from apps.recepcion.models import Correo, Documento
from apps.recepcion.tests_utils import crear_documento_recibido

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
        self, emisor, cufe, *, xml_factura=False, pdf=False, **datos,
    ):
        return crear_documento_recibido(
            self.correo, emisor, cufe, moneda=self.cat["cop"],
            xml_documento=b"<Invoice/>" if xml_factura else None,
            pdf=b"%PDF" if pdf else None, **datos,
        )

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
        self.assertNotIn("adjuntos", fila)

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

    def test_busca_el_cufe_completo(self):
        self.assertEqual(self.ids(search="1" * 96), {str(self.factura.pk)})
        # Un pedazo de CUFE no encuentra nada: la búsqueda es exacta.
        self.assertEqual(self.ids(search="1" * 20), set())

    def test_busca_el_nit_del_proveedor_por_el_comienzo(self):
        self.assertEqual(self.ids(search="8001"), {str(self.factura.pk)})
        self.assertEqual(self.ids(search="111222"), set())

    def test_busca_por_numero(self):
        self.assertEqual(self.ids(search="FE-2"), {str(self.nota.pk)})

    # --- Paginación -----------------------------------------------------------

    def test_pagina_de_25_por_defecto(self):
        for i in range(30):
            self.crear_documento(self.emisor, chr(ord("A") + i))

        cuerpo = self.client.get(URL).json()

        self.assertEqual(cuerpo["count"], 32)
        self.assertEqual(len(cuerpo["results"]), 25)
        self.assertIsNotNone(cuerpo["next"])

    def test_el_cliente_elige_el_tamano_hasta_100(self):
        self.assertEqual(len(self.client.get(URL, {"page_size": 1}).json()["results"]), 1)
        # Por encima del tope se queda en el tope, no falla.
        respuesta = self.client.get(URL, {"page_size": 500})
        self.assertEqual(respuesta.status_code, 200)

    def test_con_empates_ninguna_fila_se_repite_ni_se_pierde(self):
        """Mismo día y mismo `creado_en`: solo el `id` desempata."""
        for i in range(7):
            self.crear_documento(self.emisor, chr(ord("A") + i), fecha="2026-10-03")
        Documento.objects.filter(emisor=self.emisor).update(creado_en=self.factura.creado_en)

        for ordering in (None, "fecha_emision", "-total_a_pagar"):
            vistos = []
            for pagina in range(1, 5):
                filtros = {"page": pagina, "page_size": 3, "emisor": self.emisor.pk}
                if ordering:
                    filtros["ordering"] = ordering
                respuesta = self.client.get(URL, filtros)
                if respuesta.status_code == 404:
                    break
                vistos += [fila["id"] for fila in respuesta.json()["results"]]
            self.assertEqual(len(vistos), 9, ordering)
            self.assertEqual(len(set(vistos)), 9, ordering)

    def test_las_consultas_no_crecen_con_la_pagina(self):
        """Los flags de PDF y XML salen en la misma consulta del listado."""
        for i in range(10):
            self.crear_documento(self.emisor, chr(ord("A") + i), pdf=True)

        with CaptureQueriesContext(connection) as pocas:
            self.client.get(URL, {"page_size": 2})
        with CaptureQueriesContext(connection) as muchas:
            filas = self.client.get(URL, {"page_size": 12}).json()["results"]

        self.assertEqual(len(pocas), len(muchas))
        self.assertTrue(all(fila["tiene_pdf"] for fila in filas if fila["numero"] != "FE-2"))

    def test_filtro_invalido_es_400(self):
        self.assertEqual(self.client.get(URL, {"desde": "ayer"}).status_code, 400)

    def test_descarga_el_xml_recibido(self):
        respuesta, contenido = self.descargar(self.factura, "xml")

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta["Content-Type"], "application/xml")
        self.assertIn('filename="ad-FE-1.xml"', respuesta["Content-Disposition"])
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

    def test_no_se_crea_ni_se_edita(self):
        # Los crea el procesamiento; por la API solo se consultan y se eliminan
        # (los que no tienen eventos, en tests_eventos.EliminarDocumentoTests).
        self.assertEqual(self.client.post(URL, {}).status_code, 405)
        self.assertEqual(self.client.patch(f"{URL}{self.factura.pk}/", {}).status_code, 405)
        self.assertEqual(self.client.put(f"{URL}{self.factura.pk}/", {}).status_code, 405)

    def test_sin_autenticar_es_401(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)

    def test_no_pisa_la_ruta_de_los_documentos_emitidos(self):
        self.assertEqual(
            reverse("documento-detail", args=["x"]), "/api/documentos/documento/x/",
        )
