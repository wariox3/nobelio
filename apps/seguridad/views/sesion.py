"""Rutas de sesión: ingreso, segundo paso, refresco y cierre.

El ingreso puede terminar en dos sitios distintos. Sin segundo factor emite la
sesión de una vez; con él responde un ``mfa_token`` y **no** emite nada hasta que
`SesionMfaView` resuelva el desafío. Esa es la única razón por la que el segundo
factor sirve de algo: si el primer paso ya entregara cookies, el segundo sería
decorativo.
"""
from django.conf import settings
from django.contrib.auth import authenticate
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.nucleo.esquema import DetalleSerializer, ErrorSerializer
from apps.seguridad import mfa as servicio_mfa
from apps.seguridad import sesion as servicio_sesion
from apps.seguridad.autenticacion import COOKIE_DISPOSITIVO, COOKIE_REFRESCO
from apps.seguridad.models import METODOS_ENVIADOS
from apps.seguridad.serializers import (
    IngresoSerializer,
    MfaIngresoSerializer,
    ReenvioMfaSerializer,
    UsuarioMeSerializer,
)

_SESION_MAXIMA = int(settings.SESION_MAXIMA.total_seconds())


@extend_schema(
    tags=["Sesión"],
    summary="Iniciar sesión",
    description=(
        "Con la contraseña correcta puede terminar en **dos sitios distintos**, "
        "y el front tiene que mirar `mfa_requerido` antes de nada:\n\n"
        "- **Sin segundo factor**: emite la sesión. Los tokens NO viajan en el "
        "cuerpo, sino en cookies `httpOnly` (`access_token`, `refresh_token`); "
        "el cuerpo trae los datos de la persona. Hay que llamar con "
        "`credentials: \"include\"`.\n"
        "- **Con segundo factor**: no emite nada. Responde `mfa_requerido: true` "
        "y un `mfa_token` que se resuelve en `token/mfa/`.\n\n"
        "Un 403 significa que falta confirmar el correo."
    ),
    request=IngresoSerializer,
    responses={
        200: inline_serializer(
            name="IngresoRespuesta",
            fields={
                "mfa_requerido": serializers.BooleanField(
                    required=False,
                    help_text="Solo presente —y siempre `true`— cuando falta el segundo paso.",
                ),
                "mfa_token": serializers.CharField(
                    required=False, help_text="Se manda a `token/mfa/`. Solo con `mfa_requerido`.",
                ),
                "metodo": serializers.CharField(required=False),
                "id": serializers.IntegerField(required=False),
                "email": serializers.EmailField(required=False),
                "nombre_corto": serializers.CharField(required=False),
            },
        ),
        401: ErrorSerializer,
        403: ErrorSerializer,
        429: ErrorSerializer,
    },
)
class SesionView(APIView):
    """``POST /api/seguridad/token/`` — email y contraseña."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "login"
    throttle_scope_rafaga = "login_rafaga"
    throttle_scope_correo = "login_correo"

    def post(self, request):
        serializer = IngresoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        usuario = authenticate(
            request,
            username=email,
            password=serializer.validated_data["password"],
        )
        if usuario is None:
            return Response(
                {"detail": "Credenciales inválidas.", "errores": {}},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not usuario.is_verified:
            return Response(
                {
                    "detail": "Tienes que confirmar tu correo antes de iniciar "
                              "sesión.",
                    "errores": {},
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # El segundo factor se mira recién aquí, con la clave ya validada: así el
        # endpoint no sirve de oráculo para saber qué cuentas lo tienen puesto.
        mfa = servicio_mfa.mfa_activo(usuario)
        recordado = bool(mfa) and servicio_mfa.dispositivo_recordado(
            usuario, request.COOKIES.get(COOKIE_DISPOSITIVO)
        )
        if mfa and not recordado:
            desafio, codigo = servicio_mfa.crear_desafio(
                usuario, mfa.metodo, ip=request.META.get("REMOTE_ADDR")
            )
            if mfa.metodo in METODOS_ENVIADOS:
                servicio_mfa.enviar_codigo(usuario, codigo)
            return Response({
                "mfa_requerido": True,
                "mfa_token": servicio_mfa.firmar_desafio(desafio),
                "metodo": mfa.metodo,
            })

        return servicio_sesion.emitir(
            usuario, UsuarioMeSerializer(usuario).data, request=request
        )


@extend_schema(
    tags=["Sesión"],
    summary="Resolver el segundo factor",
    description=(
        "Segundo paso del ingreso. Con el código correcto emite la sesión en "
        "cookies, igual que `token/`.\n\n"
        "`recordar_dispositivo` deja una cookie que se salta el segundo paso en "
        "los próximos ingresos desde ese navegador. `uso_codigo_respaldo` avisa "
        "de que se gastó uno de los códigos de emergencia."
    ),
    request=MfaIngresoSerializer,
    responses={
        200: inline_serializer(
            name="IngresoMfaRespuesta",
            fields={
                "id": serializers.IntegerField(),
                "email": serializers.EmailField(),
                "nombre_corto": serializers.CharField(),
                "uso_codigo_respaldo": serializers.BooleanField(),
            },
        ),
        401: ErrorSerializer,
        429: ErrorSerializer,
    },
)
class SesionMfaView(APIView):
    """``POST /api/seguridad/token/mfa/`` — resuelve el segundo paso."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "mfa"
    throttle_scope_rafaga = "mfa_rafaga"

    def post(self, request):
        serializer = MfaIngresoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            verificacion = servicio_mfa.verificar_desafio(
                serializer.validated_data["mfa_token"],
                serializer.validated_data["codigo"],
            )
        except servicio_mfa.ErrorMfa as exc:
            return Response(
                {"detail": str(exc), "errores": {}},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        datos = UsuarioMeSerializer(verificacion.usuario).data
        datos["uso_codigo_respaldo"] = verificacion.uso_respaldo
        return servicio_sesion.emitir(
            verificacion.usuario,
            datos,
            request=request,
            recordar_dispositivo=serializer.validated_data["recordar_dispositivo"],
        )


@extend_schema(
    tags=["Sesión"],
    summary="Reenviar el código del segundo factor",
    description="Solo para los métodos que mandan el código; con TOTP no aplica.",
    request=ReenvioMfaSerializer,
    responses={200: DetalleSerializer, 400: ErrorSerializer, 429: ErrorSerializer},
)
class SesionMfaReenviarView(APIView):
    """``POST /api/seguridad/token/mfa/reenviar/`` — otro código."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "mfa_envio"
    throttle_scope_rafaga = "mfa_envio_rafaga"

    def post(self, request):
        serializer = ReenvioMfaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            servicio_mfa.reenviar_codigo(serializer.validated_data["mfa_token"])
        except servicio_mfa.ErrorMfa as exc:
            return Response(
                {"detail": str(exc), "errores": {}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"detail": "Código reenviado."})


@extend_schema(
    tags=["Sesión"],
    summary="Renovar la sesión",
    description=(
        "**Sin cuerpo**: el refresh viaja en su cookie `httpOnly` y de ahí se "
        "lee. Rota el token —el anterior queda anulado— y reemplaza las dos "
        "cookies.\n\n"
        "El 401 tiene dos causas que el front trata igual: no hay sesión, o la "
        "sesión alcanzó su duración máxima absoluta. En ambos casos toca volver "
        "a iniciar sesión."
    ),
    request=None,
    responses={200: DetalleSerializer, 401: ErrorSerializer, 429: ErrorSerializer},
)
class RefrescoView(APIView):
    """``POST /api/seguridad/token/refresh/`` — renueva el acceso."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "refresco"

    def post(self, request):
        crudo = request.COOKIES.get(COOKIE_REFRESCO)
        if not crudo:
            return Response(
                {"detail": "No hay sesión que renovar.", "errores": {}},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            refresco = RefreshToken(crudo)

            # Tope absoluto. Sin esto, `set_exp()` corre el vencimiento en cada
            # rotación y la sesión no caduca nunca: un refresh robado que se rote
            # a diario viviría para siempre, y el segundo factor solo se verifica
            # al ingresar. `iat` de respaldo cubre los tokens anteriores al claim.
            inicio = refresco.payload.get(servicio_sesion.CLAIM_INICIO) or \
                refresco.payload.get("iat")
            if inicio and timezone.now().timestamp() - inicio > _SESION_MAXIMA:
                return Response(
                    {
                        "detail": "La sesión alcanzó su duración máxima. "
                                  "Inicia sesión de nuevo.",
                        "errores": {},
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            acceso = str(refresco.access_token)
            refresco.blacklist()
            refresco.set_jti()
            refresco.set_exp()
            refresco.set_iat()
            # Se copia el inicio original: la rotación renueva el token, no la
            # sesión.
            refresco[servicio_sesion.CLAIM_INICIO] = inicio
            nuevo = str(refresco)
        except TokenError:
            return Response(
                {"detail": "La sesión no es válida o expiró.", "errores": {}},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        respuesta = Response({"detail": "Sesión renovada."})
        servicio_sesion.poner_cookies(respuesta, acceso, nuevo)
        return respuesta


@extend_schema(
    tags=["Sesión"],
    summary="Cerrar sesión",
    description=(
        "Sin cuerpo. Anula el refresh y borra las cookies. Cerrar sesión con "
        "una sesión ya rota no es un error: el resultado es el que se pedía."
    ),
    request=None,
    responses={200: DetalleSerializer, 401: ErrorSerializer},
)
class CierreSesionView(APIView):
    """``POST /api/seguridad/token/cerrar/`` — cierra la sesión."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        crudo = request.COOKIES.get(COOKIE_REFRESCO)
        if crudo:
            try:
                RefreshToken(crudo).blacklist()
            except TokenError:
                # Ya era inválido: cerrar sesión con una sesión rota no es un
                # error, el resultado es el que se pedía.
                pass
        return servicio_sesion.limpiar_cookies(
            Response({"detail": "Sesión cerrada."})
        )


@extend_schema(
    tags=["Sesión"],
    summary="Quién es quien pregunta",
    description=(
        "Resuelve la identidad de la credencial que trae la petición, sea la "
        "cookie de sesión o una llave de API."
    ),
    responses={200: UsuarioMeSerializer, 401: ErrorSerializer},
)
class MeView(APIView):
    """``GET /api/seguridad/me/`` — quién es quien pregunta."""

    def get(self, request):
        return Response(UsuarioMeSerializer(request.user).data)
