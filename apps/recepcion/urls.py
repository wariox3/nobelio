"""Rutas de la app recepcion. Montadas bajo /api/recepcion/ en config/urls.py.

La entrada del Email Worker (``/recepcion/inbound``) no está aquí: va fuera de
``/api/``, directamente en config/urls.py.
"""
from rest_framework.routers import SimpleRouter

from apps.recepcion import views

router = SimpleRouter()
router.register("correo", views.CorreoViewSet)

urlpatterns = router.urls
