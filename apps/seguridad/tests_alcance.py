"""Pruebas del aislamiento entre inquilinos (``apps.seguridad.alcance``).

Lo que se comprueba aquí es que los datos de una cuenta no se ven ni se tocan
desde otra: es la frontera que hace que una misma integración pueda facturar
para muchos emisores sin mezclarlos.
"""
import re

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient, APITestCase

from apps.documentos.models import DocumentoTipo
from apps.documentos.serializers import DocumentoCrearSerializer
from apps.documentos.tests_utils import (
    crear_catalogos_minimos,
    crear_usuario,
    crear_certificado,
    crear_documento_factura,
)
from apps.emisores.models import Emisor
from apps.seguridad.models import LlaveApi

Usuario = get_user_model()

URL_EMISORES = "/api/emisores/emisor/"
URL_SOFTWARE = "/api/emisores/software/"


class AlcanceBase(APITestCase):
    """Dos cuentas con un emisor cada una, más un segundo emisor en la primera."""

    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.usuario = crear_usuario(nombre="RedDoc ERP")
        self.emisor = self.crear_emisor(self.usuario, "900000001", "Cliente A")
        self.hermano = self.crear_emisor(self.usuario, "900000002", "Cliente B")

        self.usuario_ajeno = crear_usuario(nombre="Otra integración")
        self.emisor_ajeno = self.crear_emisor(
            self.usuario_ajeno, "900000003", "Ajena S.A.S."
        )

    def crear_emisor(self, usuario, nit, razon_social):
        c = self.cat
        return Emisor.objects.create(
            usuario=usuario,
            razon_social=razon_social,
            tipo_identificacion=c["nit"],
            numero_identificacion=nit,
            tipo_organizacion=c["juridica"],
            pais=c["colombia"],
            departamento=c["antioquia"],
            municipio=c["medellin"],
            direccion="Calle 1 # 2-3",
        )

    def _api_key(self, **kwargs):
        _, clave = LlaveApi.generar(nombre="ERP", **kwargs)
        return {"HTTP_AUTHORIZATION": f"Api-Key {clave}"}


class FlujoDeAltaTests(AlcanceBase):
    """El alta completa: staff → cuenta → llave → emisor.

    Es el recorrido real de puesta en marcha. El staff de la plataforma da de
    alta la integración y su credencial; de ahí en adelante la integración se
    autoabastece: crea sus emisores y factura con ellos sin que el staff tenga
    que intervenir por cada cliente nuevo.
    """

    def test_alta_completa_de_una_integracion(self):
        admin = Usuario.objects.create_superuser(
            email="admin@nobelio.co", password="ClaveSegura123"
        )
        self.client.force_authenticate(admin)

        # 1. Se emite una llave a nombre de quien la pide. Ya no hay cuenta que
        # crear antes: la llave cuelga directamente de la persona.
        resp = self.client.post(
            "/api/seguridad/llave-api/",
            {"nombre": "integracion1 producción"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        clave = resp.data["clave"]
        self.assertIsNotNone(clave)

        # A partir de aquí ya no interviene el staff: cliente nuevo, sin sesión,
        # autenticado solo con la API Key recién emitida.
        erp = APIClient()
        cabecera = {"HTTP_AUTHORIZATION": f"Api-Key {clave}"}

        # 2. La integración crea un emisor, que queda a nombre del dueño de la
        # llave sin que nadie lo indique.
        c = self.cat
        resp = erp.post(
            URL_EMISORES,
            {
                "razon_social": "Cliente de integracion1 S.A.S.",
                "tipo_identificacion": c["nit"].id,
                "numero_identificacion": "900000010",
                "tipo_organizacion": c["juridica"].id,
                "pais": c["colombia"].codigo,
                "departamento": c["antioquia"].codigo,
                "municipio": c["medellin"].codigo,
                "direccion": "Calle 9 # 9-9",
            },
            format="json",
            **cabecera,
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["usuario"], admin.pk)

        # 3. Y no ve nada de las demás integraciones.
        self.assertEqual(
            erp.get(URL_EMISORES, **cabecera).data["count"], 1
        )


class AlcanceLlaveDeCuentaTests(AlcanceBase):
    """Una llave alcanza todos los emisores de su cuenta, y solo esos."""

    def setUp(self):
        super().setUp()
        self.cabecera = self._api_key(usuario=self.usuario)

    def test_lista_solo_los_emisores_de_su_cuenta(self):
        resp = self.client.get(URL_EMISORES, **self.cabecera)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = {fila["id"] for fila in resp.data["results"]}
        self.assertEqual(ids, {self.emisor.id, self.hermano.id})

    def test_el_emisor_de_otra_cuenta_no_existe_para_ella(self):
        resp = self.client.get(f"{URL_EMISORES}{self.emisor_ajeno.id}/", **self.cabecera)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_no_puede_escribir_sobre_un_emisor_ajeno(self):
        # Con certificado, para que lo que corte sea el alcance y no la
        # validación del software (que ahora lo exige).
        crear_certificado(self.emisor_ajeno)
        payload = {
            "emisor": self.emisor_ajeno.id,
            "tipo": "facturacion",
            "identificador": "abc123-software-id",
            "pin": "12345",
            "test_set_id": "set-xyz",
        }
        resp = self.client.post(URL_SOFTWARE, payload, format="json", **self.cabecera)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class AltaDeEmisoresTests(AlcanceBase):
    """La cuenta de un emisor nuevo la impone la credencial, no el cuerpo."""

    def payload(self, **extra):
        datos = {
            "razon_social": "Cliente nuevo S.A.S.",
            "tipo_identificacion": self.emisor.tipo_identificacion_id,
            "numero_identificacion": "900000009",
            "tipo_organizacion": self.emisor.tipo_organizacion_id,
            "pais": self.emisor.pais.codigo,
            "departamento": self.emisor.departamento.codigo,
            "municipio": self.emisor.municipio.codigo,
            "direccion": "Calle 9 # 9-9",
        }
        datos.update(extra)
        return datos

    def crear(self, payload, cabecera):
        return self.client.post(URL_EMISORES, payload, format="json", **cabecera)

    def test_el_emisor_queda_a_nombre_de_quien_lo_crea(self):
        """El dueño no viaja en el cuerpo: lo pone la vista."""
        resp = self.crear(self.payload(), self._api_key(usuario=self.usuario))
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["usuario"], self.usuario.id)

    def test_el_cuerpo_no_puede_ponerlo_a_nombre_de_otro(self):
        """`usuario` es de solo lectura: se descarta en silencio."""
        resp = self.crear(
            {**self.payload(), "usuario": self.usuario_ajeno.id},
            self._api_key(usuario=self.usuario),
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["usuario"], self.usuario.id)

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
        resp = self.client.put(
            f"{URL_EMISORES}{self.emisor.id}/", payload, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.emisor.refresh_from_db()
        self.assertEqual(self.emisor.usuario, self.usuario)
        self.assertEqual(self.emisor.razon_social, "Cliente A renombrado")


class AlcanceDeDocumentosTests(AlcanceBase):
    """Los documentos de otra cuenta no aparecen ni se pueden referenciar."""

    def test_no_lista_documentos_de_otra_cuenta(self):
        # El helper monta su propio emisor bajo la cuenta de los catálogos,
        # que no es self.usuario.
        crear_documento_factura(catalogos=self.cat)
        resp = self.client.get(
            "/api/documentos/documento/", **self._api_key(usuario=self.usuario)
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 0)

    def _payload_documento(self, emisor_id):
        c = self.cat
        return {
            "documento_tipo": DocumentoTipo.objects.get(
                codigo=DocumentoTipo.Codigo.FACTURA_VENTA
            ).id,
            "emisor": emisor_id,
            "numero_resolucion": "18760000001",
            "adquiriente": {
                "razon_social": "Cliente Demo",
                "tipo_identificacion": c["nit"].id,
                "numero_identificacion": "800100009",
                "tipo_organizacion": c["juridica"].id,
                "pais": c["colombia"].id,
            },
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
        cabecera = self._api_key(usuario=self.usuario)
        url = "/api/documentos/documento/"

        con_ajeno = self.client.post(
            url, self._payload_documento(self.emisor_ajeno.id), format="json", **cabecera
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

    def test_no_se_puede_referenciar_un_documento_de_otro_emisor(self):
        """Una nota crédito no puede colgar de la factura de otra cuenta."""
        ajeno = crear_documento_factura(catalogos=self.cat)["documento"]
        with self.assertRaises(ValidationError) as caso:
            DocumentoCrearSerializer().validate(
                {"emisor": self.emisor, "documento_referencia": ajeno}
            )
        self.assertIn("documento_referencia", caso.exception.detail)


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

    def test_un_usuario_da_de_alta_sus_propios_emisores(self):
        """Ya no hace falta una cuenta de la que colgarlo: el dueño es él."""
        self.client.force_authenticate(
            self.crear_usuario("contable@reddoc.co", self.emisor)
        )
        resp = self.client.post(URL_EMISORES, {}, format="json")
        # 400 por el cuerpo vacío, no 403: el permiso ya no es el problema.
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_el_staff_de_la_plataforma_ve_todo(self):
        admin = Usuario.objects.create_superuser(
            email="admin@nobelio.co", password="ClaveSegura123"
        )
        self.client.force_authenticate(admin)
        resp = self.client.get(URL_EMISORES)
        self.assertEqual(resp.data["count"], 3)
