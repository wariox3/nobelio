"""Pruebas del parser de listas de valores DIAN (Genericode)."""
from datetime import date
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from apps.catalogos import genericode as gc
from apps.catalogos import models
from apps.catalogos.carga import LISTAS, cargar
from apps.catalogos.models import Municipio


class ParserGenericodeTests(SimpleTestCase):
    """Valida el parser contra los archivos .gc reales del repositorio."""

    def test_hay_archivos_disponibles(self):
        archivos = gc.listar_archivos()
        self.assertGreaterEqual(len(archivos), 30)
        self.assertTrue(all(a.suffix == ".gc" for a in archivos))

    def test_todos_los_archivos_parsean(self):
        """Ningún archivo .gc del repo debe fallar al parsearse."""
        for archivo in gc.listar_archivos():
            with self.subTest(archivo=archivo.name):
                lista = gc.parsear_archivo(archivo)
                self.assertTrue(lista.nombre_corto)
                self.assertGreaterEqual(len(lista), 1)

    def test_tipo_documento_contenido(self):
        lista = gc.cargar("TipoDocumento")
        self.assertEqual(lista.nombre_corto, "TipoDocumento")
        mapa = lista.como_diccionario()
        self.assertEqual(mapa["01"], "Factura electrónica de Venta")
        self.assertEqual(mapa["91"], "Nota Crédito")
        self.assertEqual(mapa["92"], "Nota Débito")

    def test_conserva_columnas_no_declaradas(self):
        """Las filas pueden traer columnas fuera del ColumnSet (p. ej. description)."""
        lista = gc.cargar("TipoDocumento")
        self.assertIn("description", lista.filas[0])

    def test_carga_por_prefijo_insensible_mayusculas(self):
        lista = gc.cargar("tipodocumento")
        self.assertEqual(lista.nombre_corto, "TipoDocumento")

    def test_municipio_es_lista_grande(self):
        lista = gc.cargar("Municipio")
        self.assertGreater(len(lista), 1000)
        self.assertEqual(lista.como_diccionario()["05001"], "Medellín")

    def test_lista_inexistente_lanza_error(self):
        with self.assertRaises(FileNotFoundError):
            gc.cargar("NoExisteEstaLista")


def _codigos_postales():
    """El mapeo municipio -> código postal tal cual está en la lista."""
    return {
        fila["code"]: fila.get("codigo_postal", "")
        for fila in gc.cargar("Municipio").filas
    }


class CodigoPostalDeMunicipiosTests(SimpleTestCase):
    """Valida la columna `codigo_postal` que se añadió a `Municipio-2.1.gc`.

    No es oficial de la DIAN —ella publica los códigos sueltos, sin municipio—
    sino de 4-72, y su archivo llega con los números en formato es-CO, así que
    hubo que reconstruirlos. Esto comprueba esa reconstrucción; el porqué y el
    origen están en el README de `datos/listas/`.
    """

    def test_la_columna_esta_declarada(self):
        columnas = {c.id for c in gc.cargar("Municipio").columnas}
        self.assertIn("codigo_postal", columnas)

    def test_no_falta_en_ningun_municipio(self):
        sin_postal = [m for m, p in _codigos_postales().items() if not p]
        self.assertEqual(sin_postal, [])

    def test_todos_son_seis_digitos(self):
        for municipio, postal in _codigos_postales().items():
            with self.subTest(municipio=municipio):
                self.assertRegex(postal, r"^\d{6}$")

    def test_son_codigos_que_la_dian_reconoce(self):
        """Cruce contra CodigoPostal1.gc: es lo que dice que se reconstruyó bien.

        Los 1.122 caen dentro. En el dataset completo de 4-72 hay dos que no
        —`995008` y `995009`, de Santa Rosalía (Vichada), que la lista de la
        DIAN, de 2019, todavía no tiene—, pero ninguno de los dos es el código
        de cabecera de su municipio, así que no llegan hasta aquí.
        """
        validos = set(gc.cargar("CodigoPostal").como_diccionario())
        fuera = {p for p in _codigos_postales().values() if p not in validos}
        self.assertEqual(fuera, set())

    def test_cabeceras_conocidas(self):
        postales = _codigos_postales()
        self.assertEqual(postales["05001"], "050001")  # Medellín
        self.assertEqual(postales["11001"], "110111")  # Bogotá D.C.
        self.assertEqual(postales["76001"], "760001")  # Cali
        self.assertEqual(postales["05088"], "051050")  # Bello


class CargaDeCodigosPostalesTests(TestCase):
    """La carga de catálogos deja a cada municipio con su código postal."""

    def test_la_carga_rellena_el_municipio(self):
        cargar([Municipio])
        self.assertEqual(
            Municipio.objects.get(codigo="05001").codigo_postal, "050001"
        )


def _tabla_de_ids():
    """``{modelo: {código: id}}`` de todos los `.gc` que se cargan."""
    tabla = {}
    for lista in LISTAS:
        ids = tabla.setdefault(lista.modelo.__name__, {})
        for id_fijo, campos in lista.filas().items():
            ids[campos["codigo"]] = id_fijo
    return tabla


class IdsFijosTests(SimpleTestCase):
    """Todo catálogo viene de un `.gc` y cada código trae su id fijo."""

    def test_el_id_no_es_autoincremental(self):
        self.assertEqual(
            models.Moneda._meta.pk.get_internal_type(), "BigIntegerField"
        )

    def test_todo_catalogo_viene_de_un_gc(self):
        catalogos = {
            nombre for nombre in models.__all__ if nombre != "ElementoCatalogo"
        }
        self.assertEqual(set(_tabla_de_ids()), catalogos)

    def test_ids_sin_repetir(self):
        for nombre, ids in _tabla_de_ids().items():
            with self.subTest(catalogo=nombre):
                self.assertEqual(len(set(ids.values())), len(ids))

    def test_un_solo_id_por_codigo(self):
        """Una lista puede repetir un código (UnidadesMedida lo hace), no su id."""
        for lista in LISTAS:
            with self.subTest(lista=lista.etiqueta):
                codigos = [c["codigo"] for c in lista.filas().values()]
                self.assertEqual(len(codigos), len(set(codigos)))

    def test_codigos_que_no_publica_la_dian(self):
        """Añadidos a mano: el PEP, el P.O.S. y las listas de nómina."""
        tabla = _tabla_de_ids()
        self.assertEqual(tabla["TipoIdentificacion"]["47"], 47)
        self.assertEqual(tabla["TipoFactura"]["20"], 9)
        self.assertEqual(len(tabla["PeriodoNomina"]), 6)
        self.assertEqual(len(tabla["TipoContrato"]), 5)
        self.assertEqual(len(tabla["TipoTrabajador"]), 16)
        self.assertEqual(len(tabla["SubTipoTrabajador"]), 2)

    def test_en_tipo_de_identificacion_el_id_es_el_codigo(self):
        for codigo, id_fijo in _tabla_de_ids()["TipoIdentificacion"].items():
            with self.subTest(codigo=codigo):
                self.assertEqual(id_fijo, int(codigo))

    def test_eventos_radian_y_conceptos_de_reclamo(self):
        """El id es el código: 030 → 30, 01 → 1."""
        tabla = _tabla_de_ids()
        self.assertEqual(tabla["EventoRadian"], {"030": 30, "031": 31, "032": 32, "033": 33})
        self.assertEqual(tabla["ConceptoReclamo"], {"01": 1, "02": 2, "03": 3, "04": 4})

    def test_en_festivos_el_id_es_la_fecha(self):
        for codigo, id_fijo in _tabla_de_ids()["Festivo"].items():
            with self.subTest(codigo=codigo):
                self.assertEqual(id_fijo, int(codigo.replace("-", "")))

    def test_ids_que_torio_manda(self):
        """torio envía estos ids fijos o los de su propio catálogo."""
        tabla = _tabla_de_ids()
        self.assertEqual(tabla["Moneda"]["COP"], 35)
        self.assertEqual(tabla["UnidadMedida"]["94"], 70)
        self.assertEqual(tabla["Pais"]["CO"], 46)
        self.assertEqual(tabla["TipoOrganizacion"]["1"], 1)
        self.assertEqual(tabla["Departamento"]["05"], 1)
        self.assertEqual(tabla["Departamento"]["99"], 33)
        self.assertEqual(tabla["Municipio"]["05001"], 1)


class CargaTests(TestCase):
    """La carga deja cada código con su id fijo, y se puede repetir."""

    def test_las_migraciones_no_siembran_catalogos(self):
        for lista in LISTAS:
            with self.subTest(catalogo=lista.etiqueta):
                self.assertFalse(lista.modelo.objects.exists())

    def test_carga_todo_con_el_id_del_gc(self):
        salida = StringIO()
        call_command("cargar_catalogos", stdout=salida)

        self.assertIn("Catálogos cargados.", salida.getvalue())
        for modelo, ids in _tabla_de_ids().items():
            with self.subTest(catalogo=modelo):
                self.assertEqual(
                    dict(getattr(models, modelo).objects.values_list("codigo", "id")),
                    ids,
                )

    def test_enlaza_el_municipio_a_su_departamento(self):
        cargar([models.Departamento, Municipio])
        self.assertEqual(Municipio.objects.get(codigo="05001").departamento.codigo, "05")

    def test_el_festivo_toma_la_fecha_del_codigo(self):
        cargar([models.Festivo])
        festivo = models.Festivo.objects.get(codigo="2026-01-12")
        self.assertEqual(festivo.fecha, date(2026, 1, 12))
        self.assertEqual(festivo.id, 20260112)
        self.assertEqual(festivo.nombre, "Día de los Reyes Magos")

    def test_repetir_actualiza_sin_duplicar(self):
        cargar([models.Moneda])
        models.Moneda.objects.filter(codigo="COP").update(nombre="Otro")

        (_, creados, actualizados), = cargar([models.Moneda])

        self.assertEqual(creados, 0)
        self.assertEqual(actualizados, models.Moneda.objects.count())
        self.assertEqual(models.Moneda.objects.get(id=35).nombre, "Peso colombiano")

    def test_solo_lo_que_se_pide(self):
        resultado = cargar([models.TipoFactura])
        # La de factura y la del documento soporte.
        self.assertEqual(len(resultado), 2)
        self.assertEqual(models.TipoFactura.objects.get(codigo="05").id, 7)
        self.assertFalse(models.Moneda.objects.exists())

    def test_crear_sin_id_falla(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            models.Moneda.objects.create(codigo="ZZZ", nombre="Prueba")

    def test_codigo_sin_id_detiene_la_carga(self):
        lista = gc.cargar("FormasPago")
        del lista.filas[1]["id"]
        with mock.patch.object(gc, "cargar", return_value=lista):
            with self.assertRaisesMessage(CommandError, "el código 2 no tiene"):
                call_command("cargar_catalogos", stdout=StringIO())
        self.assertFalse(models.FormaPago.objects.exists())
