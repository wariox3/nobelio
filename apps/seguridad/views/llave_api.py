"""API de gestión de llaves de API. Solo accesible para usuarios staff."""
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.seguridad.alcance import es_staff

from apps.seguridad.models import LlaveApi
from apps.seguridad.serializers import LlaveApiSerializer


class LlaveApiViewSet(viewsets.ModelViewSet):
    """CRUD de llaves de API.

    Cada persona gestiona las suyas. El secreto completo solo
    se devuelve al crear la llave; para rotarla se crea una nueva y se desactiva
    o borra la anterior (una cuenta puede tener varias llaves vivas, justo para
    que la rotación no deje al ERP sin credencial).
    """

    queryset = LlaveApi.objects.select_related("usuario").all()
    serializer_class = LlaveApiSerializer
    permission_classes = [IsAuthenticated]
    search_fields = ["nombre", "prefijo", "usuario__email"]

    def get_queryset(self):
        """Cada quien ve solo sus llaves; el staff, todas."""
        consulta = super().get_queryset()
        if es_staff(self.request):
            return consulta
        return consulta.filter(usuario=self.request.user)

    def perform_create(self, serializer):
        """La llave queda a nombre de quien la pide.

        No se acepta del cuerpo: una llave a nombre de otro sería una credencial
        para suplantarlo, y quien la crea vería el secreto.
        """
        serializer.save(usuario=self.request.user)
