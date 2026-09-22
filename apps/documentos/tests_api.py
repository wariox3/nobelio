"""Pruebas de la API REST de documentos (flujo end-to-end)."""
import copy
import json
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

import requests
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption, pkcs12,
)
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.fields import Field
from rest_framework.test import APITestCase
from rest_framework.utils.encoders import JSONEncoder

from apps.catalogos import memoria as memoria_de_catalogos
from apps.catalogos.models import (
    Departamento, FormaPago, Municipio, Pais, ResponsabilidadFiscal,
    TipoOrganizacion, Tributo,
)
from apps.dian import soap
from apps.dian.tests_firma import _generar_certificado
from apps.dian.tests_servicios import FakeCliente
from apps.documentos.models import (
    Adquiriente, Documento, DocumentoEstado, DocumentoEvento, DocumentoTipo,
)
from apps.documentos.serializers.documento_detalle import (
    CODIGO_MAYOR_QUE_CERO,
    MENSAJE_PERIODO_AL_REVES,
    mensaje_total_linea_descuadrado,
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
    DocumentoCrearSerializer,
    mensaje_descuentos_mayores_que_el_bruto,
    mensaje_lineas_repetidas,
    mensaje_retencion_no_admitida,
    MENSAJE_RESOLUCION_AMBIGUA,
    MENSAJE_RESOLUCION_NO_ENCONTRADA,
    mensaje_consecutivo_fuera_de_rango,
    mensaje_prefijo_ajeno,
    mensaje_resolucion_no_aplica,
    mensaje_sin_resolucion,
)
from apps.documentos.tests_utils import crear_documento_factura
from apps.documentos.views import DocumentoViewSet
from apps.documentos.views.documento import CODIGO_DOCUMENTO_DUPLICADO
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
URL_EVENTOS = "/api/documentos/documento-evento/"


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

    def test_filtrar_por_respuesta_validado_y_mostrarlo_en_la_lista(self):
        url = "/api/documentos/documento/"
        self.assertIs(self.client.get(url).data["results"][0]["respuesta_validado"], False)
        self.assertEqual(self.client.get(url, {"respuesta_validado": "true"}).data["count"], 0)
        self.assertGreaterEqual(
            self.client.get(url, {"respuesta_validado": "false"}).data["count"], 1
        )

        type(self.documento).objects.filter(pk=self.documento.pk).update(respuesta_validado=True)
        resp = self.client.get(url, {"respuesta_validado": "true"})
        self.assertEqual(resp.data["count"], 1)
        self.assertIs(resp.data["results"][0]["respuesta_validado"], True)

    def _emitir(self, cliente=None):
        """`emitir/` con la DIAN simulada: por defecto, acepta."""
        cliente = cliente or FakeCliente(
            soap.RespuestaDian(track_id="track-1", es_valido=True, codigo_estado="00")
        )
        with mock.patch("apps.dian.servicios.construir_cliente", return_value=cliente):
            return self.client.post(self._url("emitir/"))

    def test_emitir_firma_y_envia_en_una_llamada(self):
        cliente = FakeCliente(
            soap.RespuestaDian(track_id="track-1", es_valido=True, codigo_estado="00")
        )
        resp = self._emitir(cliente)

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data["estado"], DocumentoEstado.Nombre.ACEPTADO)
        self.assertEqual(len(resp.data["cufe_cude"]), 96)
        self.assertEqual(resp.data["track_id"], "track-1")
        self.assertEqual(len(cliente.llamadas), 1)

    def test_emitir_aceptado_marca_respuesta_validado(self):
        # Sin webhooks de validación no hay a quién avisar: se marca sin más.
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._emitir()

        self.assertEqual(resp.data["estado"], DocumentoEstado.Nombre.ACEPTADO)
        self.documento.refresh_from_db()
        self.assertTrue(self.documento.respuesta_validado)

    def test_emitir_aceptado_avisa_y_con_un_200_marca(self):
        from apps.emisores.models import Webhook, WebhookAviso

        self.emisor.referencia_externa = "12"
        self.emisor.save(update_fields=["referencia_externa"])
        Webhook.objects.create(
            emisor=self.emisor, nombre="torio", url="https://torio.co/hook",
            estado_validado=True, secreto="s3creto",
        )
        ok = mock.Mock(status_code=200, text="")
        with mock.patch("apps.emisores.servicios.webhooks.requests.post", return_value=ok) as post, \
                self.captureOnCommitCallbacks(execute=True):
            self._emitir()

        post.assert_called_once()
        self.assertEqual(WebhookAviso.objects.get().codigo_http, 200)
        self.documento.refresh_from_db()
        self.assertTrue(self.documento.respuesta_validado)

    def test_emitir_rechazado_no_marca_respuesta_validado(self):
        rechazo = FakeCliente(soap.RespuestaDian(
            track_id="track-1", es_valido=False, codigo_estado="99",
            errores=["Regla: FAD01, Rechazo: algo"],
        ))
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._emitir(rechazo)

        self.assertEqual(resp.data["estado"], DocumentoEstado.Nombre.RECHAZADO)
        self.documento.refresh_from_db()
        self.assertFalse(self.documento.respuesta_validado)

    def test_si_el_envio_falla_queda_firmado_y_el_reintento_manda_el_mismo_cufe(self):
        """La firma se confirma antes de enviar.

        Si se deshiciera con el fallo, el reintento firmaría con otra hora y
        otro CUFE para el mismo número, y la DIAN —que pudo recibir el
        primero— lo rechazaría.
        """
        caido = mock.Mock()
        caido.enviar_set_pruebas.side_effect = requests.ConnectionError("sin red")
        caido.enviar_factura_sincrono.side_effect = requests.ConnectionError("sin red")
        fallo = self._emitir(caido)

        self.assertEqual(fallo.status_code, status.HTTP_502_BAD_GATEWAY, fallo.data)
        self.documento.refresh_from_db()
        self.assertEqual(self.documento.estado.nombre, DocumentoEstado.Nombre.FIRMADO)
        cufe = self.documento.cufe_cude
        self.assertEqual(len(cufe), 96)

        reintento = self._emitir()
        self.assertEqual(reintento.status_code, status.HTTP_200_OK, reintento.data)
        self.assertEqual(reintento.data["estado"], DocumentoEstado.Nombre.ACEPTADO)
        self.assertEqual(reintento.data["cufe_cude"], cufe)

    def test_emitir_un_enviado_consulta_y_aplica_sin_reenviar(self):
        """Sin `actualizar-estado/`: el ERP llama a `emitir/` hasta el estado final."""
        sin_veredicto = FakeCliente(soap.RespuestaDian(track_id="zip-1"))
        primera = self._emitir(sin_veredicto)
        self.assertEqual(primera.data["estado"], DocumentoEstado.Nombre.ENVIADO, primera.data)
        self.assertEqual(primera.data["accion"], "enviado")
        self.assertIsNone(primera.data["fecha_validacion"])

        dian = FakeCliente(soap.RespuestaDian(es_valido=True, codigo_estado="00"))
        segunda = self._emitir(dian)

        self.assertEqual(segunda.status_code, status.HTTP_200_OK, segunda.data)
        self.assertEqual(segunda.data["estado"], DocumentoEstado.Nombre.ACEPTADO)
        self.assertEqual(segunda.data["accion"], "consultado")
        self.assertIsNotNone(segunda.data["fecha_validacion"])
        # Solo consultó —por el ZipKey, porque salió al Set de Pruebas—; no reenvió.
        self.assertEqual([llamada[0] for llamada in dian.llamadas], ["estado_zip"])
        self.assertEqual(segunda.data["cufe_cude"], primera.data["cufe_cude"])

    def test_consultar_no_cambia_el_documento(self):
        """Solo lectura, también sobre un enviado: aplicar es de `emitir/`."""
        self._emitir(FakeCliente(soap.RespuestaDian(track_id="zip-1")))
        dian = FakeCliente(soap.RespuestaDian(es_valido=True, codigo_estado="00"))
        with mock.patch("apps.dian.servicios.construir_cliente", return_value=dian):
            resp = self.client.get(self._url("consultar/"))

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertTrue(resp.data["es_valido"])
        self.documento.refresh_from_db()
        self.assertEqual(self.documento.estado.nombre, DocumentoEstado.Nombre.ENVIADO)

    def test_no_se_emite_lo_aceptado_ni_lo_rechazado(self):
        """Estados finales: 400 sin llamar a la DIAN. El rechazado se lee con `consultar/`."""
        for estado in (
            DocumentoEstado.Nombre.ACEPTADO,
            DocumentoEstado.Nombre.RECHAZADO,
        ):
            with self.subTest(estado=estado):
                self.documento.estado = DocumentoEstado.objects.get(nombre=estado)
                self.documento.save(update_fields=["estado"])
                cliente = FakeCliente(soap.RespuestaDian(es_valido=True))

                resp = self._emitir(cliente)

                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
                self.assertEqual(cliente.llamadas, [])

    # --- Eventos: un registro por cada cambio de estado ----------------------

    def _eventos(self):
        return list(self.documento.eventos.values_list("tipo", flat=True))

    def test_crear_no_deja_evento(self):
        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertFalse(Documento.objects.get(pk=resp.data["id"]).eventos.exists())

    def test_emitir_y_validarse_en_el_envio_deja_firmado_y_validado(self):
        """Nunca estuvo en `enviado`, así que ese evento no aparece."""
        self._emitir()
        self.documento.refresh_from_db()

        self.assertEqual(self._eventos(), ["firmado", "validado"])
        firmado, validado = self.documento.eventos.all()
        self.assertEqual(firmado.datos["cufe_cude"], self.documento.cufe_cude)
        self.assertEqual(validado.datos["origen"], "envio")
        self.assertEqual(validado.datos["track_id"], "track-1")
        self.assertIn("fecha_validacion", validado.datos)

    def test_sin_veredicto_deja_enviado_y_la_consulta_deja_validado(self):
        self._emitir(FakeCliente(soap.RespuestaDian(track_id="zip-1")))
        self.assertEqual(self._eventos(), ["firmado", "enviado"])

        # Una consulta que sigue sin veredicto no cambia el estado: no deja nada.
        self._emitir(FakeCliente(soap.RespuestaDian(codigo_estado="99")))
        self.assertEqual(self._eventos(), ["firmado", "enviado"])

        self._emitir(FakeCliente(soap.RespuestaDian(es_valido=True, codigo_estado="00")))
        self.assertEqual(self._eventos(), ["firmado", "enviado", "validado"])
        self.assertEqual(self.documento.eventos.last().datos["origen"], "consulta")

    def test_el_rechazo_guarda_las_reglas(self):
        errores = ["Regla: FAJ24, Rechazo: DV del NIT no es correcto"]
        self._emitir(FakeCliente(soap.RespuestaDian(codigo_estado="99", errores=errores)))

        self.assertEqual(self._eventos(), ["firmado", "rechazado"])
        self.assertEqual(self.documento.eventos.last().datos["errores"], errores)

    def test_un_502_solo_deja_la_firma(self):
        caido = mock.Mock()
        caido.enviar_set_pruebas.side_effect = requests.ConnectionError("sin red")
        caido.enviar_factura_sincrono.side_effect = requests.ConnectionError("sin red")
        self._emitir(caido)

        self.assertEqual(self._eventos(), ["firmado"])

    def test_el_endpoint_de_eventos_los_devuelve_en_orden(self):
        self._emitir(FakeCliente(soap.RespuestaDian(track_id="zip-1")))
        self._emitir(FakeCliente(soap.RespuestaDian(es_valido=True, codigo_estado="00")))

        resp = self.client.get(URL_EVENTOS, {"documento": self.documento.id})

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        eventos = resp.data["results"]
        self.assertEqual([e["tipo"] for e in eventos], ["firmado", "enviado", "validado"])
        self.assertEqual(set(eventos[0]), {"id", "documento", "tipo", "datos", "fecha"})

        por_tipo = self.client.get(URL_EVENTOS, {"documento": self.documento.id, "tipo": "enviado"})
        self.assertEqual([e["tipo"] for e in por_tipo.data["results"]], ["enviado"])

    def test_los_eventos_no_se_consultan_desde_el_documento(self):
        self.assertEqual(
            self.client.get(self._url("eventos/")).status_code, status.HTTP_404_NOT_FOUND,
        )

    def test_los_eventos_son_de_solo_lectura(self):
        self._emitir()
        evento = self.documento.eventos.first()
        self.assertEqual(
            self.client.post(URL_EVENTOS, {}, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.delete(f"{URL_EVENTOS}{evento.id}/").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_los_eventos_de_otra_cuenta_no_se_ven(self):
        self._emitir()
        evento = self.documento.eventos.first()
        extrano = get_user_model().objects.create_user(email="otro@example.com", password="x")
        self.client.force_authenticate(extrano)

        self.assertEqual(self.client.get(URL_EVENTOS).data["count"], 0)
        self.assertEqual(
            self.client.get(f"{URL_EVENTOS}{evento.id}/").status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_el_filtro_de_documento_exige_un_uuid(self):
        resp = self.client.get(URL_EVENTOS, {"documento": "abc"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)

    def test_borrar_el_documento_borra_sus_eventos(self):
        errores = ["Regla: FAJ24, Rechazo: DV del NIT no es correcto"]
        self._emitir(FakeCliente(soap.RespuestaDian(codigo_estado="99", errores=errores)))
        self.assertTrue(DocumentoEvento.objects.filter(documento=self.documento).exists())

        resp = self.client.delete(self._url())

        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT, resp.data)
        self.assertFalse(DocumentoEvento.objects.exists())

    def test_enviar_ya_no_existe(self):
        """Firmar y enviar es una sola acción: `emitir/`."""
        resp = self.client.post(self._url("enviar/"))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_actualizar_estado_ya_no_existe(self):
        """Lo hace `emitir/` con un documento enviado sin veredicto."""
        resp = self.client.post(self._url("actualizar-estado/"))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_consultar_zip_ya_no_existe(self):
        """La entrega al Set la consulta `emitir/`; el documento, `consultar/`.

        Existía por los reenvíos del mismo CUFE, que respondían "procesado
        anteriormente" por la entrega aunque el documento estuviera aceptado.
        `emitir/` ya no reenvía lo que salió hacia la DIAN.
        """
        resp = self.client.get(self._url("consultar-zip/"))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_descargar_xml_tras_emitir(self):
        self._emitir()
        resp = self.client.get(self._url("xml/"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Content-Type"], "application/xml")
        # FileResponse (stream) desde object storage.
        self.assertIn(b"<ds:Signature", b"".join(resp.streaming_content))

    def test_descargar_pdf_tras_emitir(self):
        self._emitir()
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
                    "codigo_producto": "SRV-1",
                    "cantidad": "2", "unidad_medida": c["unidad"].id,
                    "valor_unitario": "1000", "valor_total": "2000.00",
                    "descuento": "0.00",
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

    def _payload_varias_lineas(self, lineas=3, consecutivo=990000140):
        """Líneas en desorden, dos impuestos en la primera y dos responsabilidades.

        Es lo que puede delatar una respuesta armada en memoria que no respete
        el orden que daría la base.
        """
        inc, _ = Tributo.objects.get_or_create(codigo="04", defaults={"nombre": "INC"})
        r_mayor, _ = ResponsabilidadFiscal.objects.get_or_create(
            codigo="R-99-PN", defaults={"nombre": "No aplica"},
        )
        r_menor, _ = ResponsabilidadFiscal.objects.get_or_create(
            codigo="O-13", defaults={"nombre": "Gran contribuyente"},
        )
        payload = self._payload_documento()
        payload["consecutivo"] = consecutivo
        payload["numero"] = f"SETP{consecutivo}"
        payload["adquiriente"]["responsabilidades"] = [r_mayor.id, r_menor.id]
        base = payload["detalles"][0]
        payload["detalles"] = []
        for numero in reversed(range(1, lineas + 1)):
            linea = copy.deepcopy(base)
            linea["numero_linea"] = numero
            linea["codigo_producto"] = f"SRV-{numero}"
            payload["detalles"].append(linea)
        payload["detalles"][0]["impuestos"].append(
            {"tributo": inc.id, "base_gravable": "2000.00", "tarifa": "8.00", "valor": "160.00"}
        )
        return payload

    def test_la_respuesta_de_crear_es_la_misma_que_la_lectura(self):
        """El 201 se arma con lo recién guardado, sin releerlo de la base.

        Si algún día se aparta de lo que devuelve `GET` —un orden, un campo que
        solo rellena la base—, esta prueba lo dice.
        """
        resp = self.client.post(
            "/api/documentos/documento/", self._payload_varias_lineas(), format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

        lectura = self.client.get(f"/api/documentos/documento/{resp.data['id']}/")
        self.assertEqual(
            json.loads(json.dumps(resp.data, cls=JSONEncoder)),
            json.loads(json.dumps(lectura.data, cls=JSONEncoder)),
        )
        self.assertEqual([d["numero_linea"] for d in resp.data["detalles"]], [1, 2, 3])
        self.assertEqual(resp.data["total_impuestos"], "1300.00")

    def test_las_lineas_no_suman_consultas(self):
        """Ni las inserciones, ni la respuesta, ni la validación crecen con las líneas.

        Las inserciones van en bloque, la respuesta se arma sin releer y la
        unidad y el tributo repetidos se buscan una sola vez por petición.
        """
        def consultas(lineas, consecutivo):
            with CaptureQueriesContext(connection) as capturadas:
                resp = self.client.post(
                    "/api/documentos/documento/",
                    self._payload_varias_lineas(lineas, consecutivo), format="json",
                )
            self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
            return len(capturadas)

        # Los `get_or_create` de los catálogos del payload, fuera de la cuenta.
        self._payload_varias_lineas()
        una = consultas(1, 990000150)
        diez = consultas(10, 990000160)
        # Nueve líneas más con la misma unidad y el mismo IVA.
        self.assertEqual(diez, una)

    @override_settings(CATALOGOS_EN_MEMORIA_SEGUNDOS=300)
    def test_con_los_catalogos_en_memoria_crear_no_los_consulta(self):
        """Tras la primera creación del proceso, ningún catálogo va a la base."""
        memoria_de_catalogos.olvidar()
        self.addCleanup(memoria_de_catalogos.olvidar)
        primera = self.client.post(
            "/api/documentos/documento/", self._payload_varias_lineas(), format="json"
        )
        self.assertEqual(primera.status_code, status.HTTP_201_CREATED, primera.data)

        payload = self._payload_varias_lineas(consecutivo=990000170)
        with CaptureQueriesContext(connection) as capturadas:
            segunda = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(segunda.status_code, status.HTTP_201_CREATED, segunda.data)
        # Las que leen de una tabla de catálogo. Un join desde otra tabla —la
        # resolución trae su tipo de factura— no es una validación de id.
        lecturas = [
            q["sql"] for q in capturadas.captured_queries
            if ' FROM "cat_' in q["sql"]
        ]
        self.assertEqual(lecturas, [], "\n\n".join(lecturas))

    def test_un_id_de_catalogo_inexistente_se_reporta_en_cada_linea(self):
        """Recordar las búsquedas no puede tapar el error de otra línea.

        La primera línea deja la unidad buena en la memoria del campo; la
        segunda trae una que no existe y la tercera repite la buena.
        """
        payload = self._payload_varias_lineas()
        payload["detalles"][1]["unidad_medida"] = 999999
        resp = self.client.post("/api/documentos/documento/", payload, format="json")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(set(errores_por_campo(resp)), {"detalles[1].unidad_medida"})
        self.assertEqual(codigos(resp), ["does_not_exist"])

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

    def test_el_descuento_de_la_linea_es_obligatorio(self):
        """Entra en el total de la línea: sin descuento se manda 0."""
        payload = self._payload_documento()
        del payload["detalles"][0]["descuento"]

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(
            errores_por_campo(resp),
            {"detalles[0].descuento": [str(Field.default_error_messages["required"])]},
        )

    def test_el_total_de_la_linea_tiene_que_cuadrar(self):
        """cantidad × valor unitario − descuento. Antes se firmaba descuadrada."""
        payload = self._payload_documento()
        payload["detalles"][0]["valor_total"] = "1999.00"

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(errores_por_campo(resp), {
            "detalles[0].valor_total": [
                mensaje_total_linea_descuadrado(Decimal("2000.00"), Decimal("1999.00"))
            ],
        })

    def test_el_total_de_la_linea_descuenta_el_descuento(self):
        payload = self._payload_documento()
        payload["detalles"][0].update({"descuento": "100.00", "valor_total": "1900.00"})

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["valor_bruto"], "1900.00")

    def test_el_total_de_la_linea_admite_un_centimo_de_redondeo(self):
        payload = self._payload_documento()
        payload["detalles"][0]["valor_total"] = "2000.01"

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_importes_negativos_o_en_cero(self):
        """Mayores que cero: cantidad, precio, total y base. El resto, no negativos."""
        casos = [
            (("detalles", 0, "cantidad"), "0", "detalles[0].cantidad", CODIGO_MAYOR_QUE_CERO),
            (("detalles", 0, "cantidad"), "-1", "detalles[0].cantidad", CODIGO_MAYOR_QUE_CERO),
            (("detalles", 0, "valor_unitario"), "0", "detalles[0].valor_unitario",
             CODIGO_MAYOR_QUE_CERO),
            (("detalles", 0, "valor_total"), "0", "detalles[0].valor_total",
             CODIGO_MAYOR_QUE_CERO),
            (("detalles", 0, "descuento"), "-1", "detalles[0].descuento", "min_value"),
            (("detalles", 0, "impuestos", 0, "base_gravable"), "0",
             "detalles[0].impuestos[0].base_gravable", CODIGO_MAYOR_QUE_CERO),
            (("detalles", 0, "impuestos", 0, "tarifa"), "-1",
             "detalles[0].impuestos[0].tarifa", "min_value"),
            (("detalles", 0, "impuestos", 0, "valor"), "-1",
             "detalles[0].impuestos[0].valor", "min_value"),
            (("total_descuentos",), "-1", "total_descuentos", "min_value"),
            (("total_cargos",), "-1", "total_cargos", "min_value"),
        ]
        for ruta, valor, campo, codigo in casos:
            with self.subTest(campo=campo, valor=valor):
                payload = self._payload_documento()
                destino = payload
                for paso in ruta[:-1]:
                    destino = destino[paso]
                destino[ruta[-1]] = valor

                resp = self.client.post("/api/documentos/documento/", payload, format="json")
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
                self.assertEqual(set(errores_por_campo(resp)), {campo})
                self.assertEqual(codigos(resp), [codigo])

    def test_la_tarifa_y_el_valor_del_impuesto_admiten_cero(self):
        """El IVA exento va al 0 %."""
        payload = self._payload_documento()
        payload["detalles"][0]["impuestos"][0].update({"tarifa": "0.00", "valor": "0.00"})

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_el_subtotal_del_pos_no_puede_ser_negativo(self):
        from apps.documentos.serializers import DocumentoPOSSerializer

        serializer = DocumentoPOSSerializer(data={
            "caja_placa": "CAJA-1", "caja_ubicacion": "Local", "caja_tipo": "POS",
            "cajero": "Ana", "codigo_venta": "V-1", "subtotal": "-1",
        })
        self.assertFalse(serializer.is_valid())
        self.assertEqual(serializer.errors["subtotal"][0].code, "min_value")

    def test_el_codigo_de_producto_es_obligatorio(self):
        """Sin él el XML identificaba el ítem con el número de línea."""
        payload = self._payload_documento()
        del payload["detalles"][0]["codigo_producto"]

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(
            errores_por_campo(resp),
            {"detalles[0].codigo_producto": [str(Field.default_error_messages["required"])]},
        )

        payload["detalles"][0]["codigo_producto"] = ""
        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(codigos(resp), ["blank"])

    def test_un_numero_de_linea_repetido_responde_400_y_no_500(self):
        payload = self._payload_documento()
        segunda = {**payload["detalles"][0], "codigo_producto": "SRV-2"}
        payload["detalles"].append(segunda)

        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(
            errores_por_campo(resp), {"detalles": [mensaje_lineas_repetidas([1])]}
        )

    def test_el_periodo_de_la_linea_tiene_que_ir_hacia_adelante(self):
        """El inicio antes del fin; iguales tampoco pasa (decisión de MarioA)."""
        hoy = timezone.localdate()
        for desde, hasta in ((hoy, hoy - timedelta(days=1)), (hoy, hoy)):
            with self.subTest(desde=desde, hasta=hasta):
                payload = self._payload_documento()
                payload["detalles"][0].update(
                    {"periodo_desde": desde.isoformat(), "periodo_hasta": hasta.isoformat()}
                )
                resp = self.client.post("/api/documentos/documento/", payload, format="json")
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
                self.assertEqual(
                    errores_por_campo(resp),
                    {"detalles[0].periodo_hasta": [MENSAJE_PERIODO_AL_REVES]},
                )

        payload = self._payload_documento()
        payload["detalles"][0].update({
            "periodo_desde": (hoy - timedelta(days=30)).isoformat(),
            "periodo_hasta": hoy.isoformat(),
        })
        resp = self.client.post("/api/documentos/documento/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    # --- Descuentos globales -------------------------------------------------

    def test_los_descuentos_globales_no_superan_el_valor_bruto(self):
        """Antes dejaban el total a pagar en negativo."""
        payload = self._payload_documento()
        payload["total_descuentos"] = "2000.01"

        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(errores_por_campo(resp), {
            "total_descuentos": [
                mensaje_descuentos_mayores_que_el_bruto(Decimal("2000.01"), Decimal("2000.00"))
            ],
        })

    def test_los_descuentos_globales_pueden_igualar_el_valor_bruto(self):
        payload = self._payload_documento()
        payload.update({"total_descuentos": "2000.00", "descuentos_motivo": "Cortesía"})

        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        # Bruto 2000 − descuentos 2000 + IVA 380.
        self.assertEqual(resp.data["total_a_pagar"], "380.00")

    def test_un_descuento_global_menor_se_resta_del_total(self):
        payload = self._payload_documento()
        payload.update({"total_descuentos": "500.00", "descuentos_motivo": "Promoción"})

        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["total_a_pagar"], "1880.00")

    # --- Retenciones: solo en el documento soporte --------------------------

    def _con_retencion(self, payload):
        """Añade una ReteFuente a la primera línea, con la aritmética correcta."""
        retefuente, _ = Tributo.objects.get_or_create(
            codigo="06", defaults={"nombre": "ReteFuente"},
        )
        payload["detalles"][0]["impuestos"].append({
            "tributo": retefuente.id, "base_gravable": "2000.00",
            "tarifa": "2.50", "valor": "50.00",
        })
        return retefuente

    def test_una_factura_con_retencion_no_se_crea(self):
        """Antes salía como un impuesto más y aumentaba el total a pagar."""
        payload = self._payload_documento()
        retefuente = self._con_retencion(payload)
        tipo = DocumentoTipo.objects.get(codigo=DocumentoTipo.Codigo.FACTURA_VENTA)

        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(errores_por_campo(resp), {
            "detalles[0].impuestos[1].tributo": [
                mensaje_retencion_no_admitida(tipo, retefuente)
            ],
        })
        self.assertFalse(Documento.objects.filter(consecutivo=990000130).exists())

    def test_una_nota_con_retencion_no_se_crea(self):
        tipo = DocumentoTipo.objects.get(codigo=DocumentoTipo.Codigo.NOTA_CREDITO)
        payload = self._payload_documento()
        payload.update({
            "documento_tipo": tipo.id,
            "numero_resolucion": "",
            "documento_referencia": str(self.documento.id),
            "concepto_correccion": Documento.ConceptoNotaCredito.ANULACION,
            "prefijo": "NC", "consecutivo": 1, "numero": "NC1",
        })
        retefuente = self._con_retencion(payload)

        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(errores_por_campo(resp), {
            "detalles[0].impuestos[1].tributo": [
                mensaje_retencion_no_admitida(tipo, retefuente)
            ],
        })

    def test_el_documento_soporte_si_admite_retenciones(self):
        """Allí van en WithholdingTaxTotal y no suman al total a pagar."""
        retefuente, _ = Tributo.objects.get_or_create(
            codigo="06", defaults={"nombre": "ReteFuente"},
        )
        iva = self.cat["iva"]
        attrs = {"detalles": [{"impuestos": [{"tributo": iva}, {"tributo": retefuente}]}]}
        for codigo in DocumentoTipo.CODIGOS_CON_RETENCIONES:
            with self.subTest(tipo=codigo):
                tipo = DocumentoTipo.objects.get(codigo=codigo)
                # No lanza: es la única comprobación que hace falta aquí.
                DocumentoCrearSerializer()._validar_retenciones(attrs, tipo)

    def test_las_retenciones_del_documento_soporte_no_suman_al_total(self):
        """Los totales se calculan antes del INSERT y tienen que respetarlo.

        Se llama a `create` con los datos ya validados de la factura del
        payload, cambiando el tipo y añadiendo la retención: la validación de
        tipos y retenciones tiene sus propias pruebas, y lo que se mira aquí es
        solo la suma.
        """
        retefuente, _ = Tributo.objects.get_or_create(
            codigo="06", defaults={"nombre": "ReteFuente"},
        )
        soporte = DocumentoTipo.objects.get(codigo=DocumentoTipo.Codigo.DOCUMENTO_SOPORTE)
        serializer = DocumentoCrearSerializer(data=self._payload_documento())
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.validated_data["detalles"][0]["impuestos"].append({
            "tributo": retefuente, "base_gravable": Decimal("2000.00"),
            "tarifa": Decimal("2.50"), "valor": Decimal("50.00"),
        })

        documento = serializer.save(documento_tipo=soporte)

        documento.refresh_from_db()
        self.assertEqual(documento.valor_bruto, Decimal("2000.00"))
        # Solo el IVA: la retención la practica quien paga.
        self.assertEqual(documento.total_impuestos, Decimal("380.00"))
        self.assertEqual(documento.total_a_pagar, Decimal("2380.00"))
        self.assertEqual(documento.detalles.get().impuestos.count(), 2)

    # --- Duplicados: 409 con la ruta del que ya existe ---------------------

    def test_crear_dos_veces_responde_409_con_la_ruta_del_existente(self):
        """El reintento del ERP recupera lo que ya se creó, sin buscarlo."""
        primero = self._crear()
        self.assertEqual(primero.status_code, status.HTTP_201_CREATED, primero.data)

        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT, resp.data)
        self.assertEqual(codigos(resp), [CODIGO_DOCUMENTO_DUPLICADO])
        self.assertEqual(
            resp["Location"], f"/api/documentos/documento/{primero.data['id']}/"
        )
        self.assertIn(primero.data["numero"], resp.data["detail"])
        self.assertEqual(Documento.objects.filter(consecutivo=990000130).count(), 1)

    def test_el_duplicado_se_detecta_antes_que_los_datos(self):
        """Un reintento al día siguiente ya no pasaría la fecha: igual es 409."""
        self.assertEqual(self._crear().status_code, status.HTTP_201_CREATED)
        payload = self._payload_documento()
        payload["fecha_emision"] = "2020-01-01"

        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT, resp.data)

    def test_la_estructura_va_antes_que_el_duplicado(self):
        self.assertEqual(self._crear().status_code, status.HTTP_201_CREATED)
        payload = self._payload_documento()
        payload["prueba"] = "x"

        resp = self._crear_con(payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(codigos(resp), [CODIGO_CAMPO_DESCONOCIDO])

    def test_el_duplicado_de_un_emisor_ajeno_no_se_revela(self):
        """Sin alcance sobre el emisor no hay 409: responde como un emisor ajeno."""
        self.assertEqual(self._crear().status_code, status.HTTP_201_CREATED)
        otro = get_user_model().objects.create_user(email="otro@example.com", password="x")
        self.client.force_authenticate(otro)

        resp = self._crear()
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn("emisor", errores_por_campo(resp))

    def test_dos_creaciones_simultaneas_dan_409_y_no_500(self):
        """La carrera: las dos pasan la búsqueda previa y chocan en la base.

        Se simula haciendo que la primera búsqueda no vea el documento, como le
        pasaría a la petición que llega mientras la otra aún no ha insertado.
        """
        primero = self._crear()
        self.assertEqual(primero.status_code, status.HTTP_201_CREATED)
        original = DocumentoViewSet._existente
        llamadas = []

        def ciega_la_primera_vez(vista, datos):
            llamadas.append(datos)
            return None if len(llamadas) == 1 else original(vista, datos)

        with mock.patch.object(DocumentoViewSet, "_existente", ciega_la_primera_vez):
            resp = self._crear()

        self.assertEqual(len(llamadas), 2)
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT, resp.data)
        self.assertEqual(
            resp["Location"], f"/api/documentos/documento/{primero.data['id']}/"
        )

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

    def test_catalogo_sin_credencial_responde_401(self):
        resp = self.client.get("/api/catalogos/tributo/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_catalogo_con_llave_invalida_responde_401(self):
        resp = self.client.get(
            "/api/catalogos/municipio/",
            HTTP_AUTHORIZATION="Api-Key noexiste.secreto",
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_catalogo_con_llave_busca(self):
        # Cualquier llave válida basta: el catálogo no depende de los emisores
        # de la persona, así que ni siquiera hace falta que tenga uno.
        from apps.seguridad.models import LlaveApi

        usuario = get_user_model().objects.create_user(
            email="catalogos@test.co", password="x"
        )
        _, clave = LlaveApi.generar(usuario=usuario, nombre="ERP")
        resp = self.client.get(
            "/api/catalogos/tributo/?search=IVA",
            HTTP_AUTHORIZATION=f"Api-Key {clave}",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        codigos = [r["codigo"] for r in resp.data["results"]]
        self.assertIn("01", codigos)
        self.assertNotIn("04", codigos)
