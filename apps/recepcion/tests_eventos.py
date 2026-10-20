"""Eventos RADIAN del adquiriente sobre las facturas recibidas (R4)."""
import base64
from datetime import date
from unittest import mock

import requests
from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from lxml import etree
from rest_framework.test import APITestCase

from apps.catalogos.calendario import sumar_dias_habiles
from apps.catalogos.carga import cargar
from apps.catalogos.models import (
    ConceptoReclamo, EventoRadian, Festivo, FormaPago, TipoIdentificacion,
)
from apps.dian import firma, soap
from apps.dian.tests_firma import _generar_certificado
from apps.documentos.models import DocumentoTipo
from apps.documentos.tests_utils import crear_catalogos_minimos, crear_usuario
from apps.emisores.models import Emisor, SoftwareDian
from apps.nucleo.models import Ambiente
from apps.recepcion import adjuntos, eventos, tareas, verificacion
from apps.recepcion.models import (
    Correo, Documento, EstadoEvento, EstadoRadian, EstadoVerificacion, Evento,
)
from apps.recepcion.tests_utils import crear_documento_recibido, xml_documento

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
        # De habilitación: fuera de ENV=prod los eventos sobre facturas de
        # producción se bloquean (`BloqueoProduccionTests`).
        documento.ambiente = Ambiente.PRUEBAS
        if verificado:
            documento.verificacion_estado = EstadoVerificacion.VALIDO
        documento.save()
        return Documento.objects.select_related("emisor", "documento_tipo").get(pk=documento.pk)

    def firmador(self):
        return firma.FirmadorXAdES(
            self.llave, self.cert, policy_id=settings.DIAN_POLICY_ID, policy_hash="dGVzdA==",
        )

    def persona(self, **cambios):
        """La persona que recibe, como la manda la petición del 030 y el 032."""
        return {
            "tipo_identificacion": self.cedula, "numero_identificacion": "1020304050",
            "nombres": "Ana", "apellidos": "Pérez", "cargo": "Contadora", "area": "",
            **cambios,
        }

    def solicitar(self, documento, codigo, **kwargs):
        """``eventos.solicitar`` con la persona que exigen el 030 y el 032."""
        if codigo in ("030", "032"):
            kwargs.setdefault("persona", self.persona())
        return eventos.solicitar(documento, codigo, **kwargs)

    def registrado(self, codigo, documento=None, fecha=None):
        """Un evento ya registrado en RADIAN, sin pasar por el envío."""
        evento = self.solicitar(
            documento or self.factura, codigo,
            concepto_reclamo=ConceptoReclamo.objects.get(codigo="02") if codigo == "031" else None,
        )
        evento.estado = EstadoEvento.REGISTRADO
        evento.fecha = fecha or date(2026, 10, 9)
        evento.save()
        return evento


class SolicitarTests(BaseEventos, TestCase):
    def test_el_acuse_sale_con_la_persona_de_la_peticion(self):
        evento = self.solicitar(self.factura, "030")

        self.assertEqual(evento.estado, EstadoEvento.PENDIENTE)
        self.assertEqual(evento.numero, "ACR1")
        self.assertEqual(evento.persona_tipo_identificacion, self.cedula)
        self.assertEqual(evento.persona_nombres, "Ana")
        self.assertEqual(evento.persona_cargo, "Contadora")
        self.assertEqual(self.encolados, [(str(evento.pk),)])

    def test_no_toma_la_persona_del_emisor(self):
        persona = self.persona(
            numero_identificacion="99", nombres="Luis", apellidos="Gómez", cargo="",
            area="Bodega",
        )
        evento = self.solicitar(self.factura, "030", persona=persona)
        self.assertEqual(evento.persona_nombres, "Luis")
        self.assertEqual(evento.persona_area, "Bodega")

    def test_sin_persona_en_la_peticion_no_hay_acuse_ni_recibo(self):
        # El emisor tiene una configurada, y aun así no se usa: esa es solo
        # para el acuse automático.
        for codigo in ("030", "032"):
            with self.subTest(codigo=codigo), \
                    self.assertRaisesMessage(eventos.EventoInvalido, "mándala en la petición"):
                eventos.solicitar(self.factura, codigo)
        self.assertFalse(Evento.objects.exists())

    def test_solo_sobre_facturas_de_venta(self):
        nota = self.documento("2", tipo="nota_credito")
        with self.assertRaisesMessage(eventos.EventoInvalido, "solo sobre facturas de venta"):
            self.solicitar(nota, "030")

    def test_solo_sobre_facturas_verificadas(self):
        pendiente = self.documento("2", verificado=False)
        with self.assertRaisesMessage(eventos.EventoInvalido, "verificada la factura como válida"):
            self.solicitar(pendiente, "030")

    def test_solo_sobre_facturas_a_credito(self):
        contado = FormaPago.objects.create(id=1, codigo="1", nombre="Contado")
        Documento.objects.filter(pk=self.factura.pk).update(forma_pago=contado)
        factura = Documento.objects.select_related("emisor", "documento_tipo").get(pk=self.factura.pk)
        with self.assertRaisesMessage(eventos.EventoInvalido, "La factura es de contado"):
            self.solicitar(factura, "030")

    def test_la_forma_de_pago_de_un_documento_viejo_se_lee_del_xml(self):
        FormaPago.objects.create(id=1, codigo="1", nombre="Contado")
        documento = crear_documento_recibido(
            self.correo, self.emisor, "3",
            xml_documento=xml_documento(cufe="3" * 96, forma_pago="1"),
        )
        Documento.objects.filter(pk=documento.pk).update(
            verificacion_estado=EstadoVerificacion.VALIDO, ambiente=Ambiente.PRUEBAS,
        )
        documento = Documento.objects.select_related("emisor", "documento_tipo").get(pk=documento.pk)
        self.assertIsNone(documento.forma_pago)
        with self.assertRaisesMessage(eventos.EventoInvalido, "La factura es de contado"):
            self.solicitar(documento, "030")
        documento.refresh_from_db()
        self.assertEqual(documento.forma_pago.codigo, "1")

    def test_sin_forma_de_pago_conocida_decide_la_dian(self):
        # El XML del documento de prueba no la trae: no se bloquea.
        self.assertEqual(self.solicitar(self.factura, "030").numero, "ACR1")

    def test_un_evento_no_se_repite_salvo_que_lo_hayan_rechazado(self):
        primero = self.solicitar(self.factura, "030")
        with self.assertRaisesMessage(eventos.EventoInvalido, "ya tiene el acuse de recibo"):
            self.solicitar(self.factura, "030")

        Evento.objects.filter(pk=primero.pk).update(estado=EstadoEvento.RECHAZADO)
        segundo = self.solicitar(self.factura, "030")
        self.assertEqual(segundo.numero, "ACR2")

    def test_el_recibo_va_despues_del_acuse_registrado(self):
        self.solicitar(self.factura, "030")
        with self.assertRaisesMessage(eventos.EventoInvalido, "después del acuse"):
            self.solicitar(self.factura, "032")

        Evento.objects.update(estado=EstadoEvento.REGISTRADO)
        recibo = self.solicitar(self.factura, "032")
        # Cada tipo de evento lleva su propio consecutivo.
        self.assertEqual(recibo.numero, "RBS1")

    def test_la_aceptacion_va_despues_del_recibo(self):
        self.registrado("030")
        with self.assertRaisesMessage(eventos.EventoInvalido, "después del recibo"):
            self.solicitar(self.factura, "033")

    def test_aceptacion_y_reclamo_se_excluyen(self):
        self.registrado("030")
        self.registrado("032", fecha=date(2026, 10, 9))
        with mock.patch.object(eventos, "_hoy", return_value=date(2026, 10, 9)):
            self.solicitar(self.factura, "033")
            with self.assertRaisesMessage(eventos.EventoInvalido, "excluye el reclamo"):
                self.solicitar(
                    self.factura, "031",
                    concepto_reclamo=ConceptoReclamo.objects.get(codigo="02"),
                )

    def test_el_plazo_son_3_dias_habiles_desde_el_recibo(self):
        self.registrado("030")
        self.registrado("032", fecha=date(2026, 10, 9))
        limite = sumar_dias_habiles(date(2026, 10, 9), 3)
        with mock.patch.object(eventos, "_hoy", return_value=date.fromordinal(limite.toordinal() + 1)):
            with self.assertRaisesMessage(eventos.EventoInvalido, "Venció el plazo"):
                self.solicitar(self.factura, "033")
        with mock.patch.object(eventos, "_hoy", return_value=limite):
            self.assertEqual(self.solicitar(self.factura, "033").numero, "ACE1")

    def test_el_reclamo_lleva_concepto(self):
        self.registrado("030")
        self.registrado("032", fecha=date(2026, 10, 9))
        with mock.patch.object(eventos, "_hoy", return_value=date(2026, 10, 9)):
            with self.assertRaisesMessage(eventos.EventoInvalido, "lleva su concepto"):
                self.solicitar(self.factura, "031")
            reclamo = self.solicitar(
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
        evento = self.enviar(self.solicitar(self.factura, "030"), cliente)

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
        self.enviar(self.solicitar(self.factura, "030"), cliente)

        arbol = etree.fromstring(cliente.enviados[0][0])
        nit = arbol.find("cac:ReceiverParty/cac:PartyTaxScheme/cbc:CompanyID", NS)
        self.assertEqual(nit.get("schemeName"), "13")
        self.assertIsNone(nit.get("schemeID"))

    def test_rechazado_no_toca_el_resumen(self):
        cliente = ClienteFalso(respuesta_dian(False, ["Regla: AAH09, Rechazo: evento repetido"]))
        evento = self.enviar(self.solicitar(self.factura, "030"), cliente)

        self.assertEqual(evento.estado, EstadoEvento.RECHAZADO)
        self.assertIn("evento repetido", evento.respuesta_descripcion)
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.radian_estado, EstadoRadian.SIN_EVENTOS)

    def test_sin_respuesta_queda_en_error_y_el_reenvio_manda_el_mismo_xml(self):
        evento = self.solicitar(self.factura, "030")
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
        # El automático no tiene petición: es el único que usa la del emisor.
        self.assertEqual(evento.persona_nombres, "Ana")
        self.assertEqual(evento.persona_cargo, "Contadora")

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

    PERSONA = {
        "tipo_identificacion": 13, "numero_identificacion": "77",
        "nombres": "Luis", "apellidos": "Gómez",
    }

    def test_pedir_un_evento(self):
        resp = self.client.post(
            f"{URL_DOCUMENTO}{self.factura.pk}/evento/",
            {"codigo": "030", "persona": self.PERSONA}, format="json",
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

    def test_sin_persona_es_400(self):
        for codigo in ("030", "032"):
            with self.subTest(codigo=codigo):
                resp = self.client.post(
                    f"{URL_DOCUMENTO}{self.factura.pk}/evento/", {"codigo": codigo},
                    format="json",
                )
                self.assertEqual(resp.status_code, 400)
                self.assertIn(
                    f"persona: Es obligatoria en el evento {codigo}.",
                    [e["mensaje"] for e in resp.data["errores"]],
                )
        self.assertFalse(Evento.objects.exists())

    def test_un_evento_que_no_cabe_es_400(self):
        resp = self.client.post(
            f"{URL_DOCUMENTO}{self.factura.pk}/evento/",
            {"codigo": "032", "persona": self.PERSONA}, format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("después del acuse", resp.data["detail"])

    def test_el_documento_de_otro_emisor_no_existe(self):
        self.client.force_authenticate(crear_usuario(nombre="Otro", email="otro@x.co"))
        resp = self.client.post(
            f"{URL_DOCUMENTO}{self.factura.pk}/evento/", {"codigo": "030"}, format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_listar_y_filtrar(self):
        evento = self.registrado("030")
        otra = self.documento("2")
        self.solicitar(otra, "030")

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
        evento = self.solicitar(self.factura, "030")
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


class EliminarDocumentoTests(BaseEventos, APITestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.usuario)

    def test_sin_eventos_se_elimina_con_sus_archivos(self):
        from apps.recepcion.models import Adjunto

        otra = self.documento("2")
        archivos = [a.archivo.name for a in Adjunto.objects.filter(documento=self.factura)]
        self.assertTrue(archivos)

        resp = self.client.delete(f"{URL_DOCUMENTO}{self.factura.pk}/")

        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Documento.objects.filter(pk=self.factura.pk).exists())
        self.assertFalse(Adjunto.objects.filter(documento_id=self.factura.pk).exists())
        storage = Adjunto._meta.get_field("archivo").storage
        self.assertFalse(any(storage.exists(nombre) for nombre in archivos))
        # El correo y su otro documento se quedan.
        self.assertTrue(Correo.objects.filter(pk=self.correo.pk).exists())
        self.assertTrue(Documento.objects.filter(pk=otra.pk).exists())

    def test_con_eventos_no_se_elimina(self):
        evento = self.solicitar(self.factura, "030")
        Evento.objects.filter(pk=evento.pk).update(estado=EstadoEvento.RECHAZADO)

        resp = self.client.delete(f"{URL_DOCUMENTO}{self.factura.pk}/")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("tiene eventos RADIAN", resp.data["detail"])
        self.assertTrue(Documento.objects.filter(pk=self.factura.pk).exists())

    def test_la_carga_sin_documentos_se_va_con_el_ultimo(self):
        carga = Correo.objects.create(
            alias="901192048", emisor=self.emisor, origen=Correo.Origen.CARGA,
            raw_key="", envelope_to="", estado=Correo.Estado.PROCESADO,
        )
        documento = crear_documento_recibido(carga, self.emisor, "5")

        resp = self.client.delete(f"{URL_DOCUMENTO}{documento.pk}/")

        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Correo.objects.filter(pk=carga.pk).exists())

    def test_el_documento_de_otro_emisor_no_existe(self):
        self.client.force_authenticate(crear_usuario(nombre="Otro", email="otro@x.co"))
        resp = self.client.delete(f"{URL_DOCUMENTO}{self.factura.pk}/")
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(Documento.objects.filter(pk=self.factura.pk).exists())


class EliminarEventoTests(BaseEventos, APITestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.usuario)

    def rechazado(self):
        """Un 030 enviado y rechazado, con su XML y la respuesta en B2."""
        evento = self.solicitar(self.factura, "030")
        evento = Evento.objects.select_related("documento", "emisor", "evento_radian").get(pk=evento.pk)
        respuesta = respuesta_dian(False, ["Regla: LGC62, Rechazo: no es de tipo crédito"])
        b64 = base64.b64encode(b"<ApplicationResponse>RECHAZO</ApplicationResponse>").decode()
        respuesta.xml_crudo = f"<r><XmlBase64Bytes>{b64}</XmlBase64Bytes></r>"
        return eventos.enviar(evento, cliente=ClienteFalso(respuesta), firmador=self.firmador())

    def test_un_rechazado_se_elimina_con_sus_archivos(self):
        evento = self.rechazado()
        archivos = [evento.xml_archivo.name, evento.respuesta_archivo.name]
        storage = evento.xml_archivo.storage

        resp = self.client.delete(f"{URL_EVENTO}{evento.pk}/")

        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Evento.objects.exists())
        self.assertFalse(any(storage.exists(nombre) for nombre in archivos))

    def test_su_numero_no_se_vuelve_a_usar(self):
        evento = self.rechazado()
        self.client.delete(f"{URL_EVENTO}{evento.pk}/")
        self.assertEqual(self.solicitar(self.factura, "030").numero, "ACR2")

    def test_sin_el_rechazado_el_documento_se_puede_eliminar(self):
        evento = self.rechazado()
        self.assertEqual(self.client.delete(f"{URL_DOCUMENTO}{self.factura.pk}/").status_code, 400)
        self.client.delete(f"{URL_EVENTO}{evento.pk}/")
        self.assertEqual(self.client.delete(f"{URL_DOCUMENTO}{self.factura.pk}/").status_code, 204)

    def test_solo_los_rechazados(self):
        evento = self.solicitar(self.factura, "030")
        for estado in (EstadoEvento.PENDIENTE, EstadoEvento.ERROR, EstadoEvento.REGISTRADO):
            Evento.objects.filter(pk=evento.pk).update(estado=estado)
            resp = self.client.delete(f"{URL_EVENTO}{evento.pk}/")
            self.assertEqual(resp.status_code, 400, estado)
            self.assertIn("Solo se eliminan los eventos rechazados", resp.data["detail"])
        self.assertTrue(Evento.objects.filter(pk=evento.pk).exists())

    def test_el_evento_de_otro_emisor_no_existe(self):
        evento = self.rechazado()
        self.client.force_authenticate(crear_usuario(nombre="Otro", email="otro@x.co"))
        self.assertEqual(self.client.delete(f"{URL_EVENTO}{evento.pk}/").status_code, 404)


class EstadoRecepcionTests(BaseEventos, TestCase):
    """Cuándo termina el recibo de una factura (`Documento.estado_recepcion`)."""

    def setUp(self):
        super().setUp()
        self.contado = FormaPago.objects.create(id=1, codigo="1", nombre="Contado")
        self.credito = FormaPago.objects.create(id=2, codigo="2", nombre="Crédito")

    def factura_con(self, cufe, forma_pago, *, tipo="factura_venta"):
        documento = self.documento(cufe, tipo=tipo, verificado=False)
        documento.forma_pago = forma_pago
        documento.save()
        return documento

    def enviar(self, evento, cliente=None):
        evento = Evento.objects.select_related("documento", "emisor", "evento_radian").get(pk=evento.pk)
        return eventos.enviar(evento, cliente=cliente or ClienteFalso(), firmador=self.firmador())

    def test_empieza_sin_terminar(self):
        self.assertFalse(self.factura.estado_recepcion)

    def test_la_de_contado_termina_al_verificarse_y_no_saca_acuse(self):
        documento = verificacion.verificar(self.factura_con("2", self.contado), cliente=ClienteFalso())
        documento.refresh_from_db()
        self.assertTrue(documento.estado_recepcion)
        self.assertFalse(Evento.objects.exists())

    def test_la_de_contado_que_la_dian_no_reconoce_no_termina(self):
        class Invalida(ClienteFalso):
            def consultar_estado(self, track_id):
                return soap.RespuestaDian(es_valido=False, codigo_estado="66")

        documento = verificacion.verificar(self.factura_con("2", self.contado), cliente=Invalida())
        documento.refresh_from_db()
        self.assertFalse(documento.estado_recepcion)

    def test_la_de_contado_vieja_lee_la_forma_de_pago_del_xml(self):
        documento = crear_documento_recibido(
            self.correo, self.emisor, "3",
            xml_documento=xml_documento(cufe="3" * 96, forma_pago="1"),
        )
        documento = Documento.objects.select_related("emisor", "documento_tipo").get(pk=documento.pk)
        verificacion.verificar(documento, cliente=ClienteFalso())
        documento.refresh_from_db()
        self.assertTrue(documento.estado_recepcion)
        self.assertEqual(documento.forma_pago.codigo, "1")

    def test_la_de_credito_no_termina_al_verificarse(self):
        documento = verificacion.verificar(self.factura_con("2", self.credito), cliente=ClienteFalso())
        documento.refresh_from_db()
        self.assertFalse(documento.estado_recepcion)

    def test_una_nota_de_contado_no_termina(self):
        documento = verificacion.verificar(
            self.factura_con("2", self.contado, tipo="nota_credito"), cliente=ClienteFalso(),
        )
        documento.refresh_from_db()
        self.assertFalse(documento.estado_recepcion)

    def test_la_aceptacion_registrada_la_termina(self):
        self.registrado("030")
        self.registrado("032", fecha=timezone.localdate())
        self.enviar(self.solicitar(self.factura, "033"))
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.radian_estado, EstadoRadian.ACEPTADA)
        self.assertTrue(self.factura.estado_recepcion)

    def test_el_reclamo_registrado_la_termina(self):
        self.registrado("030")
        self.registrado("032", fecha=timezone.localdate())
        self.enviar(self.solicitar(
            self.factura, "031", concepto_reclamo=ConceptoReclamo.objects.get(codigo="02"),
        ))
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.radian_estado, EstadoRadian.RECLAMADA)
        self.assertTrue(self.factura.estado_recepcion)

    def test_el_acuse_y_el_recibo_no_la_terminan(self):
        self.enviar(self.solicitar(self.factura, "030"))
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.radian_estado, EstadoRadian.ACUSE)
        self.assertFalse(self.factura.estado_recepcion)

    def test_la_aceptacion_rechazada_no_la_termina(self):
        self.registrado("030")
        self.registrado("032", fecha=timezone.localdate())
        self.enviar(self.solicitar(self.factura, "033"), ClienteFalso(respuesta_dian(False)))
        self.factura.refresh_from_db()
        self.assertFalse(self.factura.estado_recepcion)


class EstadoRecepcionApiTests(BaseEventos, APITestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.usuario)
        self.terminada = self.documento("2")
        Documento.objects.filter(pk=self.terminada.pk).update(estado_recepcion=True)

    def ids(self, consulta):
        resp = self.client.get(f"{URL_DOCUMENTO}?{consulta}")
        self.assertEqual(resp.status_code, 200, resp.data)
        return {fila["id"] for fila in resp.data["results"]}

    def test_filtra_por_estado_de_recepcion(self):
        self.assertEqual(self.ids("estado_recepcion=true"), {str(self.terminada.pk)})
        self.assertEqual(self.ids("estado_recepcion=false"), {str(self.factura.pk)})
        self.assertEqual(len(self.ids("estado_recepcion=otro")), 2)

    def test_el_detalle_lo_trae(self):
        resp = self.client.get(f"{URL_DOCUMENTO}{self.terminada.pk}/")
        self.assertIs(resp.data["estado_recepcion"], True)


class BloqueoProduccionTests(BaseEventos, TestCase):
    """Fuera de ENV=prod no salen eventos sobre facturas de producción."""

    def setUp(self):
        super().setUp()
        Documento.objects.filter(pk=self.factura.pk).update(ambiente=Ambiente.PRODUCCION)
        self.factura.refresh_from_db()

    def test_no_se_pide_ni_gasta_numero(self):
        with self.assertRaisesMessage(eventos.EventoInvalido, "ENV=test"):
            self.solicitar(self.factura, "030")
        self.assertFalse(Evento.objects.exists())
        with override_settings(ENV="prod"):
            self.assertEqual(self.solicitar(self.factura, "030").numero, "ACR1")

    def test_sin_ambiente_cuenta_como_produccion(self):
        Documento.objects.filter(pk=self.factura.pk).update(ambiente=None)
        self.factura.refresh_from_db()
        with self.assertRaisesMessage(eventos.EventoInvalido, "ENV=test"):
            self.solicitar(self.factura, "030")

    def test_uno_ya_creado_queda_en_error_sin_enviarse(self):
        with override_settings(ENV="prod"):
            evento = self.solicitar(self.factura, "030")
        cliente = ClienteFalso()
        evento = eventos.enviar(
            Evento.objects.select_related("documento", "emisor", "evento_radian").get(pk=evento.pk),
            cliente=cliente, firmador=self.firmador(),
        )
        self.assertEqual(evento.estado, EstadoEvento.ERROR)
        self.assertIn("ENV=test", evento.respuesta_descripcion)
        self.assertEqual(evento.intentos, 0)
        self.assertFalse(evento.xml_archivo)
        self.assertEqual(cliente.enviados, [])

    def test_la_tarea_no_lo_deja_pendiente(self):
        with override_settings(ENV="prod"):
            evento = self.solicitar(self.factura, "030")
        tareas.enviar_evento(str(evento.pk))
        evento.refresh_from_db()
        self.assertEqual(evento.estado, EstadoEvento.ERROR)

    def test_el_acuse_automatico_se_omite_sin_fallar(self):
        documento = verificacion.verificar(self.factura, cliente=ClienteFalso())
        self.assertEqual(documento.verificacion_estado, EstadoVerificacion.VALIDO)
        self.assertFalse(Evento.objects.exists())
