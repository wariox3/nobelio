"""API del emisor."""
import requests
from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.dian import servicios as dian
from apps.emisores import models, serializers
from apps.nucleo.api import ErrorSolicitud, error_pasarela_dian
from apps.seguridad.alcance import (
    MENSAJE_SIN_CUENTA,
    AlcanceEmisorMixin,
    es_staff,
    exigir_alcance,
    exigir_cuenta,
    puede_dar_de_alta,
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

    @action(detail=False, methods=["post"], url_path="crear-habilitacion")
    def crear_habilitacion(self, request):
        """Arranca la habilitación: software, resolución y documentos de prueba.

        ``POST /api/emisores/emisor/crear-habilitacion/``

        ```json
        {"emisor": 2, "identificador": "94966156-8084-428b-b1b1-a903a053aed1",
         "pin": "12345", "test_set_id": "0d26ba8c-8584-4199-b210-2ddc063c3ddd"}
        ```

Todo en una llamada:

        1. Registra el software (``identificador``, ``pin`` y ``test_set_id``
           son los que entrega la DIAN; los tres obligatorios) y jubila el
           anterior del emisor. Si el ``identificador`` ya está registrado
           responde 400: el SoftwareID se da de alta una sola vez.
        2. Da de alta la resolución del Set de Pruebas y emite con ella la
           factura: la envía, y **espera consultando a la DIAN** hasta que la
           acepte (``SendTestSetAsync`` es asíncrono).
        3. Solo entonces crea y envía la nota crédito —que referencia a esa
           factura y la DIAN la rechazaría si aún no la tuviera registrada— y
           espera igualmente su aceptación.
        4. Con las dos aceptadas marca ``habilitado_facturacion`` en el emisor y
           ``set_pruebas_aceptado`` en el software.

        La respuesta son los ids de los dos documentos, ya en ``aceptado``:

        ```json
        {"factura": "3f2b…", "nota_credito": "9c41…"}
        ```

        Con ellos se consulta todo lo demás en
        ``GET /api/documentos/documento/{id}/``.

        Es una llamada **lenta**: espera a la DIAN dos veces. El techo son
        ``DIAN_SET_PRUEBAS_INTENTOS × DIAN_SET_PRUEBAS_ESPERA`` por documento.

        El emisor tiene que traer ya su certificado: es lo que firma el paso 2,
        y sin él el paso 1 responde 400.

        **Es todo o nada**: si la DIAN rechaza cualquiera de los dos, o no da
        veredicto a tiempo, no queda registrado nada —ni el software, ni la
        resolución, ni los documentos— y responde 400 (o 502 si no contesta).
        Así reintentar es repetir la misma llamada con los mismos datos.

        ``consecutivo`` (opcional) fuerza el número del par de prueba.
        """
        datos = request.data
        software = serializers.HabilitarSerializer(data=datos)
        software.is_valid(raise_exception=True)
        emisor = software.validated_data["emisor"]
        exigir_alcance(request, emisor)

        consecutivo = datos.get("consecutivo")
        # Todo o nada: si la DIAN no responde no puede quedar ni el software ni
        # la resolución ni los documentos. Media habilitación es peor que
        # ninguna —el identificador quedaría tomado y el consecutivo gastado—,
        # y así reintentar es volver a llamar con los mismos datos.
        try:
            with transaction.atomic():
                models.SoftwareDian.objects.filter(
                    emisor=emisor, activo=True
                ).update(activo=False)
                software.save(activo=True)
                documentos = dian.emitir_set_pruebas(
                    emisor, consecutivo=int(consecutivo) if consecutivo else None
                )
        except dian.ErrorEmision as exc:
            raise ErrorSolicitud(str(exc))
        except requests.RequestException as exc:
            raise error_pasarela_dian(exc)

        return Response(documentos, status=status.HTTP_201_CREATED)

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
