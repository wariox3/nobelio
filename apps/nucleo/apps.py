from django.apps import AppConfig


class NucleoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.nucleo'

    def ready(self):
        # `drf-spectacular` descubre las extensiones por el hecho de que la
        # clase se haya definido, así que el módulo tiene que importarse alguna
        # vez. Sin esto no falla nada: el esquema se genera igual, pero diciendo
        # que la API no tiene autenticación. Ver apps/nucleo/esquema.py.
        from apps.nucleo import esquema  # noqa: F401
