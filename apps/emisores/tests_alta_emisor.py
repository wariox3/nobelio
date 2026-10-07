"""Reglas del alta del emisor: RUES, ubicación por id y tipos de datos.

El NIT no se contrasta con el RUES.

El RUES es un registro ajeno y a veces caído: consultarlo al dar de alta ataba
la creación de emisores a que un tercero respondiera. Queda como consulta
voluntaria en ``GET /api/emisores/emisor/validar-nit/``, para autocompletar el
formulario. Lo que sí se sigue exigiendo es que el NIT no esté ya dado de alta
en la plataforma (ver ``tests_nit_unico``).
"""
from unittest import mock

from rest_framework import status
from rest_framework.test import APITestCase

from apps.documentos.tests_utils import crear_catalogos_minimos
from apps.catalogos.models import Departamento
from apps.catalogos.models.municipio import mensaje_municipio_de_otro_departamento
from apps.emisores.models import Emisor
from apps.emisores.serializers.emisor import CAMPOS_ACTUALIZABLES, MENSAJE_NO_ACTUALIZABLE
from apps.nucleo.serializers import MENSAJE_LISTA_DE_TEXTOS, MENSAJES_TIPO
from apps.seguridad.models import Usuario
from apps.nucleo.tests_utils import errores_por_campo

URL_EMISORES = "/api/emisores/emisor/"
_RUES = "apps.utilidades.rues.consultar_nit"


class AltaSinValidarRuesTests(APITestCase):
    def setUp(self):
        self.cat = crear_catalogos_minimos()
        admin = Usuario.objects.create_superuser(
            email="admin@nobelio.co", password="ClaveSegura123"
        )
        self.client.force_authenticate(admin)

    def payload(self, **extra):
        c = self.cat
        datos = {
            "razon_social": "Semantica Digital S.A.S",
            "tipo_identificacion": c["nit"].id,
            "numero_identificacion": "901192048",
            "tipo_organizacion": c["juridica"].id,
            "pais": c["colombia"].id,
            "departamento": c["antioquia"].id,
            "municipio": c["medellin"].id,
            "direccion": "Calle 1 # 2-3",
            "correo": "facturacion@empresa.co",
        }
        datos.update(extra)
        return datos

    def test_el_alta_no_consulta_el_rues(self):
        with mock.patch(_RUES) as consultar:
            resp = self.client.post(URL_EMISORES, self.payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        consultar.assert_not_called()

    def test_un_nit_que_no_esta_en_el_rues_no_bloquea_el_alta(self):
        with mock.patch(_RUES, return_value=None):
            resp = self.client.post(
                URL_EMISORES, self.payload(numero_identificacion="000000000"),
                format="json",
            )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertTrue(
            Emisor.objects.filter(numero_identificacion="000000000").exists()
        )

    # --- Ubicación por id, no por código ----------------------------------

    def test_la_ubicacion_llega_por_id_y_se_guarda_la_fila_correcta(self):
        resp = self.client.post(URL_EMISORES, self.payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        emisor = Emisor.objects.get(numero_identificacion="901192048")
        self.assertEqual(emisor.pais, self.cat["colombia"])
        self.assertEqual(emisor.departamento, self.cat["antioquia"])
        self.assertEqual(emisor.municipio, self.cat["medellin"])

    def test_la_respuesta_devuelve_los_ids(self):
        resp = self.client.post(URL_EMISORES, self.payload(), format="json")
        self.assertEqual(resp.data["pais"], self.cat["colombia"].id)
        self.assertEqual(resp.data["departamento"], self.cat["antioquia"].id)
        self.assertEqual(resp.data["municipio"], self.cat["medellin"].id)

    def test_la_ubicacion_es_obligatoria(self):
        for campo in ("pais", "departamento", "municipio"):
            with self.subTest(campo=campo):
                datos = self.payload()
                del datos[campo]
                resp = self.client.post(URL_EMISORES, datos, format="json")
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(campo, errores_por_campo(resp))

    def test_un_id_que_no_esta_en_el_catalogo_se_rechaza(self):
        resp = self.client.post(
            URL_EMISORES, self.payload(municipio=999999), format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("municipio", errores_por_campo(resp))

    def test_mandar_el_codigo_en_vez_del_id_se_rechaza(self):
        """Ni el código DANE ni el id como texto: el id va como entero."""
        for campo, valor in (
            ("pais", "CO"),
            ("departamento", "05"),
            ("municipio", "05001"),
            ("municipio", str(self.cat["medellin"].id)),
        ):
            with self.subTest(campo=campo, valor=valor):
                resp = self.client.post(
                    URL_EMISORES, self.payload(**{campo: valor}), format="json"
                )
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(
                    errores_por_campo(resp), {campo: [MENSAJES_TIPO[int]]}
                )

    def test_el_municipio_tiene_que_ser_del_departamento(self):
        cundinamarca = Departamento.objects.create(
            id=11, codigo="25", nombre="Cundinamarca"
        )
        resp = self.client.post(
            URL_EMISORES, self.payload(departamento=cundinamarca.id), format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(errores_por_campo(resp), {
            "municipio": [
                mensaje_municipio_de_otro_departamento(self.cat["medellin"], cundinamarca)
            ],
        })

    # --- Correo -------------------------------------------------------------

    def test_el_correo_es_obligatorio(self):
        for datos in (
            {k: v for k, v in self.payload().items() if k != "correo"},
            self.payload(correo=""),
            self.payload(correo=None),
            self.payload(correo="no-es-un-correo"),
        ):
            with self.subTest(correo=datos.get("correo", "<ausente>")):
                resp = self.client.post(URL_EMISORES, datos, format="json")
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("correo", errores_por_campo(resp))

    def test_un_emisor_antiguo_sin_correo_se_edita_sin_mandarlo(self):
        """El PATCH no exige lo que no toca, aunque el emisor no lo tenga."""
        self.client.post(URL_EMISORES, self.payload(), format="json")
        Emisor.objects.update(correo="")
        emisor = Emisor.objects.get()
        resp = self.client.patch(
            f"{URL_EMISORES}{emisor.id}/", {"direccion": "Calle 10 # 20-30"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

    def test_el_correo_no_se_puede_vaciar_al_editar(self):
        self.client.post(URL_EMISORES, self.payload(), format="json")
        emisor = Emisor.objects.get()
        resp = self.client.patch(
            f"{URL_EMISORES}{emisor.id}/", {"correo": ""}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("correo", errores_por_campo(resp))

    # --- Tipos de datos -----------------------------------------------------

    def test_cada_campo_exige_su_tipo(self):
        for campo, valor, tipo in (
            ("tipo_identificacion", "31", int),
            ("tipo_identificacion", True, int),
            ("tipo_organizacion", 1.0, int),
            ("razon_social", 123, str),
            ("numero_identificacion", 901192048, str),
            ("direccion", ["Calle 1"], str),
            ("activo", "true", bool),
            ("activo", 1, bool),
            ("ambiente_facturacion", "2", int),
        ):
            with self.subTest(campo=campo, valor=valor):
                resp = self.client.post(
                    URL_EMISORES, self.payload(**{campo: valor}), format="json"
                )
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(
                    errores_por_campo(resp), {campo: [MENSAJES_TIPO[tipo]]}
                )

    def test_las_responsabilidades_son_una_lista_de_codigos(self):
        for valor, mensaje in (
            ("O-13", MENSAJES_TIPO[list]),
            ([13], MENSAJE_LISTA_DE_TEXTOS),
        ):
            with self.subTest(valor=valor):
                resp = self.client.post(
                    URL_EMISORES, self.payload(responsabilidades=valor),
                    format="json",
                )
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(
                    errores_por_campo(resp), {"responsabilidades": [mensaje]}
                )

    def test_se_informan_todos_los_errores_de_tipo_juntos(self):
        resp = self.client.post(
            URL_EMISORES, self.payload(pais="CO", razon_social=1), format="json"
        )
        self.assertEqual(set(errores_por_campo(resp)), {"pais", "razon_social"})

    def test_el_nulo_lo_decide_allow_null_y_no_el_tipo(self):
        resp = self.client.post(
            URL_EMISORES, self.payload(cuenta=None), format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_editar_tambien_exige_los_tipos(self):
        self.client.post(URL_EMISORES, self.payload(), format="json")
        emisor = Emisor.objects.get(numero_identificacion="901192048")
        resp = self.client.patch(
            f"{URL_EMISORES}{emisor.id}/", {"municipio": "05001"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(errores_por_campo(resp), {"municipio": [MENSAJES_TIPO[int]]})



class ActualizacionEmisorTests(APITestCase):
    """Del emisor dado de alta solo se actualiza lo de `CAMPOS_ACTUALIZABLES`."""

    def setUp(self):
        self.cat = crear_catalogos_minimos()
        admin = Usuario.objects.create_superuser(
            email="admin@nobelio.co", password="ClaveSegura123"
        )
        self.client.force_authenticate(admin)
        c = self.cat
        resp = self.client.post(URL_EMISORES, {
            "razon_social": "Semantica Digital S.A.S",
            "tipo_identificacion": c["nit"].id,
            "numero_identificacion": "901192048",
            "tipo_organizacion": c["juridica"].id,
            "pais": c["colombia"].id,
            "departamento": c["antioquia"].id,
            "municipio": c["medellin"].id,
            "direccion": "Calle 1 # 2-3",
            "correo": "facturacion@empresa.co",
            "referencia_externa": "15",
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.url = f"{URL_EMISORES}{resp.data['id']}/"

    def test_se_actualizan_los_campos_permitidos(self):
        c = self.cat
        cambios = {
            "razon_social": "Semantica Digital SAS",
            "tipo_organizacion": c["juridica"].id,
            "direccion": "Calle 10 # 20-30",
            "pais": c["colombia"].id,
            "departamento": c["antioquia"].id,
            "municipio": c["medellin"].id,
            "correo": "otro@empresa.co",
        }
        self.assertEqual(set(cambios), set(CAMPOS_ACTUALIZABLES))
        resp = self.client.patch(self.url, cambios, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        emisor = Emisor.objects.get()
        self.assertEqual(emisor.razon_social, "Semantica Digital SAS")
        self.assertEqual(emisor.direccion, "Calle 10 # 20-30")
        self.assertEqual(emisor.correo, "otro@empresa.co")

    def test_el_resto_de_campos_no_se_actualiza(self):
        for campo, valor in (
            ("numero_identificacion", "900123456"),
            ("tipo_identificacion", self.cat["nit"].id),
            ("referencia_externa", "16"),
            ("cuenta", None),
            ("telefono", "6041234567"),
            ("codigo_postal", "050001"),
            ("correo_copia", "copia@empresa.co"),
            ("responsabilidades", []),
            ("activo", False),
            ("ambiente_facturacion", 2),
            ("habilitado_nomina", True),
            ("usuario", 1),
            ("no_existe", "x"),
        ):
            with self.subTest(campo=campo):
                resp = self.client.patch(self.url, {campo: valor}, format="json")
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(
                    errores_por_campo(resp), {campo: [MENSAJE_NO_ACTUALIZABLE]}
                )
        emisor = Emisor.objects.get()
        self.assertEqual(emisor.numero_identificacion, "901192048")
        self.assertEqual(emisor.referencia_externa, "15")

    def test_un_campo_prohibido_tumba_el_patch_entero(self):
        """Nada se guarda a medias: ni siquiera los campos permitidos."""
        resp = self.client.patch(
            self.url, {"razon_social": "Otra", "telefono": "1"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(set(errores_por_campo(resp)), {"telefono"})
        self.assertEqual(Emisor.objects.get().razon_social, "Semantica Digital S.A.S")

    def test_put_no_esta_permitido(self):
        resp = self.client.put(self.url, {"razon_social": "Otra"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
