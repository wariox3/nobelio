"""Pruebas del aviso de usuario nuevo por Zinc."""
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.seguridad import aviso_usuario_nuevo, verificacion
from apps.utilidades.zinc import ZincNoDisponible

Usuario = get_user_model()

DESTINO = "aviso@nobelio.co"


def _zinc(respuesta=None, *, falla=False):
    zinc = Mock()
    if falla:
        zinc.correo_html.side_effect = ZincNoDisponible("sin red")
    else:
        zinc.correo_html.return_value = respuesta or {"error": False}
    return zinc


@override_settings(CORREO_AVISO_USUARIO_NUEVO=DESTINO)
class AvisoUsuarioNuevoTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            email="ana@empresa.co", password="clave-secreta-123",
            nombre_corto="Ana",
        )

    def test_va_al_correo_configurado_con_los_datos_del_usuario(self):
        datos = aviso_usuario_nuevo.payload_zinc(
            self.usuario, origen=aviso_usuario_nuevo.ORIGEN_REGISTRO,
        )

        self.assertEqual(datos["correo"], DESTINO)
        self.assertIn("ana@empresa.co", datos["asunto"])
        self.assertIn("ana@empresa.co", datos["contenido"])
        self.assertIn("Ana", datos["contenido"])
        self.assertIn("Registro público", datos["contenido"])

    def test_no_lleva_la_contrasena(self):
        contenido = aviso_usuario_nuevo.cuerpo_html(
            self.usuario, origen=aviso_usuario_nuevo.ORIGEN_REGISTRO,
        )

        self.assertNotIn("clave-secreta-123", contenido)
        self.assertNotIn(self.usuario.password, contenido)

    def test_escapa_lo_que_escribio_quien_se_registra(self):
        self.usuario.nombre_corto = "<b>Ana</b>"

        contenido = aviso_usuario_nuevo.cuerpo_html(
            self.usuario, origen=aviso_usuario_nuevo.ORIGEN_REGISTRO,
        )

        self.assertNotIn("<b>Ana</b>", contenido)
        self.assertIn("&lt;b&gt;Ana&lt;/b&gt;", contenido)

    def test_envia_por_zinc(self):
        zinc = _zinc()

        enviado = aviso_usuario_nuevo.enviar_aviso_usuario_nuevo(
            self.usuario, origen=aviso_usuario_nuevo.ORIGEN_REGISTRO, zinc=zinc,
        )

        self.assertTrue(enviado)
        zinc.correo_html.assert_called_once()

    def test_un_fallo_de_zinc_no_se_propaga(self):
        with self.assertLogs(aviso_usuario_nuevo.logger, "ERROR"):
            enviado = aviso_usuario_nuevo.enviar_aviso_usuario_nuevo(
                self.usuario, origen=aviso_usuario_nuevo.ORIGEN_REGISTRO,
                zinc=_zinc(falla=True),
            )
        self.assertFalse(enviado)

    def test_si_zinc_lo_rechaza_no_cuenta_como_enviado(self):
        with self.assertLogs(aviso_usuario_nuevo.logger, "ERROR"):
            enviado = aviso_usuario_nuevo.enviar_aviso_usuario_nuevo(
                self.usuario, origen=aviso_usuario_nuevo.ORIGEN_REGISTRO,
                zinc=_zinc({"error": True}),
            )
        self.assertFalse(enviado)

    @override_settings(CORREO_AVISO_USUARIO_NUEVO="")
    def test_sin_destinatario_no_envia(self):
        zinc = _zinc()

        enviado = aviso_usuario_nuevo.enviar_aviso_usuario_nuevo(
            self.usuario, origen=aviso_usuario_nuevo.ORIGEN_REGISTRO, zinc=zinc,
        )

        self.assertFalse(enviado)
        zinc.correo_html.assert_not_called()


class AvisoAlCrearUsuarioTests(TestCase):
    """Los dos caminos de alta disparan el aviso, y solo al confirmar."""

    def test_el_registro_publico_avisa(self):
        cliente = APIClient()
        with patch.object(verificacion, "enviar_verificacion", return_value=True), \
                patch.object(aviso_usuario_nuevo, "enviar_aviso_usuario_nuevo") as aviso:
            with self.captureOnCommitCallbacks(execute=True):
                r = cliente.post(reverse("registro"), {
                    "email": "ana@empresa.co",
                    "password": "una-clave-larga-y-decente",
                }, format="json")

        self.assertEqual(r.status_code, 201, r.data)
        aviso.assert_called_once()
        self.assertEqual(aviso.call_args.args[0].email, "ana@empresa.co")
        self.assertEqual(
            aviso.call_args.kwargs["origen"], aviso_usuario_nuevo.ORIGEN_REGISTRO,
        )

    def test_la_api_de_usuarios_avisa_con_quien_lo_creo(self):
        admin = Usuario.objects.create_superuser(
            email="admin@nobelio.co", password="x",
        )
        cliente = APIClient()
        cliente.force_authenticate(admin)
        with patch.object(aviso_usuario_nuevo, "enviar_aviso_usuario_nuevo") as aviso:
            with self.captureOnCommitCallbacks(execute=True):
                r = cliente.post("/api/seguridad/usuario/", {
                    "email": "luis@empresa.co",
                    "password": "una-clave-larga-y-decente",
                }, format="json")

        self.assertEqual(r.status_code, 201, r.data)
        aviso.assert_called_once()
        self.assertEqual(aviso.call_args.args[0].email, "luis@empresa.co")
        self.assertEqual(aviso.call_args.kwargs["creado_por"], "admin@nobelio.co")

    def test_editar_un_usuario_no_avisa(self):
        admin = Usuario.objects.create_superuser(
            email="admin@nobelio.co", password="x",
        )
        otro = Usuario.objects.create_user(email="luis@empresa.co", password="x")
        cliente = APIClient()
        cliente.force_authenticate(admin)
        with patch.object(aviso_usuario_nuevo, "enviar_aviso_usuario_nuevo") as aviso:
            with self.captureOnCommitCallbacks(execute=True):
                cliente.patch(
                    f"/api/seguridad/usuario/{otro.pk}/",
                    {"nombre_corto": "Luis"}, format="json",
                )

        aviso.assert_not_called()
