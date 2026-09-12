"""Pruebas de la API de software DIAN (PIN write-only, filtro por emisor)."""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.documentos.models import Documento, DocumentoEstado, DocumentoTipo
from apps.documentos.tests_utils import crear_catalogos_minimos, crear_certificado
from apps.catalogos.models import TipoFactura
from apps.emisores.models import Emisor, Resolucion, SoftwareDian
from apps.emisores.servicios import RESOLUCION_SET_PRUEBAS
from apps.nucleo.models import Ambiente


def _crear_emisor(cat, nit="901192048"):
    emisor = Emisor.objects.create(
        usuario=cat["usuario"], razon_social="Semantica Digital S.A.S",
        tipo_identificacion=cat["nit"], numero_identificacion=nit,
        digito_verificacion="8", tipo_organizacion=cat["juridica"],
        pais=cat["colombia"], departamento=cat["antioquia"], municipio=cat["medellin"],
        direccion="Calle 1 # 2-3",
    )
    # El certificado va antes que el software: sin él no se puede registrar.
    crear_certificado(emisor)
    return emisor


class SoftwareDianAPITests(APITestCase):
    def setUp(self):
        self.cat = crear_catalogos_minimos()
        self.emisor = _crear_emisor(self.cat)
        self.usuario = get_user_model().objects.create_user(
            email="staff@nobelio.co", password="x"
        )
        self.usuario.emisores.add(self.emisor)
        self.client.force_authenticate(self.usuario)
        self.url = "/api/emisores/software/"
        # `crear_catalogos_minimos` no trae tipos de factura y el sembrado de
        # la resolución de pruebas necesita el 01. En el servidor lo carga
        # `manage.py cargar_catalogos`.
        TipoFactura.objects.get_or_create(
            codigo="01", defaults={"nombre": "Factura electrónica de Venta"},
        )

    def _payload(self):
        return {
            "emisor": self.emisor.id,
            "tipo": SoftwareDian.Tipo.FACTURACION,
            "identificador": "abc123-software-id",
            "pin": "12345",
            "test_set_id": "set-pruebas-xyz",
        }

    def test_crea_software_y_devuelve_el_pin(self):
        # El PIN se devuelve a propósito (decisión del 2026-08-28, reafirmada el
        # 2026-09-02): quien puede leer el software ya está dentro del alcance
        # del emisor, y tenerlo a la vista ahorra fricción en la habilitación.
        # Esta prueba existía afirmando lo contrario, de cuando era write_only.
        resp = self.client.post(self.url, self._payload(), format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["pin"], "12345")
        creado = SoftwareDian.objects.get(pk=resp.data["id"])
        self.assertEqual(creado.pin, "12345")

    def test_el_tipo_es_obligatorio(self):
        # Sin tipo no se sabe qué operación habilita el software, y el pipeline
        # busca el software *de su tipo*: uno sin tipo no lo encontraría nunca.
        payload = self._payload()
        del payload["tipo"]
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("tipo", resp.data["errores"])

    def test_pin_es_obligatorio(self):
        payload = self._payload()
        del payload["pin"]
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("pin", resp.data["errores"])

    def test_filtra_por_emisor(self):
        SoftwareDian.objects.create(
            emisor=self.emisor, tipo=SoftwareDian.Tipo.FACTURACION,
            identificador="s1", pin="1"
        )
        otro = _crear_emisor(self.cat, nit="800197268")
        SoftwareDian.objects.create(
            emisor=otro, tipo=SoftwareDian.Tipo.FACTURACION,
            identificador="s2", pin="2"
        )
        resp = self.client.get(self.url, {"emisor": self.emisor.id})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)

    # --- Uno por emisor y operación ----------------------------------------

    def test_rechaza_un_segundo_software_del_mismo_tipo(self):
        primero = self.client.post(self.url, self._payload(), format="json")
        self.assertEqual(primero.status_code, 201, primero.data)

        payload = self._payload()
        payload["identificador"] = "otro-software-id"
        resp = self.client.post(self.url, payload, format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
        # El mensaje trae la ruta del que ya existe, para poder actualizarlo.
        self.assertIn(
            f"/api/emisores/software/{primero.data['id']}/",
            resp.data["errores"]["tipo"][0],
        )
        self.assertEqual(SoftwareDian.objects.filter(emisor=self.emisor).count(), 1)

    def test_las_tres_operaciones_conviven(self):
        """Facturación, nómina y documento equivalente son habilitaciones aparte.

        La DIAN las da por separado, cada una con su SoftwareID y su PIN, y el
        CUFE y el CUNE llevan el de su operación: el límite es uno *de cada
        tipo*, no uno por emisor.
        """
        for tipo in SoftwareDian.Tipo:
            payload = self._payload()
            payload["tipo"] = tipo
            payload["identificador"] = f"software-{tipo}"
            resp = self.client.post(self.url, payload, format="json")
            self.assertEqual(resp.status_code, 201, (tipo, resp.data))

        self.assertEqual(SoftwareDian.objects.filter(emisor=self.emisor).count(), 3)

    def test_cambiar_de_software_es_actualizar_el_que_hay(self):
        creado = self.client.post(self.url, self._payload(), format="json")

        resp = self.client.patch(
            f"{self.url}{creado.data['id']}/",
            {"identificador": "software-nuevo", "pin": "99999"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        software = SoftwareDian.objects.get(emisor=self.emisor)
        self.assertEqual(software.identificador, "software-nuevo")
        self.assertEqual(software.pin, "99999")

    def test_el_mismo_tipo_no_choca_entre_emisores_distintos(self):
        self.assertEqual(
            self.client.post(self.url, self._payload(), format="json").status_code, 201
        )
        otro = _crear_emisor(self.cat, nit="800197268")
        self.usuario.emisores.add(otro)

        payload = self._payload()
        payload["emisor"] = otro.id
        resp = self.client.post(self.url, payload, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)

    # --- La resolución del Set de Pruebas se siembra sola -------------------

    def test_crear_software_de_facturacion_siembra_la_resolucion_de_pruebas(self):
        self.assertFalse(Resolucion.objects.filter(emisor=self.emisor).exists())

        resp = self.client.post(self.url, self._payload(), format="json")
        self.assertEqual(resp.status_code, 201, resp.data)

        resolucion = Resolucion.objects.get(emisor=self.emisor)
        self.assertEqual(resolucion.prefijo, RESOLUCION_SET_PRUEBAS["prefijo"])
        self.assertEqual(
            resolucion.numero_resolucion,
            RESOLUCION_SET_PRUEBAS["numero_resolucion"],
        )
        self.assertEqual(
            resolucion.clave_tecnica, RESOLUCION_SET_PRUEBAS["clave_tecnica"]
        )
        self.assertEqual(resolucion.tipo_factura.codigo, "01")
        self.assertTrue(resolucion.activa)

    def test_no_la_siembra_si_el_emisor_ya_esta_en_produccion(self):
        """Es la resolución del sandbox: numerar con ella en producción es

        gastar consecutivos de un rango que no es del emisor. El software sí se
        registra —eso es normal en producción—; lo que no se hace es sembrar.
        """
        self.emisor.ambiente_facturacion = Ambiente.PRODUCCION
        self.emisor.save(update_fields=["ambiente_facturacion"])

        resp = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertFalse(Resolucion.objects.filter(emisor=self.emisor).exists())

    def test_el_software_de_nomina_no_siembra_resolucion(self):
        """La nómina no se numera con resolución: numera con prefijo y consecutivo."""
        payload = self._payload()
        payload["tipo"] = SoftwareDian.Tipo.NOMINA
        payload["identificador"] = "software-de-nomina"

        resp = self.client.post(self.url, payload, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertFalse(Resolucion.objects.filter(emisor=self.emisor).exists())

    def test_sembrarla_dos_veces_no_la_duplica(self):
        """Se identifica por su índice único; repetir actualiza, no añade."""
        self.client.post(self.url, self._payload(), format="json")
        otro = _crear_emisor(self.cat, nit="800197268")
        self.usuario.emisores.add(otro)
        payload = self._payload()
        payload["emisor"] = otro.id
        self.client.post(self.url, payload, format="json")

        # Una por emisor, no una compartida ni dos del mismo.
        self.assertEqual(Resolucion.objects.filter(emisor=self.emisor).count(), 1)
        self.assertEqual(Resolucion.objects.filter(emisor=otro).count(), 1)

    # --- Y con la resolución, las facturas de prueba ------------------------

    def _facturas(self, emisor=None):
        return Documento.objects.filter(
            emisor=emisor or self.emisor,
            documento_tipo__codigo=DocumentoTipo.Codigo.FACTURA_VENTA,
        ).order_by("consecutivo")

    def test_crear_software_de_facturacion_deja_dos_facturas_en_borrador(self):
        resp = self.client.post(self.url, self._payload(), format="json")
        self.assertEqual(resp.status_code, 201, resp.data)

        facturas = list(self._facturas())
        self.assertEqual(len(facturas), 2)
        resolucion = Resolucion.objects.get(emisor=self.emisor)
        # Numeradas desde el primer consecutivo que autoriza la resolución.
        self.assertEqual(
            [f.consecutivo for f in facturas],
            [resolucion.rango_desde, resolucion.rango_desde + 1],
        )
        for factura in facturas:
            self.assertEqual(factura.estado.nombre, DocumentoEstado.Nombre.BORRADOR)
            self.assertEqual(factura.resolucion_id, resolucion.id)
            self.assertEqual(factura.prefijo, resolucion.prefijo)
            # Se crean, no se emiten: sin XML y sin CUFE.
            self.assertFalse(factura.xml_archivo)

    def test_no_crea_notas_credito(self):
        """Solo facturas; la nota del Set se crea aparte, con su pareja."""
        self.client.post(self.url, self._payload(), format="json")

        self.assertFalse(
            Documento.objects.filter(
                emisor=self.emisor,
                documento_tipo__codigo=DocumentoTipo.Codigo.NOTA_CREDITO,
            ).exists()
        )

    def test_sin_resolucion_no_hay_facturas(self):
        """En producción no se siembra resolución, así que tampoco facturas."""
        self.emisor.ambiente_facturacion = Ambiente.PRODUCCION
        self.emisor.save(update_fields=["ambiente_facturacion"])

        resp = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertFalse(self._facturas().exists())

    def test_el_software_de_nomina_no_deja_facturas(self):
        payload = self._payload()
        payload["tipo"] = SoftwareDian.Tipo.NOMINA
        payload["identificador"] = "software-de-nomina"

        resp = self.client.post(self.url, payload, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertFalse(self._facturas().exists())

    def test_volver_a_registrar_el_software_no_duplica_las_facturas(self):
        """Dar de baja el software y rehacerlo pasa otra vez por aquí."""
        primero = self.client.post(self.url, self._payload(), format="json")
        self.client.delete(f"{self.url}{primero.data['id']}/")

        resp = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(self._facturas().count(), 2)

    def test_requiere_autenticacion(self):
        from rest_framework.test import APIClient
        resp = APIClient().post(self.url, self._payload(), format="json")
        self.assertIn(resp.status_code, (401, 403))
