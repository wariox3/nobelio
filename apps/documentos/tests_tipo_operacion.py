"""Pruebas de la API: tipo de operación de la factura y remesa de transporte.

Qué tipos admite la creación del documento, qué exige cada uno a las líneas y
que la remesa solo vaya en la factura de transporte. El XML está en
``apps/dian/tests_tipo_operacion.py``.
"""
import copy

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalogos.carga import cargar
from apps.catalogos.models import TipoOperacion
from apps.documentos.models import Documento, DocumentoTipo
from apps.documentos.serializers.documento import (
    MENSAJE_AIU_SIN_NOTA,
    MENSAJE_REMESA_SOLO_EN_TRANSPORTE,
    MENSAJE_TRANSPORTE_SIN_REMESA,
    TEXTO_NOTA_AIU,
    mensaje_tipo_operacion_no_admitido,
    mensaje_tipo_operacion_no_aplica,
)
from apps.documentos.serializers.documento_detalle_remesa import (
    MENSAJE_CONSECUTIVO_INVALIDO,
)
from apps.documentos.tests_utils import crear_certificado, crear_documento_factura
from apps.nomina.tests_utils import cargar_catalogos_de_nomina
from apps.nucleo.serializers import CODIGO_CAMPO_DESCONOCIDO
from apps.nucleo.tests_utils import codigos, errores_por_campo

URL = "/api/documentos/documento/"
NOTA_AIU = f"{TEXTO_NOTA_AIU} obra civil bodega 3"
REMESA = {
    "radicado": 37796060, "consecutivo": "REM12345", "valor_flete": 45000,
    "cantidad": 2500, "unidad": "KGM",
}


class TipoOperacionAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cargar([TipoOperacion])
        datos = crear_documento_factura()
        cls.cat = datos["catalogos"]
        cls.emisor = datos["emisor"]
        cls.resolucion = datos["resolucion"]
        crear_certificado(cls.emisor)
        cls.contado, cls.efectivo = cargar_catalogos_de_nomina()
        cls.usuario = get_user_model().objects.create_user(
            email="tipo-operacion@example.com", password="x"
        )
        cls.usuario.emisores.add(cls.emisor)

    def setUp(self):
        self.client.force_authenticate(self.usuario)
        self.consecutivo = 990000300

    def _linea(self, numero=1, **extra):
        c = self.cat
        return {
            "numero_linea": numero, "descripcion": "Servicio",
            "codigo_producto": f"SRV-{numero}",
            "cantidad": "1", "unidad_medida": c["unidad"].id,
            "valor_unitario": "1000", "valor_total": "1000.00",
            "descuento": "0.00",
            "impuestos": [
                {"tributo": c["iva"].id, "base_gravable": "1000.00",
                 "tarifa": "19.00", "valor": "190.00"}
            ],
            **extra,
        }

    def _payload(self, lineas=None, **extra):
        c = self.cat
        self.consecutivo += 1
        return {
            "ambiente": 2,
            "documento_tipo": DocumentoTipo.objects.get(
                codigo=DocumentoTipo.Codigo.FACTURA_VENTA
            ).id,
            "emisor": self.emisor.id,
            "numero_resolucion": self.resolucion.numero_resolucion,
            "adquiriente": {
                "razon_social": "Cliente Demo",
                "tipo_identificacion": c["nit"].id,
                "numero_identificacion": "800199436",
                "tipo_organizacion": c["juridica"].id,
                "pais": c["colombia"].id,
                "departamento": c["antioquia"].id,
                "municipio": c["medellin"].id,
                "direccion": "Cra 4 # 5-6",
                "codigo_postal": "050001",
                "responsabilidades": [],
                "correo": "cliente@demo.co",
            },
            "prefijo": "SETP",
            "consecutivo": self.consecutivo,
            "numero": f"SETP{self.consecutivo}",
            "fecha_emision": timezone.localdate().isoformat(),
            "forma_pago": self.contado.id,
            "medio_pago": self.efectivo.id,
            "fecha_vencimiento": None,
            "moneda": c["cop"].id,
            "detalles": lineas if lineas is not None else [self._linea()],
            **extra,
        }

    def _crear(self, **kwargs):
        return self.client.post(URL, self._payload(**kwargs), format="json")

    def assertCreado(self, resp):
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def assertRechazado(self, resp, campo, mensaje):
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn(mensaje, errores_por_campo(resp).get(campo, []), resp.data)

    # --- Estándar --------------------------------------------------------

    def test_sin_tipo_de_operacion_crea_como_siempre(self):
        resp = self._crear()

        self.assertCreado(resp)
        self.assertIsNone(resp.data["tipo_operacion"])
        self.assertIsNone(resp.data["detalles"][0]["remesa"])

    def test_admite_null(self):
        resp = self._crear(tipo_operacion=None)

        self.assertCreado(resp)
        self.assertIsNone(Documento.objects.get(pk=resp.data["id"]).tipo_operacion)

    def test_el_id_es_el_codigo(self):
        resp = self._crear(tipo_operacion=10)

        self.assertCreado(resp)
        self.assertEqual(resp.data["tipo_operacion"], 10)
        self.assertEqual(
            Documento.objects.get(pk=resp.data["id"]).tipo_operacion.codigo, "10"
        )

    def test_el_listado_trae_el_tipo_de_operacion(self):
        pk = self._crear(tipo_operacion=10).data["id"]

        resp = self.client.get(URL)

        fila = next(d for d in resp.data["results"] if d["id"] == pk)
        self.assertEqual(fila["tipo_operacion"], 10)

    # --- Qué códigos se admiten -------------------------------------------

    def test_mandatos_y_cambiario_todavia_no(self):
        for codigo in ("11", "13"):
            with self.subTest(codigo=codigo):
                tipo = TipoOperacion.objects.get(codigo=codigo)

                resp = self._crear(tipo_operacion=tipo.id)

                self.assertRechazado(
                    resp, "tipo_operacion", mensaje_tipo_operacion_no_admitido(tipo),
                )

    def test_un_id_que_no_existe(self):
        resp = self._crear(tipo_operacion=99)

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn("tipo_operacion", errores_por_campo(resp))

    def test_solo_la_factura_lo_elige(self):
        """Una nota con tipo de operación responde 400 aunque sea el 10.

        Sin referencia (sale como 22): así no hace falta una factura aceptada.
        """
        tipo_nota = DocumentoTipo.objects.get(codigo=DocumentoTipo.Codigo.NOTA_CREDITO)
        payload = self._payload(
            tipo_operacion=10,
            documento_tipo=tipo_nota.id,
            numero_resolucion="",
            prefijo="NC",
            concepto_correccion="1",
        )

        resp = self.client.post(URL, payload, format="json")

        self.assertRechazado(resp, "tipo_operacion", mensaje_tipo_operacion_no_aplica(tipo_nota))

    # --- AIU ---------------------------------------------------------------

    def test_aiu_con_la_nota_de_administracion(self):
        lineas = [self._linea(1, nota=NOTA_AIU), self._linea(2), self._linea(3)]

        resp = self._crear(tipo_operacion=9, lineas=lineas)

        self.assertCreado(resp)
        self.assertEqual(resp.data["tipo_operacion"], 9)

    def test_aiu_sin_nota(self):
        resp = self._crear(tipo_operacion=9)

        self.assertRechazado(resp, "detalles", MENSAJE_AIU_SIN_NOTA)

    def test_aiu_con_una_nota_que_no_empieza_por_el_texto(self):
        lineas = [self._linea(1, nota=f"Administración. {NOTA_AIU}")]

        resp = self._crear(tipo_operacion=9, lineas=lineas)

        self.assertRechazado(resp, "detalles", MENSAJE_AIU_SIN_NOTA)

    def test_la_nota_aiu_no_obliga_fuera_de_aiu(self):
        resp = self._crear(tipo_operacion=10)

        self.assertCreado(resp)

    # --- Transporte ------------------------------------------------------

    def test_transporte_con_remesa_y_servicio_adicional(self):
        lineas = [self._linea(1, remesa=REMESA), self._linea(2)]

        resp = self._crear(tipo_operacion=12, lineas=lineas)

        self.assertCreado(resp)
        self.assertEqual(resp.data["detalles"][0]["remesa"], REMESA)
        self.assertIsNone(resp.data["detalles"][1]["remesa"])
        guardadas = Documento.objects.get(pk=resp.data["id"]).detalles.order_by("numero_linea")
        self.assertEqual([d.remesa for d in guardadas], [REMESA, None])

    def test_transporte_sin_ninguna_remesa(self):
        resp = self._crear(tipo_operacion=12)

        self.assertRechazado(resp, "detalles", MENSAJE_TRANSPORTE_SIN_REMESA)

    def test_la_remesa_fuera_de_transporte(self):
        lineas = [self._linea(1), self._linea(2, remesa=REMESA)]
        for tipo in (None, 10, 9):
            with self.subTest(tipo_operacion=tipo):
                extra = {"tipo_operacion": tipo} if tipo else {}
                if tipo == 9:
                    lineas[0]["nota"] = NOTA_AIU

                resp = self._crear(lineas=copy.deepcopy(lineas), **extra)

                self.assertRechazado(resp, "detalles[1].remesa", MENSAJE_REMESA_SOLO_EN_TRANSPORTE)

    def test_la_remesa_exige_sus_cinco_datos(self):
        for campo in REMESA:
            with self.subTest(falta=campo):
                remesa = {k: v for k, v in REMESA.items() if k != campo}

                resp = self._crear(tipo_operacion=12, lineas=[self._linea(1, remesa=remesa)])

                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
                self.assertIn(f"detalles[0].remesa.{campo}", errores_por_campo(resp))

    def test_valores_de_la_remesa_que_no_valen(self):
        casos = {
            "unidad": "LTR",
            "valor_flete": 45000.5,
            "cantidad": 0,
            "radicado": 100_000_000_001,
            "consecutivo": "X" * 16,
        }
        for campo, valor in casos.items():
            with self.subTest(campo=campo, valor=valor):
                remesa = {**REMESA, campo: valor}

                resp = self._crear(tipo_operacion=12, lineas=[self._linea(1, remesa=remesa)])

                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
                self.assertIn(f"detalles[0].remesa.{campo}", errores_por_campo(resp))

    def test_el_consecutivo_solo_alfanumerico(self):
        remesa = {**REMESA, "consecutivo": "REM 12/3"}

        resp = self._crear(tipo_operacion=12, lineas=[self._linea(1, remesa=remesa)])

        self.assertRechazado(resp, "detalles[0].remesa.consecutivo", MENSAJE_CONSECUTIVO_INVALIDO)

    def test_la_remesa_en_galones(self):
        remesa = {**REMESA, "unidad": "GLL"}

        resp = self._crear(tipo_operacion=12, lineas=[self._linea(1, remesa=remesa)])

        self.assertCreado(resp)

    def test_un_campo_de_mas_en_la_remesa(self):
        remesa = {**REMESA, "factura_original": "FAC1"}

        resp = self._crear(tipo_operacion=12, lineas=[self._linea(1, remesa=remesa)])

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn(CODIGO_CAMPO_DESCONOCIDO, codigos(resp))
