"""La API de catálogos: pública, de solo lectura, exportable y documentada."""
from datetime import datetime

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase
from drf_spectacular.generators import SchemaGenerator
from rest_framework import serializers, status
from rest_framework.relations import ManyRelatedField
from rest_framework.test import APITestCase

from apps.catalogos import urls
from apps.catalogos.models import Departamento, ElementoCatalogo, Municipio, Tributo
from apps.catalogos.registro import CATALOGOS, POR_NOMBRE
from apps.documentos.tests_utils import crear_usuario
from apps.seguridad.models import LlaveApi
from apps.seguridad.tests_limites_publicos import con_topes

RUTAS = (
    "/api/catalogos/",
    "/api/catalogos/tributo/",
    "/api/catalogos/tributo/exportar/",
    "/api/catalogos/municipio/",
    "/api/catalogos/municipio/exportar/",
)


class PermisosTests(APITestCase):
    """Públicos y de solo lectura: el sitio de documentación los baja sin llave."""

    @classmethod
    def setUpTestData(cls):
        cls.iva = Tributo.objects.create(codigo="01", nombre="IVA")

    def test_sin_credencial_responden(self):
        for ruta in (*RUTAS, f"/api/catalogos/tributo/{self.iva.pk}/"):
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, status.HTTP_200_OK)

    def test_una_llave_invalida_no_los_tumba(self):
        """No autentican: una credencial rota no convierte en 401 algo público."""
        for ruta in RUTAS:
            with self.subTest(ruta=ruta):
                resp = self.client.get(ruta, HTTP_AUTHORIZATION="Api-Key noexiste.secreto")
                self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_con_llave_valida_siguen_respondiendo(self):
        _, clave = LlaveApi.generar(usuario=crear_usuario(), nombre="ERP")
        resp = self.client.get(
            "/api/catalogos/tributo/?search=IVA", HTTP_AUTHORIZATION=f"Api-Key {clave}"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual([r["codigo"] for r in resp.data["results"]], ["01"])

    def test_no_se_escriben(self):
        detalle = f"/api/catalogos/tributo/{self.iva.pk}/"
        intentos = (
            ("post", "/api/catalogos/tributo/"),
            ("post", "/api/catalogos/tributo/exportar/"),
            ("post", "/api/catalogos/"),
            ("put", detalle),
            ("patch", detalle),
            ("delete", detalle),
        )
        for metodo, ruta in intentos:
            with self.subTest(metodo=metodo, ruta=ruta):
                resp = getattr(self.client, metodo)(
                    ruta, {"codigo": "99", "nombre": "X"}, format="json"
                )
                self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(Tributo.objects.get(pk=self.iva.pk).nombre, "IVA")


class TopeTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    @con_topes(catalogos="2/hour")
    def test_se_corta_por_ip(self):
        for _ in range(2):
            self.assertEqual(self.client.get("/api/catalogos/moneda/").status_code, 200)
        # Todas las rutas de catálogos comparten el cupo, también el índice.
        resp = self.client.get("/api/catalogos/")
        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    @con_topes(catalogos_rafaga="1/min")
    def test_la_rafaga_se_corta(self):
        self.assertEqual(self.client.get("/api/catalogos/moneda/exportar/").status_code, 200)
        resp = self.client.get("/api/catalogos/moneda/exportar/")
        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class ExportarTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        # Más filas que una página, para que se note si pagina.
        cls.total = settings.REST_FRAMEWORK["PAGE_SIZE"] * 2 + 3
        Tributo.objects.bulk_create(
            Tributo(codigo=f"{i:02}", nombre=f"Tributo {i}") for i in range(cls.total)
        )
        cls.antioquia = Departamento.objects.create(codigo="05", nombre="Antioquia")
        Municipio.objects.create(
            codigo="05001", nombre="Medellín", codigo_postal="050001",
            departamento=cls.antioquia,
        )
        Municipio.objects.create(codigo="99999", nombre="Sin departamento")

    def test_devuelve_el_catalogo_entero_sin_paginar(self):
        resp = self.client.get("/api/catalogos/tributo/exportar/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsInstance(resp.data, list)
        self.assertEqual(len(resp.data), self.total)
        self.assertEqual(resp.data[0]["codigo"], "00")

    def test_lleva_los_mismos_campos_que_el_detalle(self):
        exportado = self.client.get("/api/catalogos/municipio/exportar/").data
        medellin = next(m for m in exportado if m["codigo"] == "05001")
        detalle = self.client.get(f"/api/catalogos/municipio/{medellin['id']}/").data
        self.assertEqual(medellin, detalle)

    def test_municipio_trae_departamento_y_codigo_postal(self):
        exportado = {
            m["codigo"]: m
            for m in self.client.get("/api/catalogos/municipio/exportar/").data
        }
        self.assertEqual(exportado["05001"]["departamento"], self.antioquia.pk)
        self.assertEqual(exportado["05001"]["departamento_codigo"], "05")
        self.assertEqual(exportado["05001"]["codigo_postal"], "050001")
        self.assertIsNone(exportado["99999"]["departamento"])
        self.assertEqual(exportado["99999"]["departamento_codigo"], "")

    def test_admite_search(self):
        resp = self.client.get("/api/catalogos/municipio/exportar/?search=medell")
        self.assertEqual([m["codigo"] for m in resp.data], ["05001"])

    def test_el_listado_sigue_paginado(self):
        resp = self.client.get("/api/catalogos/tributo/")
        self.assertEqual(resp.data["count"], self.total)
        self.assertEqual(len(resp.data["results"]), settings.REST_FRAMEWORK["PAGE_SIZE"])

    def test_todos_los_catalogos_exportan(self):
        for catalogo in CATALOGOS:
            with self.subTest(catalogo=catalogo.nombre):
                resp = self.client.get(f"/api/catalogos/{catalogo.nombre}/exportar/")
                self.assertEqual(resp.status_code, status.HTTP_200_OK)
                self.assertEqual(len(resp.data), catalogo.modelo.objects.count())


class IndiceTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        Tributo.objects.create(codigo="01", nombre="IVA")
        cls.ultimo = Tributo.objects.create(codigo="04", nombre="INC")

    def test_enumera_todos_los_catalogos(self):
        resp = self.client.get("/api/catalogos/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual([c["nombre"] for c in resp.data], [c.nombre for c in CATALOGOS])

    def test_cifras_del_catalogo(self):
        tributo = next(c for c in self.client.get("/api/catalogos/").data
                       if c["nombre"] == "tributo")
        self.assertEqual(tributo["registros"], 2)
        self.assertEqual(
            datetime.fromisoformat(tributo["ultima_modificacion"]),
            self.ultimo.actualizado_en,
        )
        self.assertEqual(tributo["lista_dian"], "TipoImpuesto")
        self.assertEqual(tributo["anexos_tecnicos"], [{
            "documento": "Factura Electrónica de Venta",
            "version": "1.9",
            "resolucion": "Resolución 000165 de 2023",
        }])
        self.assertIn(
            {"ruta": "/api/documentos/documento/",
             "campo": "detalles[].impuestos[].tributo", "valor": "id"},
            tributo["usos"],
        )

    def test_catalogo_vacio(self):
        moneda = next(c for c in self.client.get("/api/catalogos/").data
                      if c["nombre"] == "moneda")
        self.assertEqual(moneda["registros"], 0)
        self.assertIsNone(moneda["ultima_modificacion"])

    def test_las_urls_llevan_a_su_catalogo(self):
        for catalogo in self.client.get("/api/catalogos/").data:
            with self.subTest(catalogo=catalogo["nombre"]):
                self.assertEqual(
                    catalogo["url"],
                    f"http://testserver/api/catalogos/{catalogo['nombre']}/",
                )
                resp = self.client.get(catalogo["url_exportar"])
                self.assertEqual(resp.status_code, status.HTTP_200_OK)
                self.assertEqual(len(resp.data), catalogo["registros"])


def _usos_reales():
    """(catálogo, campo, valor) de cada campo de escritura que recibe un catálogo.

    Recorre los serializers de todas las operaciones que escriben, igual que el
    generador del esquema, así que un campo nuevo en cualquier recurso aparece.
    """
    generador = SchemaGenerator()
    generador._initialise_endpoints()
    usos = set()

    def recorrer(serializer, prefijo):
        for nombre, campo in serializer.fields.items():
            if campo.read_only:
                continue
            varios = isinstance(campo, ManyRelatedField)
            relacion = campo.child_relation if varios else campo
            ruta = f"{prefijo}{nombre}{'[]' if varios else ''}"
            if isinstance(relacion, serializers.RelatedField):
                modelo = relacion.queryset.model
                if issubclass(modelo, ElementoCatalogo):
                    valor = "codigo" if getattr(relacion, "slug_field", None) else "id"
                    usos.add((modelo, ruta, valor))
            elif isinstance(campo, serializers.ListSerializer):
                recorrer(campo.child, f"{ruta}[].")
            elif isinstance(campo, serializers.Serializer):
                recorrer(campo, f"{ruta}.")

    for _, _, metodo, callback in generador.endpoints:
        if metodo not in ("POST", "PUT", "PATCH"):
            continue
        vista = generador.create_view(callback, metodo)
        if not hasattr(vista, "get_serializer"):
            continue
        serializer = vista.get_serializer()
        if isinstance(serializer, serializers.ListSerializer):
            serializer = serializer.child
        recorrer(serializer, "")
    return usos


class RegistroTests(SimpleTestCase):
    """La ficha de cada catálogo no se queda atrás del código."""

    def test_hay_ficha_para_cada_ruta(self):
        rutas = [prefijo for prefijo, _, _ in urls.router.registry]
        self.assertEqual(rutas, [c.nombre for c in CATALOGOS])
        for prefijo, vista, _ in urls.router.registry:
            self.assertIs(vista.queryset.model, POR_NOMBRE[prefijo].modelo)

    def test_los_usos_son_los_de_los_serializers(self):
        documentados = {
            (c.modelo, u.campo, u.valor) for c in CATALOGOS for u in c.usos
        }
        reales = _usos_reales()
        self.assertEqual(
            documentados - reales, set(),
            "Usos documentados en registro.py que ningún serializer tiene.",
        )
        self.assertEqual(
            reales - documentados, set(),
            "Campos de catálogo sin documentar en apps/catalogos/registro.py.",
        )
