"""Eventos RADIAN del adquiriente sobre las facturas recibidas (R4)."""
import base64
from datetime import date
from unittest import mock

import requests
from django.conf import settings
from django.test import TestCase
from lxml import etree
from rest_framework.test import APITestCase

from apps.catalogos.calendario import sumar_dias_habiles
from apps.catalogos.carga import cargar
from apps.catalogos.models import ConceptoReclamo, EventoRadian, Festivo, TipoIdentificacion
from apps.dian import firma, soap
from apps.dian.tests_firma import _generar_certificado
from apps.documentos.models import DocumentoTipo
from apps.documentos.tests_utils import crear_catalogos_minimos, crear_usuario
from apps.emisores.models import Emisor, SoftwareDian
from apps.recepcion import adjuntos, eventos, tareas, verificacion
from apps.recepcion.models import (
    Correo, Documento, EstadoEvento, EstadoRadian, EstadoVerificacion, Evento,
)
from apps.recepcion.tests_utils import crear_documento_recibido

ESQUEMA = etree.XMLSchema(etree.parse(
    str(settings.DIAN_XSD_DIR / "maindoc" / "UBL-ApplicationResponse-2.1.xsd")
))
NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}
URL_DOCUMENTO = "/api/recepcion/documento/"
URL_EVENTO = "/api/recepcion/evento/"


def respuesta_dian(valido=True, errores=()):
    """Lo que devuelve ``SendEventUpdateStatus``, con el ApplicationResponse."""
    xml_crudo = ""
    if valido:
        b64 = base64.b64encode(b"<ApplicationResponse>DIAN</ApplicationResponse>").decode()
        xml_crudo = f"<r><XmlBase64Bytes>{b64}</XmlBase64Bytes></r>"
    return soap.RespuestaDian(
        es_valido=valido, codigo_estado="00" if valido else "99",
        descripcion_estado="Procesado Correctamente." if valido else "Validación contiene errores.",
        errores=list(errores), xml_crudo=xml_crudo,
    )


class ClienteFalso:
    """Un ``ClienteDian`` que responde ``respuesta`` (o lanza ``error``)."""

    def __init__(self, respuesta=None, error=None):
        self.respuesta = respuesta or respuesta_dian()
        self.error = error
        self.enviados = []

    def enviar_evento(self, xml_firmado, nombre_archivo):
        self.enviados.append((xml_firmado, nombre_archivo))
        if self.error:
            raise self.error
        return self.respuesta

    def consultar_estado(self, track_id):
        return soap.RespuestaDian(es_valido=True, codigo_estado="00")


class BaseEventos:
    """Un emisor con certificado, software y persona que recibe, y una factura
    recibida que la DIAN tiene como válida."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.llave, cls.cert = _generar_certificado()

    @classmethod
    def setUpTestData(cls):
        cargar([EventoRadian, ConceptoReclamo, Festivo])

    def setUp(self):
        c = self.cat = crear_catalogos_minimos()
        self.cedula = TipoIdentificacion.objects.create(id=13, codigo="13", nombre="Cédula")
        self.usuario = crear_usuario(nombre="Dueño")
        self.emisor = Emisor.objects.create(
            usuario=self.usuario, razon_social="Empresa", tipo_identificacion=c["nit"],
            numero_identificacion="901192048", tipo_organizacion=c["juridica"],
            pais=c["colombia"], departamento=c["antioquia"], municipio=c["medellin"],
            direccion="Calle 1 # 2-3",
            recibe_tipo_identificacion=self.cedula, recibe_numero_identificacion="1020304050",
            recibe_nombres="Ana", recibe_apellidos="Pérez", recibe_cargo="Contadora",
            acuse_automatico=True,
        )
        SoftwareDian.objects.create(
            emisor=self.emisor, tipo=SoftwareDian.Tipo.FACTURACION,
            identificador="fa326ca7-c1f8-40d3-a6fc-24d7c1040607", pin="20191",
        )
        self.correo = Correo.objects.create(
            alias="901192048", emisor=self.emisor, sha256="a" * 64,
            raw_key="k.eml", envelope_to="901192048@recepcion.rededoc.co",
        )
        self.factura = self.documento("1")
        self.encolados = []
        for objetivo, nombre, valor in (
            (eventos, "motivo_no_puede_emitir", mock.Mock(return_value=None)),
            (eventos, "encolar", lambda tarea, *args: self.encolados.append(args)),
        ):
            parche = mock.patch.object(objetivo, nombre, valor)
            parche.start()
            self.addCleanup(parche.stop)

    def documento(self, cufe, *, tipo="factura_venta", verificado=True):
        documento = crear_documento_recibido(self.correo, self.emisor, cufe, tipo=tipo)
        if verificado:
            documento.verificacion_estado = EstadoVerificacion.VALIDO
            documento.save()
        return Documento.objects.select_related("emisor", "documento_tipo").get(pk=documento.pk)

    def firmador(self):
        return firma.FirmadorXAdES(
            self.llave, self.cert, policy_id=settings.DIAN_POLICY_ID, policy_hash="dGVzdA==",
        )

    def registrado(self, codigo, documento=None, fecha=None):
        """Un evento ya registrado en RADIAN, sin pasar por el envío."""
        evento = eventos.solicitar(
            documento or self.factura, codigo,
            concepto_reclamo=ConceptoReclamo.objects.get(codigo="02") if codigo == "031" else None,
        )
        evento.estado = EstadoEvento.REGISTRADO
        evento.fecha = fecha or date(2026, 10, 9)
        evento.save()
        return evento


class SolicitarTests(BaseEventos, TestCase):
    def test_el_acuse_sale_con_la_persona_del_emisor(self):
        evento = eventos.solicitar(self.factura, "030")

        self.assertEqual(evento.estado, EstadoEvento.PENDIENTE)
        self.assertEqual(evento.numero, "ACR1")
        self.assertEqual(evento.persona_tipo_identificacion, self.cedula)
        self.assertEqual(evento.persona_nombres, "Ana")
        self.assertEqual(evento.persona_cargo, "Contadora")
        self.assertEqual(self.encolados, [(str(evento.pk),)])

    def test_la_persona_de_la_peticion_reemplaza_la_del_emisor(self):
        persona = {
            "tipo_identificacion": self.cedula, "numero_identificacion": "99",
            "nombres": "Luis", "apellidos": "Gómez", "cargo": "", "area": "Bodega",
        }
        evento = eventos.solicitar(self.factura, "030", persona=persona)
        self.assertEqual(evento.persona_nombres, "Luis")
        self.assertEqual(evento.persona_area, "Bodega")

    def test_sin_persona_no_hay_acuse(self):
        Emisor.objects.filter(pk=self.emisor.pk).update(recibe_nombres="")
        factura = self.documento("2")
        with self.assertRaisesMessage(eventos.EventoInvalido, "lleva la persona que recibe"):
            eventos.solicitar(factura, "030")

    def test_solo_sobre_facturas_de_venta(self):
        nota = self.documento("2", tipo="nota_credito")
        with self.assertRaisesMessage(eventos.EventoInvalido, "solo sobre facturas de venta"):
            eventos.solicitar(nota, "030")

    def test_solo_sobre_facturas_verificadas(self):
        pendiente = self.documento("2", verificado=False)
        with self.assertRaisesMessage(eventos.EventoInvalido, "verificada la factura como válida"):
            eventos.solicitar(pendiente, "030")

    def test_un_evento_no_se_repite_salvo_que_lo_hayan_rechazado(self):
        primero = eventos.solicitar(self.factura, "030")
        with self.assertRaisesMessage(eventos.EventoInvalido, "ya tiene el acuse de recibo"):
            eventos.solicitar(self.factura, "030")

        Evento.objects.filter(pk=primero.pk).update(estado=EstadoEvento.RECHAZADO)
        segundo = eventos.solicitar(self.factura, "030")
        self.assertEqual(segundo.numero, "ACR2")

    def test_el_recibo_va_despues_del_acuse_registrado(self):
        eventos.solicitar(self.factura, "030")
        with self.assertRaisesMessage(eventos.EventoInvalido, "después de el acuse"):
            eventos.solicitar(self.factura, "032")

        Evento.objects.update(estado=EstadoEvento.REGISTRADO)
        recibo = eventos.solicitar(self.factura, "032")
        # Cada tipo de evento lleva su propio consecutivo.
        self.assertEqual(recibo.numero, "RBS1")

    def test_la_aceptacion_va_despues_del_recibo(self):
        self.registrado("030")
        with self.assertRaisesMessage(eventos.EventoInvalido, "después de el recibo"):
            eventos.solicitar(self.factura, "033")

    def test_aceptacion_y_reclamo_se_excluyen(self):
        self.registrado("030")
        self.registrado("032", fecha=date(2026, 10, 9))
        with mock.patch.object(eventos, "_hoy", return_value=date(2026, 10, 9)):
            eventos.solicitar(self.factura, "033")
            with self.assertRaisesMessage(eventos.EventoInvalido, "excluye el reclamo"):
                eventos.solicitar(
                    self.factura, "031",
                    concepto_reclamo=ConceptoReclamo.objects.get(codigo="02"),
                )

    def test_el_plazo_son_3_dias_habiles_desde_el_recibo(self):
        self.registrado("030")
        self.registrado("032", fecha=date(2026, 10, 9))
        limite = sumar_dias_habiles(date(2026, 10, 9), 3)
        with mock.patch.object(eventos, "_hoy", return_value=date.fromordinal(limite.toordinal() + 1)):
            with self.assertRaisesMessage(eventos.EventoInvalido, "Venció el plazo"):
                eventos.solicitar(self.factura, "033")
        with mock.patch.object(eventos, "_hoy", return_value=limite):
            self.assertEqual(eventos.solicitar(self.factura, "033").numero, "ACE1")

    def test_el_reclamo_lleva_concepto(self):
        self.registrado("030")
        self.registrado("032", fecha=date(2026, 10, 9))
        with mock.patch.object(eventos, "_hoy", return_value=date(2026, 10, 9)):
            with self.assertRaisesMessage(eventos.EventoInvalido, "lleva su concepto"):
                eventos.solicitar(self.factura, "031")
            reclamo = eventos.solicitar(
                self.factura, "031", concepto_reclamo=ConceptoReclamo.objects.get(codigo="02"),
            )
        self.assertEqual(reclamo.numero, "REC1")
        # El 031 no lleva persona aunque el emisor tenga una configurada.
        self.assertEqual(reclamo.persona_numero_identificacion, "")


class EnviarTests(BaseEventos, TestCase):
    def enviar(self, evento, cliente):
        evento = Evento.objects.select_related("documento", "emisor", "evento_radian").get(pk=evento.pk)
        return eventos.enviar(evento, cliente=cliente, firmador=self.firmador())

    def test_registrado_guarda_el_xml_la_respuesta_y_el_resumen(self):
        cliente = ClienteFalso()
        evento = self.enviar(eventos.solicitar(self.factura, "030"), cliente)

        self.assertEqual(evento.estado, EstadoEvento.REGISTRADO)
        self.assertEqual(evento.respuesta_codigo, "00")
        self.assertEqual(len(evento.cude), 96)
        self.assertIsNotNone(evento.enviado_en)
        firmado, nombre = cliente.enviados[0]
        self.assertEqual(nombre, "ar090119204803000000001.xml")
        arbol = etree.fromstring(firmado)
        if not ESQUEMA.validate(arbol):
            self.fail(str(ESQUEMA.error_log))
        self.assertEqual(arbol.findtext("cbc:ID", namespaces=NS), "ACR1")
        self.assertEqual(arbol.findtext("cbc:UUID", namespaces=NS), evento.cude)
        self.assertEqual(
            arbol.findtext(".//cac:IssuerParty/cac:Person/cbc:FirstName", namespaces=NS), "Ana",
        )
        with evento.respuesta_archivo.open("rb") as fh:
            self.assertEqual(fh.read(), b"<ApplicationResponse>DIAN</ApplicationResponse>")
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.radian_estado, EstadoRadian.ACUSE)

    def test_al_proveedor_persona_natural_lo_identifica_como_vino(self):
        Documento.objects.filter(pk=self.factura.pk).update(
            proveedor_tipo_identificacion="13", proveedor_tipo_organizacion="2",
        )
        cliente = ClienteFalso()
        self.enviar(eventos.solicitar(self.factura, "030"), cliente)

        arbol = etree.fromstring(cliente.enviados[0][0])
        nit = arbol.find("cac:ReceiverParty/cac:PartyTaxScheme/cbc:CompanyID", NS)
        self.assertEqual(nit.get("schemeName"), "13")
        self.assertIsNone(nit.get("schemeID"))

    def test_rechazado_no_toca_el_resumen(self):
        cliente = ClienteFalso(respuesta_dian(False, ["Regla: AAH09, Rechazo: evento repetido"]))
        evento = self.enviar(eventos.solicitar(self.factura, "030"), cliente)

        self.assertEqual(evento.estado, EstadoEvento.RECHAZADO)
        self.assertIn("evento repetido", evento.respuesta_descripcion)
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.radian_estado, EstadoRadian.SIN_EVENTOS)

    def test_sin_respuesta_queda_en_error_y_el_reenvio_manda_el_mismo_xml(self):
        evento = eventos.solicitar(self.factura, "030")
        caido = ClienteFalso(error=requests.ConnectionError("sin red"))
        with mock.patch.object(eventos, "construir_cliente_emisor", return_value=caido), \
                mock.patch.object(eventos, "construir_firmador_emisor", return_value=self.firmador()):
            tareas.enviar_evento(str(evento.pk))
        evento.refresh_from_db()
        self.assertEqual(evento.estado, EstadoEvento.ERROR)
        self.assertIn("sin red", evento.respuesta_descripcion)
        cude = evento.cude

        cliente = ClienteFalso()
        evento = self.enviar(evento, cliente)
        self.assertEqual(evento.estado, EstadoEvento.REGISTRADO)
        self.assertEqual(evento.cude, cude)
        self.assertEqual(evento.intentos, 2)
        self.assertEqual(cliente.enviados[0][0], caido.enviados[0][0])

    def test_lo_que_ya_termino_no_se_reenvia(self):
        evento = self.registrado("030")
        cliente = ClienteFalso()
        self.enviar(evento, cliente)
        self.assertEqual(cliente.enviados, [])


class AcuseAutomaticoTests(BaseEventos, TestCase):
    def test_apagado_por_defecto(self):
        self.assertFalse(Emisor._meta.get_field("acuse_automatico").default)

    def verificar(self, documento):
        documento.verificacion_estado = EstadoVerificacion.PENDIENTE
        documento.save()
        return verificacion.verificar(documento, cliente=ClienteFalso())

    def test_la_factura_valida_saca_su_acuse(self):
        self.verificar(self.factura)
        evento = Evento.objects.get()
        self.assertEqual(evento.evento_radian.codigo, "030")
        self.assertIsNone(evento.usuario)

    def test_apagado_no_sale(self):
        Emisor.objects.filter(pk=self.emisor.pk).update(acuse_automatico=False)
        self.verificar(self.documento("2"))
        self.assertFalse(Evento.objects.exists())

    def test_sin_persona_no_sale_ni_falla(self):
        Emisor.objects.filter(pk=self.emisor.pk).update(recibe_numero_identificacion="")
        documento = self.verificar(self.documento("2"))
        self.assertEqual(documento.verificacion_estado, EstadoVerificacion.VALIDO)
        self.assertFalse(Evento.objects.exists())

    def test_no_se_repite_al_verificar_otra_vez(self):
        self.verificar(self.factura)
        self.verificar(Documento.objects.get(pk=self.factura.pk))
        self.assertEqual(Evento.objects.count(), 1)


class EventoApiTests(BaseEventos, APITestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.usuario)

    def test_pedir_un_evento(self):
        resp = self.client.post(
            f"{URL_DOCUMENTO}{self.factura.pk}/evento/", {"codigo": "030"}, format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["codigo"], "030")
        self.assertEqual(resp.data["numero"], "ACR1")
        self.assertEqual(resp.data["estado"], "pendiente")
        self.assertEqual(Evento.objects.get().usuario, self.usuario)

    def test_pedir_con_persona(self):
        resp = self.client.post(f"{URL_DOCUMENTO}{self.factura.pk}/evento/", {
            "codigo": "030",
            "persona": {
                "tipo_identificacion": 13, "numero_identificacion": "77",
                "nombres": "Luis", "apellidos": "Gómez",
            },
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["persona_nombres"], "Luis")

    def test_un_evento_que_no_cabe_es_400(self):
        resp = self.client.post(
            f"{URL_DOCUMENTO}{self.factura.pk}/evento/", {"codigo": "032"}, format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("después de el acuse", resp.data["detail"])

    def test_el_documento_de_otro_emisor_no_existe(self):
        self.client.force_authenticate(crear_usuario(nombre="Otro", email="otro@x.co"))
        resp = self.client.post(
            f"{URL_DOCUMENTO}{self.factura.pk}/evento/", {"codigo": "030"}, format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_listar_y_filtrar(self):
        evento = self.registrado("030")
        otra = self.documento("2")
        eventos.solicitar(otra, "030")

        resp = self.client.get(URL_EVENTO, {"documento": str(self.factura.pk)})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([e["id"] for e in resp.data["results"]], [str(evento.pk)])

        resp = self.client.get(URL_DOCUMENTO, {"radian_estado": "sin_eventos"})
        self.assertEqual({d["id"] for d in resp.data["results"]}, {str(self.factura.pk), str(otra.pk)})

    def test_solo_se_reenvian_los_de_error(self):
        evento = self.registrado("030")
        resp = self.client.post(f"{URL_EVENTO}{evento.pk}/enviar/")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Solo se reenvían los eventos en error", resp.data["detail"])

    def test_reenviar_uno_en_error(self):
        evento = eventos.solicitar(self.factura, "030")
        Evento.objects.filter(pk=evento.pk).update(estado=EstadoEvento.ERROR)
        with mock.patch.object(eventos, "construir_cliente_emisor", return_value=ClienteFalso()), \
                mock.patch.object(eventos, "construir_firmador_emisor", return_value=self.firmador()):
            resp = self.client.post(f"{URL_EVENTO}{evento.pk}/enviar/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["estado"], "registrado")
        self.assertTrue(resp.data["tiene_xml"])

        resp = self.client.get(f"{URL_EVENTO}{evento.pk}/respuesta/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), b"<ApplicationResponse>DIAN</ApplicationResponse>")


class EliminarCorreoTests(BaseEventos, TestCase):
    def test_el_borrado_forzado_se_lleva_los_eventos(self):
        self.registrado("030")
        Correo.objects.filter(pk=self.correo.pk).update(raw_key="")
        documentos, _ = adjuntos.eliminar_correo(Correo.objects.get(pk=self.correo.pk))
        self.assertEqual(documentos, 1)
        self.assertFalse(Evento.objects.exists())


class ConsultarEventosComandoTests(BaseEventos, TestCase):
    def test_consulta_por_el_cufe_y_guarda_la_respuesta(self):
        import io
        import shutil
        import tempfile

        from django.core.management import call_command

        from apps.recepcion.management.commands import consultar_eventos

        salida = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, salida)
        cliente = mock.Mock()
        cliente.consultar_eventos.return_value = soap.RespuestaEventos(
            respuesta=soap.RespuestaDian(es_valido=True, codigo_estado="00", xml_crudo="<r/>"),
            eventos=[soap.EventoRegistrado("030", "Acuse de recibo")],
        )
        texto = io.StringIO()
        with mock.patch.object(consultar_eventos, "construir_cliente_emisor", return_value=cliente):
            call_command(
                "consultar_eventos", "--id", str(self.factura.pk), "--salida", salida,
                stdout=texto,
            )
        cliente.consultar_eventos.assert_called_once_with(self.factura.cufe_cude)
        self.assertIn("evento 030: Acuse de recibo", texto.getvalue())
        with open(f"{salida}/GetStatusEvent-{self.factura.numero}-respuesta.xml") as fh:
            self.assertEqual(fh.read(), "<r/>")
