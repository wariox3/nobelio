"""Las claves Fernet mal formadas no dejan arrancar Django."""
import secrets

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.claves import clave_fernet


class ClaveFernetTests(SimpleTestCase):
    def test_una_clave_valida_pasa_tal_cual(self):
        clave = Fernet.generate_key().decode()
        self.assertEqual(clave_fernet("CERT_ENCRYPTION_KEY", clave), clave)

    def test_la_clave_de_token_urlsafe_no_arranca(self):
        """El caso de producción: el comando de la SECRET_KEY en lugar del Fernet."""
        clave = secrets.token_urlsafe(64)
        with self.assertRaises(ImproperlyConfigured) as contexto:
            clave_fernet("CERT_ENCRYPTION_KEY", clave)
        mensaje = str(contexto.exception)
        self.assertIn("CERT_ENCRYPTION_KEY", mensaje)
        self.assertIn("Fernet.generate_key", mensaje)
        # El valor no se filtra al mensaje, que acaba en logs y en Sentry.
        self.assertNotIn(clave, mensaje)

    def test_una_clave_sin_el_igual_final_no_arranca(self):
        clave = Fernet.generate_key().decode().rstrip("=")
        with self.assertRaises(ImproperlyConfigured):
            clave_fernet("CERT_ENCRYPTION_KEY", clave)

    def test_vacia_y_obligatoria_no_arranca(self):
        with self.assertRaises(ImproperlyConfigured):
            clave_fernet("CERT_ENCRYPTION_KEY", "")

    def test_vacia_y_opcional_pasa(self):
        """La del segundo factor puede faltar: el MFA falla al usarlo, no antes."""
        self.assertEqual(clave_fernet("MFA_ENCRYPTION_KEY", "", obligatoria=False), "")

    def test_opcional_pero_mal_formada_no_arranca(self):
        with self.assertRaises(ImproperlyConfigured):
            clave_fernet("MFA_ENCRYPTION_KEY", "no-es-una-clave", obligatoria=False)
