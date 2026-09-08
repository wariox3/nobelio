"""El NIT es único en toda la plataforma, y la numeración también.

Antes un mismo NIT podía estar dado de alta en varias cuentas —facturación por
una integración y nómina por otra, o dos a la vez mientras un cliente migraba de
proveedor—. Al desaparecer la cuenta, esa convivencia desapareció con ella: hay
un solo emisor por identificación y todo cuelga de él.

Lo que no cambió es la regla de numeración: la DIAN autoriza un solo rango por
prefijo, así que dos resoluciones **activas** con el mismo número y prefijo
producirían consecutivos repetidos.
"""
from datetime import date

from django.db.utils import IntegrityError
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalogos.models import TipoFactura
from apps.documentos.tests_utils import crear_catalogos_minimos, crear_usuario
from apps.emisores.models import Emisor, Resolucion
from apps.emisores.serializers.emisor import MENSAJE_DUPLICADO
from apps.nucleo.api import MENSAJE_GENERICO
from apps.seguridad.models import Usuario

NIT = "901192048"
URL_RESOLUCIONES = "/api/emisores/resolucion/"
URL_EMISORES = "/api/emisores/emisor/"


class NitUnicoTests(APITestCase):
    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.tipo_factura = TipoFactura.objects.create(codigo="01", nombre="Factura")

        self.dueno = crear_usuario(nombre="Semantica")
        self.otro = crear_usuario(nombre="Otra persona")
        self.emisor = self.crear_emisor(self.dueno, correo="facturacion@empresa.co")

        admin = Usuario.objects.create_superuser(
            email="admin@nobelio.co", password="ClaveSegura123"
        )
        self.client.force_authenticate(admin)

    def crear_emisor(self, usuario, correo, nit=NIT):
        c = self.cat
        return Emisor.objects.create(
            usuario=usuario,
            razon_social="Semantica Digital S.A.S",
            correo=correo,
            tipo_identificacion=c["nit"],
            numero_identificacion=nit,
            tipo_organizacion=c["juridica"],
            pais=c["colombia"],
            departamento=c["antioquia"],
            municipio=c["medellin"],
            direccion="Calle 1 # 2-3",
        )

    def payload_emisor(self, nit=NIT):
        c = self.cat
        return {
            "razon_social": "Semantica Digital S.A.S",
            "tipo_identificacion": c["nit"].id,
            "numero_identificacion": nit,
            "tipo_organizacion": c["juridica"].id,
            "pais": c["colombia"].codigo,
            "departamento": c["antioquia"].codigo,
            "municipio": c["medellin"].codigo,
            "direccion": "Calle 1 # 2-3",
        }

    def payload_resolucion(self, emisor, prefijo="SETP", numero="18760000001"):
        return {
            "emisor": emisor.id,
            "tipo_factura": self.tipo_factura.id,
            "numero_resolucion": numero,
            "fecha_resolucion": "2024-01-01",
            "prefijo": prefijo,
            "rango_desde": 1,
            "rango_hasta": 5000,
            "vigente_desde": "2024-01-01",
            "vigente_hasta": "2030-01-01",
        }

    # --- Unicidad del NIT --------------------------------------------------

    def test_el_mismo_nit_no_se_repite_ni_para_otra_persona(self):
        """La unicidad es global: antes esto convivía en dos cuentas."""
        with self.assertRaises(IntegrityError):
            self.crear_emisor(self.otro, correo="otro@empresa.co")

    def test_el_alta_duplicada_se_explica_en_el_campo_del_documento(self):
        """El error señala el campo a corregir, no `non_field_errors`."""
        resp = self.client.post(URL_EMISORES, self.payload_emisor(), format="json")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("non_field_errors", resp.data["errores"])
        self.assertEqual(
            resp.data["errores"]["numero_identificacion"],
            [MENSAJE_DUPLICADO.format(numero=NIT)],
        )
        self.assertEqual(resp.data["detail"], MENSAJE_GENERICO)

    def test_el_mensaje_no_revela_de_quien_es_el_nit(self):
        """Decir quién lo tiene sería filtrar quién usa la plataforma."""
        resp = self.client.post(URL_EMISORES, self.payload_emisor(), format="json")
        texto = str(resp.data["errores"]["numero_identificacion"])
        self.assertNotIn(self.dueno.email, texto)
        self.assertNotIn("Semantica", texto.replace("Semantica Digital", ""))

    def test_otro_nit_si_se_puede_dar_de_alta(self):
        resp = self.client.post(
            URL_EMISORES, self.payload_emisor(nit="900123456"), format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_editar_un_emisor_sin_cambiar_su_nit_no_choca_consigo_mismo(self):
        resp = self.client.patch(
            f"{URL_EMISORES}{self.emisor.id}/",
            {"nombre_comercial": "Semántica"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

    # --- La numeración sigue siendo una sola -------------------------------

    def test_no_se_puede_activar_dos_veces_la_misma_resolucion(self):
        primera = self.client.post(
            URL_RESOLUCIONES, self.payload_resolucion(self.emisor), format="json"
        )
        self.assertEqual(primera.status_code, status.HTTP_201_CREATED, primera.data)

        segunda = self.client.post(
            URL_RESOLUCIONES, self.payload_resolucion(self.emisor), format="json"
        )
        self.assertEqual(segunda.status_code, status.HTTP_400_BAD_REQUEST)

    def test_un_prefijo_distinto_si_puede_convivir(self):
        # Facturación con un rango y nómina con otro: son rangos DIAN distintos.
        self.client.post(
            URL_RESOLUCIONES, self.payload_resolucion(self.emisor), format="json"
        )
        otra = self.client.post(
            URL_RESOLUCIONES,
            self.payload_resolucion(self.emisor, prefijo="NOMI", numero="18760000002"),
            format="json",
        )
        self.assertEqual(otra.status_code, status.HTTP_201_CREATED, otra.data)

    def test_la_misma_resolucion_no_se_registra_dos_veces(self):
        """Ni desactivando la anterior: la unicidad la impone la base.

        Antes esto se probaba entre dos emisores del mismo NIT en cuentas
        distintas, donde sí tenía sentido "liberar el rango". Con un solo emisor
        por identificación, `(emisor, tipo_factura, prefijo, numero)` ya es único
        y no hay nada que liberar.
        """
        vieja = self.client.post(
            URL_RESOLUCIONES, self.payload_resolucion(self.emisor), format="json"
        )
        self.client.patch(
            f"{URL_RESOLUCIONES}{vieja.data['id']}/", {"activa": False}, format="json"
        )
        repetida = self.client.post(
            URL_RESOLUCIONES, self.payload_resolucion(self.emisor), format="json"
        )
        self.assertEqual(repetida.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Resolucion.objects.filter(emisor=self.emisor).count(), 1)

    def test_editar_la_propia_resolucion_no_choca_consigo_misma(self):
        creada = self.client.post(
            URL_RESOLUCIONES, self.payload_resolucion(self.emisor), format="json"
        )
        resp = self.client.patch(
            f"{URL_RESOLUCIONES}{creada.data['id']}/",
            {"rango_hasta": 9000},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

    def test_fecha_resolucion_se_guarda(self):
        creada = self.client.post(
            URL_RESOLUCIONES, self.payload_resolucion(self.emisor), format="json"
        )
        resolucion = Resolucion.objects.get(pk=creada.data["id"])
        self.assertEqual(resolucion.fecha_resolucion, date(2024, 1, 1))
