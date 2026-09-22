"""La tarea `emitir_documento` y el ayudante `encolar`, por su cuenta.

De punta a punta —crear, encolar, emitir— está en `tests_api.py`.
"""
import uuid
from unittest import mock

import requests
from django.test import TestCase

from apps.dian import servicios
from apps.documentos.tareas import emitir_documento
from apps.documentos.tests_utils import crear_documento_factura
from apps.nucleo.colas import encolar

EMITIR = "apps.dian.servicios.emitir"


class EmitirDocumentoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.documento = crear_documento_factura()["documento"]

    def test_emite_con_la_misma_funcion_que_emitir(self):
        with mock.patch(EMITIR) as emitir:
            emitir_documento(str(self.documento.pk))

        self.assertEqual(emitir.call_args.args[0].pk, self.documento.pk)

    def test_lo_no_emitible_no_lanza_ni_reintenta(self):
        with mock.patch(EMITIR, side_effect=servicios.ErrorEmision("sin certificado")) as emitir, \
                self.assertLogs("apps.documentos.tareas", "WARNING") as logs:
            emitir_documento(str(self.documento.pk))

        emitir.assert_called_once()
        self.assertIn("sin certificado", logs.output[0])

    def test_la_dian_sin_respuesta_no_lanza_ni_reintenta(self):
        with mock.patch(EMITIR, side_effect=requests.ConnectionError("sin red")) as emitir, \
                self.assertLogs("apps.documentos.tareas", "WARNING"):
            emitir_documento(str(self.documento.pk))

        emitir.assert_called_once()

    def test_un_documento_borrado_no_hace_nada(self):
        with mock.patch(EMITIR) as emitir:
            emitir_documento(str(uuid.uuid4()))

        emitir.assert_not_called()


class EncolarTests(TestCase):
    def test_espera_a_que_se_confirme(self):
        tarea = mock.Mock()

        with self.captureOnCommitCallbacks(execute=False) as pendientes:
            encolar(tarea, "a", 1)
            tarea.delay.assert_not_called()

        for callback in pendientes:
            callback()
        tarea.delay.assert_called_once_with("a", 1)

    def test_si_la_transaccion_se_deshace_no_encola(self):
        from django.db import transaction

        tarea = mock.Mock()
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    encolar(tarea, "a")
                    raise RuntimeError("se deshace")
            except RuntimeError:
                pass

        tarea.delay.assert_not_called()

    def test_el_broker_caido_se_registra_y_no_lanza(self):
        tarea = mock.Mock()
        tarea.name = "prueba"
        tarea.delay.side_effect = OSError("sin broker")

        with self.assertLogs("apps.nucleo.colas", "ERROR"), \
                self.captureOnCommitCallbacks(execute=True):
            encolar(tarea, "a")
