"""API del emisor."""

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.emisores import models, serializers
from apps.nucleo.api import ErrorSolicitud
from apps.seguridad.alcance import AlcanceEmisorMixin, usuario_del_request
from apps.utilidades.rues import RuesNoDisponible, consultar_detalle


class EmisorViewSet(AlcanceEmisorMixin, viewsets.ModelViewSet):

    campo_emisor = "id"
    # Sin PUT: reemplazar el emisor entero exigiría mandar campos que después
    # no se pueden actualizar (ver `CAMPOS_ACTUALIZABLES`). Se edita por PATCH.
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

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
            # Del más nuevo al más viejo. El modelo ordena por razón social, que
            # sirve para los selects pero deja los recién creados perdidos en
            # medio del listado. El `id` es único: la paginación no repite filas.
            consulta = consulta.prefetch_related(None).prefetch_related(
                "responsabilidades"
            ).select_related("municipio").order_by("-id")
        return consulta

    # Lo que impide borrar el emisor: (relación inversa, cómo se nombra).
    # Unas son PROTECT y harían fallar el borrado; las de configuración
    # (software, resoluciones) son CASCADE y se irían con él sin avisar.
    BLOQUEAN_BORRADO = [
        ("documentos", "documento(s) emitido(s)"),
        ("nominas", "nómina(s)"),
        ("empleados", "empleado(s)"),
        ("documentos_recibidos", "documento(s) recibido(s)"),
        ("eventos_recepcion", "evento(s) de recepción"),
        ("adjuntos_recibidos", "adjunto(s) recibido(s)"),
        ("softwares", "software(s)"),
        ("resoluciones", "resolución(es)"),
    ]

    def perform_destroy(self, instance):
        # Comprobado antes de borrar y no capturando el ProtectedError: así el
        # mensaje dice qué tiene el emisor, en vez de un 500. Solo se borra un
        # emisor vacío (dado de alta por error); uno con historia o ya
        # configurado se queda.
        pendientes = []
        for relacion, nombre in self.BLOQUEAN_BORRADO:
            cantidad = getattr(instance, relacion).count()
            if cantidad:
                pendientes.append(f"{cantidad} {nombre}")
        if models.Certificado.objects.filter(emisor=instance).exists():
            pendientes.append("certificado")
        if pendientes:
            raise ErrorSolicitud(
                f"El emisor {instance.numero_identificacion} tiene "
                f"{', '.join(pendientes)} y no se puede borrar. Solo se borra "
                f"un emisor sin documentos ni configuración."
            )
        instance.delete()

    def create(self, request, *args, **kwargs):
        """El de DRF, salvo que un NIT repetido devuelve el `emisor_id` existente.

        Así quien integra puede enlazar el emisor que ya está dado de alta en
        vez de quedarse solo con el mensaje.
        """
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            error = ValidationError(serializer.errors)
            duplicado_id = getattr(serializer, "emisor_duplicado_id", None)
            if duplicado_id is not None:
                error.datos_extra = {"emisor_id": duplicado_id}
            raise error
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

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
