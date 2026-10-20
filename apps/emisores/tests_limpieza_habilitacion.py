"""Al aceptarse un Set de Pruebas se borra lo de habilitación que no quedó aceptado.

Facturación, documento equivalente y nómina, cada uno con lo suyo. Lo aceptado,
lo enviado sin veredicto y lo de producción no se tocan nunca.
"""
from datetime import time
from decimal import Decimal
from unittest import mock

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from apps.dian import servicios
from apps.documentos.models import Documento, DocumentoEstado, DocumentoPOS, DocumentoTipo
from apps.documentos.tests_utils import crear_documento_factura
from apps.emisores.models import SoftwareDian
from apps.emisores.servicios.factura_prueba import (
    limpiar_documentos_equivalentes_de_habilitacion,
    limpiar_facturas_de_habilitacion,
)
from apps.emisores.servicios.nomina_prueba import limpiar_nominas_de_habilitacion
from apps.nomina.models import Nomina
from apps.nomina.servicios import crear_nota_ajuste
from apps.nomina.tests_utils import crear_emisor_de_nomina, crear_nomina
from apps.nucleo.models import Ambiente

BORRADOR = DocumentoEstado.Nombre.BORRADOR
FIRMADO = DocumentoEstado.Nombre.FIRMADO
ENVIADO = DocumentoEstado.Nombre.ENVIADO
ACEPTADO = DocumentoEstado.Nombre.ACEPTADO
RECHAZADO = DocumentoEstado.Nombre.RECHAZADO


def _estado(nombre):
    return DocumentoEstado.objects.get(nombre=nombre)


class LimpiezaDeDocumentosTests(TestCase):
    def setUp(self):
        datos = crear_documento_factura()
        self.cat = datos["catalogos"]
        self.emisor = datos["emisor"]
        self.resolucion = datos["resolucion"]
        self.moneda = datos["catalogos"]["cop"]
        self.consecutivo = 1
        # El del helper: una factura de habilitación en borrador.
        self.base = datos["documento"]

    def doc(self, estado, *, tipo=DocumentoTipo.Codigo.FACTURA_VENTA,
            ambiente=Ambiente.PRUEBAS, referencia=None, emisor=None):
        self.consecutivo += 1
        documento = Documento.objects.create(
            documento_tipo=DocumentoTipo.objects.get(codigo=tipo),
            emisor=emisor or self.emisor, resolucion=self.resolucion,
            prefijo="SETP", consecutivo=self.consecutivo, numero=f"SETP{self.consecutivo}",
            fecha_emision=timezone.localdate(), hora_emision=time(10, 0),
            moneda=self.moneda, ambiente=Ambiente.PRUEBAS, estado=_estado(estado),
            documento_referencia=referencia,
            valor_bruto=Decimal("1000"), total_impuestos=Decimal("0"),
            total_a_pagar=Decimal("1000"),
        )
        # Fuera de ENV=prod no se guarda uno de producción: se le cambia después.
        if ambiente != Ambiente.PRUEBAS:
            Documento.objects.filter(pk=documento.pk).update(ambiente=ambiente)
        return documento

    def existen(self, *documentos):
        return [Documento.objects.filter(pk=d.pk).exists() for d in documentos]

    # --- Facturación ----------------------------------------------------------

    def test_borra_lo_no_aceptado_del_set_de_facturacion(self):
        borrador, firmado, rechazado = (self.doc(e) for e in (BORRADOR, FIRMADO, RECHAZADO))
        nota_credito = self.doc(RECHAZADO, tipo=DocumentoTipo.Codigo.NOTA_CREDITO)
        nota_debito = self.doc(BORRADOR, tipo=DocumentoTipo.Codigo.NOTA_DEBITO)

        borrados = limpiar_facturas_de_habilitacion(self.emisor)

        self.assertEqual(
            self.existen(self.base, borrador, firmado, rechazado, nota_credito, nota_debito),
            [False] * 6,
        )
        self.assertEqual(len(borrados), 6)

    def test_lo_aceptado_lo_enviado_y_lo_de_produccion_no_se_tocan(self):
        aceptado = self.doc(ACEPTADO)
        enviado = self.doc(ENVIADO)
        de_produccion = self.doc(RECHAZADO, ambiente=Ambiente.PRODUCCION)
        nota_aceptada = self.doc(
            ACEPTADO, tipo=DocumentoTipo.Codigo.NOTA_CREDITO, referencia=aceptado,
        )

        limpiar_facturas_de_habilitacion(self.emisor)

        self.assertEqual(
            self.existen(aceptado, enviado, de_produccion, nota_aceptada), [True] * 4,
        )

    def test_borra_la_nota_rechazada_de_una_factura_aceptada_sin_tocar_la_factura(self):
        aceptada = self.doc(ACEPTADO)
        nota = self.doc(RECHAZADO, tipo=DocumentoTipo.Codigo.NOTA_CREDITO, referencia=aceptada)

        limpiar_facturas_de_habilitacion(self.emisor)

        self.assertEqual(self.existen(aceptada, nota), [True, False])

    def test_la_factura_rechazada_con_una_nota_que_se_queda_no_se_borra(self):
        rechazada = self.doc(RECHAZADO)
        # Una nota enviada que la referencia: no se borra, así que su factura tampoco.
        nota = self.doc(ENVIADO, tipo=DocumentoTipo.Codigo.NOTA_CREDITO, referencia=rechazada)

        limpiar_facturas_de_habilitacion(self.emisor)

        self.assertEqual(self.existen(rechazada, nota), [True, True])

    def test_la_factura_rechazada_y_su_nota_rechazada_se_van_juntas(self):
        rechazada = self.doc(RECHAZADO)
        nota = self.doc(RECHAZADO, tipo=DocumentoTipo.Codigo.NOTA_CREDITO, referencia=rechazada)

        limpiar_facturas_de_habilitacion(self.emisor)

        self.assertEqual(self.existen(rechazada, nota), [False, False])

    def test_el_set_de_facturacion_no_toca_el_soporte_ni_el_pos(self):
        soporte = self.doc(BORRADOR, tipo=DocumentoTipo.Codigo.DOCUMENTO_SOPORTE)
        pos = self.doc(BORRADOR, tipo=DocumentoTipo.Codigo.DOCUMENTO_EQUIVALENTE_POS)

        limpiar_facturas_de_habilitacion(self.emisor)

        self.assertEqual(self.existen(soporte, pos), [True, True])

    def test_no_toca_los_documentos_de_otro_emisor(self):
        otro = crear_emisor_de_nomina(self.cat, nit="800199436")["emisor"]
        ajeno = self.doc(RECHAZADO, emisor=otro)

        limpiar_facturas_de_habilitacion(self.emisor)

        self.assertEqual(self.existen(ajeno), [True])

    # --- Documento equivalente ------------------------------------------------

    def test_borra_lo_no_aceptado_del_set_del_pos_con_su_caja(self):
        pos = self.doc(RECHAZADO, tipo=DocumentoTipo.Codigo.DOCUMENTO_EQUIVALENTE_POS)
        DocumentoPOS.objects.create(
            documento=pos, caja_placa="CAJA-PRUEBA", caja_ubicacion="x", caja_tipo="x",
            cajero="x", codigo_venta="x",
        )
        nota = self.doc(
            BORRADOR, tipo=DocumentoTipo.Codigo.NOTA_AJUSTE_DE_CREDITO, referencia=pos,
        )
        aceptado = self.doc(ACEPTADO, tipo=DocumentoTipo.Codigo.DOCUMENTO_EQUIVALENTE_POS)

        limpiar_documentos_equivalentes_de_habilitacion(self.emisor)

        self.assertEqual(self.existen(pos, nota, aceptado), [False, False, True])
        self.assertFalse(DocumentoPOS.objects.exists())
        # Y las facturas no son de este set.
        self.assertEqual(self.existen(self.base), [True])

    # --- Cuándo corre ---------------------------------------------------------

    def _software(self, tipo):
        return SoftwareDian.objects.update_or_create(
            emisor=self.emisor, tipo=tipo,
            defaults={"identificador": f"sw-{tipo}", "pin": "1"},
        )[0]

    def _cerrar_el_set(self, software):
        with self.captureOnCommitCallbacks(execute=True), transaction.atomic():
            servicios._marcar_habilitacion_superada(software, self.emisor)

    def test_corre_al_aceptarse_el_set_de_cada_software(self):
        factura = self.doc(RECHAZADO)
        pos = self.doc(RECHAZADO, tipo=DocumentoTipo.Codigo.DOCUMENTO_EQUIVALENTE_POS)

        self._cerrar_el_set(self._software(SoftwareDian.Tipo.DOCUMENTO_EQUIVALENTE))
        self.assertEqual(self.existen(factura, pos), [True, False])

        self._cerrar_el_set(self._software(SoftwareDian.Tipo.FACTURACION))
        self.assertEqual(self.existen(factura), [False])

    def test_solo_la_primera_vez(self):
        software = self._software(SoftwareDian.Tipo.FACTURACION)
        self._cerrar_el_set(software)
        rechazado = self.doc(RECHAZADO)

        self._cerrar_el_set(software)  # ya estaba aceptado

        self.assertEqual(self.existen(rechazado), [True])

    def test_corre_al_confirmar_no_dentro_de_la_transaccion(self):
        rechazado = self.doc(RECHAZADO)
        software = self._software(SoftwareDian.Tipo.FACTURACION)

        with self.captureOnCommitCallbacks(execute=False) as pendientes, transaction.atomic():
            servicios._marcar_habilitacion_superada(software, self.emisor)
            self.assertEqual(self.existen(rechazado), [True])

        self.assertEqual(self.existen(rechazado), [True])
        for callback in pendientes:
            callback()
        self.assertEqual(self.existen(rechazado), [False])

    def test_un_fallo_de_la_limpieza_no_deshace_la_habilitacion(self):
        software = self._software(SoftwareDian.Tipo.FACTURACION)
        with mock.patch(
            "apps.emisores.servicios.factura_prueba.limpiar_facturas_de_habilitacion",
            side_effect=RuntimeError("se cayó"),
        ), self.assertLogs("apps.dian.servicios", level="ERROR") as log:
            self._cerrar_el_set(software)

        software.refresh_from_db()
        self.emisor.refresh_from_db()
        self.assertTrue(software.set_pruebas_aceptado)
        self.assertTrue(self.emisor.habilitado_facturacion)
        self.assertIn("habilitacion.limpieza_fallida", log.output[0])


class LimpiezaDeNominasTests(TestCase):
    def setUp(self):
        self.base = crear_emisor_de_nomina()
        self.emisor = self.base["emisor"]

    def nomina(self, estado, *, ambiente=Ambiente.PRUEBAS):
        nomina, _ = crear_nomina(self.base)
        Nomina.objects.filter(pk=nomina.pk).update(estado=_estado(estado), ambiente=ambiente)
        nomina.refresh_from_db()
        return nomina

    def nota(self, nomina, estado):
        nota = crear_nota_ajuste(
            nomina, tipo_nota=Nomina.TipoNota.REEMPLAZAR, exigir_cune=False,
        )
        Nomina.objects.filter(pk=nota.pk).update(estado=_estado(estado))
        return nota

    def existen(self, *nominas):
        return [Nomina.objects.filter(pk=n.pk).exists() for n in nominas]

    def test_borra_lo_no_aceptado_con_sus_notas(self):
        borrador, firmado, rechazado = (self.nomina(e) for e in (BORRADOR, FIRMADO, RECHAZADO))
        nota = self.nota(borrador, BORRADOR)

        borradas = limpiar_nominas_de_habilitacion(self.emisor)

        self.assertEqual(self.existen(borrador, firmado, rechazado, nota), [False] * 4)
        self.assertEqual(len(borradas), 4)

    def test_lo_aceptado_lo_enviado_y_lo_de_produccion_no_se_tocan(self):
        aceptada = self.nomina(ACEPTADO)
        enviada = self.nomina(ENVIADO)
        de_produccion = self.nomina(RECHAZADO, ambiente=Ambiente.PRODUCCION)
        nota_aceptada = self.nota(aceptada, ACEPTADO)
        nota_rechazada = self.nota(aceptada, RECHAZADO)

        limpiar_nominas_de_habilitacion(self.emisor)

        self.assertEqual(
            self.existen(aceptada, enviada, de_produccion, nota_aceptada, nota_rechazada),
            [True, True, True, True, False],
        )

    def test_la_nomina_con_una_nota_que_se_queda_no_se_borra(self):
        rechazada = self.nomina(RECHAZADO)
        nota = self.nota(rechazada, ENVIADO)

        limpiar_nominas_de_habilitacion(self.emisor)

        self.assertEqual(self.existen(rechazada, nota), [True, True])

    def test_corre_al_aceptarse_el_set_de_nomina(self):
        rechazada = self.nomina(RECHAZADO)
        aceptada = self.nomina(ACEPTADO)

        with self.captureOnCommitCallbacks(execute=True), transaction.atomic():
            servicios._marcar_habilitacion_superada(self.base["software"], self.emisor)

        self.assertEqual(self.existen(rechazada, aceptada), [False, True])
