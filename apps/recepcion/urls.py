"""Rutas de la app recepcion. Montadas bajo /api/recepcion/ en config/urls.py.

La entrada del Email Worker (``/recepcion/inbound``) no está aquí: va fuera de
``/api/``, directamente en config/urls.py.
"""
from rest_framework.routers import SimpleRouter

from apps.recepcion import views

router = SimpleRouter()
router.register("correo", views.CorreoViewSet)
router.register("adjunto", views.AdjuntoViewSet, basename="adjunto")
# `basename` propio: el de por defecto (`documento`) choca con el de
# apps.documentos, que se usa en `reverse("documento-detail")`.
router.register("documento", views.DocumentoRecibidoViewSet, basename="documento-recibido")

urlpatterns = router.urls
