"""Pruebas de ``emitir_set_pruebas``: los dos documentos de la habilitación.

Lo que se cuida aquí es que la DIAN reciba los dos documentos y que en la base
no quede rastro de ellos: existen solo para que acepten el Set de Pruebas.
"""
from datetime import date

from django.test import TestCase

from apps.dian import servicios, soap
from apps.documentos.models import (
    Adquiriente,
    Documento,
    DocumentoDetalle,
    DocumentoDetalleImpuesto,
)
from apps.documentos.tests_utils import crear_catalogos_minimos
from apps.catalogos.models import TipoFactura
from apps.emisores.models import Emisor, ResolucionFacturacion, SoftwareDian


class FirmadorFalso:
    def firmar(self, xml):
        return b"<firmado/>"


class ClienteFalso:
    def __init__(self):
        self.envios = []

    def enviar_set_pruebas(self, xml, nombre, test_set_id):
        self.envios.append({"nombre": nombre, "test_set_id": test_set_id, "xml": xml})
        return soap.RespuestaDian(
            track_id=f"zipkey-{len(self.envios)}", es_valido=True,
            codigo_estado="00", descripcion_estado="Procesado correctamente",
        )


class EmitirSetPruebasTests(TestCase):
    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.emisor = Emisor.objects.create(
            cuenta=self.cat["cuenta"], razon_social="Empresa Demo SAS",
            tipo_identificacion=self.cat["nit"], numero_identificacion="700085371",
            digito_verificacion="1", tipo_organizacion=self.cat["juridica"],
            pais=self.cat["colombia"], departamento=self.cat["antioquia"],
            municipio=self.cat["medellin"], direccion="Calle 1 # 2-3",
        )
        self.software = SoftwareDian.objects.create(
            emisor=self.emisor, identificador="56f2ae4e-9812-4fad-9255-08fcfcd5ccb0",
            pin="12345", test_set_id="0d26ba8c-8584-4199-b210-2ddc063c3ddd",
        )
        # La resolución de pruebas la arma el servicio; el tipo de factura
        # sale del catálogo, que en producción carga `cargar_catalogos`.
        self.tipo_factura, _ = TipoFactura.objects.get_or_create(
            codigo="01", defaults={"nombre": "Factura de Venta"}
        )
        self.cliente = ClienteFalso()

    def emitir(self, **extra):
        return servicios.emitir_set_pruebas(
            self.emisor, firmador=FirmadorFalso(), cliente=self.cliente, **extra
        )

    def test_envia_los_dos_documentos_al_set_de_pruebas(self):
        resultados = self.emitir()
        self.assertEqual(len(self.cliente.envios), 2)
        for envio in self.cliente.envios:
            self.assertEqual(envio["test_set_id"], self.software.test_set_id)
        self.assertTrue(resultados["factura"]["es_valido"])
        self.assertTrue(resultados["nota_credito"]["es_valido"])
        self.assertEqual(resultados["factura"]["track_id"], "zipkey-1")
        self.assertEqual(resultados["nota_credito"]["track_id"], "zipkey-2")

    def test_cada_documento_lleva_su_cufe_o_cude(self):
        resultados = self.emitir()
        cufe = resultados["factura"]["cufe_cude"]
        cude = resultados["nota_credito"]["cufe_cude"]
        self.assertEqual(len(cufe), 96)  # SHA-384 en hexadecimal
        self.assertEqual(len(cude), 96)
        self.assertNotEqual(cufe, cude)

    def test_marca_al_emisor_como_habilitado(self):
        self.assertFalse(self.emisor.habilitado_facturacion)
        self.emitir()
        self.emisor.refresh_from_db()
        self.assertTrue(self.emisor.habilitado_facturacion)

    def test_si_no_se_envia_nada_el_emisor_no_queda_habilitado(self):
        self.software.test_set_id = ""
        self.software.save(update_fields=["test_set_id"])
        with self.assertRaises(servicios.ErrorEmision):
            self.emitir()
        self.emisor.refresh_from_db()
        self.assertFalse(self.emisor.habilitado_facturacion)

    def test_no_queda_nada_registrado(self):
        self.emitir()
        self.assertEqual(Documento.objects.count(), 0)
        self.assertEqual(Adquiriente.objects.count(), 0)
        self.assertEqual(DocumentoDetalle.objects.count(), 0)
        self.assertEqual(DocumentoDetalleImpuesto.objects.count(), 0)

    def test_numera_con_la_resolucion_de_pruebas(self):
        resultados = self.emitir()
        self.assertEqual(resultados["factura"]["numero"], "SETP990000000")
        self.assertEqual(resultados["nota_credito"]["numero"], "990000001")

    def test_la_resolucion_de_pruebas_no_queda_guardada(self):
        self.emitir()
        self.assertFalse(ResolucionFacturacion.objects.exists())

    def test_si_el_emisor_ya_tiene_esa_resolucion_no_se_duplica(self):
        """La habilitación importa esa misma resolución: no puede chocar."""
        
        tipo_factura, _ = TipoFactura.objects.get_or_create(
            codigo="01", defaults={"nombre": "Factura de Venta"}
        )
        propia = ResolucionFacturacion.objects.create(
            emisor=self.emisor, tipo_factura=tipo_factura,
            **servicios.RESOLUCION_PRUEBAS,
        )
        self.emitir()
        self.assertEqual(ResolucionFacturacion.objects.count(), 1)
        propia.refresh_from_db()
        self.assertEqual(propia.consecutivo_actual, 0)

    def test_se_puede_forzar_el_consecutivo(self):
        resultados = self.emitir(consecutivo=990000500)
        self.assertEqual(resultados["factura"]["numero"], "SETP990000500")
        self.assertEqual(resultados["nota_credito"]["numero"], "990000501")

    def test_sin_test_set_id_no_se_envia_nada(self):
        self.software.test_set_id = ""
        self.software.save(update_fields=["test_set_id"])
        with self.assertRaises(servicios.ErrorEmision):
            self.emitir()
        self.assertEqual(self.cliente.envios, [])

    def test_un_emisor_inactivo_no_emite(self):
        self.emisor.activo = False
        self.emisor.save(update_fields=["activo"])
        with self.assertRaises(servicios.ErrorEmision):
            self.emitir()
