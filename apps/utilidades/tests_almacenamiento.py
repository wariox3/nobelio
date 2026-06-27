"""Pruebas del servicio de almacenamiento (Backblaze B2 con fallback local)."""
from django.core.files.storage import default_storage
from django.test import SimpleTestCase, override_settings

from apps.utilidades import almacenamiento


class AlmacenamientoBackblazeTests(SimpleTestCase):
    def setUp(self):
        almacenamiento._storage_b2.cache_clear()

    def tearDown(self):
        almacenamiento._storage_b2.cache_clear()

    @override_settings(B2_HABILITADO=False)
    def test_sin_credenciales_usa_local(self):
        self.assertIs(almacenamiento.almacenamiento_backblaze(), default_storage)

    @override_settings(
        B2_HABILITADO=True, B2_BUCKET="b", B2_ENDPOINT_URL="https://s3.x.backblazeb2.com",
        B2_REGION="us-west-004", B2_KEY_ID="k", B2_APP_KEY="s",
    )
    def test_con_credenciales_usa_s3(self):
        from storages.backends.s3 import S3Storage

        storage = almacenamiento.almacenamiento_backblaze()
        self.assertIsInstance(storage, S3Storage)
        self.assertEqual(storage.bucket_name, "b")
