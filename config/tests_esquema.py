"""El esquema versionado tiene que describir el código de hoy.

`schema.yml` está en el repositorio porque es el contrato que se entrega a quien
integra un ERP: con él genera su cliente sin depender de que nuestro servidor
esté arriba, y el diff de cada cambio se ve en la revisión igual que el código.

El problema de un archivo generado y versionado es que se queda atrás en
silencio. De eso se ocupa esta prueba: si alguien añade un campo y no regenera,
la suite falla y dice qué comando correr. La alternativa —confiar en acordarse—
ya sabemos cómo termina.
"""
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase

ARCHIVO = Path(__file__).resolve().parent.parent / "schema.yml"

INSTRUCCION = (
    "\n\n`schema.yml` no coincide con el código. Regenéralo con:\n"
    "    python manage.py spectacular --file schema.yml\n"
    "y súbelo en el mismo commit que el cambio de la API."
)


class EsquemaVersionadoTests(SimpleTestCase):
    def test_el_esquema_del_repo_esta_al_dia(self):
        salida = StringIO()
        call_command("spectacular", stdout=salida)
        self.assertEqual(
            salida.getvalue().strip(),
            ARCHIVO.read_text(encoding="utf-8").strip(),
            INSTRUCCION,
        )

    def test_el_esquema_se_genera_sin_avisos(self):
        """Un aviso es una parte de la API que queda sin describir.

        `--fail-on-warn` es lo que convierte «spectacular no supo deducirlo» en
        un fallo de la suite, que es donde se ve. Sin esto, un endpoint nuevo sin
        `@extend_schema` sale en la documentación como una caja vacía y nadie se
        entera hasta que alguien intenta usarlo.
        """
        call_command("spectacular", "--fail-on-warn", stdout=StringIO())
