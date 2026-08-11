"""Pruebas de la API REST de documentos (flujo end-to-end)."""
import tempfile
from datetime import date, timedelta

from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption, pkcs12,
)
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalogos.models import TipoFactura
from apps.dian.tests_firma import _generar_certificado
from apps.documentos.models import (
    Adquiriente, Documento, DocumentoEstado, DocumentoTipo,
)
from apps.documentos.serializers.documento import (
    MENSAJE_RESOLUCION_AMBIGUA,
    MENSAJE_RESOLUCION_NO_ENCONTRADA,
    mensaje_consecutivo_fuera_de_rango,
    mensaje_prefijo_ajeno,
)
from apps.documentos.tests_utils import crear_documento_factura
from apps.emisores.models import Certificado, Emisor, ResolucionFacturacion
from apps.emisores.servicios import (
    MENSAJE_EMISOR_INACTIVO,
    MENSAJE_SIN_CERTIFICADO,
)

MEDIA_TEMP = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_TEMP)
class DocumentoAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        datos = crear_documento_factura()
        cls.documento = datos["documento"]
        cls.emisor = datos["emisor"]
        cls.cat = datos["catalogos"]

        # Adjuntar un certificado .p12 real al emisor.
        llave, cert = _generar_certificado()
        p12 = pkcs12.serialize_key_and_certificates(
            b"alias", llave, cert, None, BestAvailableEncryption(b"clave123")
        )
        certificado = Certificado(emisor=cls.emisor, clave="clave123", alias="test")
        certificado.archivo.save("test.p12", ContentFile(p12), save=True)

        cls.usuario = get_user_model().objects.create_user(
            email="tester@example.com", password="x"
        )
        cls.usuario.emisores.add(cls.emisor)

    def setUp(self):
        self.client.force_authenticate(self.usuario)

    def _url(self, sufijo=""):
        return f"/api/documentos/documento/{self.documento.id}/{sufijo}"

    def test_listar_documentos(self):
        resp = self.client.get("/api/documentos/documento/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp.data["count"], 1)
        # El listado NO incluye las líneas; el detalle (retrieve) sí.
        self.assertNotIn("detalles", resp.data["results"][0])
        detalle = self.client.get(f"/api/documentos/documento/{self.documento.id}/")
        self.assertIn("detalles", detalle.data)

    def test_filtrar_por_emisor_y_estado(self):
        url = "/api/documentos/documento/"
        # Emisor existente -> al menos 1; estado inexistente -> 0.
        self.assertGreaterEqual(
            self.client.get(url, {"emisor": self.emisor.id}).data["count"], 1
        )
        self.assertEqual(
            self.client.get(url, {"estado": "aceptado"}).data["count"], 0
        )
        self.assertEqual(
            self.client.get(url, {"emisor": 999999}).data["count"], 0
        )

    def test_emitir_firma_el_documento(self):
        resp = self.client.post(self._url("emitir/"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["estado"], DocumentoEstado.Nombre.FIRMADO)
        self.assertEqual(len(resp.data["cufe_cude"]), 96)

    def test_descargar_xml_tras_emitir(self):
        self.client.post(self._url("emitir/"))
        resp = self.client.get(self._url("xml/"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Content-Type"], "application/xml")
        # FileResponse (stream) desde object storage.
        self.assertIn(b"<ds:Signature", b"".join(resp.streaming_content))

    def test_descargar_pdf_tras_emitir(self):
        self.client.post(self._url("emitir/"))
        resp = self.client.get(self._url("pdf/"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF-"))

    def test_pdf_antes_de_emitir_falla(self):
        resp = self.client.get(self._url("pdf/"))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def _payload_documento(self):
        c = self.cat
        return {
            "documento_tipo": DocumentoTipo.objects.get(
                codigo=DocumentoTipo.Codigo.FACTURA_VENTA
            ).id,
            "emisor": self.emisor.id,
            # El emisor solo conoce el número que le dio la DIAN, no nuestro id.
            "numero_resolucion": self.documento.resolucion.numero_resolucion,
            # Los datos del receptor van en cada documento, no por id.
            "adquiriente": {
                "razon_social": "Cliente Demo",
                "tipo_identificacion": c["nit"].id,
                "numero_identificacion": "800199436",
                "digito_verificacion": "6",
                "tipo_organizacion": c["juridica"].id,
                "pais": c["colombia"].id,
                "departamento": c["antioquia"].id,
                "municipio": c["medellin"].id,
                "direccion": "Cra 4 # 5-6",
            },
            "prefijo": "SETP",
            "consecutivo": 990000130,
            "numero": "SETP990000130",
            "fecha_emision": "2024-01-10",
            "hora_emision": "10:00:00",
            "moneda": c["cop"].id,
            "detalles": [
                {
                    "numero_linea": 1, "descripcion": "Servicio",
                    "cantidad": "2", "unidad_medida": c["unidad"].id,
                    "valor_unitario": "1000", "valor_total": "2000.00",
                    "impuestos": [
                        {"tributo": c["iva"].id, "base_gravable": "2000.00",
                         "tarifa": "19.00", "valor": "380.00"}
                    ],
                }
            ],
        }

    def _crear(self):
        return self.client.post(
            "/api/documentos/documento/", self._payload_documento(), format="json"
        )

    def test_crear_documento_calcula_totales(self):
        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["valor_bruto"], "2000.00")
        self.assertEqual(resp.data["total_impuestos"], "380.00")
        self.assertEqual(resp.data["total_a_pagar"], "2380.00")

    # --- El receptor viaja en cada documento y se guarda con él ------------

    def test_el_receptor_se_guarda_pegado_al_documento(self):
        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["adquiriente"]["razon_social"], "Cliente Demo")

        creado = Documento.objects.get(pk=resp.data["id"])
        self.assertEqual(creado.adquiriente.numero_identificacion, "800199436")
        self.assertEqual(creado.adquiriente.municipio, self.cat["medellin"])

    def test_cada_documento_lleva_su_propia_copia_del_receptor(self):
        """Corregir al cliente en una factura no reescribe las anteriores."""
        primera = self._crear()
        payload = self._payload_documento()
        payload["consecutivo"] = 990000131
        payload["numero"] = "SETP990000131"
        payload["adquiriente"]["razon_social"] = "Cliente Demo S.A.S."
        segunda = self.client.post(
            "/api/documentos/documento/", payload, format="json"
        )
        self.assertEqual(segunda.status_code, status.HTTP_201_CREATED, segunda.data)

        self.assertEqual(primera.data["adquiriente"]["razon_social"], "Cliente Demo")
        self.assertEqual(
            segunda.data["adquiriente"]["razon_social"], "Cliente Demo S.A.S."
        )
        # Mismo NIT, dos filas: el receptor es del documento, no una cartera.
        self.assertEqual(
            Adquiriente.objects.filter(numero_identificacion="800199436").count(), 3
        )

    def test_el_receptor_es_obligatorio(self):
        payload = self._payload_documento()
        del payload["adquiriente"]

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("adquiriente", resp.data["errores"])

    def test_se_puede_corregir_el_receptor_de_un_borrador(self):
        resp = self.client.patch(
            self._url(), {"adquiriente": {"correo": "nuevo@cliente.co"}}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.documento.adquiriente.refresh_from_db()
        self.assertEqual(self.documento.adquiriente.correo, "nuevo@cliente.co")

    def test_no_se_puede_tocar_el_receptor_de_un_documento_firmado(self):
        """Cambiarlo dejaría el PDF distinto del XML que ya se firmó."""
        self.documento.estado = DocumentoEstado.objects.get(
            nombre=DocumentoEstado.Nombre.FIRMADO
        )
        self.documento.save(update_fields=["estado"])
        self.addCleanup(self._volver_a_borrador)

        resp = self.client.patch(
            self._url(), {"adquiriente": {"correo": "otro@cliente.co"}}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.documento.adquiriente.refresh_from_db()
        self.assertNotEqual(self.documento.adquiriente.correo, "otro@cliente.co")

    def _volver_a_borrador(self):
        Documento.objects.filter(pk=self.documento.pk).update(
            estado=DocumentoEstado.objects.get(nombre=DocumentoEstado.Nombre.BORRADOR)
        )

    def test_borrar_el_documento_se_lleva_a_su_receptor(self):
        resp = self._crear()
        documento = Documento.objects.get(pk=resp.data["id"])
        adquiriente_id = documento.adquiriente.id

        documento.delete()
        self.assertFalse(Adquiriente.objects.filter(pk=adquiriente_id).exists())

    # --- La resolución se indica por su número, no por nuestro id ----------

    def test_crear_documento_resuelve_la_resolucion_por_numero(self):
        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["resolucion"], self.documento.resolucion.id)
        self.assertEqual(
            resp.data["resolucion_numero"], self.documento.resolucion.numero_resolucion
        )

    def test_el_numero_de_resolucion_es_obligatorio(self):
        payload = self._payload_documento()
        del payload["numero_resolucion"]

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("numero_resolucion", resp.data["errores"])
        self.assertFalse(Documento.objects.filter(consecutivo=990000130).exists())

    def test_el_id_de_la_resolucion_no_se_acepta_al_crear(self):
        """Mandar el id no numera: el campo ya no existe en la creación."""
        payload = self._payload_documento()
        del payload["numero_resolucion"]
        payload["resolucion"] = self.documento.resolucion.id

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("numero_resolucion", resp.data["errores"])

    def test_numero_de_resolucion_inexistente(self):
        payload = self._payload_documento()
        payload["numero_resolucion"] = "99999999999"

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            resp.data["errores"]["numero_resolucion"],
            [MENSAJE_RESOLUCION_NO_ENCONTRADA],
        )

    def test_el_numero_se_busca_solo_dentro_del_emisor(self):
        """El mismo número en otro emisor no se puede colar en este documento."""
        original = self.documento.resolucion
        otro_emisor = Emisor.objects.get(pk=self.emisor.pk)
        otro_emisor.pk = None
        otro_emisor.numero_identificacion = "900000001"
        otro_emisor.save(force_insert=True)
        ajena = ResolucionFacturacion.objects.create(
            emisor=otro_emisor, tipo_factura=original.tipo_factura,
            numero_resolucion=original.numero_resolucion,
            fecha_resolucion=original.fecha_resolucion, prefijo=original.prefijo,
            rango_desde=1, rango_hasta=100,
            vigente_desde=original.vigente_desde, vigente_hasta=original.vigente_hasta,
        )

        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["resolucion"], original.id)
        self.assertNotEqual(resp.data["resolucion"], ajena.id)

    def test_resolucion_inactiva_no_sirve_para_numerar(self):
        resolucion = self.documento.resolucion
        resolucion.activa = False
        resolucion.save(update_fields=["activa"])
        self.addCleanup(
            lambda: type(resolucion).objects.filter(pk=resolucion.pk).update(activa=True)
        )

        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            resp.data["errores"]["numero_resolucion"],
            [MENSAJE_RESOLUCION_NO_ENCONTRADA],
        )

    def test_numero_repetido_se_desempata_con_el_prefijo(self):
        original = self.documento.resolucion
        otro_tipo, _ = TipoFactura.objects.get_or_create(
            codigo="91", defaults={"nombre": "Nota Crédito"}
        )
        gemela = ResolucionFacturacion.objects.create(
            emisor=self.emisor, tipo_factura=otro_tipo,
            numero_resolucion=original.numero_resolucion,
            fecha_resolucion=original.fecha_resolucion, prefijo="NC",
            rango_desde=1, rango_hasta=100,
            vigente_desde=original.vigente_desde, vigente_hasta=original.vigente_hasta,
        )

        # El payload va con prefijo SETP: tiene que elegir la original.
        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["resolucion"], original.id)

        # Con un prefijo que no es de ninguna de las dos, no hay forma de saber.
        payload = self._payload_documento()
        payload["prefijo"] = "OTRO"
        payload["consecutivo"] = 990000131
        payload["numero"] = "OTRO990000131"
        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            resp.data["errores"]["numero_resolucion"], [MENSAJE_RESOLUCION_AMBIGUA]
        )
        self.assertTrue(ResolucionFacturacion.objects.filter(pk=gemela.pk).exists())

    # --- El número tiene que caber en lo que autorizó la resolución --------

    def test_no_se_crea_con_un_prefijo_que_no_es_el_de_la_resolucion(self):
        payload = self._payload_documento()
        payload["prefijo"] = "FE"
        payload["numero"] = "FE990000130"

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            resp.data["errores"]["prefijo"],
            [mensaje_prefijo_ajeno(self.documento.resolucion)],
        )
        self.assertFalse(Documento.objects.filter(consecutivo=990000130).exists())

    def test_no_se_crea_con_el_consecutivo_fuera_del_rango(self):
        resolucion = self.documento.resolucion
        payload = self._payload_documento()
        payload["consecutivo"] = resolucion.rango_hasta + 1
        payload["numero"] = f"SETP{resolucion.rango_hasta + 1}"

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            resp.data["errores"]["consecutivo"],
            [mensaje_consecutivo_fuera_de_rango(resolucion)],
        )

    def test_se_crea_en_los_extremos_del_rango(self):
        resolucion = self.documento.resolucion
        payload = self._payload_documento()
        payload["consecutivo"] = resolucion.rango_desde
        payload["numero"] = f"SETP{resolucion.rango_desde}"

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_al_modificar_se_valida_contra_la_resolucion_que_ya_tiene(self):
        """En un PATCH no se repite el número: la resolución sale del documento."""
        resp = self.client.patch(
            self._url(), {"consecutivo": 1}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            resp.data["errores"]["consecutivo"],
            [mensaje_consecutivo_fuera_de_rango(self.documento.resolucion)],
        )

    # --- El emisor tiene que estar en condiciones de firmar ----------------

    def test_no_se_crea_si_el_emisor_no_tiene_certificado(self):
        Certificado.objects.filter(emisor=self.emisor).delete()
        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data["errores"]["emisor"], [MENSAJE_SIN_CERTIFICADO])
        self.assertFalse(Documento.objects.filter(consecutivo=990000130).exists())

    def test_no_se_crea_con_el_certificado_vencido(self):
        certificado = Certificado.objects.get(emisor=self.emisor)
        certificado.vigente_hasta = date.today() - timedelta(days=1)
        certificado.save(update_fields=["vigente_hasta"])

        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("venció", resp.data["errores"]["emisor"][0])

    def test_no_se_crea_si_el_certificado_aun_no_rige(self):
        certificado = Certificado.objects.get(emisor=self.emisor)
        certificado.vigente_desde = date.today() + timedelta(days=1)
        certificado.save(update_fields=["vigente_desde"])

        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("no rige hasta", resp.data["errores"]["emisor"][0])

    def test_se_crea_con_el_certificado_en_vigencia(self):
        certificado = Certificado.objects.get(emisor=self.emisor)
        certificado.vigente_desde = date.today() - timedelta(days=1)
        certificado.vigente_hasta = date.today() + timedelta(days=1)
        certificado.save(update_fields=["vigente_desde", "vigente_hasta"])

        self.assertEqual(self._crear().status_code, status.HTTP_201_CREATED)

    def test_no_se_crea_si_el_emisor_esta_inactivo(self):
        self.emisor.activo = False
        self.emisor.save(update_fields=["activo"])

        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data["errores"]["emisor"], [MENSAJE_EMISOR_INACTIVO])


class CatalogoAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.catalogos.models import Tributo

        Tributo.objects.create(codigo="01", nombre="IVA")
        Tributo.objects.create(codigo="04", nombre="INC")

    def test_catalogo_es_publico_y_busca(self):
        resp = self.client.get("/api/catalogos/tributo/?search=IVA")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        codigos = [r["codigo"] for r in resp.data["results"]]
        self.assertIn("01", codigos)
        self.assertNotIn("04", codigos)
