"""Pruebas de la API REST de documentos (flujo end-to-end)."""
import tempfile
from datetime import date, timedelta

from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption, pkcs12,
)
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.fields import Field
from rest_framework.test import APITestCase

from apps.catalogos.models import (
    Departamento, FormaPago, Municipio, Pais, TipoOrganizacion,
)
from apps.dian.tests_firma import _generar_certificado
from apps.documentos.models import (
    Adquiriente, Documento, DocumentoEstado, DocumentoTipo,
)
from apps.documentos.serializers.adquiriente import (
    CODIGO_PERSONA_NATURAL,
    mensaje_falta_en_colombia,
    mensaje_falta_en_persona_natural,
    mensaje_municipio_de_otro_departamento,
)
from apps.documentos.serializers.documento import (
    CODIGO_FORMA_PAGO_CREDITO,
    MENSAJE_CREDITO_SIN_VENCIMIENTO,
    MENSAJE_VENCIMIENTO_ANTERIOR_A_EMISION,
    mensaje_vencimiento_en_nota,
    MENSAJE_RESOLUCION_AMBIGUA,
    MENSAJE_RESOLUCION_NO_ENCONTRADA,
    mensaje_consecutivo_fuera_de_rango,
    mensaje_prefijo_ajeno,
    mensaje_resolucion_no_aplica,
    mensaje_sin_resolucion,
)
from apps.documentos.tests_utils import crear_documento_factura
from apps.emisores.models import Certificado, Emisor, Resolucion
from apps.emisores.servicios import (
    MENSAJE_EMISOR_INACTIVO,
    MENSAJE_SIN_CERTIFICADO,
)
from apps.nucleo.serializers import (
    CODIGO_CAMPO_DESCONOCIDO,
    CODIGO_CAMPO_SOLO_LECTURA,
    CODIGO_OBLIGATORIO,
    MENSAJE_CAMPO_DESCONOCIDO,
    MENSAJE_CAMPO_SOLO_LECTURA,
)
from apps.nomina.tests_utils import crear_catalogos_de_pago
from apps.nucleo.tests_utils import codigos, errores_por_campo

MEDIA_TEMP = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_TEMP)
class DocumentoAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        datos = crear_documento_factura()
        cls.documento = datos["documento"]
        cls.emisor = datos["emisor"]
        cls.cat = datos["catalogos"]
        cls.contado, cls.efectivo = crear_catalogos_de_pago()
        cls.credito, _ = FormaPago.objects.get_or_create(
            codigo=CODIGO_FORMA_PAGO_CREDITO, defaults={"nombre": "Crédito"},
        )
        # El documento del helper lleva la fecha del ejemplo oficial de la DIAN,
        # con la que se comprueba el CUFE en otras pruebas. Aquí se emite de
        # verdad, y firmar exige la fecha de hoy (regla FAD09).
        cls.documento.fecha_emision = timezone.localdate()
        cls.documento.save(update_fields=["fecha_emision"])

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
                "tipo_organizacion": c["juridica"].id,
                "pais": c["colombia"].id,
                "departamento": c["antioquia"].id,
                "municipio": c["medellin"].id,
                "direccion": "Cra 4 # 5-6",
                # Obligatorio desde que el anexo lo exige en el AddressLine del
                # adquiriente; el serializer lo pide y sin él la creación es 400.
                "codigo_postal": "050001",
                "responsabilidades": [],
                "correo": "cliente@demo.co",
            },
            "prefijo": "SETP",
            "consecutivo": 990000130,
            "numero": "SETP990000130",
            # Firmar exige la fecha de hoy (FAD09), y el serializer la valida ya
            # al crear: una fecha fija dejaría de valer al día siguiente.
            "fecha_emision": timezone.localdate().isoformat(),
            "forma_pago": self.contado.id,
            "medio_pago": self.efectivo.id,
            "fecha_vencimiento": None,
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
        self.assertIn("adquiriente", errores_por_campo(resp))

    # --- La estructura de la petición es estricta ---------------------------

    def test_un_campo_mal_escrito_no_crea_el_documento(self):
        """Antes se descartaba en silencio y el documento nacía sin ese dato."""
        payload = self._payload_documento()
        payload["fecha_vencimento"] = timezone.localdate().isoformat()
        antes = Documento.objects.count()

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(
            errores_por_campo(resp), {"fecha_vencimento": [MENSAJE_CAMPO_DESCONOCIDO]}
        )
        self.assertEqual(codigos(resp), [CODIGO_CAMPO_DESCONOCIDO])
        self.assertEqual(Documento.objects.count(), antes)

    def test_un_campo_mal_escrito_en_un_impuesto_se_rechaza(self):
        """El caso del P.O.S. del 2026-09-01: los impuestos con otro nombre."""
        payload = self._payload_documento()
        payload["detalles"][0]["impuestos"][0]["porcentaje"] = "19.00"

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(
            errores_por_campo(resp),
            {"detalles[0].impuestos[0].porcentaje": [MENSAJE_CAMPO_DESCONOCIDO]},
        )

    def test_la_estructura_se_valida_antes_que_los_datos(self):
        """Con una clave de más la respuesta solo trae eso.

        Aquí el emisor no existe y la fecha no es la de hoy, dos errores que
        la validación de campos informaría; no salen hasta que la estructura
        esté bien, para no mezclar «lee otro contrato» con «un dato está mal».
        """
        payload = self._payload_documento()
        payload["emisor"] = 999999
        payload["fecha_emision"] = "2020-01-01"
        payload["prueba"] = "x"

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(errores_por_campo(resp), {"prueba": [MENSAJE_CAMPO_DESCONOCIDO]})

        del payload["prueba"]
        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(set(errores_por_campo(resp)), {"emisor", "fecha_emision"})

    def test_lo_que_sobra_y_lo_que_falta_salen_juntos(self):
        """Los dos son la misma pregunta: ¿la petición sigue el contrato?"""
        payload = self._payload_documento()
        payload["prueba"] = "x"
        del payload["moneda"]
        del payload["detalles"][0]["impuestos"][0]["tributo"]

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        obligatorio = str(Field.default_error_messages["required"])
        self.assertEqual(errores_por_campo(resp), {
            "prueba": [MENSAJE_CAMPO_DESCONOCIDO],
            "moneda": [obligatorio],
            "detalles[0].impuestos[0].tributo": [obligatorio],
        })
        self.assertEqual(
            sorted(codigos(resp)),
            sorted([CODIGO_CAMPO_DESCONOCIDO, CODIGO_OBLIGATORIO, CODIGO_OBLIGATORIO]),
        )

    def test_los_importes_no_se_pueden_omitir(self):
        """Antes un importe que no venía se guardaba en cero sin avisar."""
        payload = self._payload_documento()
        linea = payload["detalles"][0]
        for campo in ("cantidad", "valor_unitario", "valor_total"):
            del linea[campo]
        for campo in ("base_gravable", "tarifa", "valor"):
            del linea["impuestos"][0][campo]

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        obligatorio = str(Field.default_error_messages["required"])
        self.assertEqual(errores_por_campo(resp), {
            "detalles[0].cantidad": [obligatorio],
            "detalles[0].valor_unitario": [obligatorio],
            "detalles[0].valor_total": [obligatorio],
            "detalles[0].impuestos[0].base_gravable": [obligatorio],
            "detalles[0].impuestos[0].tarifa": [obligatorio],
            "detalles[0].impuestos[0].valor": [obligatorio],
        })

    def test_el_descuento_de_la_linea_sigue_siendo_opcional(self):
        payload = self._payload_documento()
        self.assertNotIn("descuento", payload["detalles"][0])

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_la_clave_prefijo_es_obligatoria(self):
        payload = self._payload_documento()
        del payload["prefijo"]

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(
            errores_por_campo(resp),
            {"prefijo": [str(Field.default_error_messages["required"])]},
        )
        self.assertEqual(codigos(resp), [CODIGO_OBLIGATORIO])

    def test_la_fecha_de_emision_es_obligatoria(self):
        payload = self._payload_documento()
        del payload["fecha_emision"]

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(
            errores_por_campo(resp),
            {"fecha_emision": [str(Field.default_error_messages["required"])]},
        )

    def test_la_hora_de_emision_no_se_envia(self):
        """La pone el sistema al firmar: la del ERP se descartaba sin avisar."""
        payload = self._payload_documento()
        payload["hora_emision"] = "10:00:00"

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(
            errores_por_campo(resp), {"hora_emision": [MENSAJE_CAMPO_DESCONOCIDO]}
        )

    def test_el_documento_creado_lleva_la_hora_del_sistema(self):
        antes = timezone.localtime().replace(microsecond=0).time()
        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertGreaterEqual(resp.data["hora_emision"], antes.isoformat())

    # --- Forma de pago y vencimiento ----------------------------------------

    def _post(self, payload):
        return self.client.post("/api/documentos/documento/", payload, format="json")

    def test_forma_medio_y_vencimiento_son_claves_obligatorias(self):
        """Sin forma de pago el XML salía como contado en efectivo."""
        payload = self._payload_documento()
        for campo in ("forma_pago", "medio_pago", "fecha_vencimiento"):
            del payload[campo]

        resp = self._post(payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        obligatorio = str(Field.default_error_messages["required"])
        self.assertEqual(errores_por_campo(resp), {
            "forma_pago": [obligatorio],
            "medio_pago": [obligatorio],
            "fecha_vencimiento": [obligatorio],
        })

    def test_la_forma_de_pago_no_admite_nulo(self):
        payload = self._payload_documento()
        payload["forma_pago"] = None

        resp = self._post(payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(codigos(resp), ["null"])

    def test_contado_sin_vencimiento_se_crea(self):
        resp = self._post(self._payload_documento())
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertIsNone(resp.data["fecha_vencimiento"])

    def test_contado_con_vencimiento_se_crea_sin_el(self):
        """La fecha se descarta: antes salía con DueDate en un XML que decía contado."""
        payload = self._payload_documento()
        payload["fecha_vencimiento"] = (timezone.localdate() + timedelta(days=30)).isoformat()

        resp = self._post(payload)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertIsNone(resp.data["fecha_vencimiento"])
        self.assertIsNone(Documento.objects.get(pk=resp.data["id"]).fecha_vencimiento)

    def test_credito_sin_vencimiento_no_se_crea(self):
        payload = self._payload_documento()
        payload["forma_pago"] = self.credito.id

        resp = self._post(payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(
            errores_por_campo(resp), {"fecha_vencimiento": [MENSAJE_CREDITO_SIN_VENCIMIENTO]}
        )

    def test_credito_con_vencimiento_anterior_a_la_emision_no_se_crea(self):
        payload = self._payload_documento()
        payload["forma_pago"] = self.credito.id
        payload["fecha_vencimiento"] = (timezone.localdate() - timedelta(days=1)).isoformat()

        resp = self._post(payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(
            errores_por_campo(resp),
            {"fecha_vencimiento": [MENSAJE_VENCIMIENTO_ANTERIOR_A_EMISION]},
        )

    def test_credito_con_vencimiento_se_crea(self):
        payload = self._payload_documento()
        payload["forma_pago"] = self.credito.id
        vence = timezone.localdate() + timedelta(days=30)
        payload["fecha_vencimiento"] = vence.isoformat()

        resp = self._post(payload)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["fecha_vencimiento"], vence.isoformat())

    def test_una_nota_con_vencimiento_no_se_crea(self):
        """En la nota salía a medias: sin DueDate pero con PaymentDueDate.

        Vale aunque la nota sea a crédito: la regla de las notas va primero, y
        no se le exige un vencimiento que no puede llevar.
        """
        tipo = DocumentoTipo.objects.get(codigo=DocumentoTipo.Codigo.NOTA_CREDITO)
        payload = self._payload_documento()
        payload.update({
            "documento_tipo": tipo.id,
            "numero_resolucion": "",
            "documento_referencia": str(self.documento.id),
            "concepto_correccion": Documento.ConceptoNotaCredito.ANULACION,
            "prefijo": "NC", "consecutivo": 1, "numero": "NC1",
            "forma_pago": self.credito.id,
            "fecha_vencimiento": (timezone.localdate() + timedelta(days=30)).isoformat(),
        })

        resp = self._post(payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(
            errores_por_campo(resp),
            {"fecha_vencimiento": [mensaje_vencimiento_en_nota(tipo)]},
        )

    def test_una_nota_a_credito_sin_vencimiento_se_crea(self):
        tipo = DocumentoTipo.objects.get(codigo=DocumentoTipo.Codigo.NOTA_CREDITO)
        payload = self._payload_documento()
        payload.update({
            "documento_tipo": tipo.id,
            "numero_resolucion": "",
            "documento_referencia": str(self.documento.id),
            "concepto_correccion": Documento.ConceptoNotaCredito.ANULACION,
            "prefijo": "NC", "consecutivo": 1, "numero": "NC1",
            "forma_pago": self.credito.id,
        })

        resp = self._post(payload)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_el_prefijo_vacio_pasa_la_estructura_y_lo_juzga_la_resolucion(self):
        """`""` dice «sin prefijo»: es válido como estructura, y si la resolución
        sí numera con prefijo lo rechaza la regla de numeración, no el contrato."""
        payload = self._payload_documento()
        payload["prefijo"] = ""
        payload["numero"] = "990000130"

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(
            errores_por_campo(resp),
            {"prefijo": [mensaje_prefijo_ajeno(self.documento.resolucion)]},
        )
        self.assertEqual(codigos(resp), ["invalid"])

    def test_un_campo_de_solo_lectura_se_rechaza(self):
        """Lo que devuelve la lectura no se puede reenviar tal cual al crear."""
        payload = self._payload_documento()
        payload["detalles"][0]["impuestos"][0]["tributo_codigo"] = "01"

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(
            errores_por_campo(resp),
            {"detalles[0].impuestos[0].tributo_codigo": [MENSAJE_CAMPO_SOLO_LECTURA]},
        )
        self.assertEqual(codigos(resp), [CODIGO_CAMPO_SOLO_LECTURA])

    def test_el_digito_de_verificacion_no_se_envia(self):
        """Lo calcula el sistema: el del ERP se sobrescribía sin avisar."""
        payload = self._payload_documento()
        payload["adquiriente"]["digito_verificacion"] = "1"

        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(
            errores_por_campo(resp),
            {"adquiriente.digito_verificacion": [MENSAJE_CAMPO_SOLO_LECTURA]},
        )
        self.assertEqual(codigos(resp), [CODIGO_CAMPO_SOLO_LECTURA])

    def test_el_digito_de_verificacion_sale_calculado(self):
        """El de 800199436 es 4. Este payload lo mandaba como 6, igual que el
        ejemplo del README: el caso exacto que el cálculo evita."""
        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["adquiriente"]["digito_verificacion"], "4")

    def _crear_con(self, payload):
        return self.client.post("/api/documentos/documento/", payload, format="json")

    # --- Nombre desglosado según el tipo de organización --------------------

    def _payload_persona_natural(self, **nombres):
        natural, _ = TipoOrganizacion.objects.get_or_create(
            codigo=CODIGO_PERSONA_NATURAL, defaults={"nombre": "Persona Natural"},
        )
        payload = self._payload_documento()
        payload["adquiriente"].update(
            {"tipo_organizacion": natural.id, "razon_social": "Ana Pérez", **nombres}
        )
        return payload

    def test_las_responsabilidades_del_adquiriente_son_clave_obligatoria(self):
        payload = self._payload_documento()
        del payload["adquiriente"]["responsabilidades"]

        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(
            errores_por_campo(resp),
            {"adquiriente.responsabilidades": [str(Field.default_error_messages["required"])]},
        )

    def test_las_responsabilidades_admiten_la_lista_vacia(self):
        """Vacía sale como R-99-PN en el XML."""
        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["adquiriente"]["responsabilidades"], [])

    def test_persona_natural_sin_nombres_no_se_crea(self):
        resp = self._crear_con(self._payload_persona_natural())
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(errores_por_campo(resp), {
            "adquiriente.primer_nombre": [mensaje_falta_en_persona_natural("primer_nombre")],
            "adquiriente.primer_apellido": [
                mensaje_falta_en_persona_natural("primer_apellido")
            ],
        })

    def test_persona_natural_sin_segundo_nombre_ni_segundo_apellido_se_crea(self):
        payload = self._payload_persona_natural(primer_nombre="Ana", primer_apellido="Pérez")

        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["adquiriente"]["primer_nombre"], "Ana")
        self.assertEqual(resp.data["adquiriente"]["primer_apellido"], "Pérez")

    def test_persona_juridica_con_nombres_se_crea_sin_ellos(self):
        """Se descartan: antes una empresa con nombres salía con cac:Person."""
        payload = self._payload_documento()
        payload["adquiriente"].update({
            "primer_nombre": "Ana", "segundo_nombre": "María",
            "primer_apellido": "Pérez", "segundo_apellido": "Gómez",
        })

        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        adquiriente = Documento.objects.get(pk=resp.data["id"]).adquiriente
        for campo in ("primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido"):
            self.assertEqual(getattr(adquiriente, campo), "", campo)

    # --- Correo y ubicación del adquiriente ---------------------------------

    def test_el_correo_del_adquiriente_es_obligatorio(self):
        """Es a donde `notificar` entrega el documento."""
        payload = self._payload_documento()
        del payload["adquiriente"]["correo"]

        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(
            errores_por_campo(resp),
            {"adquiriente.correo": [str(Field.default_error_messages["required"])]},
        )

    def test_el_correo_del_adquiriente_no_puede_ir_vacio(self):
        payload = self._payload_documento()
        payload["adquiriente"]["correo"] = ""

        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(codigos(resp), ["blank"])

    def test_adquiriente_en_colombia_sin_ubicacion_no_se_crea(self):
        payload = self._payload_documento()
        for campo in ("departamento", "municipio", "direccion"):
            del payload["adquiriente"][campo]

        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(errores_por_campo(resp), {
            f"adquiriente.{campo}": [mensaje_falta_en_colombia(campo)]
            for campo in ("departamento", "municipio", "direccion")
        })

    def test_el_municipio_tiene_que_ser_del_departamento(self):
        """Antes Medellín con Cundinamarca pasaba."""
        cundinamarca, _ = Departamento.objects.get_or_create(
            codigo="25", defaults={"nombre": "Cundinamarca"},
        )
        payload = self._payload_documento()
        payload["adquiriente"]["departamento"] = cundinamarca.id

        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(errores_por_campo(resp), {
            "adquiriente.municipio": [
                mensaje_municipio_de_otro_departamento(self.cat["medellin"], cundinamarca)
            ],
        })

    def test_sin_departamento_en_el_catalogo_se_compara_por_el_codigo(self):
        """La relación del catálogo es nullable; sin ella mandan los dos dígitos."""
        bogota_dc, _ = Departamento.objects.get_or_create(
            codigo="11", defaults={"nombre": "Bogotá D.C."},
        )
        bogota, _ = Municipio.objects.get_or_create(
            codigo="11001", defaults={"nombre": "Bogotá"},
        )
        self.assertIsNone(bogota.departamento_id)
        payload = self._payload_documento()
        payload["adquiriente"]["municipio"] = bogota.id

        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn("adquiriente.municipio", errores_por_campo(resp))

        payload["adquiriente"]["departamento"] = bogota_dc.id
        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_adquiriente_extranjero_se_crea_sin_departamento_ni_municipio(self):
        """Son catálogos colombianos: se descartan. La dirección se conserva."""
        extranjero, _ = Pais.objects.get_or_create(
            codigo="US", defaults={"nombre": "Estados Unidos"},
        )
        payload = self._payload_documento()
        payload["adquiriente"]["pais"] = extranjero.id

        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        adquiriente = Documento.objects.get(pk=resp.data["id"]).adquiriente
        self.assertIsNone(adquiriente.departamento)
        self.assertIsNone(adquiriente.municipio)
        self.assertEqual(adquiriente.direccion, "Cra 4 # 5-6")

    def test_adquiriente_extranjero_no_necesita_ubicacion(self):
        extranjero, _ = Pais.objects.get_or_create(
            codigo="US", defaults={"nombre": "Estados Unidos"},
        )
        payload = self._payload_documento()
        payload["adquiriente"]["pais"] = extranjero.id
        for campo in ("departamento", "municipio", "direccion"):
            del payload["adquiriente"][campo]

        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_el_documento_no_se_edita(self):
        """Ni `PUT` ni `PATCH`, en ningún estado.

        Antes se podía corregir un borrador —el correo del receptor, el
        consecutivo— y era el propio serializer el que frenaba al llegar a
        firmado. Ahora no hay nada que frenar: la ruta no existe, así que
        tampoco hay una regla que se pueda escapar por una rama nueva.

        Corregir un borrador es borrarlo y volver a crearlo, que además libera
        el consecutivo; corregir uno emitido es una nota.
        """
        for metodo in (self.client.put, self.client.patch):
            with self.subTest(metodo=metodo.__name__):
                resp = metodo(
                    self._url(),
                    {"adquiriente": {"correo": "nuevo@cliente.co"}},
                    format="json",
                )
                self.assertEqual(
                    resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED, resp.data
                )

        self.documento.adquiriente.refresh_from_db()
        self.assertNotEqual(self.documento.adquiriente.correo, "nuevo@cliente.co")

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

    def test_la_clave_numero_de_resolucion_es_obligatoria(self):
        payload = self._payload_documento()
        del payload["numero_resolucion"]

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            errores_por_campo(resp),
            {"numero_resolucion": [str(Field.default_error_messages["required"])]},
        )
        self.assertEqual(codigos(resp), [CODIGO_OBLIGATORIO])
        self.assertFalse(Documento.objects.filter(consecutivo=990000130).exists())

    def test_una_factura_con_la_resolucion_vacia_no_se_crea(self):
        """`""` pasa la estructura —es lo que mandan las notas—, pero una factura
        se numera con resolución y eso lo dice la regla de datos."""
        payload = self._payload_documento()
        payload["numero_resolucion"] = ""

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        tipo = DocumentoTipo.objects.get(codigo=DocumentoTipo.Codigo.FACTURA_VENTA)
        self.assertEqual(
            errores_por_campo(resp), {"numero_resolucion": [mensaje_sin_resolucion(tipo)]}
        )
        self.assertFalse(Documento.objects.filter(consecutivo=990000130).exists())

    def test_una_nota_se_crea_con_la_resolucion_vacia(self):
        """La nota no se numera con resolución: manda la clave vacía y se crea."""
        payload = self._payload_documento()
        payload.update({
            "documento_tipo": DocumentoTipo.objects.get(
                codigo=DocumentoTipo.Codigo.NOTA_CREDITO
            ).id,
            "numero_resolucion": "",
            "documento_referencia": str(self.documento.id),
            "concepto_correccion": Documento.ConceptoNotaCredito.ANULACION,
            "prefijo": "NC", "consecutivo": 1, "numero": "NC1",
        })

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertIsNone(resp.data["resolucion"])

    def test_una_nota_con_resolucion_no_se_crea(self):
        """Antes se le asociaba la resolución y se validaba contra ella."""
        payload = self._payload_documento()
        tipo = DocumentoTipo.objects.get(codigo=DocumentoTipo.Codigo.NOTA_CREDITO)
        payload.update({
            "documento_tipo": tipo.id,
            "documento_referencia": str(self.documento.id),
            "concepto_correccion": Documento.ConceptoNotaCredito.ANULACION,
            "prefijo": "NC", "consecutivo": 1, "numero": "NC1",
        })
        self.assertTrue(payload["numero_resolucion"])

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(
            errores_por_campo(resp),
            {"numero_resolucion": [mensaje_resolucion_no_aplica(tipo)]},
        )
        self.assertFalse(Documento.objects.filter(documento_tipo=tipo).exists())

    def test_el_id_de_la_resolucion_no_se_acepta_al_crear(self):
        """Mandar el id no numera: el campo ya no existe en la creación.

        Y lo dice él mismo. Antes el `resolucion` se descartaba y el 400 salía
        por el `numero_resolucion` que faltaba, que no le explica a quien
        integra por qué su id no valió.
        """
        payload = self._payload_documento()
        del payload["numero_resolucion"]
        payload["resolucion"] = self.documento.resolucion.id

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(errores_por_campo(resp), {
            "resolucion": [MENSAJE_CAMPO_DESCONOCIDO],
            "numero_resolucion": [str(Field.default_error_messages["required"])],
        })

    def test_numero_de_resolucion_inexistente(self):
        payload = self._payload_documento()
        payload["numero_resolucion"] = "99999999999"

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            errores_por_campo(resp)["numero_resolucion"],
            [MENSAJE_RESOLUCION_NO_ENCONTRADA],
        )

    def test_el_numero_se_busca_solo_dentro_del_emisor(self):
        """El mismo número en otro emisor no se puede colar en este documento."""
        original = self.documento.resolucion
        otro_emisor = Emisor.objects.get(pk=self.emisor.pk)
        otro_emisor.pk = None
        otro_emisor.numero_identificacion = "900000001"
        otro_emisor.save(force_insert=True)
        ajena = Resolucion.objects.create(
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
            errores_por_campo(resp)["numero_resolucion"],
            [MENSAJE_RESOLUCION_NO_ENCONTRADA],
        )

    def test_numero_repetido_se_desempata_con_el_prefijo(self):
        original = self.documento.resolucion
        # La gemela es del **mismo tipo** que la original y solo cambia el
        # prefijo. Tiene que serlo para que el caso siga probando lo que dice
        # su nombre: el desempate mira primero el tipo del documento, así que
        # con una resolución de otro tipo (una nota crédito) el tipo ya
        # resolvería y el prefijo no llegaría a usarse nunca.
        gemela = Resolucion.objects.create(
            emisor=self.emisor, tipo_factura=original.tipo_factura,
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
            errores_por_campo(resp)["numero_resolucion"], [MENSAJE_RESOLUCION_AMBIGUA]
        )
        self.assertTrue(Resolucion.objects.filter(pk=gemela.pk).exists())

    # --- El número tiene que caber en lo que autorizó la resolución --------

    def test_no_se_crea_con_un_prefijo_que_no_es_el_de_la_resolucion(self):
        payload = self._payload_documento()
        payload["prefijo"] = "FE"
        payload["numero"] = "FE990000130"

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            errores_por_campo(resp)["prefijo"],
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
            errores_por_campo(resp)["consecutivo"],
            [mensaje_consecutivo_fuera_de_rango(resolucion)],
        )

    def test_se_crea_en_los_extremos_del_rango(self):
        resolucion = self.documento.resolucion
        payload = self._payload_documento()
        payload["consecutivo"] = resolucion.rango_desde
        payload["numero"] = f"SETP{resolucion.rango_desde}"

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    # --- El emisor tiene que estar en condiciones de firmar ----------------

    def test_no_se_crea_si_el_emisor_no_tiene_certificado(self):
        Certificado.objects.filter(emisor=self.emisor).delete()
        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(errores_por_campo(resp)["emisor"], [MENSAJE_SIN_CERTIFICADO])
        self.assertFalse(Documento.objects.filter(consecutivo=990000130).exists())

    def test_no_se_crea_con_el_certificado_vencido(self):
        certificado = Certificado.objects.get(emisor=self.emisor)
        certificado.vigente_hasta = date.today() - timedelta(days=1)
        certificado.save(update_fields=["vigente_hasta"])

        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("venció", errores_por_campo(resp)["emisor"][0])

    def test_no_se_crea_si_el_certificado_aun_no_rige(self):
        certificado = Certificado.objects.get(emisor=self.emisor)
        certificado.vigente_desde = date.today() + timedelta(days=1)
        certificado.save(update_fields=["vigente_desde"])

        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("no rige hasta", errores_por_campo(resp)["emisor"][0])

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
        self.assertEqual(errores_por_campo(resp)["emisor"], [MENSAJE_EMISOR_INACTIVO])


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
