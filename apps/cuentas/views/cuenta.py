"""API de cuentas (clientes/tenants).

Cada quien ve y toca **solo las cuentas de las que es dueño**; el staff, todas.
El aislamiento se hace acotando el queryset y no comprobando permisos objeto a
objeto, porque así una cuenta ajena no es un 403 sino un 404: no se puede usar
el endpoint para averiguar qué ids existen.
"""
from django.db.models import ProtectedError
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.cuentas.models import Cuenta
from apps.cuentas.serializers import CuentaSerializer
from apps.nucleo.api import ErrorSolicitud
from apps.seguridad.alcance import cuentas_propias, es_staff


class CuentaViewSet(viewsets.ModelViewSet):
    """CRUD de cuentas. Cada usuario gestiona las suyas; el staff, todas."""

    queryset = Cuenta.objects.all()
    serializer_class = CuentaSerializer
    permission_classes = [IsAuthenticated]
    search_fields = ["nombre", "identificacion", "correo_contacto"]

    def get_queryset(self):
        """El staff ve todas; los demás, solo las suyas.

        Una integración (API Key) no ve ninguna: su cuenta se la impone la
        llave, no la administra. ``cuentas_propias`` ya devuelve vacío para
        ella, que es la única definición de "cuenta propia" del proyecto.
        """
        if es_staff(self.request):
            return super().get_queryset()
        return cuentas_propias(self.request)

    def perform_create(self, serializer):
        """El dueño es quien la crea. Solo el staff puede ponerla a otro nombre.

        Se impone aquí y no en el serializer para que un ``usuario`` en el
        cuerpo no pueda abrir una cuenta a nombre de un tercero: el valor que
        llegue se descarta.
        """
        if es_staff(self.request):
            if serializer.validated_data.get("usuario") is None:
                raise ErrorSolicitud("El campo 'usuario' es obligatorio para el staff.")
            serializer.save()
            return
        serializer.save(usuario=self.request.user)

    def perform_update(self, serializer):
        """Nadie que no sea staff puede regalar ni robar una cuenta.

        El queryset ya impide tocar la de otro; esto impide lo simétrico:
        cambiarle el dueño a la propia para pasársela a un tercero, o
        apuntársela a uno mismo.
        """
        if es_staff(self.request):
            serializer.save()
            return
        serializer.save(usuario=serializer.instance.usuario)

    def perform_destroy(self, instance):
        """Borra la cuenta, salvo que todavía cuelgue algo de ella.

        ``Emisor.cuenta`` es ``PROTECT``, así que borrar una cuenta con emisores
        lanza ``ProtectedError``, que no es una excepción de DRF y sin esto
        saldría como un 500 con traceback. Es una situación normal —no un
        fallo—, y la respuesta tiene que decir qué hacer: los documentos
        fiscales que cuelgan de esos emisores no se pueden perder por un DELETE.
        """
        try:
            instance.delete()
        except ProtectedError:
            raise ErrorSolicitud(
                "No se puede eliminar una cuenta que todavía tiene emisores. "
                "Elimina primero sus emisores."
            )
