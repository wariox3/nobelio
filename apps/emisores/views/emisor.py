"""API del emisor."""
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.emisores import models, serializers
from apps.seguridad.alcance import (
    AlcanceEmisorMixin,
    cuenta_de_la_credencial,
    es_staff,
)
from apps.utilidades.rues import RuesNoDisponible, consultar_detalle


class EmisorViewSet(AlcanceEmisorMixin, viewsets.ModelViewSet):
    """CRUD de emisores, acotado a los que alcanza el solicitante.

    Aquí el propio modelo es el emisor, así que el alcance se aplica sobre el
    ``id``. Dar de alta un emisor es cosa de la integración (que lo cuelga de su
    propia cuenta) o del staff (que indica cuál); un usuario humano no tiene
    cuenta de la que colgarlo, así que no puede crearlos.
    """

    # El modelo es el emisor: el alcance filtra por su propia clave.
    campo_emisor = "id"

    queryset = models.Emisor.objects.prefetch_related("resoluciones", "responsabilidades")
    serializer_class = serializers.EmisorSerializer
    search_fields = ["razon_social", "numero_identificacion", "nombre_comercial"]

    def create(self, request, *args, **kwargs):
        # Se comprueba antes de validar el cuerpo: quien no tiene cuenta de la
        # que colgar el emisor merece un 403 claro, no un 400 sobre los campos.
        if cuenta_de_la_credencial(request) is None and not es_staff(request):
            raise PermissionDenied(
                "Solo el staff o una integración pueden dar de alta emisores."
            )
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        # Para una integración la cuenta ya la puso el default del serializer y
        # `validate_cuenta` impidió que apuntara a otra. Al staff, que no tiene
        # credencial de cuenta, hay que exigírsela.
        if not serializer.validated_data.get("cuenta"):
            raise ValidationError({"cuenta": "Es obligatoria para el staff."})
        serializer.save()

    def perform_update(self, serializer):
        # El alcance ya limitó el queryset; esto impide además que un emisor se
        # mueva a otra cuenta desde el cuerpo de la petición.
        if es_staff(self.request):
            serializer.save()
        else:
            serializer.save(cuenta=serializer.instance.cuenta)

    @action(detail=False, methods=["get"], url_path="validar-nit")
    def validar_nit(self, request):
        """Valida un NIT contra el RUES y devuelve sus datos para autocompletar.

        ``GET /api/emisores/validar-nit/?nit=900123456``

        Respuestas:
          - 200 ``{"existe": true, ...datos...}``  si el NIT está en el RUES.
          - 200 ``{"existe": false}``              si no se encuentra.
          - 400 si falta el parámetro ``nit``.
          - 503 si el servicio RUES no está disponible.
        """
        nit = (request.query_params.get("nit") or "").strip()
        if not nit:
            return Response(
                {"detail": "Debe indicar el parámetro 'nit'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            empresa = consultar_detalle(nit)
        except RuesNoDisponible as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if empresa is None:
            return Response({"existe": False, "nit": nit})

        return Response(
            {
                "existe": True,
                "nit": empresa.nit,
                "digito_verificacion": empresa.digito_verificacion,
                "razon_social": empresa.razon_social,
                "estado_matricula": empresa.estado_matricula,
                "activa": empresa.activa,
                "organizacion_juridica": empresa.organizacion_juridica,
                "camara_comercio": empresa.camara_comercio,
                "correo": empresa.correo,
                "direccion": empresa.direccion,
                "telefono": empresa.telefono,
                "actividad_ciiu": empresa.actividad_ciiu,
                "actividad_ciiu_descripcion": empresa.actividad_ciiu_descripcion,
            }
        )
