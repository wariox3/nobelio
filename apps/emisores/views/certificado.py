"""API del certificado digital del emisor."""
from django.conf import settings
from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.response import Response

from apps.emisores import models, serializers
from apps.emisores.servicios import CertificadoInvalido, validar_pkcs12
from apps.nucleo.api import ErrorSolicitud


class CertificadoViewSet(viewsets.ModelViewSet):
    serializer_class = serializers.CertificadoSerializer
    queryset = models.Certificado.objects.select_related("emisor")

    def get_queryset(self):
        """Permite filtrar por emisor: ``/api/emisores/certificado/?emisor=<id>``."""
        qs = super().get_queryset()
        emisor = self.request.query_params.get("emisor")
        return qs.filter(emisor=emisor) if emisor else qs

    def create(self, request, *args, **kwargs):
        # La subida del .p12 se hace siempre por 'cargar', que valida que
        # Backblaze B2 esté configurado antes de persistir.
        raise MethodNotAllowed(
            "POST",
            detail="Usa /api/emisores/certificado/cargar/ "
            "para subir el certificado.",
        )

    @action(detail=False, methods=["post"], url_path="cargar")
    def cargar(self, request):
        """Sube un certificado ``.p12`` a Backblaze B2 y crea el registro.

        ``POST /api/emisores/certificado/cargar/`` (multipart con
        ``emisor``, ``archivo`` y ``clave``).

        El ``.p12`` es material criptográfico sensible: se almacena siempre en
        B2 (dev y prod), nunca en disco local. Si B2 no está configurado se
        rechaza la subida (400) para no escribir el certificado en disco.

        Antes de almacenarlo se valida el .p12 (integridad, clave, vigencia,
        que la llave sea RSA y que el NIT corresponda al emisor); ver
        ``validar_pkcs12``. Las fechas de vigencia se toman del propio
        certificado.

        Cada emisor mantiene un único certificado vigente: al cargar uno nuevo
        se desactivan (``activo=False``) los anteriores del mismo emisor, que
        se conservan como histórico.
        """
        if not settings.B2_HABILITADO:
            raise ErrorSolicitud(
                "El almacenamiento Backblaze B2 no está configurado; no se "
                "pueden guardar certificados digitales (variables B2_*)."
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        emisor = serializer.validated_data["emisor"]
        archivo = serializer.validated_data["archivo"]

        # Validamos el .p12 con los bytes subidos y devolvemos el puntero al
        # inicio para que el guardado a B2 no quede vacío.
        datos = archivo.read()
        archivo.seek(0)
        try:
            metadatos = validar_pkcs12(
                datos, serializer.validated_data["clave"], emisor
            )
        except CertificadoInvalido as exc:
            raise ErrorSolicitud(str(exc))

        with transaction.atomic():
            # Un solo certificado vigente por emisor: jubilamos los previos y
            # el recién cargado queda activo. Forzamos activo=True porque en
            # multipart un BooleanField ausente llega como False (DRF lo trata
            # como una casilla HTML sin marcar). Las fechas salen del .p12.
            models.Certificado.objects.filter(
                emisor=emisor, activo=True
            ).update(activo=False)
            serializer.save(activo=True, **metadatos)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
