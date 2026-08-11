"""Pruebas del aislamiento entre inquilinos (``apps.seguridad.alcance``).

Lo que se comprueba aquí es que los datos de una cuenta no se ven ni se tocan
desde otra: es la frontera que hace que una misma integración pueda facturar
para muchos emisores sin mezclarlos.
"""
import re
from unittest import mock

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient, APITestCase

from apps.cuentas.models import Cuenta
from apps.documentos.models import Adquiriente, DocumentoTipo
from apps.documentos.serializers import DocumentoCrearSerializer
from apps.documentos.tests_utils import (
    crear_catalogos_minimos,
    crear_documento_factura,
)
from apps.emisores.models import Emisor
from apps.seguridad.models import LlaveApi

Usuario = get_user_model()

URL_EMISORES = "/api/emisores/emisor/"
URL_ADQUIRIENTES = "/api/documentos/adquiriente/"
URL_SOFTWARE = "/api/emisores/software/"


class AlcanceBase(APITestCase):
    """Dos cuentas con un emisor cada una, más un segundo emisor en la primera."""

    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.cuenta = Cuenta.objects.create(nombre="RedDoc ERP")
        self.emisor = self.crear_emisor(self.cuenta, "900000001", "Cliente A")
        self.hermano = self.crear_emisor(self.cuenta, "900000002", "Cliente B")

        self.cuenta_ajena = Cuenta.objects.create(nombre="Otra integración")
        self.emisor_ajeno = self.crear_emisor(
            self.cuenta_ajena, "900000003", "Ajena S.A.S."
        )

    def crear_emisor(self, cuenta, nit, razon_social):
        c = self.cat
        return Emisor.objects.create(
            cuenta=cuenta,
            razon_social=razon_social,
            tipo_identificacion=c["nit"],
            numero_identificacion=nit,
            tipo_organizacion=c["juridica"],
            pais=c["colombia"],
            departamento=c["antioquia"],
            municipio=c["medellin"],
            direccion="Calle 1 # 2-3",
        )

    def crear_adquiriente(self, emisor, nit):
        """Un cliente del emisor: la cartera que no debe cruzarse entre cuentas."""
        c = self.cat
        return Adquiriente.objects.create(
            emisor=emisor,
            razon_social="Cliente Demo",
            tipo_identificacion=c["nit"],
            numero_identificacion=nit,
            tipo_organizacion=c["juridica"],
            pais=c["colombia"],
        )

    def _api_key(self, **kwargs):
        _, clave = LlaveApi.generar(nombre="ERP", **kwargs)
        return {"HTTP_AUTHORIZATION": f"Api-Key {clave}"}


class FlujoDeAltaTests(AlcanceBase):
    """El alta completa: staff → cuenta → llave → emisor → adquiriente.

    Es el recorrido real de puesta en marcha. El staff de la plataforma da de
    alta la integración y su credencial; de ahí en adelante la integración se
    autoabastece: crea sus emisores y cada emisor su cartera de clientes, sin
    que el staff tenga que intervenir por cada cliente nuevo.
    """

    def test_alta_completa_de_una_integracion(self):
        admin = Usuario.objects.create_superuser(
            email="admin@nobelio.co", password="ClaveSegura123"
        )
        self.client.force_authenticate(admin)

        # 1. El staff crea la cuenta de la integración.
        resp = self.client.post(
            "/api/cuentas/cuenta/", {"nombre": "integracion1"}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        cuenta_id = resp.data["id"]

        # 2. Y su llave. El secreto solo se ve en esta respuesta.
        resp = self.client.post(
            "/api/seguridad/llave-api/",
            {"cuenta": cuenta_id, "nombre": "integracion1 producción"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        clave = resp.data["clave"]
        self.assertIsNotNone(clave)

        # A partir de aquí ya no interviene el staff: cliente nuevo, sin sesión,
        # autenticado solo con la API Key recién emitida.
        erp = APIClient()
        cabecera = {"HTTP_AUTHORIZATION": f"Api-Key {clave}"}

        # 3. La integración crea un emisor, que cae en su cuenta sola.
        c = self.cat
        with mock.patch(
            "apps.emisores.serializers.emisor.consultar_nit", return_value={"nit": "x"}
        ):
            resp = erp.post(
                URL_EMISORES,
                {
                    "razon_social": "Cliente de integracion1 S.A.S.",
                    "tipo_identificacion": c["nit"].id,
                    "numero_identificacion": "900000010",
                    "tipo_organizacion": c["juridica"].id,
                    "pais": c["colombia"].id,
                    "departamento": c["antioquia"].id,
                    "municipio": c["medellin"].id,
                    "direccion": "Calle 9 # 9-9",
                },
                format="json",
                **cabecera,
            )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["cuenta"], cuenta_id)
        emisor_id = resp.data["id"]

        # 4. Y la cartera de clientes de ese emisor.
        resp = erp.post(
            URL_ADQUIRIENTES,
            {
                "emisor": emisor_id,
                "razon_social": "Cliente Final S.A.S.",
                "tipo_identificacion": c["nit"].id,
                "numero_identificacion": "800100010",
                "tipo_organizacion": c["juridica"].id,
                "pais": c["colombia"].id,
            },
            format="json",
            **cabecera,
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

        # 5. Y no ve nada de las demás integraciones.
        self.assertEqual(
            erp.get(URL_EMISORES, **cabecera).data["count"], 1
        )


class AlcanceLlaveDeCuentaTests(AlcanceBase):
    """Una llave alcanza todos los emisores de su cuenta, y solo esos."""

    def setUp(self):
        super().setUp()
        self.cabecera = self._api_key(cuenta=self.cuenta)

    def test_lista_solo_los_emisores_de_su_cuenta(self):
        resp = self.client.get(URL_EMISORES, **self.cabecera)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {fila["id"] for fila in resp.data["results"]}
        self.assertEqual(ids, {self.emisor.id, self.hermano.id})

    def test_el_emisor_de_otra_cuenta_no_existe_para_ella(self):
        resp = self.client.get(f"{URL_EMISORES}{self.emisor_ajeno.id}/", **self.cabecera)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_no_puede_escribir_sobre_un_emisor_ajeno(self):
        payload = {
            "emisor": self.emisor_ajeno.id,
            "identificador": "abc123-software-id",
            "pin": "12345",
            "id_proveedor": "901192048",
            "test_set_id": "set-xyz",
        }
        resp = self.client.post(URL_SOFTWARE, payload, format="json", **self.cabecera)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_no_ve_la_cartera_de_clientes_de_otra_cuenta(self):
        propio = self.crear_adquiriente(self.emisor, nit="800100001")
        ajeno = self.crear_adquiriente(self.emisor_ajeno, nit="800100002")

        resp = self.client.get(URL_ADQUIRIENTES, **self.cabecera)
        ids = {fila["id"] for fila in resp.data["results"]}
        self.assertIn(propio.id, ids)
        self.assertNotIn(ajeno.id, ids)

        detalle = self.client.get(f"{URL_ADQUIRIENTES}{ajeno.id}/", **self.cabecera)
        self.assertEqual(detalle.status_code, status.HTTP_404_NOT_FOUND)

    def test_el_mismo_nit_puede_ser_cliente_de_dos_emisores(self):
        # Antes la unicidad era global y el segundo emisor no podía registrarlo.
        self.crear_adquiriente(self.emisor, nit="800100003")
        self.crear_adquiriente(self.hermano, nit="800100003")
        self.assertEqual(
            Adquiriente.objects.filter(numero_identificacion="800100003").count(), 2
        )


class AltaDeEmisoresTests(AlcanceBase):
    """La cuenta de un emisor nuevo la impone la credencial, no el cuerpo."""

    def payload(self, **extra):
        datos = {
            "razon_social": "Cliente nuevo S.A.S.",
            "tipo_identificacion": self.emisor.tipo_identificacion_id,
            "numero_identificacion": "900000009",
            "tipo_organizacion": self.emisor.tipo_organizacion_id,
            "pais": self.emisor.pais_id,
            "departamento": self.emisor.departamento_id,
            "municipio": self.emisor.municipio_id,
            "direccion": "Calle 9 # 9-9",
        }
        datos.update(extra)
        return datos

    def crear(self, payload, cabecera):
        # El serializer consulta el RUES al validar el NIT; se evita aquí.
        with mock.patch(
            "apps.emisores.serializers.emisor.consultar_nit", return_value={"nit": "x"}
        ):
            return self.client.post(URL_EMISORES, payload, format="json", **cabecera)

    def test_sin_indicar_cuenta_cae_en_la_de_la_credencial(self):
        resp = self.crear(self.payload(), self._api_key(cuenta=self.cuenta))
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["cuenta"], self.cuenta.id)

    def test_la_integracion_no_puede_colgar_un_emisor_de_otra_cuenta(self):
        resp = self.crear(
            self.payload(cuenta=self.cuenta_ajena.id),
            self._api_key(cuenta=self.cuenta),
        )
        # Se rechaza en vez de ignorarse: mejor un error claro que un silencio.
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cuenta", resp.data["errores"])

    def test_el_staff_debe_indicar_la_cuenta(self):
        # No tiene credencial de cuenta, así que no hay default que aplicar.
        self.client.force_authenticate(
            Usuario.objects.create_user(
                email="staff@nobelio.co", password="ClaveSegura123", is_staff=True
            )
        )
        resp = self.crear(self.payload(), {})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cuenta", resp.data["errores"])

    def test_no_se_puede_dar_de_alta_en_una_cuenta_inactiva(self):
        self.cuenta_ajena.activa = False
        self.cuenta_ajena.save(update_fields=["activa"])
        self.client.force_authenticate(
            Usuario.objects.create_user(
                email="staff@nobelio.co", password="ClaveSegura123", is_staff=True
            )
        )
        resp = self.crear(self.payload(cuenta=self.cuenta_ajena.id), {})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cuenta", resp.data["errores"])

    def test_el_staff_puede_editar_sin_reenviar_la_cuenta(self):
        # PUT sin 'cuenta': el default de la credencial es None para el staff y
        # antes se intentaba guardar el emisor sin cuenta (IntegrityError).
        self.client.force_authenticate(
            Usuario.objects.create_user(
                email="staff@nobelio.co", password="ClaveSegura123", is_staff=True
            )
        )
        payload = self.payload(
            numero_identificacion=self.emisor.numero_identificacion,
            razon_social="Cliente A renombrado",
        )
        with mock.patch(
            "apps.emisores.serializers.emisor.consultar_nit", return_value={"nit": "x"}
        ):
            resp = self.client.put(
                f"{URL_EMISORES}{self.emisor.id}/", payload, format="json"
            )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.emisor.refresh_from_db()
        self.assertEqual(self.emisor.cuenta, self.cuenta)
        self.assertEqual(self.emisor.razon_social, "Cliente A renombrado")


class AlcanceDeDocumentosTests(AlcanceBase):
    """Los documentos de otra cuenta no aparecen ni se pueden referenciar."""

    def test_no_lista_documentos_de_otra_cuenta(self):
        # El helper monta su propio emisor bajo la cuenta de los catálogos,
        # que no es self.cuenta.
        crear_documento_factura(catalogos=self.cat)
        resp = self.client.get(
            "/api/documentos/documento/", **self._api_key(cuenta=self.cuenta)
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 0)

    def _payload_documento(self, adquiriente_id):
        c = self.cat
        return {
            "documento_tipo": DocumentoTipo.objects.get(
                codigo=DocumentoTipo.Codigo.FACTURA_VENTA
            ).id,
            "emisor": self.emisor.id,
            "adquiriente": adquiriente_id,
            "prefijo": "SETP", "consecutivo": 1, "numero": "SETP1",
            "fecha_emision": "2024-01-10", "hora_emision": "10:00:00",
            "moneda": c["cop"].id,
            "detalles": [
                {
                    "numero_linea": 1, "descripcion": "Servicio",
                    "cantidad": "1", "unidad_medida": c["unidad"].id,
                    "valor_unitario": "1000", "valor_total": "1000.00",
                    "impuestos": [],
                }
            ],
        }

    def test_un_id_ajeno_no_se_distingue_de_uno_inexistente(self):
        """El error no puede servir de oráculo de existencia entre cuentas."""
        ajeno = self.crear_adquiriente(self.emisor_ajeno, nit="800100009")
        cabecera = self._api_key(cuenta=self.cuenta)
        url = "/api/documentos/documento/"

        con_ajeno = self.client.post(
            url, self._payload_documento(ajeno.id), format="json", **cabecera
        )
        con_inexistente = self.client.post(
            url, self._payload_documento(999999), format="json", **cabecera
        )

        self.assertEqual(con_ajeno.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(con_ajeno.status_code, con_inexistente.status_code)
        # Las respuestas solo pueden diferir en el id que se envió.
        def sin_ids(errores):
            return re.sub(r"\d+", "N", str(errores))

        self.assertEqual(
            sin_ids(con_ajeno.data["errores"]), sin_ids(con_inexistente.data["errores"])
        )

    def test_no_se_puede_facturar_con_el_adquiriente_de_otro_emisor(self):
        ajeno = self.crear_adquiriente(self.emisor_ajeno, nit="800100004")
        with self.assertRaises(ValidationError) as caso:
            DocumentoCrearSerializer().validate(
                {"emisor": self.emisor, "adquiriente": ajeno}
            )
        self.assertIn("adquiriente", caso.exception.detail)


class AlcanceDeUsuariosTests(AlcanceBase):
    """El alcance de una persona son exactamente sus emisores asignados."""

    def crear_usuario(self, email, *emisores):
        usuario = Usuario.objects.create_user(email=email, password="ClaveSegura123")
        usuario.emisores.set(emisores)
        return usuario

    def test_usuario_sin_emisores_no_ve_nada(self):
        # Falla cerrado: un usuario recién creado no ve ningún dato.
        self.client.force_authenticate(self.crear_usuario("nuevo@reddoc.co"))
        resp = self.client.get(URL_EMISORES)
        self.assertEqual(resp.data["count"], 0)

    def test_usuario_solo_ve_los_emisores_asignados(self):
        self.client.force_authenticate(
            self.crear_usuario("contable@reddoc.co", self.emisor)
        )

        resp = self.client.get(URL_EMISORES)
        ids = {fila["id"] for fila in resp.data["results"]}
        self.assertEqual(ids, {self.emisor.id})

        # El hermano está en la misma cuenta y aun así no lo ve.
        detalle = self.client.get(f"{URL_EMISORES}{self.hermano.id}/")
        self.assertEqual(detalle.status_code, status.HTTP_404_NOT_FOUND)

    def test_un_usuario_no_puede_dar_de_alta_emisores(self):
        # No tiene cuenta de la que colgarlo: eso es cosa de la integración.
        self.client.force_authenticate(
            self.crear_usuario("contable@reddoc.co", self.emisor)
        )
        resp = self.client.post(URL_EMISORES, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_no_se_pueden_asignar_emisores_de_varias_cuentas(self):
        usuario = self.crear_usuario("mixto@reddoc.co")
        admin = Usuario.objects.create_superuser(
            email="admin@nobelio.co", password="ClaveSegura123"
        )
        self.client.force_authenticate(admin)
        resp = self.client.patch(
            f"/api/seguridad/usuario/{usuario.id}/",
            {"emisores": [self.emisor.id, self.emisor_ajeno.id]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("emisores", resp.data["errores"])

    def test_el_staff_de_la_plataforma_ve_todo(self):
        admin = Usuario.objects.create_superuser(
            email="admin@nobelio.co", password="ClaveSegura123"
        )
        self.client.force_authenticate(admin)
        resp = self.client.get(URL_EMISORES)
        self.assertEqual(resp.data["count"], 3)
