"""Login JWT con topes propios.

SimpleJWT trae la vista, pero no una forma de ponerle topes: `as_view()` solo
acepta atributos que la clase ya tenga, y `throttle_scope` no lo es. De ahí esta
subclase, que además fija el serializer que exige el correo confirmado.
"""
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.seguridad.serializers import TokenVerificadoSerializer


class TokenView(TokenObtainPairView):
    """``POST /api/seguridad/token/`` — email + contraseña a cambio de los JWT."""

    serializer_class = TokenVerificadoSerializer

    throttle_scope = "login"
    throttle_scope_rafaga = "login_rafaga"
    # Por correo, no solo por IP: un ataque de contraseñas repartido entre
    # muchas IP pasa por debajo de cualquier tope por origen, y lo que hay que
    # proteger es la cuenta.
    throttle_scope_correo = "login_correo"
