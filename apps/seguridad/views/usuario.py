"""API de usuarios. Solo accesible para usuarios staff/admin."""
from django.contrib.auth import get_user_model
from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser

from apps.seguridad import aviso_usuario_nuevo, serializers


class UsuarioViewSet(viewsets.ModelViewSet):
    """CRUD de usuarios. Crear/listar/editar requiere ser staff (IsAdminUser)."""

    queryset = get_user_model().objects.all()
    serializer_class = serializers.UsuarioSerializer
    permission_classes = [IsAdminUser]
    search_fields = ["email", "nombre_corto"]

    def perform_create(self, serializer):
        """Crea el usuario y avisa del alta, con quién lo creó."""
        usuario = serializer.save()
        aviso_usuario_nuevo.avisar_al_confirmar(
            usuario,
            origen=aviso_usuario_nuevo.ORIGEN_API,
            creado_por=self.request.user.email,
        )
