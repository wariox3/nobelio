"""Pruebas del parser de listas de valores DIAN (Genericode)."""
from django.test import SimpleTestCase

from apps.catalogos import genericode as gc


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
