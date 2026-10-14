"""Pruebas del `ambiente` y el `modulo` de la resolución, y de sus filtros."""
from rest_framework.test import APITestCase

from apps.catalogos.models import TipoFactura
from apps.documentos.tests_utils import crear_catalogos_minimos, crear_usuario
from apps.emisores.models import Emisor, Resolucion
from apps.nucleo.models import Ambiente
from apps.nucleo.tests_utils import errores_por_campo
from apps.seguridad.models import Usuario

URL = "/api/emisores/resolucion/"


class ResolucionAmbienteYModuloTests(APITestCase):
    def setUp(self):
        self.cat = crear_catalogos_minimos()
        # Con los ids del catálogo real: el módulo por defecto sale de ellos.
        self.factura = TipoFactura.objects.create(id=1, codigo="01", nombre="Factura")
        self.soporte = TipoFactura.objects.create(
            id=7, codigo="05", nombre="Documento soporte",
        )
        self.nota_ajuste = TipoFactura.objects.create(
            id=8, codigo="95", nombre="Nota de ajuste al documento soporte",
        )
        self.pos = TipoFactura.objects.create(
            id=9, codigo="20", nombre="Documento equivalente P.O.S.",
        )
        self.emisor = self._emisor("901192048")
        admin = Usuario.objects.create_superuser(
            email="admin@nobelio.co", password="ClaveSegura123"
        )
        self.client.force_authenticate(admin)

    def _emisor(self, nit):
        c = self.cat
        return Emisor.objects.create(
            usuario=crear_usuario(nombre=nit), razon_social="Semantica Digital S.A.S",
            tipo_identificacion=c["nit"], numero_identificacion=nit,
            tipo_organizacion=c["juridica"], pais=c["colombia"],
            departamento=c["antioquia"], municipio=c["medellin"],
            direccion="Calle 1 # 2-3",
        )

    def _payload(self, **extra):
        payload = {
            "emisor": self.emisor.id,
            "tipo_factura": self.factura.id,
            "numero_resolucion": "18764000001",
            "fecha_resolucion": "2026-01-01",
            "prefijo": "FE",
            "rango_desde": 1,
            "rango_hasta": 5000,
            "vigente_desde": "2026-01-01",
            "vigente_hasta": "2030-01-01",
            "ambiente": Ambiente.PRODUCCION,
        }
        payload.update(extra)
        return payload

    def _crear(self, **extra):
        datos = dict(
            emisor=self.emisor, tipo_factura=self.factura,
            numero_resolucion="18764000001", fecha_resolucion="2026-01-01",
            prefijo="FE", rango_desde=1, rango_hasta=5000,
            vigente_desde="2026-01-01", vigente_hasta="2030-01-01",
            ambiente=Ambiente.PRODUCCION,
        )
        datos.update(extra)
        return Resolucion.objects.create(**datos)

    def _prefijos(self, **filtros):
        resp = self.client.get(URL, filtros)
        self.assertEqual(resp.status_code, 200, resp.data)
        return sorted(r["prefijo"] for r in resp.data["results"])

    # --- ambiente -----------------------------------------------------------

    def test_el_alta_manual_exige_el_ambiente(self):
        payload = self._payload()
        del payload["ambiente"]

        resp = self.client.post(URL, payload, format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("ambiente", errores_por_campo(resp))
        self.assertFalse(Resolucion.objects.exists())

    def test_el_ambiente_tiene_que_ser_uno_de_la_dian(self):
        resp = self.client.post(URL, self._payload(ambiente=3), format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("ambiente", errores_por_campo(resp))

    def test_el_ambiente_se_guarda_y_se_devuelve(self):
        resp = self.client.post(
            URL, self._payload(ambiente=Ambiente.PRUEBAS), format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["ambiente"], Ambiente.PRUEBAS)
        self.assertEqual(Resolucion.objects.get().ambiente, Ambiente.PRUEBAS)

    def test_filtra_por_ambiente(self):
        self._crear(prefijo="FE")
        self._crear(prefijo="SETP", ambiente=Ambiente.PRUEBAS)

        self.assertEqual(self._prefijos(ambiente=Ambiente.PRODUCCION), ["FE"])
        self.assertEqual(self._prefijos(ambiente=Ambiente.PRUEBAS), ["SETP"])
        self.assertEqual(self._prefijos(), ["FE", "SETP"])

    def test_el_filtro_de_ambiente_con_basura_es_400(self):
        resp = self.client.get(URL, {"ambiente": "pruebas"})

        self.assertEqual(resp.status_code, 400, resp.data)

    # --- modulo -------------------------------------------------------------

    def test_sin_modulo_sale_del_tipo_de_factura(self):
        esperado = {
            self.factura: Resolucion.Modulo.FACTURACION,
            self.pos: Resolucion.Modulo.FACTURACION,
            self.soporte: Resolucion.Modulo.COMPRAS,
            self.nota_ajuste: Resolucion.Modulo.COMPRAS,
        }
        for tipo, modulo in esperado.items():
            with self.subTest(tipo=tipo.codigo):
                resp = self.client.post(
                    URL,
                    self._payload(tipo_factura=tipo.id, prefijo=f"P{tipo.codigo}"),
                    format="json",
                )

                self.assertEqual(resp.status_code, 201, resp.data)
                self.assertEqual(resp.data["modulo"], modulo)

    def test_el_modulo_indicado_gana_al_del_tipo(self):
        """La importación de la DIAN registra el documento soporte como 01."""
        resp = self.client.post(
            URL, self._payload(modulo=Resolucion.Modulo.COMPRAS), format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["modulo"], Resolucion.Modulo.COMPRAS)
        self.assertEqual(Resolucion.objects.get().modulo, Resolucion.Modulo.COMPRAS)

    def test_el_modulo_se_puede_corregir(self):
        resolucion = self._crear()
        self.assertEqual(resolucion.modulo, Resolucion.Modulo.FACTURACION)

        resp = self.client.patch(
            f"{URL}{resolucion.pk}/", {"modulo": Resolucion.Modulo.COMPRAS},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        resolucion.refresh_from_db()
        self.assertEqual(resolucion.modulo, Resolucion.Modulo.COMPRAS)

    def test_el_modulo_corregido_no_se_pisa_al_guardar_otra_cosa(self):
        resolucion = self._crear(modulo=Resolucion.Modulo.COMPRAS)

        resp = self.client.patch(
            f"{URL}{resolucion.pk}/", {"rango_hasta": 9000}, format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        resolucion.refresh_from_db()
        self.assertEqual(resolucion.modulo, Resolucion.Modulo.COMPRAS)

    def test_un_modulo_desconocido_es_400(self):
        resp = self.client.post(URL, self._payload(modulo="ventas"), format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("modulo", errores_por_campo(resp))

    def test_filtra_por_modulo(self):
        self._crear(prefijo="FE")
        self._crear(prefijo="DS", tipo_factura=self.soporte)

        self.assertEqual(self._prefijos(modulo="facturacion"), ["FE"])
        self.assertEqual(self._prefijos(modulo="compras"), ["DS"])
        # La nómina no se numera con resolución: nada cae ahí por sí solo.
        self.assertEqual(self._prefijos(modulo="nomina"), [])

    def test_el_filtro_de_modulo_desconocido_es_400(self):
        resp = self.client.get(URL, {"modulo": "ventas"})

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("facturacion, nomina, compras", str(resp.data))

    def test_los_filtros_se_combinan(self):
        otro = self._emisor("900000001")
        self._crear(prefijo="FE")
        self._crear(prefijo="SETP", ambiente=Ambiente.PRUEBAS)
        self._crear(prefijo="DS", tipo_factura=self.soporte)
        self._crear(prefijo="OTRO", emisor=otro)

        self.assertEqual(
            self._prefijos(
                emisor=self.emisor.id, ambiente=Ambiente.PRODUCCION,
                modulo="facturacion",
            ),
            ["FE"],
        )
