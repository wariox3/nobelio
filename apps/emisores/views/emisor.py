"""API del emisor."""

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.emisores import models, serializers
from apps.seguridad.alcance import AlcanceEmisorMixin, usuario_del_request
from apps.utilidades.rues import RuesNoDisponible, consultar_detalle


class EmisorViewSet(AlcanceEmisorMixin, viewsets.ModelViewSet):

    campo_emisor = "id"

    queryset = models.Emisor.objects.prefetch_related("resoluciones", "responsabilidades")
    serializer_class = serializers.EmisorSerializer
    search_fields = ["razon_social", "numero_identificacion"]

    def get_serializer_class(self):
        """El listado va sin resoluciones; el detalle y las escrituras sí las llevan."""
        if self.action == "list":
            return serializers.EmisorListaSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        # Sin el campo anidado, traerse las resoluciones del listado entero es
        # una consulta que nadie aprovecha.
        consulta = super().get_queryset()
        if self.action == "list":
            consulta = consulta.prefetch_related(None).prefetch_related(
                "responsabilidades"
            ).select_related("municipio")
        return consulta

    def perform_create(self, serializer):
        """El emisor queda a nombre de quien lo da de alta.

        Con una API Key, a nombre de la persona dueña de la llave: la llave
        actúa por ella, no por sí misma. `usuario` es de solo lectura en el
        serializer, así que el cuerpo no puede ponerlo a nombre de un tercero.
        """
        usuario = usuario_del_request(self.request)
        if usuario is None:
            raise PermissionDenied(
                "Hay que iniciar sesión para dar de alta un emisor."
            )
        serializer.save(usuario=usuario)

    def perform_update(self, serializer):
        """El dueño no se cambia al editar.

        `usuario` es de solo lectura, así que el cuerpo no lo mueve; esto lo
        deja explícito y protege de que alguien lo vuelva escribible sin
        pensarlo. Transferir un emisor a otra persona, si algún día hace falta,
        merece su propia acción y su propia comprobación.
        """
        serializer.save(usuario=serializer.instance.usuario)

    @action(detail=False, methods=["get"], url_path="validar-nit")
    def validar_nit(self, request):
        """Valida un NIT contra el RUES y devuelve sus datos para autocompletar.

        ``GET /api/emisores/emisor/validar-nit/?nit=900123456``

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
