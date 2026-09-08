"""Recuperar y restablecer la contraseña.

Las dos rutas son anónimas —quien olvidó su clave no puede autenticarse— y por
eso llevan topes propios: la primera manda un correo a una dirección que elige
quien pide, así que sin tope por destinatario es una máquina de spam contra
terceros.
"""
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.seguridad import mfa as servicio_mfa
from apps.seguridad import recuperacion as servicio
from apps.seguridad.serializers import (
    RecuperacionSerializer,
    RestablecerSerializer,
)


class RecuperarView(APIView):
    """``POST /api/seguridad/token/recuperar/`` — manda el enlace."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "recuperar"
    throttle_scope_rafaga = "recuperar_rafaga"
    throttle_scope_correo = "recuperar_correo"

    def post(self, request):
        serializer = RecuperacionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        usuario = get_user_model().objects.filter(
            email__iexact=serializer.validated_data["email"], is_active=True
        ).first()
        if usuario is not None:
            servicio.enviar_recuperacion(usuario)

        # Respuesta única exista o no la cuenta: si cambiara, este endpoint sería
        # un comprobador de quién está registrado en la plataforma.
        return Response({
            "detail": "Si hay una cuenta con ese correo, le enviamos un enlace "
                      "para restablecer la contraseña.",
        })


class RestablecerView(APIView):
    """``POST /api/seguridad/token/restablecer/`` — fija la contraseña nueva."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "restablecer"
    throttle_scope_rafaga = "restablecer_rafaga"

    def post(self, request):
        serializer = RestablecerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            usuario = servicio.usuario_de_token(serializer.validated_data["token"])
        except servicio.TokenInvalido as exc:
            return Response(
                {"detail": str(exc), "errores": {}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        usuario.set_password(serializer.validated_data["password"])
        # Recibir el correo demuestra que controla esa dirección, que es
        # exactamente lo que verifica el alta. Sin esto, quien se registró y
        # nunca confirmó quedaría en un callejón sin salida: puede cambiar la
        # contraseña pero no entrar.
        usuario.is_verified = True
        usuario.save(update_fields=["password", "is_verified", "actualizado_en"])

        # Si alguien ya estaba dentro con la clave vieja, cambiarla tiene que
        # echarlo; si no, cambiarla no serviría de nada frente a un robo. Y los
        # dispositivos recordados se olvidan por lo mismo que al activar el MFA:
        # cualquier cambio en cómo se protege la cuenta anula las excepciones
        # concedidas antes.
        servicio_mfa.invalidar_sesiones(usuario)
        servicio_mfa.olvidar_dispositivos(usuario)

        return Response({
            "detail": "Contraseña actualizada. Ya puedes iniciar sesión.",
        })
