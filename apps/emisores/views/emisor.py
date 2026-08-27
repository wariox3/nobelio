"""API del emisor."""
from datetime import date

from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.catalogos.models import TipoFactura
from apps.emisores import models, serializers
from apps.emisores.models.emisor import Emisor
from apps.emisores.models.certificado import Certificado
from apps.emisores.servicios import crear_documento_habilitacion
from apps.nucleo.api import ErrorSolicitud
from apps.seguridad.alcance import (
    MENSAJE_SIN_CUENTA,
    AlcanceEmisorMixin,
    es_staff,
    exigir_cuenta,
    puede_dar_de_alta,
)
from apps.utilidades.rues import RuesNoDisponible, consultar_detalle


class EmisorViewSet(AlcanceEmisorMixin, viewsets.ModelViewSet):

    campo_emisor = "id"

    queryset = models.Emisor.objects.prefetch_related("resoluciones", "responsabilidades")
    serializer_class = serializers.EmisorSerializer
    search_fields = ["razon_social", "numero_identificacion", "nombre_comercial"]

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

    def create(self, request, *args, **kwargs):
        # Se comprueba antes de validar el cuerpo: quien no tiene cuenta de la
        # que colgar el emisor merece un 403 claro, no un 400 sobre los campos.
        if not puede_dar_de_alta(request):
            raise PermissionDenied(MENSAJE_SIN_CUENTA)
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        # Para una integración la cuenta ya la puso el default del serializer y
        # `validate_cuenta` impidió que apuntara a otra. Al staff, que no tiene
        # credencial de cuenta, hay que exigírsela.
        cuenta = serializer.validated_data.get("cuenta")
        if not cuenta:
            raise ValidationError({"cuenta": "Es obligatoria para el staff."})
        # Última puerta antes de escribir: el serializer ya lo comprobó, pero la
        # regla se verifica aquí contra `alcance` para que ningún cambio futuro
        # en el serializer pueda abrir un alta en cuenta ajena en silencio.
        exigir_cuenta(self.request, cuenta)
        serializer.save()

    def perform_update(self, serializer):
        # El alcance ya limitó el queryset; esto impide además que un emisor se
        # mueva a otra cuenta desde el cuerpo de la petición. Solo el staff
        # puede cambiarla; si no la indica (PUT sin 'cuenta', donde el default
        # de la credencial es None) se conserva la que ya tenía.
        cuenta = serializer.instance.cuenta
        if es_staff(self.request):
            cuenta = serializer.validated_data.get("cuenta") or cuenta
        serializer.save(cuenta=cuenta)

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

    @action(detail=False, methods=["post"], url_path="crear-habilitacion")
    def crear_habilitacion(self, request):
        
        try:
            emisor = Emisor.objects.get(pk=request.data.get("emisor"))
        except (Emisor.DoesNotExist, TypeError, ValueError):
            raise ErrorSolicitud("El emisor indicado no existe.")

        certificado = Certificado.objects.filter(emisor=emisor, activo=True).first()
        if certificado is None:
            raise ErrorSolicitud("No hay certificado digital activo para el emisor. ")
        hoy = timezone.localdate()
        if certificado.vigente_hasta and certificado.vigente_hasta < hoy:
            raise ErrorSolicitud("EL certificado esta vencido")

        identificador = request.data.get("identificador")
        existente = models.SoftwareDian.objects.filter(emisor=emisor, identificador=identificador).exists()

        consecutivo = request.data.get("consecutivo")
        if consecutivo not in (None, ""):
            try:
                consecutivo = int(consecutivo)
            except (TypeError, ValueError):
                raise ErrorSolicitud("El consecutivo debe ser un número entero.")
            if consecutivo < 1:
                raise ErrorSolicitud("El consecutivo debe ser mayor que cero.")
        else:
            consecutivo = None

        if not existente:
            software = serializers.SoftwareDianSerializer(data=request.data)
            software.is_valid(raise_exception=True)

        with transaction.atomic():
            if not existente:
                models.SoftwareDian.objects.filter(emisor=emisor, activo=True).update(activo=False)
                software.save(activo=True)

            resolucion, _ = models.Resolucion.objects.update_or_create(
                emisor=emisor,
                tipo_factura=TipoFactura.objects.get(codigo="01"),
                prefijo="SETP",
                numero_resolucion="18760000001",
                defaults={
                    "fecha_resolucion": date(2026, 6, 29),
                    "rango_desde": 990000000,
                    "rango_hasta": 995000000,
                    "clave_tecnica": "fc8eac422eba16e22ffd8c6f94b3f40a6e38162c",
                    "vigente_desde": date(2019, 1, 19),
                    "vigente_hasta": date(2030, 1, 19),
                    "activa": True,
                },
            )
            crear_documento_habilitacion(emisor, resolucion, consecutivo=consecutivo)

        return Response({}, status=status.HTTP_200_OK)

    
