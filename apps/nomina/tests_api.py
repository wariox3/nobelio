"""El ciclo de vida de una nómina por la API: emitir (firma y envía) y consultar.

No hay red: el envío y la consulta usan un cliente SOAP falso, igual que en
`apps.dian.tests_servicios`. Lo que se prueba es que la vista encadene bien el
pipeline y que el estado acabe donde debe, no que la DIAN conteste.
"""
from unittest.mock import Mock, patch

import requests
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.catalogos import memoria as memoria_de_catalogos
from apps.dian import soap
from apps.dian.tests_firma import _generar_certificado
from apps.documentos.models import DocumentoEstado
from apps.emisores.models import Certificado
from apps.nomina.models import Nomina
from apps.nomina.serializers import NominaCrearSerializer, NominaSerializer
from apps.nomina.serializers.empleado import EmpleadoAnidadoSerializer
from apps.nomina.serializers.nomina_concepto import NominaConceptoSerializer
from apps.nomina.tests_utils import crear_emisor_de_nomina, crear_nomina
from apps.nucleo.serializers import (
    CODIGO_CAMPO_DESCONOCIDO,
    CODIGO_OBLIGATORIO,
    MENSAJE_CAMPO_DESCONOCIDO,
)
from apps.nucleo.tests_utils import codigos, errores_por_campo


# El parcheo apunta a `servicios_nomina` y no a `servicios`: el pipeline de
# nómina vive en su propio módulo desde que se partió el de 1.069 líneas, y
# `mock.patch` sustituye el nombre **donde se busca**, no donde se define.
class ClienteNominaFalso:
    """El cliente SOAP de nómina, sin red. Registra con qué se le llamó."""

    def __init__(self, respuesta):
        self.respuesta = respuesta
        self.llamadas = []

    def enviar_set_pruebas(self, xml, nombre, test_set_id, nombre_zip=None):
        self.llamadas.append(("set_pruebas", nombre, test_set_id, nombre_zip))
        return self.respuesta

    def enviar_nomina_sincrono(self, xml, nombre):
        self.llamadas.append(("sincrono", nombre))
        return self.respuesta

    def consultar_estado(self, clave):
        self.llamadas.append(("estado", clave))
        return self.respuesta

    def consultar_estado_zip(self, clave):
        self.llamadas.append(("estado_zip", clave))
        return self.respuesta


def _respuesta(*, es_valido=True, errores=(), track_id="ZIPKEY-1"):
    return soap.RespuestaDian(
        es_valido=es_valido,
        codigo_estado="00" if es_valido else "99",
        descripcion_estado="Procesado Correctamente" if es_valido else "Rechazo",
        errores=list(errores),
        track_id=track_id,
        xml_crudo="<ApplicationResponse/>",
    )


class NominaAPIBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.base = crear_emisor_de_nomina()
        cls.emisor = cls.base["emisor"]
        cls.nomina, _ = crear_nomina(cls.base)

        # Un .p12 de verdad, para que `emitir` firme de verdad.
        llave, cert = _generar_certificado()
        p12 = pkcs12.serialize_key_and_certificates(
            b"alias", llave, cert, None, BestAvailableEncryption(b"clave123")
        )
        Certificado.objects.filter(emisor=cls.emisor).delete()
        certificado = Certificado(emisor=cls.emisor, clave="clave123", alias="test")
        certificado.archivo.save("test.p12", ContentFile(p12), save=True)

        cls.usuario = get_user_model().objects.create_user(
            email="nomina@example.com", password="x"
        )
        cls.usuario.emisores.add(cls.emisor)

    def setUp(self):
        self.client.force_authenticate(self.usuario)

    def _url(self, sufijo="", nomina=None):
        return f"/api/nomina/nomina/{(nomina or self.nomina).id}/{sufijo}"

    def _emitir(self, cliente=None, nomina=None):
        """`emitir/` con la DIAN simulada: por defecto, acepta."""
        cliente = cliente or ClienteNominaFalso(_respuesta())
        with patch("apps.dian.servicios_nomina.construir_cliente_emisor", return_value=cliente):
            return self.client.post(self._url("emitir/", nomina))


class NominaCicloTests(NominaAPIBase):
    def test_la_nomina_no_se_edita(self):
        """Ni `PUT` ni `PATCH`, igual que en documentos.

        Lo que corrige una nómina ya emitida es su **nota de ajuste**
        (`tipo_xml` 103), que es el mecanismo que la DIAN tiene previsto; un
        borrador se corrige borrándolo y volviéndolo a crear.

        Se fija aquí porque el ViewSet era un `ModelViewSet` y nadie lo cubría:
        el día que alguien lo vuelva a serlo, esta prueba lo dice.
        """
        for metodo in (self.client.put, self.client.patch):
            with self.subTest(metodo=metodo.__name__):
                resp = metodo(self._url(), {"consecutivo": 99}, format="json")
                self.assertEqual(
                    resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED, resp.data
                )

    def test_emitir_firma_y_envia_en_una_llamada(self):
        cliente = ClienteNominaFalso(_respuesta())
        resp = self._emitir(cliente)

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data["estado"], DocumentoEstado.Nombre.ACEPTADO)
        self.assertEqual(len(resp.data["cune"]), 96)
        self.assertEqual(resp.data["track_id"], "ZIPKEY-1")
        self.assertTrue(resp.data["es_valido"])
        self.assertEqual(len(cliente.llamadas), 1)

        self.nomina.refresh_from_db()
        self.assertTrue(self.nomina.xml_archivo)
        self.assertIsNotNone(self.nomina.fecha_validacion)
        # El ambiente queda sellado en la nómina: lo que entra en el CUNE tiene
        # que ser lo mismo que después decide a qué servidor se envía.
        self.assertEqual(self.nomina.ambiente, 2)

    def test_si_el_envio_falla_queda_firmada_y_el_reintento_manda_el_mismo_cune(self):
        """La firma se confirma antes de enviar.

        Si se deshiciera con el fallo, el reintento firmaría con otra
        ``HoraGen`` y otro CUNE para el mismo número.
        """
        caido = Mock()
        caido.enviar_set_pruebas.side_effect = requests.ConnectionError("sin red")
        caido.enviar_nomina_sincrono.side_effect = requests.ConnectionError("sin red")
        fallo = self._emitir(caido)

        self.assertEqual(fallo.status_code, status.HTTP_502_BAD_GATEWAY, fallo.data)
        self.nomina.refresh_from_db()
        self.assertEqual(self.nomina.estado.nombre, DocumentoEstado.Nombre.FIRMADO)
        cune = self.nomina.cune
        self.assertEqual(len(cune), 96)

        reintento = self._emitir()
        self.assertEqual(reintento.status_code, status.HTTP_200_OK, reintento.data)
        self.assertEqual(reintento.data["estado"], DocumentoEstado.Nombre.ACEPTADO)
        self.assertEqual(reintento.data["cune"], cune)

    def test_no_se_emite_lo_aceptado_ni_lo_rechazado(self):
        """Estados finales: 400 sin llamar a la DIAN. La rechazada se lee con `consultar/`."""
        for estado in (
            DocumentoEstado.Nombre.ACEPTADO,
            DocumentoEstado.Nombre.RECHAZADO,
        ):
            with self.subTest(estado=estado):
                self.nomina.estado = DocumentoEstado.objects.get(nombre=estado)
                self.nomina.save(update_fields=["estado"])
                cliente = ClienteNominaFalso(_respuesta())

                resp = self._emitir(cliente)

                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
                self.assertEqual(cliente.llamadas, [])

    def test_enviar_ya_no_existe(self):
        """Firmar y enviar es una sola acción: `emitir/`."""
        resp = self.client.post(self._url("enviar/"))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_en_habilitacion_sale_por_el_set_de_pruebas(self):
        """Con el Set sin aceptar y ambiente 2, va por ``SendTestSetAsync``.

        Y queda anotado en ``envio``, que es lo que decide cómo se consulta
        después: el Set de Pruebas es asíncrono y se pregunta por ZipKey.
        """
        cliente = ClienteNominaFalso(_respuesta())
        self._emitir(cliente)

        self.assertEqual(cliente.llamadas[0][0], "set_pruebas")
        self.assertEqual(cliente.llamadas[0][2], "set-de-pruebas-nomina")
        self.nomina.refresh_from_db()
        self.assertEqual(self.nomina.envio, Nomina.Envio.SET_PRUEBAS)

    def test_con_el_set_aceptado_sale_por_el_sincrono(self):
        self.base["software"].set_pruebas_aceptado = True
        self.base["software"].save(update_fields=["set_pruebas_aceptado"])
        cliente = ClienteNominaFalso(_respuesta())
        self._emitir(cliente)

        self.assertEqual(cliente.llamadas[0][0], "sincrono")
        self.nomina.refresh_from_db()
        self.assertEqual(self.nomina.envio, Nomina.Envio.SINCRONO)

    def test_el_rechazo_guarda_los_errores(self):
        cliente = ClienteNominaFalso(_respuesta(
            es_valido=False,
            errores=["Regla: NIE001, Rechazo: El CUNE no corresponde."],
        ))
        resp = self._emitir(cliente)

        self.assertFalse(resp.data["es_valido"])
        self.nomina.refresh_from_db()
        self.assertEqual(self.nomina.estado.nombre, DocumentoEstado.Nombre.RECHAZADO)
        error = self.nomina.errores.get()
        self.assertEqual(error.regla, "NIE001")

    def test_emitir_una_enviada_consulta_y_aplica_sin_reenviar(self):
        """El envío asíncrono solo devuelve un ZipKey; el veredicto llega al volver a emitir.

        Sin esto una nómina rechazada se quedaría en ``enviado`` y con cero
        errores para siempre.
        """
        # Envío al Set de Pruebas: sin veredicto (ni válido ni con errores).
        primera = self._emitir(ClienteNominaFalso(_respuesta(es_valido=False, errores=[])))
        self.assertEqual(primera.data["estado"], DocumentoEstado.Nombre.ENVIADO, primera.data)
        self.assertEqual(primera.data["accion"], "enviado")

        cliente = ClienteNominaFalso(_respuesta())
        resp = self._emitir(cliente)

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data["estado"], DocumentoEstado.Nombre.ACEPTADO)
        self.assertEqual(resp.data["accion"], "consultado")
        self.assertIsNotNone(resp.data["fecha_validacion"])
        # Salió por el Set de Pruebas: se pregunta por el ZipKey, y no se reenvía.
        self.assertEqual([llamada[0] for llamada in cliente.llamadas], ["estado_zip"])

    def test_los_eventos_siguen_los_cambios_de_estado(self):
        """Firmada y enviada sin veredicto; la consulta sin cambio no deja nada."""
        self._emitir(ClienteNominaFalso(_respuesta(es_valido=False, errores=[])))
        self._emitir(ClienteNominaFalso(_respuesta(es_valido=False, errores=[])))
        self.assertEqual(
            list(self.nomina.eventos.values_list("tipo", flat=True)), ["firmado", "enviado"],
        )

        self._emitir(ClienteNominaFalso(_respuesta()))

        resp = self.client.get("/api/nomina/nomina-evento/", {"nomina": self.nomina.id})
        self.nomina.refresh_from_db()
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        eventos = resp.data["results"]
        self.assertEqual([e["tipo"] for e in eventos], ["firmado", "enviado", "validado"])
        self.assertEqual(eventos[0]["datos"]["cune"], self.nomina.cune)
        self.assertEqual(eventos[2]["datos"]["origen"], "consulta")
        # No se consultan desde la nómina.
        self.assertEqual(
            self.client.get(self._url("eventos/")).status_code, status.HTTP_404_NOT_FOUND,
        )

    def test_documento_y_nomina_tienen_los_mismos_tipos(self):
        """Los registra el mismo servicio, con el mismo mapa de estado a evento."""
        from apps.documentos.models import DocumentoEvento
        from apps.nomina.models import NominaEvento

        self.assertEqual(DocumentoEvento.Tipo.choices, NominaEvento.Tipo.choices)

    def test_el_rechazo_de_la_nomina_guarda_las_reglas(self):
        errores = ["Regla: NIE001, Rechazo: El CUNE no corresponde."]
        self._emitir(ClienteNominaFalso(_respuesta(es_valido=False, errores=errores)))

        evento = self.nomina.eventos.last()
        self.assertEqual(evento.tipo, "rechazado")
        self.assertEqual(evento.datos["errores"], errores)

    def test_consultar_solo_lee(self):
        """Antes aplicaba el resultado; ahora eso es de `emitir/`, y el POST ya no existe."""
        self._emitir(ClienteNominaFalso(_respuesta(es_valido=False, errores=[])))
        cliente = ClienteNominaFalso(_respuesta())
        with patch("apps.dian.servicios_nomina.construir_cliente_emisor", return_value=cliente):
            resp = self.client.get(self._url("consultar/"))
            post = self.client.post(self._url("consultar/"))

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertTrue(resp.data["es_valido"])
        self.nomina.refresh_from_db()
        self.assertEqual(self.nomina.estado.nombre, DocumentoEstado.Nombre.ENVIADO)
        self.assertEqual(post.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class NominaAlcanceTests(NominaAPIBase):
    """Una cuenta no ve las nóminas de otra."""

    def test_la_nomina_de_otro_emisor_no_existe(self):
        otra_base = crear_emisor_de_nomina(self.base["catalogos"], nit="800199436")
        ajena, _ = crear_nomina(otra_base)

        resp = self.client.get(self._url(nomina=ajena))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_el_listado_solo_trae_las_suyas(self):
        otra_base = crear_emisor_de_nomina(self.base["catalogos"], nit="800199436")
        crear_nomina(otra_base)

        resp = self.client.get("/api/nomina/nomina/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        emisores = {fila["emisor"] for fila in resp.data["results"]}
        self.assertEqual(emisores, {self.emisor.id})


class NominaEstructuraTests(NominaAPIBase):
    def test_la_estructura_se_rechaza_en_todos_los_niveles(self):
        """En la nómina un campo perdido no falla: hereda el valor del maestro.

        Un `sueldo` mal escrito en la nómina o en el empleado haría firmar con
        el sueldo que ya tuviera guardado el trabajador. Lo que sobra sale junto
        con los obligatorios que faltan, y nada más: el `grupo` inventado es un
        error de datos y no se informa hasta que la estructura esté bien.
        """
        resp = self.client.post("/api/nomina/nomina/", {
            "emisor": self.emisor.id,
            "ambiente": 2,
            "sueldoo": "1500000",
            "empleado": {"numero_documento": "123", "sueldoo": "1500000"},
            "conceptos": [{"grupo": "no-existe", "valorr": "1500000"}],
        }, format="json")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        errores = errores_por_campo(resp)
        self.assertEqual(errores["sueldoo"], [MENSAJE_CAMPO_DESCONOCIDO])
        self.assertEqual(errores["empleado.sueldoo"], [MENSAJE_CAMPO_DESCONOCIDO])
        self.assertEqual(errores["conceptos[0].valorr"], [MENSAJE_CAMPO_DESCONOCIDO])
        self.assertEqual(
            set(codigos(resp)), {CODIGO_CAMPO_DESCONOCIDO, CODIGO_OBLIGATORIO}
        )


class NominaCreacionTests(NominaAPIBase):
    """Crear una nómina por la API, con su empleado y sus conceptos."""

    def _payload(self, consecutivo):
        """La nómina del fixture, tal como la mandaría el ERP.

        Se arma desde los serializers de escritura para no copiar a mano la
        lista de campos: lo que sea de solo lectura se queda fuera.
        """
        def escribibles(serializer):
            return {n for n, campo in serializer.fields.items() if not campo.read_only}

        empleado = EmpleadoAnidadoSerializer(self.nomina.empleado)
        conceptos = NominaConceptoSerializer(self.nomina.conceptos.all(), many=True)
        crear = NominaCrearSerializer()
        datos = {
            campo: valor
            for campo, valor in NominaSerializer(self.nomina).data.items()
            if campo in escribibles(crear) and valor is not None
        }
        datos.update({
            "consecutivo": consecutivo,
            "empleado": {
                k: v for k, v in empleado.data.items()
                if k in escribibles(EmpleadoAnidadoSerializer()) and v is not None
            },
            "conceptos": [
                {
                    k: v for k, v in concepto.items()
                    if k in escribibles(NominaConceptoSerializer()) and v is not None
                }
                for concepto in conceptos.data
            ],
        })
        return datos

    def test_crea_la_nomina_con_su_empleado_y_sus_conceptos(self):
        resp = self.client.post("/api/nomina/nomina/", self._payload(900), format="json")

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        creada = Nomina.objects.get(pk=resp.data["id"])
        self.assertEqual(creada.empleado, self.nomina.empleado)
        self.assertEqual(creada.conceptos.count(), self.nomina.conceptos.count())
        # Sale del tipo con el que se buscó al empleado, sin releerlo.
        self.assertEqual(
            resp.data["empleado"]["tipo_identificacion_codigo"],
            self.nomina.empleado.tipo_identificacion.codigo,
        )

    def test_la_nomina_normal_no_toca_la_resolucion_de_nomina(self):
        """Solo las de prueba siguen la resolución: la normal trae su número."""
        from apps.catalogos.models import TipoFactura
        from apps.emisores.models import Resolucion

        tipo, _ = TipoFactura.objects.get_or_create(
            codigo="01", defaults={"id": 1, "nombre": "Factura electrónica de Venta"},
        )
        resolucion = Resolucion.objects.create(
            emisor=self.emisor, tipo_factura=tipo, modulo=Resolucion.Modulo.NOMINA,
            prefijo=self.nomina.prefijo, numero_resolucion="1", fecha_resolucion="2026-01-01",
            rango_desde=50, rango_hasta=5000, vigente_desde="2026-01-01",
            vigente_hasta="2030-01-01", ambiente=2,
        )

        resp = self.client.post("/api/nomina/nomina/", self._payload(900), format="json")

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        resolucion.refresh_from_db()
        self.assertEqual(resolucion.consecutivo_actual, 50)

    def test_el_ambiente_es_obligatorio(self):
        payload = self._payload(901)
        del payload["ambiente"]

        resp = self.client.post("/api/nomina/nomina/", payload, format="json")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(Nomina.objects.count(), 1)

    def test_fuera_de_prod_no_crea_con_ambiente_1(self):
        payload = {**self._payload(902), "ambiente": 1}

        resp = self.client.post("/api/nomina/nomina/", payload, format="json")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(Nomina.objects.count(), 1)

    @override_settings(ENV="prod")
    def test_en_prod_el_ambiente_1_exige_estar_habilitado_para_nomina(self):
        payload = {**self._payload(903), "ambiente": 1}

        resp = self.client.post("/api/nomina/nomina/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)

        self.emisor.habilitado_nomina = True
        self.emisor.save(update_fields=["habilitado_nomina"])
        resp = self.client.post("/api/nomina/nomina/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(Nomina.objects.get(pk=resp.data["id"]).ambiente, 1)

    @override_settings(CATALOGOS_EN_MEMORIA_SEGUNDOS=300)
    def test_con_los_catalogos_en_memoria_crear_no_los_consulta(self):
        """Tras la primera creación del proceso, ningún catálogo va a la base.

        En la nómina pesa más que en la factura: las condiciones del trabajador
        repiten los catálogos del empleado en otros campos, y eran diecinueve
        consultas.
        """
        memoria_de_catalogos.olvidar()
        self.addCleanup(memoria_de_catalogos.olvidar)
        primera = self.client.post("/api/nomina/nomina/", self._payload(901), format="json")
        self.assertEqual(primera.status_code, status.HTTP_201_CREATED, primera.data)

        payload = self._payload(902)
        with CaptureQueriesContext(connection) as capturadas:
            segunda = self.client.post("/api/nomina/nomina/", payload, format="json")
        self.assertEqual(segunda.status_code, status.HTTP_201_CREATED, segunda.data)
        lecturas = [
            q["sql"] for q in capturadas.captured_queries if ' FROM "cat_' in q["sql"]
        ]
        self.assertEqual(lecturas, [], "\n\n".join(lecturas))


def _certificado_real(emisor):
    """Un .p12 de verdad para ``emisor``, para que la firma firme de verdad."""
    llave, cert = _generar_certificado()
    p12 = pkcs12.serialize_key_and_certificates(
        b"alias", llave, cert, None, BestAvailableEncryption(b"clave123")
    )
    Certificado.objects.filter(emisor=emisor).delete()
    certificado = Certificado(emisor=emisor, clave="clave123", alias="test")
    certificado.archivo.save("test.p12", ContentFile(p12), save=True)


class NominaLoteTests(NominaAPIBase):
    """`emitir-pendientes/` y `actualizar-estado/`: de toda la plataforma, solo
    con una llave de alcance global."""

    URL_EMITIR = "/api/nomina/nomina/emitir-pendientes/"
    URL_ACTUALIZAR = "/api/nomina/nomina/actualizar-estado/"

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.documentos.tests_utils import crear_usuario
        from apps.seguridad.models import LlaveApi

        self.staff = crear_usuario(nombre="Técnico", email="tecnico@x.co", is_staff=True)
        _, clave = LlaveApi.generar(
            usuario=self.staff, nombre="Procesos", alcance_global=True,
            expira_en=timezone.now() + timedelta(days=30),
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {clave}")
        # Una segunda nómina de otro emisor: los procesos no son por emisor.
        self.otra_base = crear_emisor_de_nomina(self.base["catalogos"], nit="800199436")
        _certificado_real(self.otra_base["emisor"])
        self.otra, _ = crear_nomina(self.otra_base)

    def _post(self, url, cliente):
        with patch("apps.dian.servicios_nomina.construir_cliente_emisor", return_value=cliente):
            return self.client.post(url)

    def _estado(self, nomina):
        nomina.refresh_from_db()
        return nomina.estado.nombre

    def _enviar_sin_veredicto(self):
        """Las dos nóminas en `enviado`, como las deja el Set de Pruebas."""
        resp = self._post(self.URL_EMITIR, ClienteNominaFalso(_respuesta(es_valido=False)))
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(self._estado(self.nomina), DocumentoEstado.Nombre.ENVIADO)
        self.assertEqual(self._estado(self.otra), DocumentoEstado.Nombre.ENVIADO)

    # --- Quién puede ----------------------------------------------------------

    def test_solo_una_llave_global(self):
        from apps.seguridad.models import LlaveApi

        _, normal = LlaveApi.generar(usuario=self.usuario, nombre="ERP")
        casos = {
            "llave normal": {"credenciales": f"Api-Key {normal}"},
            "usuario del emisor": {"usuario": self.usuario},
            "staff": {"usuario": self.staff},
        }
        for nombre, caso in casos.items():
            for url in (self.URL_EMITIR, self.URL_ACTUALIZAR):
                with self.subTest(quien=nombre, url=url):
                    cliente = APIClient()
                    if "usuario" in caso:
                        cliente.force_authenticate(caso["usuario"])
                    else:
                        cliente.credentials(HTTP_AUTHORIZATION=caso["credenciales"])
                    with patch("apps.dian.servicios_nomina.construir_cliente_emisor") as dian:
                        resp = cliente.post(url)
                    self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, resp.data)
                    dian.assert_not_called()
        self.assertEqual(self._estado(self.nomina), DocumentoEstado.Nombre.BORRADOR)

    # --- emitir-pendientes ----------------------------------------------------

    def test_emite_los_borradores_de_todos_los_emisores(self):
        resp = self._post(self.URL_EMITIR, ClienteNominaFalso(_respuesta()))

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data["emitidas"], 2)
        self.assertEqual(resp.data["pendientes"], 0)
        self.assertEqual(
            {(str(r["id"]), r["emisor"], r["estado"], r["error"]) for r in resp.data["resultados"]},
            {
                (str(self.nomina.id), self.emisor.id, "aceptado", ""),
                (str(self.otra.id), self.otra_base["emisor"].id, "aceptado", ""),
            },
        )
        self.assertTrue(all(len(r["cune"]) == 96 for r in resp.data["resultados"]))

    def test_reenvia_la_firmada_con_el_mismo_cune(self):
        caido = Mock()
        caido.enviar_set_pruebas.side_effect = requests.ConnectionError("sin red")
        primero = self._post(self.URL_EMITIR, caido)
        self.assertEqual(primero.data["emitidas"], 2)
        self.assertTrue(all("sin red" in r["error"] for r in primero.data["resultados"]))
        self.assertEqual(primero.data["pendientes"], 2)
        self.assertEqual(self._estado(self.nomina), DocumentoEstado.Nombre.FIRMADO)
        cune = self.nomina.cune

        segundo = self._post(self.URL_EMITIR, ClienteNominaFalso(_respuesta()))

        self.assertEqual(segundo.data["pendientes"], 0)
        self.assertEqual(self._estado(self.nomina), DocumentoEstado.Nombre.ACEPTADO)
        self.assertEqual(self.nomina.cune, cune)

    def test_un_fallo_no_corta_las_demas(self):
        # La del primer emisor no se puede firmar: el emisor está dado de baja.
        type(self.emisor).objects.filter(pk=self.emisor.pk).update(activo=False)

        resp = self._post(self.URL_EMITIR, ClienteNominaFalso(_respuesta()))

        por_id = {str(r["id"]): r for r in resp.data["resultados"]}
        self.assertTrue(por_id[str(self.nomina.id)]["error"])
        self.assertEqual(por_id[str(self.nomina.id)]["estado"], DocumentoEstado.Nombre.BORRADOR)
        self.assertEqual(por_id[str(self.otra.id)]["estado"], DocumentoEstado.Nombre.ACEPTADO)
        self.assertEqual(resp.data["pendientes"], 1)

    def test_respeta_el_tope_por_llamada(self):
        with patch("apps.nomina.views.nomina.LIMITE_EMITIR_PENDIENTES", 1):
            resp = self._post(self.URL_EMITIR, ClienteNominaFalso(_respuesta()))

        self.assertEqual(resp.data["emitidas"], 1)
        self.assertEqual(resp.data["pendientes"], 1)
        # La más vieja primero.
        self.assertEqual(str(resp.data["resultados"][0]["id"]), str(self.nomina.id))

    def test_no_toca_lo_enviado_ni_lo_final(self):
        self._enviar_sin_veredicto()
        cliente = ClienteNominaFalso(_respuesta())

        resp = self._post(self.URL_EMITIR, cliente)

        self.assertEqual(resp.data, {"emitidas": 0, "pendientes": 0, "resultados": []})
        self.assertEqual(cliente.llamadas, [])

    def test_la_nota_espera_a_que_su_nomina_este_aceptada(self):
        from apps.nomina.servicios import crear_nota_ajuste

        nota = crear_nota_ajuste(
            self.nomina, tipo_nota=Nomina.TipoNota.REEMPLAZAR, exigir_cune=False,
        )
        self._enviar_sin_veredicto()
        self.assertEqual(self._estado(nota), DocumentoEstado.Nombre.BORRADOR)

        # Su nómina sigue en `enviado`: la nota no se firma.
        resp = self._post(self.URL_EMITIR, ClienteNominaFalso(_respuesta()))
        resultado = resp.data["resultados"][0]
        self.assertEqual(str(resultado["id"]), str(nota.id))
        self.assertIn("todavía no está aceptada", resultado["error"])
        self.assertEqual(self._estado(nota), DocumentoEstado.Nombre.BORRADOR)

        # Aceptada la nómina, la nota sale en la llamada siguiente.
        self._post(self.URL_ACTUALIZAR, ClienteNominaFalso(_respuesta()))
        self._post(self.URL_EMITIR, ClienteNominaFalso(_respuesta()))
        self.assertEqual(self._estado(nota), DocumentoEstado.Nombre.ACEPTADO)

    # --- actualizar-estado ----------------------------------------------------

    def test_actualiza_las_enviadas_de_todos_los_emisores(self):
        self._enviar_sin_veredicto()
        cliente = ClienteNominaFalso(_respuesta())

        resp = self._post(self.URL_ACTUALIZAR, cliente)

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data["consultadas"], 2)
        self.assertEqual(resp.data["pendientes"], 0)
        self.assertEqual({r["estado"] for r in resp.data["resultados"]}, {"aceptado"})
        self.assertEqual(
            {r["emisor"] for r in resp.data["resultados"]},
            {self.emisor.id, self.otra_base["emisor"].id},
        )
        # Salieron al Set de Pruebas: se pregunta por el ZipKey, sin reenviar.
        self.assertEqual({llamada[0] for llamada in cliente.llamadas}, {"estado_zip"})

    def test_aplica_el_rechazo_y_deja_lo_que_sigue_sin_veredicto(self):
        self._enviar_sin_veredicto()
        errores = ["Regla: NIE001, Rechazo: El CUNE no corresponde."]

        rechazo = self._post(
            self.URL_ACTUALIZAR, ClienteNominaFalso(_respuesta(es_valido=False, errores=errores)),
        )

        self.assertEqual({r["estado"] for r in rechazo.data["resultados"]}, {"rechazado"})
        self.assertEqual(rechazo.data["resultados"][0]["errores"], errores)
        self.assertEqual(rechazo.data["pendientes"], 0)

    def test_sin_veredicto_siguen_pendientes(self):
        self._enviar_sin_veredicto()

        resp = self._post(self.URL_ACTUALIZAR, ClienteNominaFalso(_respuesta(es_valido=False)))

        self.assertEqual(resp.data["consultadas"], 2)
        self.assertEqual(resp.data["pendientes"], 2)
        self.assertEqual({r["estado"] for r in resp.data["resultados"]}, {"enviado"})

    def test_si_la_dian_no_responde_lo_dice_y_sigue(self):
        self._enviar_sin_veredicto()
        caido = Mock()
        caido.consultar_estado_zip.side_effect = requests.ConnectionError("sin red")

        resp = self._post(self.URL_ACTUALIZAR, caido)

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data["consultadas"], 2)
        self.assertTrue(all("sin red" in r["error"] for r in resp.data["resultados"]))
        self.assertEqual(self._estado(self.nomina), DocumentoEstado.Nombre.ENVIADO)

    def test_actualizar_respeta_el_tope_y_no_toca_borradores(self):
        self._enviar_sin_veredicto()
        crear_nomina(self.base)  # un borrador: no se consulta
        with patch("apps.nomina.views.nomina.LIMITE_ACTUALIZAR_ESTADO", 1):
            resp = self._post(self.URL_ACTUALIZAR, ClienteNominaFalso(_respuesta()))

        self.assertEqual(resp.data["consultadas"], 1)
        self.assertEqual(resp.data["pendientes"], 1)


class NotaDeAjusteTests(NominaAPIBase):
    def _nota(self):
        from apps.nomina.servicios import crear_nota_ajuste

        return crear_nota_ajuste(
            self.nomina, tipo_nota=Nomina.TipoNota.REEMPLAZAR, exigir_cune=False,
        )

    def test_crear_nota_sin_cune_solo_si_se_pide(self):
        from apps.nomina.servicios import crear_nota_ajuste

        with self.assertRaisesMessage(ValueError, "todavía no tiene CUNE"):
            crear_nota_ajuste(self.nomina, tipo_nota=Nomina.TipoNota.REEMPLAZAR)
        self.assertEqual(self._nota().nomina_predecesora, self.nomina)

    def test_la_nota_no_se_firma_sin_su_nomina_aceptada(self):
        nota = self._nota()
        # Firmada y enviada, pero sin veredicto: tampoco basta.
        self._emitir(ClienteNominaFalso(_respuesta(es_valido=False)))

        resp = self._emitir(nomina=nota)

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn("todavía no está aceptada", resp.data["detail"])
        self.assertIn("'enviado'", resp.data["detail"])
        nota.refresh_from_db()
        self.assertEqual(nota.estado.nombre, DocumentoEstado.Nombre.BORRADOR)
        self.assertFalse(nota.cune)

    def test_con_su_nomina_aceptada_se_firma_con_su_cune(self):
        nota = self._nota()
        self._emitir()  # acepta la nómina
        self.nomina.refresh_from_db()

        resp = self._emitir(nomina=nota)

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        nota.refresh_from_db()
        self.assertIn(self.nomina.cune.encode(), nota.leer_xml())

    def test_una_nomina_con_notas_no_se_borra(self):
        nota = self._nota()

        resp = self.client.delete(self._url())

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn(nota.numero, resp.data["detail"])
        self.assertTrue(Nomina.objects.filter(pk=self.nomina.pk).exists())

        self.assertEqual(self.client.delete(self._url(nomina=nota)).status_code, 204)
        self.assertEqual(self.client.delete(self._url()).status_code, 204)
