"""Pruebas del parser de listas de valores DIAN (Genericode)."""
from io import StringIO

from django.test import SimpleTestCase, TestCase

from apps.catalogos import genericode as gc
from apps.catalogos.management.commands.cargar_catalogos import Command
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
        Command(stdout=StringIO())._cargar({"Municipio": Municipio})
        self.assertEqual(
            Municipio.objects.get(codigo="05001").codigo_postal, "050001"
        )
