from django.apps import AppConfig


class CatalogosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.catalogos'

    def ready(self):
        # Registra las extensiones del esquema (`x-catalogo` en los campos).
        from apps.catalogos import esquema  # noqa: F401
