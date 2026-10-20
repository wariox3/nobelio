"""Descartar y restaurar un documento recibido (``apps.recepcion.descarte``)."""
from rest_framework.test import APITestCase

from apps.catalogos.models import FormaPago
from apps.documentos.tests_utils import crear_usuario
from apps.recepcion import descarte, eventos
from apps.recepcion.models import Documento, EstadoEvento, EstadoVerificacion, Evento
from apps.recepcion.tests_eventos import URL_DOCUMENTO, BaseEventos


class DescarteTests(BaseEventos, APITestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.usuario)

    def descartar(self, documento=None):
        return self.client.post(f"{URL_DOCUMENTO}{(documento or self.factura).pk}/descartar/")

    def restaurar(self, documento=None):
        return self.client.post(f"{URL_DOCUMENTO}{(documento or self.factura).pk}/restaurar/")

    def releer(self, documento=None):
        return Documento.objects.select_related("emisor", "documento_tipo").get(
            pk=(documento or self.factura).pk,
        )

    # --- Descartar ------------------------------------------------------------

    def test_descartar_termina_la_recepcion_y_deja_el_documento_intacto(self):
        antes = self.releer()

        resp = self.descartar()

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data["estado_descartado"])
        self.assertTrue(resp.data["estado_recepcion"])
        despues = self.releer()
        self.assertTrue(despues.estado_descartado)
        self.assertTrue(despues.estado_recepcion)
        for campo in ("numero", "cufe_cude", "total_a_pagar", "verificacion_estado", "radian_estado"):
            self.assertEqual(getattr(despues, campo), getattr(antes, campo), campo)

    def test_no_se_descarta_dos_veces(self):
        self.descartar()

        resp = self.descartar()

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["detail"], "El documento ya está descartado.")

    def test_no_se_descarta_con_la_recepcion_terminada(self):
        Documento.objects.filter(pk=self.factura.pk).update(estado_recepcion=True)

        resp = self.descartar()

        self.assertEqual(resp.status_code, 400)
        self.assertIn("ya terminó", resp.data["detail"])
        self.assertFalse(self.releer().estado_descartado)

    def test_con_cualquier_evento_es_400(self):
        for estado in (EstadoEvento.PENDIENTE, EstadoEvento.ERROR, EstadoEvento.RECHAZADO,
                       EstadoEvento.REGISTRADO):
            with self.subTest(estado=estado):
                Evento.objects.all().delete()
                evento = self.solicitar(self.factura, "030")
                Evento.objects.filter(pk=evento.pk).update(estado=estado)

                resp = self.descartar()

                self.assertEqual(resp.status_code, 400)
                self.assertIn("tiene eventos RADIAN", resp.data["detail"])
                self.assertIn("030", resp.data["detail"])
                self.assertFalse(self.releer().estado_descartado)

    def test_el_documento_de_otro_emisor_no_existe(self):
        self.client.force_authenticate(crear_usuario(nombre="Otro", email="otro@x.co"))

        self.assertEqual(self.descartar().status_code, 404)
        self.assertEqual(self.restaurar().status_code, 404)

    # --- Lo que bloquea -------------------------------------------------------

    def test_el_descartado_no_admite_eventos(self):
        self.descartar()

        with self.assertRaisesMessage(eventos.EventoInvalido, "está descartada"):
            self.solicitar(self.releer(), "030")
        self.assertFalse(Evento.objects.exists())

    def test_el_evento_pedido_con_el_documento_viejo_tambien_se_corta(self):
        """Lo que se leyó antes del descarte se vuelve a mirar bajo el candado."""
        viejo = self.releer()
        descarte.descartar(viejo)

        with self.assertRaisesMessage(eventos.EventoInvalido, "está descartada"):
            self.solicitar(viejo, "030")
        self.assertFalse(Evento.objects.exists())

    def test_el_acuse_automatico_se_omite_sin_fallar(self):
        self.descartar()

        self.assertIsNone(eventos.acuse_automatico(self.releer()))
        self.assertFalse(Evento.objects.exists())

    # --- Filtro ---------------------------------------------------------------

    def test_filtro_por_descartado(self):
        otra = self.documento("2")
        self.descartar()

        descartados = self.client.get(URL_DOCUMENTO, {"estado_descartado": "true"})
        vigentes = self.client.get(URL_DOCUMENTO, {"estado_descartado": "false"})
        todos = self.client.get(URL_DOCUMENTO)

        self.assertEqual([d["id"] for d in descartados.data["results"]], [str(self.factura.pk)])
        self.assertEqual([d["id"] for d in vigentes.data["results"]], [str(otra.pk)])
        self.assertEqual(todos.data["count"], 2)

    # --- Restaurar ------------------------------------------------------------

    def test_restaurar_vuelve_a_admitir_eventos(self):
        self.descartar()

        resp = self.restaurar()

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(resp.data["estado_descartado"])
        # Factura a crédito sin eventos: la recepción sigue abierta.
        self.assertFalse(resp.data["estado_recepcion"])
        self.assertEqual(self.solicitar(self.releer(), "030").numero, "ACR1")

    def test_restaurar_uno_no_descartado_es_400(self):
        resp = self.restaurar()

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["detail"], "El documento no está descartado.")

    def test_restaurar_una_factura_de_contado_valida_la_deja_terminada(self):
        """Lo que diría la recepción sin el descarte: de contado y válida, terminada."""
        contado = FormaPago.objects.create(id=1, codigo="1", nombre="Contado")
        pendiente = self.documento("2", verificado=False)
        Documento.objects.filter(pk=pendiente.pk).update(forma_pago=contado)
        self.descartar(pendiente)
        Documento.objects.filter(pk=pendiente.pk).update(
            verificacion_estado=EstadoVerificacion.VALIDO,
        )

        resp = self.restaurar(pendiente)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(resp.data["estado_descartado"])
        self.assertTrue(resp.data["estado_recepcion"])

    def test_restaurar_una_de_contado_sin_verificar_la_deja_abierta(self):
        contado = FormaPago.objects.create(id=1, codigo="1", nombre="Contado")
        pendiente = self.documento("2", verificado=False)
        Documento.objects.filter(pk=pendiente.pk).update(forma_pago=contado)
        self.descartar(pendiente)

        resp = self.restaurar(pendiente)

        self.assertFalse(resp.data["estado_recepcion"])
