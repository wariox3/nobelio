"""Días hábiles con los festivos del catálogo (``calendario.py``)."""
from datetime import date

from django.test import TestCase

from apps.catalogos import calendario
from apps.catalogos.carga import cargar
from apps.catalogos.models import Festivo


class CalendarioTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cargar([Festivo])

    def test_los_festivos_de_2026(self):
        """Los 18 de la Ley 51 de 1983, con los trasladados al lunes."""
        fechas = list(
            Festivo.objects.filter(fecha__year=2026).values_list("fecha", flat=True)
        )
        self.assertEqual(len(fechas), 18)
        for fecha in (
            date(2026, 1, 12),   # Reyes, trasladado del 6
            date(2026, 4, 2),    # Jueves Santo
            date(2026, 4, 3),    # Viernes Santo
            date(2026, 5, 18),   # Ascensión
            date(2026, 11, 2),   # Todos los Santos, trasladado del 1
            date(2026, 12, 8),   # Inmaculada, no se traslada
        ):
            self.assertIn(fecha, fechas)

    def test_hay_festivos_de_2027(self):
        self.assertEqual(Festivo.objects.filter(fecha__year=2027).count(), 18)

    def test_es_habil(self):
        self.assertTrue(calendario.es_habil(date(2026, 10, 6)))     # martes
        self.assertFalse(calendario.es_habil(date(2026, 10, 10)))   # sábado
        self.assertFalse(calendario.es_habil(date(2026, 10, 11)))   # domingo
        self.assertFalse(calendario.es_habil(date(2026, 10, 12)))   # festivo

    def test_de_viernes_a_lunes(self):
        self.assertEqual(
            calendario.sumar_dias_habiles(date(2026, 10, 2), 1), date(2026, 10, 5),
        )

    def test_salta_el_lunes_festivo(self):
        # Viernes 9 de octubre + 1: el 12 es festivo, así que el martes 13.
        self.assertEqual(
            calendario.sumar_dias_habiles(date(2026, 10, 9), 1), date(2026, 10, 13),
        )

    def test_tres_dias_habiles_sobre_semana_santa(self):
        # Martes 31 de marzo: miércoles 1, (jueves y viernes santos), lunes 6, martes 7.
        self.assertEqual(
            calendario.sumar_dias_habiles(date(2026, 3, 31), 3), date(2026, 4, 7),
        )

    def test_cruza_el_fin_de_anio(self):
        # Jueves 24 de diciembre: el 25 es festivo; lunes 28, martes 29, miércoles 30.
        self.assertEqual(
            calendario.sumar_dias_habiles(date(2026, 12, 24), 3), date(2026, 12, 30),
        )
        # Miércoles 30: jueves 31, (1 de enero festivo), lunes 4, martes 5.
        self.assertEqual(
            calendario.sumar_dias_habiles(date(2026, 12, 30), 3), date(2027, 1, 5),
        )

    def test_sin_los_festivos_del_anio_no_cuenta(self):
        with self.assertRaisesMessage(calendario.FestivosNoCargados, "Faltan los festivos de 2028"):
            calendario.sumar_dias_habiles(date(2027, 12, 29), 3)
        with self.assertRaises(calendario.FestivosNoCargados):
            calendario.es_habil(date(2025, 6, 2))

    def test_a_fin_de_anio_no_exige_el_siguiente_si_no_llega(self):
        # Lunes 20 de diciembre de 2027 + 1: el martes 21, sin mirar 2028.
        self.assertEqual(
            calendario.sumar_dias_habiles(date(2027, 12, 20), 1), date(2027, 12, 21),
        )

    def test_al_menos_un_dia(self):
        with self.assertRaises(ValueError):
            calendario.sumar_dias_habiles(date(2026, 10, 6), 0)
