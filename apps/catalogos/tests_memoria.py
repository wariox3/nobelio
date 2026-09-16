"""Pruebas de los catálogos en la memoria del proceso.

La suite corre con la memoria apagada (`config.settings.test`); estas la
encienden y la vacían al empezar y al terminar, porque es del proceso y
sobreviviría a la prueba.
"""
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError

from apps.catalogos import memoria
from apps.catalogos.memoria import RelacionDeCatalogo
from apps.catalogos.models import UnidadMedida


@override_settings(CATALOGOS_EN_MEMORIA_SEGUNDOS=300)
class CatalogosEnMemoriaTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.unidad = UnidadMedida.objects.create(codigo="94", nombre="Unidad")

    def setUp(self):
        memoria.olvidar()
        self.addCleanup(memoria.olvidar)

    def _campo(self, queryset=None):
        return RelacionDeCatalogo(queryset=queryset or UnidadMedida.objects.all())

    def test_la_segunda_vez_no_va_a_la_base(self):
        self._campo().to_internal_value(self.unidad.pk)
        # Un campo nuevo, como en otra petición: no vale la memoria por petición.
        with self.assertNumQueries(0):
            fila = self._campo().to_internal_value(str(self.unidad.pk))
        self.assertEqual(fila, self.unidad)

    def test_entrega_una_copia_y_no_la_fila_guardada(self):
        """La misma instancia pasaría por varias peticiones e hilos a la vez."""
        una = memoria.buscar(UnidadMedida, self.unidad.pk)
        otra = memoria.buscar(UnidadMedida, self.unidad.pk)
        self.assertIsNot(una, otra)
        una.nombre = "cambiada"
        self.assertEqual(memoria.buscar(UnidadMedida, self.unidad.pk).nombre, "Unidad")

    def test_una_fila_cargada_despues_se_encuentra_en_la_base(self):
        self._campo().to_internal_value(self.unidad.pk)
        nueva = UnidadMedida.objects.create(codigo="KGM", nombre="Kilogramo")

        self.assertEqual(self._campo().to_internal_value(nueva.pk), nueva)

    def test_un_id_inexistente_sigue_siendo_does_not_exist(self):
        with self.assertRaises(ValidationError) as caso:
            self._campo().to_internal_value(999999)
        self.assertEqual(caso.exception.detail[0].code, "does_not_exist")

    def test_un_booleano_lo_rechaza_drf(self):
        """`True` es un `int` para Python; no puede colarse como el id 1."""
        with self.assertRaises(ValidationError) as caso:
            self._campo().to_internal_value(True)
        self.assertEqual(caso.exception.detail[0].code, "incorrect_type")

    def test_un_queryset_acotado_no_usa_la_memoria(self):
        """La memoria guarda la tabla entera y aceptaría lo que el campo excluye."""
        inactiva = UnidadMedida.objects.create(codigo="X1", nombre="Vieja", activo=False)
        memoria.buscar(UnidadMedida, inactiva.pk)  # ya está en memoria

        with self.assertRaises(ValidationError):
            self._campo(UnidadMedida.objects.filter(activo=True)).to_internal_value(inactiva.pk)

    def test_al_vencer_vuelve_a_leer_la_tabla(self):
        with mock.patch.object(memoria.time, "monotonic", return_value=1000.0):
            memoria.buscar(UnidadMedida, self.unidad.pk)
        UnidadMedida.objects.filter(pk=self.unidad.pk).update(nombre="Unidad corregida")

        with mock.patch.object(memoria.time, "monotonic", return_value=1299.0):
            self.assertEqual(memoria.buscar(UnidadMedida, self.unidad.pk).nombre, "Unidad")
        with mock.patch.object(memoria.time, "monotonic", return_value=1300.0):
            self.assertEqual(
                memoria.buscar(UnidadMedida, self.unidad.pk).nombre, "Unidad corregida"
            )

    @override_settings(CATALOGOS_EN_MEMORIA_SEGUNDOS=0)
    def test_con_cero_no_guarda_nada(self):
        self.assertIsNone(memoria.buscar(UnidadMedida, self.unidad.pk))
        with self.assertNumQueries(1):
            self._campo().to_internal_value(self.unidad.pk)
