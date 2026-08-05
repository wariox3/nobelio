"""El usuario deja de pertenecer a una cuenta.

Su alcance son los emisores asignados (``Usuario.emisores``, migración 0006) y
nada más: la cuenta no aportaba permisos, solo un techo intermedio. El concepto
sigue existiendo donde sí hace falta, en ``LlaveApi.cuenta``, porque una
integración necesita alcanzar a todos los emisores de su cuenta con una sola
credencial.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("cuentas", "0001_initial"),
        ("seguridad", "0006_llaveapi_cuenta_usuario_emisores"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="usuario",
            name="cuenta",
        ),
    ]
