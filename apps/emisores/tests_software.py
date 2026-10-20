"""Pruebas de la API de software DIAN (PIN write-only, filtro por emisor)."""
from datetime import date
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.documentos.models import Documento, DocumentoEstado, DocumentoTipo
from apps.documentos.tests_utils import crear_catalogos_minimos, crear_certificado
from apps.catalogos.carga import cargar
from apps.catalogos.models import TipoFactura
from apps.emisores.models import Emisor, Resolucion, SoftwareDian
from apps.emisores.servicios import RESOLUCION_SET_PRUEBAS
from apps.nomina.tests_utils import cargar_catalogos_de_nomina
from apps.nucleo.models import Ambiente
from apps.nucleo.tests_utils import errores_por_campo


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
        # `crear_catalogos_minimos` no trae tipos de factura, y el sembrado de
        # la resolución de pruebas necesita el 01 y el 20 (P.O.S.); ni los de
        # nómina, que necesita la nómina de prueba.
        cargar([TipoFactura])
        cargar_catalogos_de_nomina()

    def _payload(self):
        return {
            "emisor": self.emisor.id,
            "tipo": SoftwareDian.Tipo.FACTURACION,
            "identificador": "abc123-software-id",
            "pin": "12345",
            "test_set_id": "set-pruebas-xyz",
        }

    def test_crea_software_y_no_devuelve_el_pin(self):
        """El PIN se guarda pero no sale nunca: ni al crear, ni en el detalle,
        ni en el listado.

        Estuvo visible del 2026-08-28 al 2026-09-15; volvió a `write_only` por
        decisión de MarioA tras marcarlo un escáner como campo sensible.
        """
        resp = self.client.post(self.url, self._payload(), format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertNotIn("pin", resp.data)
        creado = SoftwareDian.objects.get(pk=resp.data["id"])
        self.assertEqual(creado.pin, "12345")

        detalle = self.client.get(f"{self.url}{creado.id}/")
        self.assertNotIn("pin", detalle.data)
        listado = self.client.get(self.url)
        filas = listado.data.get("results", listado.data)
        self.assertTrue(filas)
        for fila in filas:
            self.assertNotIn("pin", fila)

    def test_el_tipo_es_obligatorio(self):
        # Sin tipo no se sabe qué operación habilita el software, y el pipeline
        # busca el software *de su tipo*: uno sin tipo no lo encontraría nunca.
        payload = self._payload()
        del payload["tipo"]
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("tipo", errores_por_campo(resp))

    def test_pin_es_obligatorio(self):
        payload = self._payload()
        del payload["pin"]
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("pin", errores_por_campo(resp))

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

    # --- Módulo -------------------------------------------------------------

    def test_el_modulo_sale_del_tipo(self):
        """Nómina es su módulo; factura y documento equivalente, facturación."""
        esperado = {
            SoftwareDian.Tipo.FACTURACION: SoftwareDian.Modulo.FACTURACION,
            SoftwareDian.Tipo.DOCUMENTO_EQUIVALENTE: SoftwareDian.Modulo.FACTURACION,
            SoftwareDian.Tipo.NOMINA: SoftwareDian.Modulo.NOMINA,
        }
        for tipo, modulo in esperado.items():
            payload = self._payload()
            payload["tipo"] = tipo
            resp = self.client.post(self.url, payload, format="json")
            self.assertEqual(resp.status_code, 201, (tipo, resp.data))
            self.assertEqual(resp.data["modulo"], modulo, tipo)

    def test_el_modulo_no_se_escribe(self):
        payload = self._payload()
        payload["modulo"] = SoftwareDian.Modulo.NOMINA
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["modulo"], SoftwareDian.Modulo.FACTURACION)

    def test_filtra_por_modulo(self):
        for tipo in SoftwareDian.Tipo:
            SoftwareDian.objects.create(
                emisor=self.emisor, tipo=tipo, identificador=tipo, pin="1",
            )
        facturacion = self.client.get(self.url, {"modulo": "facturacion"})
        nomina = self.client.get(self.url, {"modulo": "nomina"})
        self.assertEqual(
            sorted(s["tipo"] for s in facturacion.data["results"]),
            ["documento_equivalente", "facturacion"],
        )
        self.assertEqual(
            [s["tipo"] for s in nomina.data["results"]], ["nomina"],
        )

    def test_filtra_por_set_de_pruebas_aceptado(self):
        aceptado = SoftwareDian.objects.create(
            emisor=self.emisor, tipo=SoftwareDian.Tipo.FACTURACION,
            identificador="a", pin="1", set_pruebas_aceptado=True,
        )
        pendiente = SoftwareDian.objects.create(
            emisor=self.emisor, tipo=SoftwareDian.Tipo.NOMINA, identificador="b", pin="1",
        )
        otro = _crear_emisor(self.cat, nit="800197268")
        self.usuario.emisores.add(otro)
        SoftwareDian.objects.create(
            emisor=otro, tipo=SoftwareDian.Tipo.FACTURACION, identificador="c", pin="1",
            set_pruebas_aceptado=True,
        )

        def ids(**filtros):
            resp = self.client.get(self.url, filtros)
            self.assertEqual(resp.status_code, 200, resp.data)
            return sorted(s["id"] for s in resp.data["results"])

        self.assertEqual(ids(emisor=self.emisor.id, set_pruebas_aceptado="true"), [aceptado.id])
        self.assertEqual(ids(emisor=self.emisor.id, set_pruebas_aceptado="false"), [pendiente.id])
        # Sin distinguir mayúsculas, y combinado con el módulo.
        self.assertEqual(
            ids(emisor=self.emisor.id, set_pruebas_aceptado="TRUE", modulo="nomina"), [],
        )
        self.assertEqual(len(ids(set_pruebas_aceptado="true")), 2)

    def test_un_set_de_pruebas_aceptado_que_no_es_booleano_responde_400(self):
        resp = self.client.get(self.url, {"set_pruebas_aceptado": "si"})
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("'true' o 'false'", resp.data["detail"])

    def test_lleva_el_nombre_del_emisor(self):
        resp = self.client.post(self.url, self._payload(), format="json")
        self.assertEqual(resp.data["emisor_nombre"], self.emisor.razon_social)

        listado = self.client.get(self.url)
        self.assertEqual(listado.data["results"][0]["emisor_nombre"], self.emisor.razon_social)

    def test_el_nombre_del_emisor_es_de_solo_lectura(self):
        payload = {**self._payload(), "emisor_nombre": "Otro nombre"}
        resp = self.client.post(self.url, payload, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["emisor_nombre"], self.emisor.razon_social)
        self.emisor.refresh_from_db()
        self.assertNotEqual(self.emisor.razon_social, "Otro nombre")

    def test_un_modulo_desconocido_responde_400(self):
        resp = self.client.get(self.url, {"modulo": "contabilidad"})
        self.assertEqual(resp.status_code, 400, resp.data)

    # --- Qué se puede actualizar --------------------------------------------

    def _crear(self, **cambios):
        resp = self.client.post(self.url, {**self._payload(), **cambios}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        return resp.data["id"]

    def test_set_pruebas_aceptado_no_se_escribe(self):
        """Lo marca el backend cuando la DIAN acepta el set, no el cliente."""
        id_ = self._crear(set_pruebas_aceptado=True)
        self.assertFalse(SoftwareDian.objects.get(pk=id_).set_pruebas_aceptado)

        self.client.patch(
            f"{self.url}{id_}/", {"set_pruebas_aceptado": True}, format="json",
        )
        self.assertFalse(SoftwareDian.objects.get(pk=id_).set_pruebas_aceptado)

    def test_el_emisor_no_se_puede_cambiar(self):
        id_ = self._crear()
        otro = _crear_emisor(self.cat, nit="800197268")
        self.usuario.emisores.add(otro)

        resp = self.client.patch(f"{self.url}{id_}/", {"emisor": otro.id}, format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("emisor", errores_por_campo(resp))
        self.assertEqual(SoftwareDian.objects.get(pk=id_).emisor, self.emisor)

    def test_el_tipo_no_se_puede_cambiar(self):
        id_ = self._crear()

        resp = self.client.patch(f"{self.url}{id_}/", {"tipo": "nomina"}, format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("tipo", errores_por_campo(resp))
        self.assertEqual(SoftwareDian.objects.get(pk=id_).tipo, "facturacion")

    def test_un_put_con_el_mismo_emisor_y_tipo_vale(self):
        id_ = self._crear()
        payload = {**self._payload(), "identificador": "otro-id"}

        resp = self.client.put(f"{self.url}{id_}/", payload, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(SoftwareDian.objects.get(pk=id_).identificador, "otro-id")

    def test_con_el_set_aceptado_no_se_puede_actualizar(self):
        id_ = self._crear()
        SoftwareDian.objects.filter(pk=id_).update(set_pruebas_aceptado=True)

        resp = self.client.patch(
            f"{self.url}{id_}/", {"identificador": "otro", "pin": "1"}, format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        software = SoftwareDian.objects.get(pk=id_)
        self.assertEqual(software.identificador, "abc123-software-id")
        self.assertEqual(software.pin, "12345")

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
            errores_por_campo(resp)["tipo"][0],
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
        # Es del sandbox de la DIAN: nace marcada como de habilitación.
        self.assertEqual(resolucion.ambiente, Ambiente.PRUEBAS)
        self.assertEqual(resolucion.modulo, Resolucion.Modulo.FACTURACION)

    def test_no_la_siembra_si_el_emisor_ya_esta_habilitado(self):
        """Es la resolución del sandbox: a un emisor ya habilitado no se le

        siembra, porque numera con su rango real. El software sí se registra;
        lo que no se hace es sembrar.
        """
        self.emisor.habilitado_facturacion = True
        self.emisor.save(update_fields=["habilitado_facturacion"])

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
        # Y la resolución queda apuntando al siguiente libre.
        self.assertEqual(
            resolucion.consecutivo_actual, resolucion.rango_desde + 2
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
        """Habilitado no se siembra resolución, así que tampoco facturas."""
        self.emisor.habilitado_facturacion = True
        self.emisor.save(update_fields=["habilitado_facturacion"])

        resp = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertFalse(self._facturas().exists())

    def test_el_software_de_nomina_no_deja_facturas(self):
        resp = self.client.post(self.url, self._payload_nomina(), format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertFalse(self._facturas().exists())

    # --- Y el de documento equivalente, su resolución y dos P.O.S. ----------

    def _payload_pos(self):
        payload = self._payload()
        payload["tipo"] = SoftwareDian.Tipo.DOCUMENTO_EQUIVALENTE
        payload["identificador"] = "software-pos"
        return payload

    def _documentos_pos(self):
        return Documento.objects.filter(
            emisor=self.emisor,
            documento_tipo__codigo=DocumentoTipo.Codigo.DOCUMENTO_EQUIVALENTE_POS,
        )

    def test_crear_software_pos_siembra_la_resolucion_epos(self):
        resp = self.client.post(self.url, self._payload_pos(), format="json")
        self.assertEqual(resp.status_code, 201, resp.data)

        resolucion = Resolucion.objects.get(emisor=self.emisor)
        self.assertEqual(resolucion.prefijo, "EPOS")
        self.assertEqual(resolucion.numero_resolucion, "18760000001")
        self.assertEqual(resolucion.rango_desde, 1)
        self.assertEqual(resolucion.rango_hasta, 1000000)
        self.assertTrue(resolucion.activa)
        # El 20 es el `codigo_dian` del P.O.S.: es lo que empareja la
        # resolución con el documento.
        self.assertEqual(resolucion.tipo_factura.codigo, "20")
        self.assertEqual(resolucion.ambiente, Ambiente.PRUEBAS)
        # El P.O.S. va con el software de documento equivalente, pero para los
        # ERP su numeración es de facturación.
        self.assertEqual(resolucion.modulo, Resolucion.Modulo.FACTURACION)

    def test_crear_software_pos_deja_dos_pos_en_borrador(self):
        self.client.post(self.url, self._payload_pos(), format="json")

        documentos = list(self._documentos_pos().order_by("consecutivo"))
        self.assertEqual([d.consecutivo for d in documentos], [1, 2])
        self.assertEqual(
            Resolucion.objects.get(emisor=self.emisor).consecutivo_actual, 3
        )
        for documento in documentos:
            self.assertEqual(documento.estado.nombre, DocumentoEstado.Nombre.BORRADOR)
            self.assertEqual(documento.resolucion.prefijo, "EPOS")
            # Sin el satélite no hay extensiones de caja y la DIAN lo rechaza.
            self.assertEqual(documento.pos.codigo_venta, documento.numero)

    def test_no_siembra_pos_si_ya_esta_habilitado_para_documento_equivalente(self):
        self.emisor.habilitado_documento_equivalente = True
        self.emisor.save(update_fields=["habilitado_documento_equivalente"])

        resp = self.client.post(self.url, self._payload_pos(), format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertFalse(Resolucion.objects.filter(emisor=self.emisor).exists())
        self.assertFalse(self._documentos_pos().exists())

    def test_sembrar_pos_dos_veces_no_duplica(self):
        from apps.emisores.servicios import (
            sembrar_documentos_de_prueba,
            sembrar_resolucion_de_pruebas,
        )

        for _ in range(2):
            resolucion, _ = sembrar_resolucion_de_pruebas(
                self.emisor, SoftwareDian.Tipo.DOCUMENTO_EQUIVALENTE,
            )
            sembrar_documentos_de_prueba(
                self.emisor, SoftwareDian.Tipo.DOCUMENTO_EQUIVALENTE, resolucion,
            )

        self.assertEqual(Resolucion.objects.filter(emisor=self.emisor).count(), 1)
        self.assertEqual(self._documentos_pos().count(), 2)

    # --- Y el de nómina deja diez nóminas -----------------------------------

    def _payload_nomina(self):
        payload = self._payload()
        payload["tipo"] = SoftwareDian.Tipo.NOMINA
        payload["identificador"] = "software-de-nomina"
        return payload

    def test_crear_software_de_nomina_deja_diez_nominas_y_diez_notas(self):
        from apps.nomina.models import Nomina

        resp = self.client.post(self.url, self._payload_nomina(), format="json")
        self.assertEqual(resp.status_code, 201, resp.data)

        nominas = list(
            Nomina.objects.filter(emisor=self.emisor, tipo_xml=Nomina.TipoXML.NOMINA)
            .order_by("consecutivo")
        )
        notas = list(
            Nomina.objects.filter(emisor=self.emisor, tipo_xml=Nomina.TipoXML.AJUSTE)
            .order_by("consecutivo")
        )
        self.assertEqual([n.consecutivo for n in nominas], list(range(1, 11)))
        # Las notas van después de las nóminas, una por cada una y en orden.
        self.assertEqual([n.consecutivo for n in notas], list(range(11, 21)))
        self.assertEqual([n.nomina_predecesora_id for n in notas], [n.id for n in nominas])
        for documento in nominas + notas:
            self.assertEqual(documento.estado.nombre, DocumentoEstado.Nombre.BORRADOR)
            self.assertFalse(documento.cune)  # se crean, no se emiten
        for nota, nomina in zip(notas, nominas):
            self.assertEqual(nota.tipo_nota, Nomina.TipoNota.REEMPLAZAR)
            self.assertEqual(nota.conceptos.count(), nomina.conceptos.count())
            self.assertEqual(nota.prefijo, nomina.prefijo)

    def test_cada_nomina_va_de_un_mes_distinto(self):
        """La regla 90 rechaza dos nóminas del mismo trabajador y periodo."""
        from apps.nomina.models import Nomina

        self.client.post(self.url, self._payload_nomina(), format="json")

        periodos = [
            (n.fecha_liquidacion_inicio, n.fecha_liquidacion_fin)
            for n in Nomina.objects.filter(emisor=self.emisor)
        ]
        self.assertEqual(len(set(periodos)), 10)
        # Y es el mismo trabajador en las diez, que es lo que hace que importe.
        self.assertEqual(
            Nomina.objects.filter(emisor=self.emisor)
            .values("empleado").distinct().count(),
            1,
        )

    def test_no_deja_nominas_si_ya_esta_habilitado_para_nomina(self):
        from apps.nomina.models import Nomina

        self.emisor.habilitado_nomina = True
        self.emisor.save(update_fields=["habilitado_nomina"])

        resp = self.client.post(self.url, self._payload_nomina(), format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertFalse(Nomina.objects.filter(emisor=self.emisor).exists())

    def test_volver_a_registrar_el_software_de_nomina_no_duplica(self):
        from apps.nomina.models import Nomina

        primero = self.client.post(self.url, self._payload_nomina(), format="json")
        self.client.delete(f"{self.url}{primero.data['id']}/")

        self.client.post(self.url, self._payload_nomina(), format="json")

        self.assertEqual(Nomina.objects.filter(emisor=self.emisor).count(), 20)

    def test_volver_a_registrar_el_software_no_duplica_las_facturas(self):
        """Dar de baja el software y rehacerlo pasa otra vez por aquí."""
        primero = self.client.post(self.url, self._payload(), format="json")
        self.client.delete(f"{self.url}{primero.data['id']}/")

        resp = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(self._facturas().count(), 2)

    # --- Borrar el software -------------------------------------------------

    def test_borra_el_software_en_pruebas_y_sin_set_aceptado(self):
        alta = self.client.post(self.url, self._payload(), format="json")

        resp = self.client.delete(f"{self.url}{alta.data['id']}/")

        self.assertEqual(resp.status_code, 204)
        self.assertFalse(SoftwareDian.objects.filter(pk=alta.data["id"]).exists())

    def test_no_borra_el_software_con_el_set_de_pruebas_aceptado(self):
        alta = self.client.post(self.url, self._payload(), format="json")
        SoftwareDian.objects.filter(pk=alta.data["id"]).update(
            set_pruebas_aceptado=True
        )

        resp = self.client.delete(f"{self.url}{alta.data['id']}/")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("Set de Pruebas", resp.data["detail"])
        self.assertTrue(SoftwareDian.objects.filter(pk=alta.data["id"]).exists())

    def test_no_borra_el_software_de_un_emisor_habilitado(self):
        alta = self.client.post(self.url, self._payload(), format="json")
        self.emisor.habilitado_facturacion = True
        self.emisor.save(update_fields=["habilitado_facturacion"])

        resp = self.client.delete(f"{self.url}{alta.data['id']}/")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("habilitado", resp.data["detail"])
        self.assertTrue(SoftwareDian.objects.filter(pk=alta.data["id"]).exists())

    def test_la_habilitacion_de_otra_operacion_no_bloquea_el_borrado(self):
        """Cada operación se habilita aparte: la nómina no frena la factura."""
        alta = self.client.post(self.url, self._payload(), format="json")
        self.emisor.habilitado_nomina = True
        self.emisor.save(update_fields=["habilitado_nomina"])

        resp = self.client.delete(f"{self.url}{alta.data['id']}/")

        self.assertEqual(resp.status_code, 204)

    # --- Desactivar: baja la habilitación del software y del emisor ---------

    def _habilitar(self, software_id, campo):
        SoftwareDian.objects.filter(pk=software_id).update(
            set_pruebas_aceptado=True
        )
        setattr(self.emisor, campo, True)
        self.emisor.save(update_fields=[campo])

    def test_desactivar_baja_la_bandera_del_software_y_la_del_emisor(self):
        alta = self.client.post(self.url, self._payload(), format="json")
        self._habilitar(alta.data["id"], "habilitado_facturacion")

        resp = self.client.post(f"{self.url}{alta.data['id']}/desactivar/")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(resp.data["set_pruebas_aceptado"])
        self.assertFalse(resp.data["habilitado_facturacion"])
        self.assertFalse(
            SoftwareDian.objects.get(pk=alta.data["id"]).set_pruebas_aceptado
        )
        self.emisor.refresh_from_db()
        self.assertFalse(self.emisor.habilitado_facturacion)

    def test_desactivar_no_toca_la_habilitacion_de_otra_operacion(self):
        alta = self.client.post(self.url, self._payload_nomina(), format="json")
        self._habilitar(alta.data["id"], "habilitado_nomina")
        self.emisor.habilitado_facturacion = True
        self.emisor.save(update_fields=["habilitado_facturacion"])

        resp = self.client.post(f"{self.url}{alta.data['id']}/desactivar/")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.emisor.refresh_from_db()
        self.assertFalse(self.emisor.habilitado_nomina)
        self.assertTrue(self.emisor.habilitado_facturacion)

    def test_desactivar_es_idempotente(self):
        alta = self.client.post(self.url, self._payload(), format="json")

        resp = self.client.post(f"{self.url}{alta.data['id']}/desactivar/")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(resp.data["set_pruebas_aceptado"])

    def test_desactivado_se_puede_borrar(self):
        alta = self.client.post(self.url, self._payload(), format="json")
        self._habilitar(alta.data["id"], "habilitado_facturacion")
        self.client.post(f"{self.url}{alta.data['id']}/desactivar/")

        resp = self.client.delete(f"{self.url}{alta.data['id']}/")

        self.assertEqual(resp.status_code, 204)

    # --- Una nómina suelta: crear-nomina-prueba -----------------------------

    def _alta_nomina(self):
        """Registra el software de nómina y devuelve su id."""
        resp = self.client.post(self.url, self._payload_nomina(), format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        return resp.data["id"]

    def test_crea_una_nomina_mas(self):
        from apps.nomina.models import Nomina

        software = self._alta_nomina()
        resp = self.client.post(
            f"{self.url}{software}/crear-nomina-prueba/", {}, format="json"
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["estado"], DocumentoEstado.Nombre.BORRADOR)
        self.assertEqual(Nomina.objects.filter(emisor=self.emisor).count(), 21)

    def test_sin_consecutivo_sigue_por_el_ultimo(self):
        software = self._alta_nomina()

        resp = self.client.post(
            f"{self.url}{software}/crear-nomina-prueba/", {}, format="json"
        )

        # Las diez nóminas y las diez notas del alta gastaron del 1 al 20.
        self.assertEqual(resp.data["consecutivo"], 21)

    def test_el_periodo_continua_la_serie_hacia_atras(self):
        """La regla 90 rechaza dos nóminas del mismo trabajador y periodo."""
        from apps.nomina.models import Nomina

        software = self._alta_nomina()
        anteriores = set(
            Nomina.objects.filter(emisor=self.emisor)
            .values_list("fecha_liquidacion_inicio", flat=True)
        )

        resp = self.client.post(
            f"{self.url}{software}/crear-nomina-prueba/", {}, format="json"
        )

        inicio = date.fromisoformat(resp.data["periodo"][0])
        self.assertNotIn(inicio, anteriores)
        self.assertLess(inicio, min(anteriores))

    def test_respeta_el_consecutivo_que_se_le_pasa(self):
        software = self._alta_nomina()

        resp = self.client.post(
            f"{self.url}{software}/crear-nomina-prueba/",
            {"consecutivo": 500}, format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["consecutivo"], 500)

    def test_rechaza_un_consecutivo_ya_usado(self):
        from apps.nomina.models import Nomina

        software = self._alta_nomina()
        resp = self.client.post(
            f"{self.url}{software}/crear-nomina-prueba/",
            {"consecutivo": 1}, format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(Nomina.objects.filter(emisor=self.emisor).count(), 20)

    def test_solo_sobre_un_software_de_nomina(self):
        from apps.nomina.models import Nomina

        facturacion = self.client.post(self.url, self._payload(), format="json")

        resp = self.client.post(
            f"{self.url}{facturacion.data['id']}/crear-nomina-prueba/",
            {}, format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("no de nómina", resp.data["detail"])
        self.assertFalse(Nomina.objects.filter(emisor=self.emisor).exists())

    def test_rechaza_si_el_emisor_ya_esta_habilitado(self):
        from apps.nomina.models import Nomina

        software = self._alta_nomina()
        self.emisor.habilitado_nomina = True
        self.emisor.save(update_fields=["habilitado_nomina"])

        resp = self.client.post(
            f"{self.url}{software}/crear-nomina-prueba/", {}, format="json"
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("habilitado", resp.data["detail"])
        self.assertEqual(Nomina.objects.filter(emisor=self.emisor).count(), 20)

    def test_no_alcanza_el_software_de_otro_emisor(self):
        from apps.emisores.models import SoftwareDian as SW

        otro = _crear_emisor(self.cat, nit="800197268")
        ajeno = SW.objects.create(
            emisor=otro, tipo=SW.Tipo.NOMINA, identificador="x", pin="1",
        )

        resp = self.client.post(
            f"{self.url}{ajeno.id}/crear-nomina-prueba/", {}, format="json"
        )

        self.assertEqual(resp.status_code, 404)

    # --- La resolución de nómina, si el ERP lleva una ------------------------

    def _resolucion_de_nomina(self, consecutivo_actual, **extra):
        datos = dict(
            emisor=self.emisor, tipo_factura=TipoFactura.objects.get(codigo="01"),
            modulo=Resolucion.Modulo.NOMINA, prefijo="NESETP",
            numero_resolucion="NOM-1", fecha_resolucion="2026-01-01",
            rango_desde=1, rango_hasta=5000, consecutivo_actual=consecutivo_actual,
            vigente_desde="2026-01-01", vigente_hasta="2030-01-01", ambiente=2,
        )
        datos.update(extra)
        return Resolucion.objects.create(**datos)

    def _consecutivos(self, tipo_xml):
        from apps.nomina.models import Nomina

        return list(
            Nomina.objects.filter(emisor=self.emisor, tipo_xml=tipo_xml)
            .order_by("consecutivo").values_list("consecutivo", flat=True)
        )

    def test_el_alta_sigue_la_resolucion_de_nomina_y_la_avanza(self):
        from apps.nomina.models import Nomina

        resolucion = self._resolucion_de_nomina(100)

        self._alta_nomina()

        self.assertEqual(self._consecutivos(Nomina.TipoXML.NOMINA), list(range(100, 110)))
        self.assertEqual(self._consecutivos(Nomina.TipoXML.AJUSTE), list(range(110, 120)))
        resolucion.refresh_from_db()
        self.assertEqual(resolucion.consecutivo_actual, 120)

    def test_una_resolucion_atrasada_no_hace_repetir_numeros(self):
        software = self._alta_nomina()  # gasta del 1 al 20
        resolucion = self._resolucion_de_nomina(5)

        resp = self.client.post(f"{self.url}{software}/crear-nomina-prueba/", {}, format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["consecutivo"], 21)
        resolucion.refresh_from_db()
        self.assertEqual(resolucion.consecutivo_actual, 22)

    def test_un_consecutivo_a_mano_tambien_la_avanza_pero_nunca_la_retrocede(self):
        software = self._alta_nomina()
        resolucion = self._resolucion_de_nomina(30)
        url = f"{self.url}{software}/crear-nomina-prueba/"

        self.client.post(url, {"consecutivo": 500}, format="json")
        resolucion.refresh_from_db()
        self.assertEqual(resolucion.consecutivo_actual, 501)

        self.client.post(url, {"consecutivo": 25}, format="json")
        resolucion.refresh_from_db()
        self.assertEqual(resolucion.consecutivo_actual, 501)

    def test_ignora_la_resolucion_inactiva_de_otro_prefijo_o_de_otro_modulo(self):
        inactiva = self._resolucion_de_nomina(100, activa=False)
        otro_prefijo = self._resolucion_de_nomina(200, prefijo="NE", numero_resolucion="NOM-2")
        facturacion = self._resolucion_de_nomina(
            300, modulo=Resolucion.Modulo.FACTURACION, numero_resolucion="NOM-3",
        )

        self._alta_nomina()

        from apps.nomina.models import Nomina
        self.assertEqual(self._consecutivos(Nomina.TipoXML.NOMINA), list(range(1, 11)))
        for resolucion, actual in ((inactiva, 100), (otro_prefijo, 200), (facturacion, 300)):
            resolucion.refresh_from_db()
            self.assertEqual(resolucion.consecutivo_actual, actual)

    def test_si_choca_el_numero_la_resolucion_no_avanza(self):
        software = self._alta_nomina()
        resolucion = self._resolucion_de_nomina(30)

        resp = self.client.post(
            f"{self.url}{software}/crear-nomina-prueba/", {"consecutivo": 1}, format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        resolucion.refresh_from_db()
        self.assertEqual(resolucion.consecutivo_actual, 30)

    def test_las_notas_de_prueba_tambien_la_avanzan(self):
        software = self._alta_nomina()
        self._aceptar(3)
        resolucion = self._resolucion_de_nomina(40)

        resp = self.client.post(
            f"{self.url}{software}/crear-nota-ajuste-prueba/", {}, format="json"
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(
            sorted(n["consecutivo"] for n in resp.data["notas"]), list(range(40, 51)),
        )
        resolucion.refresh_from_db()
        self.assertEqual(resolucion.consecutivo_actual, 51)

    # --- Notas de ajuste: crear-nota-ajuste-prueba -------------------------

    def _aceptar(self, consecutivo):
        """Marca como aceptada por la DIAN una de las nóminas del alta."""
        from apps.nomina.models import Nomina

        nomina = Nomina.objects.get(emisor=self.emisor, consecutivo=consecutivo)
        nomina.estado, _ = DocumentoEstado.objects.get_or_create(
            nombre=DocumentoEstado.Nombre.ACEPTADO,
        )
        nomina.cune = f"cune-de-prueba-{consecutivo}"
        nomina.save(update_fields=["estado", "cune"])
        return nomina

    def test_crea_once_notas_sobre_la_nomina_aceptada(self):
        from apps.nomina.models import Nomina

        software = self._alta_nomina()
        aceptada = self._aceptar(3)

        resp = self.client.post(
            f"{self.url}{software}/crear-nota-ajuste-prueba/", {}, format="json"
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["nomina_ajustada"]["id"], str(aceptada.id))
        self.assertEqual(len(resp.data["notas"]), 11)
        notas = Nomina.objects.filter(pk__in=[n["id"] for n in resp.data["notas"]])
        self.assertEqual(notas.count(), 11)
        for nota in notas:
            self.assertEqual(nota.nomina_predecesora_id, aceptada.id)
            self.assertEqual(nota.tipo_nota, Nomina.TipoNota.REEMPLAZAR)
            self.assertEqual(nota.estado.nombre, DocumentoEstado.Nombre.BORRADOR)
            self.assertEqual(nota.conceptos.count(), aceptada.conceptos.count())
        # Las diez nóminas y las diez notas del alta gastaron del 1 al 20.
        self.assertEqual(
            sorted(notas.values_list("consecutivo", flat=True)),
            list(range(21, 32)),
        )

    def test_sin_nomina_aceptada_responde_400(self):
        from apps.nomina.models import Nomina

        software = self._alta_nomina()

        resp = self.client.post(
            f"{self.url}{software}/crear-nota-ajuste-prueba/", {}, format="json"
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("aceptada", resp.data["detail"])
        self.assertEqual(Nomina.objects.filter(emisor=self.emisor).count(), 20)

    def test_ignora_la_aceptada_con_errores(self):
        from apps.nomina.models import Nomina, NominaError

        software = self._alta_nomina()
        aceptada = self._aceptar(3)
        NominaError.objects.create(
            nomina=aceptada, regla="NIE021",
            tipo=NominaError.Tipo.NOTIFICACION, mensaje="Notificación",
        )

        resp = self.client.post(
            f"{self.url}{software}/crear-nota-ajuste-prueba/", {}, format="json"
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(Nomina.objects.filter(emisor=self.emisor).count(), 20)

    def test_toma_la_aceptada_sin_errores_aunque_haya_otra_con_errores(self):
        from apps.nomina.models import NominaError

        software = self._alta_nomina()
        limpia = self._aceptar(2)
        con_errores = self._aceptar(5)
        NominaError.objects.create(
            nomina=con_errores, tipo=NominaError.Tipo.RECHAZO, mensaje="Rechazo",
        )

        resp = self.client.post(
            f"{self.url}{software}/crear-nota-ajuste-prueba/", {}, format="json"
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["nomina_ajustada"]["id"], str(limpia.id))

    def test_notas_solo_sobre_un_software_de_nomina(self):
        facturacion = self.client.post(self.url, self._payload(), format="json")

        resp = self.client.post(
            f"{self.url}{facturacion.data['id']}/crear-nota-ajuste-prueba/",
            {}, format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("no de nómina", resp.data["detail"])

    def test_requiere_autenticacion(self):
        from rest_framework.test import APIClient
        resp = APIClient().post(self.url, self._payload(), format="json")
        self.assertIn(resp.status_code, (401, 403))
