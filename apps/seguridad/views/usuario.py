"""API de usuarios. Solo accesible para usuarios staff/admin."""
from django.contrib.auth import get_user_model
from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser

from apps.seguridad import serializers


class UsuarioViewSet(viewsets.ModelViewSet):
    """CRUD de usuarios. Crear/listar/editar requiere ser staff (IsAdminUser)."""

    queryset = get_user_model().objects.all()
    serializer_class = serializers.UsuarioSerializer
    permission_classes = [IsAdminUser]
    search_fields = ["email", "nombres", "apellidos"]
