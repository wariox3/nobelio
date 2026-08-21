"""Pruebas de ``emitir_set_pruebas``: la habilitación completa en una llamada.

El orden es lo que se cuida aquí: la nota crédito referencia a la factura, y la
DIAN la rechaza (CBG04a) si la envía antes de haber registrado la factura. Así
que se envía la factura, se espera su aceptación y solo entonces va la nota.
"""
import shutil
import tempfile

from django.test import TestCase, override_settings

from apps.catalogos.models import TipoFactura
from apps.dian import servicios, soap
from apps.documentos.models import (
    Adquiriente,
    Documento,
    DocumentoDetalle,
    DocumentoDetalleImpuesto,
    DocumentoEstado,
)
from apps.documentos.tests_utils import crear_catalogos_minimos
from apps.emisores.models import Emisor, ResolucionFacturacion, SoftwareDian

ACEPTADO = soap.RespuestaDian(
    es_valido=True, codigo_estado="00", descripcion_estado="Procesado correctamente"
)
PENDIENTE = soap.RespuestaDian()  # ni IsValid ni errores: la DIAN sigue procesando
RECHAZADO = soap.RespuestaDian(
    codigo_estado="99", errores=["Regla: CBG04a, Rechazo: Documento referenciado no existe."]
)


class FirmadorFalso:
    def firmar(self, xml):
        return b"<firmado/>"


class ClienteFalso:
    """Dobla a la DIAN: acepta el envío y responde veredictos de una cola."""

    def __init__(self, veredictos=None):
        self.envios = []
        self.consultas = []
        # Se consumen en orden; al agotarse, todo lo demás sale aceptado.
        self.veredictos = list(veredictos or [])

    def enviar_set_pruebas(self, xml, nombre, test_set_id):
        # Se anota el estado de todo lo emitido en el instante del envío: es lo
        # que permite comprobar que la factura ya estaba aceptada al ir la nota.
        estados = {
            d.numero: d.estado.nombre
            for d in Documento.objects.select_related("estado")
        }
        self.envios.append({
            "nombre": nombre, "test_set_id": test_set_id, "estados": estados,
        })
        return soap.RespuestaDian(track_id=f"zipkey-{len(self.envios)}")

    def consultar_estado_zip(self, track_id):
        self.consultas.append(track_id)
        return self.veredictos.pop(0) if self.veredictos else ACEPTADO


_TMP_MEDIA = tempfile.mkdtemp(prefix="set-pruebas-")


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class EmitirSetPruebasTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.emisor = Emisor.objects.create(
            cuenta=self.cat["cuenta"], razon_social="Empresa Demo SAS",
            tipo_identificacion=self.cat["nit"], numero_identificacion="700085371",
            tipo_organizacion=self.cat["juridica"], correo="demo@empresa.co",
            pais=self.cat["colombia"], departamento=self.cat["antioquia"],
            municipio=self.cat["medellin"], direccion="Calle 1 # 2-3",
        )
        self.software = SoftwareDian.objects.create(
            emisor=self.emisor, identificador="56f2ae4e-9812-4fad-9255-08fcfcd5ccb0",
            pin="12345", test_set_id="0d26ba8c-8584-4199-b210-2ddc063c3ddd",
        )
        TipoFactura.objects.get_or_create(
            codigo="01", defaults={"nombre": "Factura de Venta"}
        )
        self.cliente = ClienteFalso()

    def emitir(self, **extra):
        return servicios.emitir_set_pruebas(
            self.emisor, firmador=FirmadorFalso(), cliente=self.cliente, **extra
        )

    def documentos(self, ids):
        return {
            clave: Documento.objects.select_related("estado").get(pk=pk)
            for clave, pk in ids.items()
        }

    # --- El orden: factura aceptada antes de mandar la nota ----------------

    def test_la_nota_se_envia_con_la_factura_ya_aceptada(self):
        """El fondo de CBG04a: la DIAN tiene que tener ya la factura."""
        self.emitir()

        primero, segundo = self.cliente.envios
        self.assertEqual(primero["nombre"], "SETP990000000.xml")
        # Al mandar la factura no había nada aceptado todavía.
        self.assertNotIn(
            DocumentoEstado.Nombre.ACEPTADO, primero["estados"].values()
        )
        # Al mandar la nota, la factura ya estaba aceptada.
        self.assertEqual(segundo["nombre"], "990000001.xml")
        self.assertEqual(
            segundo["estados"]["SETP990000000"], DocumentoEstado.Nombre.ACEPTADO
        )

    def test_espera_hasta_que_la_dian_se_pronuncie(self):
        # La factura tarda dos consultas en salir; la nota, una.
        self.cliente.veredictos = [PENDIENTE, PENDIENTE, ACEPTADO, ACEPTADO]
        ids = self.emitir()

        self.assertEqual(len(self.cliente.consultas), 4)
        for documento in self.documentos(ids).values():
            self.assertEqual(documento.estado.nombre, DocumentoEstado.Nombre.ACEPTADO)

    def test_consulta_por_el_zipkey_y_no_por_el_cufe(self):
        self.emitir()
        self.assertEqual(self.cliente.consultas, ["zipkey-1", "zipkey-2"])

    # --- Final feliz -------------------------------------------------------

    def test_habilita_al_emisor_y_al_software(self):
        self.assertFalse(self.emisor.habilitado_facturacion)
        self.emitir()

        self.emisor.refresh_from_db()
        self.software.refresh_from_db()
        self.assertTrue(self.emisor.habilitado_facturacion)
        self.assertTrue(self.software.set_pruebas_aceptado)

    def test_devuelve_los_ids_de_los_dos_documentos_aceptados(self):
        ids = self.emitir()
        self.assertEqual(set(ids), {"factura", "nota_credito"})
        for documento in self.documentos(ids).values():
            self.assertEqual(documento.estado.nombre, DocumentoEstado.Nombre.ACEPTADO)
            self.assertTrue(documento.xml_archivo)
            self.assertEqual(len(documento.cufe_cude), 96)

    def test_los_documentos_quedan_registrados_con_sus_lineas(self):
        self.emitir()
        self.assertEqual(Documento.objects.count(), 2)
        self.assertEqual(Adquiriente.objects.count(), 2)
        self.assertEqual(DocumentoDetalle.objects.count(), 2)
        self.assertEqual(DocumentoDetalleImpuesto.objects.count(), 2)

    def test_la_nota_referencia_a_la_factura(self):
        ids = self.emitir()
        docs = self.documentos(ids)
        self.assertEqual(
            docs["nota_credito"].documento_referencia_id, docs["factura"].pk
        )

    def test_el_ambiente_de_los_documentos_es_2(self):
        self.emitir()
        for documento in Documento.objects.all():
            self.assertEqual(documento.ambiente, Documento.Ambiente.PRUEBAS)

    def test_el_adquiriente_hereda_correo_y_responsabilidades_del_emisor(self):
        """CAK55 y CAK26: sin correo la DIAN notifica; sin responsabilidad, rechaza."""
        self.emisor.responsabilidades.set(
            [self.cat["responsabilidad"]] if self.cat.get("responsabilidad") else []
        )
        self.emitir()
        adquiriente = Adquiriente.objects.first()
        self.assertEqual(adquiriente.correo, self.emisor.correo)
        self.assertEqual(
            list(adquiriente.responsabilidades.all()),
            list(self.emisor.responsabilidades.all()),
        )

    # --- Rechazos y silencios ----------------------------------------------

    def test_si_rechazan_la_factura_no_se_envia_la_nota(self):
        self.cliente.veredictos = [RECHAZADO]

        with self.assertRaises(servicios.ErrorEmision) as ctx:
            self.emitir()

        self.assertIn("CBG04a", str(ctx.exception))
        self.assertEqual(len(self.cliente.envios), 1)  # solo la factura
        self.assertFalse(Documento.objects.filter(
            documento_tipo__codigo="nota_credito"
        ).exists())

    def test_si_rechazan_la_nota_tambien_falla(self):
        self.cliente.veredictos = [ACEPTADO, RECHAZADO]

        with self.assertRaises(servicios.ErrorEmision):
            self.emitir()

        self.assertEqual(len(self.cliente.envios), 2)

    def test_un_rechazo_no_habilita_al_emisor(self):
        self.cliente.veredictos = [RECHAZADO]
        with self.assertRaises(servicios.ErrorEmision):
            self.emitir()

        self.emisor.refresh_from_db()
        self.software.refresh_from_db()
        self.assertFalse(self.emisor.habilitado_facturacion)
        self.assertFalse(self.software.set_pruebas_aceptado)

    @override_settings(DIAN_SET_PRUEBAS_INTENTOS=3, DIAN_SET_PRUEBAS_ESPERA=0)
    def test_si_la_dian_nunca_responde_se_agotan_los_intentos(self):
        self.cliente.veredictos = [PENDIENTE] * 10

        with self.assertRaises(servicios.ErrorEmision) as ctx:
            self.emitir()

        self.assertIn("3 consultas", str(ctx.exception))
        self.assertEqual(len(self.cliente.consultas), 3)

    # --- Numeración y resolución -------------------------------------------

    def test_numera_con_la_resolucion_de_pruebas(self):
        docs = self.documentos(self.emitir())
        self.assertEqual(docs["factura"].numero, "SETP990000000")
        self.assertEqual(docs["nota_credito"].numero, "990000001")

    def test_la_resolucion_de_pruebas_queda_guardada(self):
        self.emitir()
        resolucion = ResolucionFacturacion.objects.get()
        self.assertEqual(resolucion.emisor, self.emisor)
        self.assertEqual(resolucion.prefijo, "SETP")

    def test_si_el_emisor_ya_tiene_esa_resolucion_no_se_duplica(self):
        propia = ResolucionFacturacion.objects.create(
            emisor=self.emisor,
            tipo_factura=TipoFactura.objects.get(codigo="01"),
            **servicios.RESOLUCION_PRUEBAS,
        )
        self.emitir()
        self.assertEqual(ResolucionFacturacion.objects.count(), 1)
        propia.refresh_from_db()
        self.assertEqual(propia.consecutivo_actual, 0)

    def test_un_segundo_intento_continua_la_numeracion(self):
        self.documentos(self.emitir())
        # Una segunda habilitación pasa por un software nuevo, que todavía no
        # tiene aceptado el set (si no, los envíos irían ya por SendBillSync).
        self.software.set_pruebas_aceptado = False
        self.software.save(update_fields=["set_pruebas_aceptado"])
        self.cliente = ClienteFalso()
        segundo = self.documentos(self.emitir())
        self.assertEqual(segundo["factura"].numero, "SETP990000002")
        self.assertEqual(segundo["nota_credito"].numero, "990000003")

    def test_se_puede_forzar_el_consecutivo(self):
        docs = self.documentos(self.emitir(consecutivo=990000500))
        self.assertEqual(docs["factura"].numero, "SETP990000500")
        self.assertEqual(docs["nota_credito"].numero, "990000501")

    def test_forzar_un_consecutivo_ya_usado_se_rechaza_antes_de_enviar(self):
        self.emitir()
        self.software.set_pruebas_aceptado = False
        self.software.save(update_fields=["set_pruebas_aceptado"])
        self.cliente = ClienteFalso()
        with self.assertRaises(servicios.ErrorEmision) as ctx:
            self.emitir(consecutivo=990000000)
        self.assertIn("990000000", str(ctx.exception))
        self.assertEqual(self.cliente.envios, [])

    def test_un_consecutivo_fuera_del_rango_se_rechaza(self):
        with self.assertRaises(servicios.ErrorEmision):
            self.emitir(consecutivo=999999999)
        self.assertEqual(self.cliente.envios, [])

    # --- Guardas previas ----------------------------------------------------

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
        self.assertEqual(self.cliente.envios, [])
