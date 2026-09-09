"""Alta pública: registro, confirmación del correo y reenvío del enlace.

Las tres rutas son anónimas —quien se registra todavía no tiene credencial— y
por eso llevan su propio tope (`throttle_scope`), más estrecho que el general de
anónimos: cada una crea filas o dispara un correo por la pasarela.
"""
from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.nucleo.esquema import DetalleSerializer, ErrorSerializer
from apps.seguridad import verificacion
from apps.seguridad.serializers import (
    ReenvioSerializer,
    RegistroSerializer,
    VerificacionSerializer,
)

@extend_schema(
    tags=["Registro"],
    summary="Dar de alta una cuenta",
    description=(
        "Crea el usuario y le manda el correo de confirmación. Hasta que no "
        "confirme, el ingreso responde 403.\n\n"
        "`correo_enviado` en `false` significa que la cuenta sí quedó creada "
        "pero la pasarela de correo falló: hay que pedir el enlace otra vez en "
        "`registro/reenviar/`, no repetir el alta."
    ),
    request=RegistroSerializer,
    responses={
        201: inline_serializer(
            name="RegistroCreado",
            fields={
                "id": serializers.IntegerField(),
                "email": serializers.EmailField(),
                "correo_enviado": serializers.BooleanField(),
                "detail": serializers.CharField(),
            },
        ),
        400: ErrorSerializer,
        429: ErrorSerializer,
    },
)
class RegistroView(APIView):
    """``POST /api/seguridad/registro/`` — crea la cuenta y su usuario dueño."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "registro"
    throttle_scope_rafaga = "registro_rafaga"

    def post(self, request):
        serializer = RegistroSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        usuario = serializer.save()

        # Fuera de la transacción del serializer y después de que haya cerrado:
        # si el correo se mandara dentro y algo hiciera rollback, saldría un
        # enlace hacia un usuario que no llegó a existir.
        enviado = verificacion.enviar_verificacion(usuario)

        return Response(
            {
                "id": usuario.pk,
                "email": usuario.email,
                "correo_enviado": enviado,
                "detail": (
                    "Cuenta creada. Revisa tu correo para confirmarla."
                    if enviado
                    else "Cuenta creada, pero no pudimos enviar el correo de "
                         "confirmación. Pídelo de nuevo en unos minutos."
                ),
            },
            status=status.HTTP_201_CREATED,
        )

@extend_schema(
    tags=["Registro"],
    summary="Confirmar el correo",
    description=(
        "Recibe el token que llegó en el enlace del correo. Es idempotente: "
        "abrirlo dos veces no es un error, porque el segundo clic es lo normal "
        "—el cliente de correo precarga el enlace y la persona recarga—."
    ),
    request=VerificacionSerializer,
    responses={200: DetalleSerializer, 400: ErrorSerializer, 429: ErrorSerializer},
)
class VerificarView(APIView):
    """``POST /api/seguridad/registro/verificar/`` — confirma el correo."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "verificacion"
    throttle_scope_rafaga = "verificacion_rafaga"

    def post(self, request):
        serializer = VerificacionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            usuario = verificacion.usuario_de_token(
                serializer.validated_data["token"]
            )
        except verificacion.TokenInvalido as exc:
            return Response(
                {"detail": str(exc), "errores": {}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Idempotente: abrir el enlace dos veces no es un error. El segundo clic
        # es lo normal (el cliente de correo precarga, la persona recarga).
        if not usuario.is_verified:
            usuario.is_verified = True
            usuario.save(update_fields=["is_verified", "actualizado_en"])

        return Response({"detail": "Correo confirmado. Ya puedes iniciar sesión."})

@extend_schema(
    tags=["Registro"],
    summary="Reenviar el enlace de confirmación",
    description=(
        "Responde siempre lo mismo, exista o no la cuenta y esté o no ya "
        "verificada: distinguirlo convertiría esta ruta en un comprobador de "
        "quién está registrado en la plataforma."
    ),
    request=ReenvioSerializer,
    responses={200: DetalleSerializer, 400: ErrorSerializer, 429: ErrorSerializer},
)
class ReenviarView(APIView):
    """``POST /api/seguridad/registro/reenviar/`` — otro enlace de confirmación."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "reenvio"
    throttle_scope_rafaga = "reenvio_rafaga"
    # El que de verdad protege: sin tope por destinatario, rotar de IP basta
    # para llenarle la bandeja a cualquiera.
    throttle_scope_correo = "reenvio_correo"

    def post(self, request):
        serializer = ReenvioSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        usuario = (
            get_user_model()
            .objects.filter(
                email__iexact=serializer.validated_data["email"],
                is_verified=False,
                is_active=True,
            )
            .first()
        )
        if usuario is not None:
            verificacion.enviar_verificacion(usuario)

        # Respuesta única: no distingue si el correo existe ni si ya estaba
        # verificado. Es lo contrario de lo que hace el alta —que sí dice si el
        # correo está tomado, porque quien lo escribe necesita saberlo— y la
        # asimetría es a propósito: este endpoint acepta cualquier dirección, así
        # que responder distinto lo convertiría en un comprobador de quién tiene
        # cuenta.
        return Response({
            "detail": "Si hay una cuenta sin confirmar con ese correo, le "
                      "enviamos un enlace nuevo.",
        })
