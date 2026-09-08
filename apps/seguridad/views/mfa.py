"""Gestión del segundo factor por su dueño.

Todo aquí exige sesión iniciada y actúa **sobre la propia cuenta**: no hay
endpoint para tocar el segundo factor de otra persona, ni siquiera para el staff.
El segundo factor es de la cuenta, y poder quitárselo a alguien desde fuera
convertiría a quien administra en la manera más fácil de entrar.
"""
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.seguridad import mfa as servicio_mfa
from apps.seguridad.models import METODO_TOTP, METODOS, MfaUsuario
from apps.seguridad.serializers import (
    MfaConfirmarSerializer,
    MfaDesactivarSerializer,
    MfaEnrolarSerializer,
)


class MfaMetodosView(APIView):
    """``GET`` — los métodos disponibles, en el orden en que se ofrecen.

    Se sirve desde aquí para que el front no repita la lista ni el orden.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            "metodos": [{"codigo": c, "nombre": n} for c, n in METODOS],
        })


class MfaEstadoView(APIView):
    """``GET`` — cómo está el segundo factor de quien pregunta."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        mfa = MfaUsuario.objects.filter(usuario=request.user).first()
        return Response({
            "activo": bool(mfa and mfa.activo),
            "metodo": mfa.metodo if mfa else None,
            "codigos_respaldo_restantes": servicio_mfa.respaldos_restantes(
                request.user
            ),
        })


class MfaEnrolarView(APIView):
    """``POST`` — empieza el enrolamiento; todavía no lo enciende.

    Deja la configuración en ``activo=False``. Encenderlo antes de comprobar que
    la app genera códigos válidos dejaría a la persona fuera de su cuenta si el
    reloj de su teléfono anda mal o escaneó otro QR.
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "mfa_gestion"

    def post(self, request):
        serializer = MfaEnrolarSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        metodo = serializer.validated_data["metodo"]

        mfa, _ = MfaUsuario.objects.get_or_create(usuario=request.user)
        mfa.metodo = metodo
        mfa.activo = False
        mfa.ultimo_contador = None

        datos = {"metodo": metodo}
        if metodo == METODO_TOTP:
            secreto = servicio_mfa.generar_secreto()
            mfa.secreto = servicio_mfa.cifrar_secreto(secreto)
            # El secreto en claro sale una sola vez, aquí: es lo que la app tiene
            # que guardar. Después solo existe cifrado.
            datos["secreto"] = secreto
            datos["uri"] = servicio_mfa.uri_otpauth(request.user, secreto)
        else:
            mfa.secreto = ""
            desafio, codigo = servicio_mfa.crear_desafio(request.user, metodo)
            servicio_mfa.enviar_codigo(request.user, codigo)
            datos["mfa_token"] = servicio_mfa.firmar_desafio(desafio)

        mfa.save()
        return Response(datos, status=status.HTTP_201_CREATED)


class MfaConfirmarView(APIView):
    """``POST`` — comprueba el código y enciende el segundo factor.

    Devuelve los códigos de respaldo: es la única vez que existen legibles.
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "mfa_gestion"

    def post(self, request):
        serializer = MfaConfirmarSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        mfa = MfaUsuario.objects.filter(usuario=request.user).first()
        if mfa is None:
            return Response(
                {"detail": "No hay un enrolamiento en curso.", "errores": {}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        codigo = serializer.validated_data["codigo"].strip()
        if mfa.metodo == METODO_TOTP:
            valido = servicio_mfa._verificar_totp(mfa, codigo)
        else:
            desafio, _ = servicio_mfa.crear_desafio(request.user, mfa.metodo)
            valido = False
            token = request.data.get("mfa_token", "")
            try:
                # Sin respaldo: se está probando que el factor nuevo funciona, y
                # un código viejo no lo prueba.
                servicio_mfa.verificar_desafio(token, codigo, permitir_respaldo=False)
                valido = True
            except servicio_mfa.ErrorMfa as exc:
                return Response(
                    {"detail": str(exc), "errores": {}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            finally:
                desafio.delete()

        if not valido:
            return Response(
                {"detail": "El código no es válido.", "errores": {}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        mfa.activo = True
        mfa.save(update_fields=["activo", "actualizado_en"])
        # Encender el segundo factor tiene que echar a quien ya estuviera dentro
        # con la clave robada; si no, solo protegería los ingresos futuros.
        servicio_mfa.olvidar_dispositivos(request.user)
        servicio_mfa.invalidar_sesiones(request.user)

        return Response({
            "activo": True,
            "metodo": mfa.metodo,
            "codigos_respaldo": servicio_mfa.generar_codigos_respaldo(request.user),
        })


class MfaDesactivarView(APIView):
    """``POST`` — apaga el segundo factor. Exige la contraseña."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "mfa_gestion"

    def post(self, request):
        serializer = MfaDesactivarSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not request.user.check_password(serializer.validated_data["password"]):
            return Response(
                {"detail": "La contraseña no es correcta.", "errores": {}},
                status=status.HTTP_403_FORBIDDEN,
            )

        MfaUsuario.objects.filter(usuario=request.user).delete()
        servicio_mfa.olvidar_dispositivos(request.user)
        return Response({"detail": "Segundo factor desactivado."})


class MfaCodigosRespaldoView(APIView):
    """``POST`` — regenera los códigos de respaldo. Exige la contraseña."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "mfa_gestion"

    def post(self, request):
        serializer = MfaDesactivarSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not request.user.check_password(serializer.validated_data["password"]):
            return Response(
                {"detail": "La contraseña no es correcta.", "errores": {}},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response({
            "codigos_respaldo": servicio_mfa.generar_codigos_respaldo(request.user),
        })
