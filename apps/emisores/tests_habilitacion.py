"""Pruebas de `POST /api/emisores/software/{id}/habilitar/` y de su servicio.

La DIAN se simula reemplazando `emitir` y `emitir_nomina` por un falso que
mueve el estado como lo haría ella: el borrador pasa a `enviado` (el Set de
Pruebas no da veredicto al enviar) y el enviado, al consultarse, a `aceptado`
o `rechazado`. Lo que se prueba aquí es el orden, las esperas y los cortes; la
firma y el envío de verdad tienen sus propias pruebas.
"""
from datetime import timedelta
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.catalogos.carga import cargar
from apps.catalogos.models import TipoFactura
from apps.dian import servicios as dian
from apps.documentos.models import Documento, DocumentoEstado, DocumentoTipo
from apps.documentos.tests_utils import crear_catalogos_minimos
from apps.emisores.models import Certificado, SoftwareDian
from apps.emisores.servicios import habilitacion
from apps.emisores.tests_software import _crear_emisor
from apps.nomina.models import Nomina
from apps.nomina.tests_utils import cargar_catalogos_de_nomina

BORRADOR = DocumentoEstado.Nombre.BORRADOR
ENVIADO = DocumentoEstado.Nombre.ENVIADO
ACEPTADO = DocumentoEstado.Nombre.ACEPTADO
RECHAZADO = DocumentoEstado.Nombre.RECHAZADO


def _poner(obj, nombre):
    obj.estado = DocumentoEstado.objects.get(nombre=nombre)
    obj.save(update_fields=["estado"])


class FalsaDian:
    """Hace de `emitir`: un paso adelante por llamada, como la DIAN de verdad.

    - borrador → enviado (el Set de Pruebas responde un ZipKey, sin veredicto).
    - enviado → aceptado, o rechazado si su número está en ``rechazar``.

    ``fallos`` es ``{numero: excepción}``: esa llamada la lanza en vez de
    avanzar (una sola vez). ``aceptar_set_en`` es el número cuya consulta trae
    el "Set de prueba ... Aceptado". Anota cada llamada en ``llamadas`` con el
    estado del documento que corrige, para comprobar el orden.
    """

    def __init__(self, software=None, *, rechazar=(), fallos=None, aceptar_set_en=None):
        self.software = software
        self.rechazar = set(rechazar)
        self.fallos = dict(fallos or {})
        self.aceptar_set_en = aceptar_set_en
        self.llamadas = []

    def _referencia(self, obj):
        return getattr(obj, "documento_referencia", None) or getattr(
            obj, "nomina_predecesora", None
        )

    def __call__(self, obj):
        obj = type(obj).objects.select_related("estado").get(pk=obj.pk)
        referencia = self._referencia(obj)
        self.llamadas.append((
            obj.numero, obj.estado.nombre,
            referencia.estado.nombre if referencia is not None else None,
        ))
        if obj.numero in self.fallos:
            raise self.fallos.pop(obj.numero)
        if obj.estado.nombre == BORRADOR:
            _poner(obj, ENVIADO)
        elif obj.estado.nombre == ENVIADO:
            _poner(obj, RECHAZADO if obj.numero in self.rechazar else ACEPTADO)
            if obj.numero == self.aceptar_set_en:
                self.software.set_pruebas_aceptado = True
                self.software.save(update_fields=["set_pruebas_aceptado"])
        return obj, "x", None

    def numeros_llamados(self):
        return [numero for numero, _, _ in self.llamadas]


class _Base(APITestCase):
    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.emisor = _crear_emisor(self.cat)
        self.usuario = get_user_model().objects.create_user(
            email="staff@nobelio.co", password="x"
        )
        self.usuario.emisores.add(self.emisor)
        self.client.force_authenticate(self.usuario)
        cargar([TipoFactura])
        cargar_catalogos_de_nomina()
        # Sin pausas de verdad: se cuentan.
        parche = mock.patch.object(habilitacion.time, "sleep")
        self.sleep = parche.start()
        self.addCleanup(parche.stop)

    def _registrar(self, tipo):
        resp = self.client.post("/api/emisores/software/", {
            "emisor": self.emisor.id,
            "tipo": tipo,
            "identificador": f"software-{tipo}",
            "pin": "12345",
            "test_set_id": "set-pruebas-xyz",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        return SoftwareDian.objects.get(pk=resp.data["id"])

    def _habilitar(self, software):
        return self.client.post(f"/api/emisores/software/{software.pk}/habilitar/")


class HabilitacionFacturacionTests(_Base):
    def setUp(self):
        super().setUp()
        self.software = self._registrar(SoftwareDian.Tipo.FACTURACION)
        self.facturas = list(Documento.objects.filter(
            emisor=self.emisor, documento_tipo__codigo=DocumentoTipo.Codigo.FACTURA_VENTA,
        ).order_by("consecutivo"))
        self.nota = Documento.objects.get(
            emisor=self.emisor, documento_tipo__codigo=DocumentoTipo.Codigo.NOTA_CREDITO,
        )

    def _correr(self, dian_falsa, **kw):
        with mock.patch.object(dian, "emitir", side_effect=dian_falsa):
            return habilitacion.habilitar(self.software, espera=0, **kw)

    def _estados(self):
        return {
            d.numero + ("-NC" if d.documento_tipo.codigo == "nota_credito" else ""):
            d.estado.nombre
            for d in Documento.objects.filter(emisor=self.emisor).select_related(
                "estado", "documento_tipo",
            )
        }

    def test_emite_todo_hasta_aceptado(self):
        falsa = FalsaDian(self.software)
        motivo, documentos = self._correr(falsa)

        self.assertEqual(motivo, habilitacion.SIN_PENDIENTES)
        self.assertEqual(len(documentos), 4)
        self.assertTrue(all(d["estado"] == ACEPTADO for d in documentos))
        self.assertEqual(
            [d["tipo"] for d in documentos],
            ["factura_venta"] * 3 + ["nota_credito"],
        )

    def test_la_nota_solo_se_emite_con_su_factura_aceptada(self):
        falsa = FalsaDian(self.software)
        self._correr(falsa)

        llamadas_nota = [
            ref for numero, _, ref in falsa.llamadas
            if ref is not None
        ]
        self.assertEqual(len(llamadas_nota), 2)  # firmar/enviar y consultar
        self.assertTrue(all(ref == ACEPTADO for ref in llamadas_nota))
        # Las tres facturas salen en la primera vuelta, antes que la nota.
        self.assertEqual(
            [n for n, estado, _ in falsa.llamadas[:3]],
            [f.numero for f in self.facturas],
        )

    def test_espera_entre_consultas_solo_si_hay_enviados(self):
        self._correr(FalsaDian(self.software))

        # Vuelta 1: facturas enviadas → pausa. Vuelta 2: facturas aceptadas, la
        # nota aún esperaba al empezar la vuelta → sin enviados, sin pausa.
        # Vuelta 3: nota enviada → pausa. Vuelta 4: nota aceptada.
        self.assertEqual(self.sleep.call_count, 2)

    def test_se_detiene_cuando_la_dian_acepta_el_set(self):
        primera = self.facturas[0].numero
        falsa = FalsaDian(self.software, aceptar_set_en=primera)

        motivo, _ = self._correr(falsa)

        self.assertEqual(motivo, habilitacion.SET_ACEPTADO)
        # La consulta que trajo la aceptación es la última llamada.
        self.assertEqual(falsa.llamadas[-1][0], primera)
        self.assertEqual(falsa.llamadas[-1][1], ENVIADO)

    def test_factura_rechazada_deja_la_nota_bloqueada_y_termina(self):
        anulada = self.facturas[0].numero
        falsa = FalsaDian(self.software, rechazar={anulada})

        motivo, documentos = self._correr(falsa)

        self.assertEqual(motivo, habilitacion.SIN_PENDIENTES)
        nota = next(d for d in documentos if d["tipo"] == "nota_credito")
        self.assertEqual(nota["estado"], BORRADOR)
        self.assertTrue(all(ref is None for _, _, ref in falsa.llamadas))

    def test_error_de_emision_se_anota_y_no_se_reintenta(self):
        numero = self.facturas[1].numero
        falsa = FalsaDian(self.software, fallos={
            numero: dian.ErrorEmision("Sin certificado."),
        })

        motivo, documentos = self._correr(falsa)

        self.assertEqual(motivo, habilitacion.CON_ERRORES)
        fallida = next(d for d in documentos if d["numero"] == numero and d["tipo"] == "factura_venta")
        self.assertEqual(fallida["estado"], BORRADOR)
        self.assertEqual(fallida["error"], "Sin certificado.")
        self.assertEqual(falsa.numeros_llamados().count(numero), 1)
        # Lo demás sí terminó.
        otras = [d for d in documentos if d is not fallida]
        self.assertTrue(all(d["estado"] == ACEPTADO for d in otras))

    def test_value_error_cuenta_como_error_de_emision(self):
        numero = self.facturas[0].numero
        falsa = FalsaDian(self.software, fallos={numero: ValueError("Dato inválido.")})

        motivo, documentos = self._correr(falsa)

        self.assertEqual(motivo, habilitacion.CON_ERRORES)
        self.assertIn("Dato inválido.", [d["error"] for d in documentos])

    def test_la_dian_sin_respuesta_corta_y_se_retoma_en_la_siguiente_llamada(self):
        numero = self.facturas[1].numero
        falsa = FalsaDian(self.software, fallos={
            numero: requests.ConnectionError("timeout"),
        })

        motivo, documentos = self._correr(falsa)

        self.assertEqual(motivo, habilitacion.DIAN_SIN_RESPUESTA)
        # Se cortó en la segunda: la tercera ni se intentó.
        self.assertEqual(falsa.numeros_llamados(), [f.numero for f in self.facturas[:2]])
        fallida = next(d for d in documentos if d["numero"] == numero and d["tipo"] == "factura_venta")
        self.assertIn("La DIAN no respondió", fallida["error"])

        # Se vuelve a llamar y termina, sin reenviar la que ya estaba enviada.
        motivo, documentos = self._correr(falsa)
        self.assertEqual(motivo, habilitacion.SIN_PENDIENTES)
        primera = self.facturas[0].numero
        self.assertEqual(
            [estado for n, estado, ref in falsa.llamadas if n == primera and ref is None],
            [BORRADOR, ENVIADO],
        )

    def test_sin_presupuesto_corta_por_tiempo_sin_emitir_nada(self):
        falsa = FalsaDian(self.software)

        motivo, _ = self._correr(falsa, presupuesto=0)

        self.assertEqual(motivo, habilitacion.TIEMPO_AGOTADO)
        self.assertEqual(falsa.llamadas, [])

    def test_no_espera_si_la_pausa_pasaria_del_presupuesto(self):
        falsa = FalsaDian(self.software)
        with mock.patch.object(dian, "emitir", side_effect=falsa):
            motivo, documentos = habilitacion.habilitar(
                self.software, presupuesto=5, espera=10,
            )

        self.assertEqual(motivo, habilitacion.TIEMPO_AGOTADO)
        self.sleep.assert_not_called()
        # Lo de la primera vuelta quedó enviado.
        self.assertEqual(
            sorted(d["estado"] for d in documentos),
            [BORRADOR, ENVIADO, ENVIADO, ENVIADO],
        )

    def test_refecha_el_borrador_sembrado_otro_dia(self):
        ayer = timezone.localdate() - timedelta(days=1)
        Documento.objects.filter(emisor=self.emisor).update(fecha_emision=ayer)

        self._correr(FalsaDian(self.software))

        fechas = set(
            Documento.objects.filter(emisor=self.emisor).values_list("fecha_emision", flat=True)
        )
        self.assertEqual(fechas, {timezone.localdate()})

    def test_lo_ya_terminado_no_se_toca(self):
        for factura in self.facturas:
            _poner(factura, ACEPTADO)
        _poner(self.nota, RECHAZADO)
        falsa = FalsaDian(self.software)

        motivo, _ = self._correr(falsa)

        self.assertEqual(motivo, habilitacion.SIN_PENDIENTES)
        self.assertEqual(falsa.llamadas, [])

    def test_no_toca_documentos_de_produccion(self):
        Documento.objects.filter(pk=self.facturas[2].pk).update(ambiente=1)
        falsa = FalsaDian(self.software)

        _, documentos = self._correr(falsa)

        self.assertNotIn(self.facturas[2].numero, falsa.numeros_llamados())
        self.assertEqual(len(documentos), 3)

    # --- El endpoint --------------------------------------------------------

    def test_endpoint_responde_el_resultado(self):
        with mock.patch.object(dian, "emitir", side_effect=FalsaDian(self.software)):
            resp = self._habilitar(self.software)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data["terminado"])
        self.assertEqual(resp.data["motivo"], habilitacion.SIN_PENDIENTES)
        self.assertEqual(resp.data["id"], self.software.pk)
        self.assertFalse(resp.data["set_pruebas_aceptado"])
        self.assertEqual(len(resp.data["documentos"]), 4)
        self.assertEqual(
            set(resp.data["documentos"][0]), {"id", "numero", "tipo", "estado", "error"},
        )

    def test_endpoint_marca_aceptado_cuando_la_dian_cierra_el_set(self):
        falsa = FalsaDian(self.software, aceptar_set_en=self.facturas[0].numero)
        with mock.patch.object(dian, "emitir", side_effect=falsa):
            resp = self._habilitar(self.software)

        self.assertTrue(resp.data["terminado"])
        self.assertEqual(resp.data["motivo"], habilitacion.SET_ACEPTADO)
        self.assertTrue(resp.data["set_pruebas_aceptado"])

    def test_endpoint_cortado_no_esta_terminado(self):
        falsa = FalsaDian(self.software, fallos={
            self.facturas[0].numero: requests.Timeout("lento"),
        })
        with mock.patch.object(dian, "emitir", side_effect=falsa):
            resp = self._habilitar(self.software)

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["terminado"])
        self.assertEqual(resp.data["motivo"], habilitacion.DIAN_SIN_RESPUESTA)

    def test_endpoint_400_con_el_set_ya_aceptado(self):
        self.software.set_pruebas_aceptado = True
        self.software.save(update_fields=["set_pruebas_aceptado"])

        resp = self._habilitar(self.software)

        self.assertEqual(resp.status_code, 400)
        self.assertIn("ya fue aceptado", str(resp.data))

    def test_endpoint_400_sin_certificado(self):
        Certificado.objects.filter(emisor=self.emisor).delete()

        with mock.patch.object(dian, "emitir") as emitir:
            resp = self._habilitar(self.software)

        self.assertEqual(resp.status_code, 400)
        emitir.assert_not_called()

    def test_endpoint_400_sin_documentos(self):
        Documento.objects.filter(documento_referencia__isnull=False).delete()
        Documento.objects.filter(emisor=self.emisor).delete()

        resp = self._habilitar(self.software)

        self.assertEqual(resp.status_code, 400)
        self.assertIn("no tiene documentos de pruebas", str(resp.data))

    def test_endpoint_404_fuera_del_alcance(self):
        otro = get_user_model().objects.create_user(email="otro@nobelio.co", password="x")
        self.client.force_authenticate(otro)

        resp = self._habilitar(self.software)

        self.assertEqual(resp.status_code, 404)

    def test_endpoint_400_para_documento_equivalente(self):
        software = self._registrar(SoftwareDian.Tipo.DOCUMENTO_EQUIVALENTE)

        resp = self._habilitar(software)

        self.assertEqual(resp.status_code, 400)
        self.assertIn("facturación o de nómina", str(resp.data))


class HabilitacionNominaTests(_Base):
    def setUp(self):
        super().setUp()
        self.software = self._registrar(SoftwareDian.Tipo.NOMINA)
        self.nominas = list(
            Nomina.objects.filter(emisor=self.emisor, tipo_xml=Nomina.TipoXML.NOMINA)
            .order_by("consecutivo")
        )
        self.notas = list(
            Nomina.objects.filter(emisor=self.emisor, tipo_xml=Nomina.TipoXML.AJUSTE)
            .order_by("consecutivo")
        )

    def _correr(self, dian_falsa, **kw):
        with mock.patch.object(dian, "emitir_nomina", side_effect=dian_falsa):
            return habilitacion.habilitar(self.software, espera=0, **kw)

    def test_el_alta_sembro_lo_que_se_va_a_emitir(self):
        self.assertEqual(len(self.nominas), 10)
        self.assertEqual(len(self.notas), 10)

    def test_emite_nominas_y_despues_sus_notas_hasta_aceptadas(self):
        falsa = FalsaDian(self.software)

        motivo, documentos = self._correr(falsa)

        self.assertEqual(motivo, habilitacion.SIN_PENDIENTES)
        self.assertEqual(len(documentos), 20)
        self.assertTrue(all(d["estado"] == ACEPTADO for d in documentos))
        self.assertEqual(
            [d["tipo"] for d in documentos], ["nomina"] * 10 + ["nota_ajuste"] * 10,
        )
        # Primero las diez nóminas.
        self.assertEqual(falsa.numeros_llamados()[:10], [n.numero for n in self.nominas])

    def test_cada_nota_solo_con_su_nomina_aceptada(self):
        falsa = FalsaDian(self.software)
        self._correr(falsa)

        de_notas = [ref for _, _, ref in falsa.llamadas if ref is not None]
        self.assertEqual(len(de_notas), 20)  # firmar/enviar y consultar cada una
        self.assertTrue(all(ref == ACEPTADO for ref in de_notas))

    def test_nomina_rechazada_bloquea_solo_su_nota(self):
        rechazada = self.nominas[0]
        falsa = FalsaDian(self.software, rechazar={rechazada.numero})

        motivo, _ = self._correr(falsa)

        self.assertEqual(motivo, habilitacion.SIN_PENDIENTES)
        nota_bloqueada = Nomina.objects.get(nomina_predecesora=rechazada)
        self.assertEqual(nota_bloqueada.estado.nombre, BORRADOR)
        otras = Nomina.objects.filter(
            tipo_xml=Nomina.TipoXML.AJUSTE,
        ).exclude(pk=nota_bloqueada.pk)
        self.assertEqual(
            set(otras.values_list("estado__nombre", flat=True)), {ACEPTADO},
        )

    def test_se_detiene_cuando_la_dian_acepta_el_set(self):
        falsa = FalsaDian(self.software, aceptar_set_en=self.nominas[3].numero)

        motivo, _ = self._correr(falsa)

        self.assertEqual(motivo, habilitacion.SET_ACEPTADO)
        self.assertEqual(falsa.llamadas[-1][0], self.nominas[3].numero)

    def test_la_dian_sin_respuesta_corta(self):
        falsa = FalsaDian(self.software, fallos={
            self.nominas[0].numero: requests.ConnectionError("caída"),
        })

        motivo, _ = self._correr(falsa)

        self.assertEqual(motivo, habilitacion.DIAN_SIN_RESPUESTA)
        self.assertEqual(len(falsa.llamadas), 1)

    def test_endpoint_de_nomina(self):
        with mock.patch.object(dian, "emitir_nomina", side_effect=FalsaDian(self.software)), \
                mock.patch.object(dian, "emitir") as emitir_documento:
            resp = self._habilitar(self.software)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data["terminado"])
        self.assertEqual(len(resp.data["documentos"]), 20)
        # No se cruza con los documentos de facturación.
        emitir_documento.assert_not_called()


class FirmaDeNotaSobreFacturaSinAceptarTests(_Base):
    """`generar_y_firmar` no firma una nota cuya factura no está aceptada.

    La sembrada al registrar el software nace así; firmarla daría una nota sin
    el CUFE de su factura, que la DIAN rechaza.
    """

    def setUp(self):
        super().setUp()
        self._registrar(SoftwareDian.Tipo.FACTURACION)
        self.nota = Documento.objects.get(
            emisor=self.emisor, documento_tipo__codigo=DocumentoTipo.Codigo.NOTA_CREDITO,
        )

    def test_nota_sembrada_no_se_firma_con_la_factura_en_borrador(self):
        with self.assertRaisesMessage(dian.ErrorEmision, "emítalo primero"):
            dian.generar_y_firmar(self.nota, firmador=mock.Mock())
        self.nota.refresh_from_db()
        self.assertEqual(self.nota.estado.nombre, BORRADOR)
        self.assertEqual(self.nota.cufe_cude, "")

    def test_ni_con_la_factura_rechazada(self):
        _poner(self.nota.documento_referencia, RECHAZADO)

        with self.assertRaisesMessage(dian.ErrorEmision, "Rechazado"):
            dian.generar_y_firmar(self.nota, firmador=mock.Mock())
