"""API del certificado digital del emisor."""
from django.conf import settings
from django.db import IntegrityError, transaction
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.emisores import models, serializers
from apps.emisores.servicios import CertificadoInvalido, validar_pkcs12
from apps.nucleo.api import ErrorSolicitud, entero_de_query
from apps.seguridad.alcance import AlcanceEmisorMixin, exigir_alcance


class CertificadoViewSet(
    AlcanceEmisorMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Consulta y baja de certificados; la escritura entra solo por ``cargar``.

    No es un ``ModelViewSet`` a propósito. La única forma de que un ``.p12``
    llegue a la base es ``cargar``, que valida el archivo (``validar_pkcs12``:
    integridad, clave, vigencia, llave RSA y NIT del emisor) y exige que
    Backblaze B2 esté configurado para no dejar material criptográfico en disco
    local. Mientras esto fue un ``ModelViewSet``, ``PUT`` y ``PATCH`` seguían
    publicados y rodeaban las dos comprobaciones: bastaba un ``PATCH`` con un
    ``archivo`` nuevo para guardarlo sin validar, o un ``PATCH`` de ``emisor``
    para mover el certificado a otro emisor del alcance.

    Por eso quedan solo ``list``, ``retrieve``, ``destroy`` y ``cargar``. No hay
    edición: el ``alias`` se fija al subir, y el certificado se reemplaza
    borrando el que hay y cargando el nuevo.

    Tampoco hay un ``create`` que devuelva un 405 con la pista de ``cargar``:
    el router decide qué verbos monta mirando si el viewset *tiene* el método,
    no de qué mixin viene, así que definirlo volvería a publicar ``POST`` en la
    ruta de lista y drf-spectacular lo documentaría como una creación normal.
    """

    serializer_class = serializers.CertificadoSerializer
    queryset = models.Certificado.objects.select_related("emisor")

    def get_queryset(self):
        """Permite filtrar por emisor: ``/api/emisores/certificado/?emisor=<id>``."""
        qs = super().get_queryset()
        emisor = entero_de_query(self.request.query_params, "emisor")
        return qs.filter(emisor=emisor) if emisor else qs

    def perform_destroy(self, instance):
        """Da de baja el certificado y borra su ``.p12`` del almacenamiento.

        ``DELETE /api/emisores/certificado/{id}/``. El alcance ya lo impone el
        queryset: un certificado de otro emisor no se encuentra (404).

        Desde Django 1.3 ``Model.delete()`` no toca el archivo de un
        ``FileField``, así que la baja que traía el mixin dejaba el ``.p12``
        huérfano en el bucket: material criptográfico vivo que la API ya no
        lista ni puede volver a nombrar, y que solo se ve entrando a B2.

        Las dos bajas van juntas: si B2 falla, el error de botocore sube —el
        handler de ``apps.nucleo.api`` lo traduce a 502— y la transacción
        devuelve la fila, de modo que el certificado se puede reintentar en vez
        de quedar como una referencia a un archivo que sigue ahí.

        Es además el paso obligado para renovar: como cada emisor tiene uno
        solo, ``cargar`` rechaza mientras exista este. Durante ese rato el
        emisor no puede emitir —``motivo_no_puede_emitir`` lo dirá— y el
        resumen del emisor queda en ``certificado_activo=False``, que es la
        verdad: no hay con qué firmar hasta que suba el nuevo.
        """
        nombre = instance.archivo.name
        with transaction.atomic():
            instance.delete()
            if nombre:
                # `storage.delete` es idempotente: si el archivo ya no está
                # (una baja anterior a medias, un borrado desde la consola de
                # B2), no se queja.
                instance.archivo.storage.delete(nombre)

    @action(detail=False, methods=["post"], url_path="cargar")
    def cargar(self, request):
        """Sube un certificado ``.p12`` a Backblaze B2 y crea el registro.

        ``POST /api/emisores/certificado/cargar/`` (multipart con
        ``emisor``, ``archivo`` y ``clave``).

        El ``.p12`` es material criptográfico sensible: se almacena siempre en
        B2 (dev y prod), nunca en disco local. Si B2 no está configurado se
        rechaza la subida (400) para no escribir el certificado en disco.

        Antes de almacenarlo se valida el .p12 (integridad, clave, vigencia,
        que la llave sea RSA y que el NIT corresponda al emisor); ver
        ``validar_pkcs12``. Las fechas de vigencia se toman del propio
        certificado.

        Cada emisor tiene **un** certificado, y lo impone la base (el ``emisor``
        del certificado es ``OneToOne``). Si ya hay uno, la carga se rechaza
        con un 400 que dice cuál borrar: renovar es dos pasos explícitos, no un
        reemplazo silencioso, porque subir un .p12 no debería destruir el que
        estaba sin que nadie lo pida.
        """
        if not settings.B2_HABILITADO:
            raise ErrorSolicitud(
                "El almacenamiento Backblaze B2 no está configurado; no se "
                "pueden guardar certificados digitales (variables B2_*)."
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        emisor = serializer.validated_data["emisor"]
        # 'cargar' guarda por su cuenta, así que el alcance se comprueba aquí y
        # no en el perform_create del mixin.
        exigir_alcance(request, emisor)
        self._exigir_sin_certificado(emisor)
        archivo = serializer.validated_data["archivo"]

        # Validamos el .p12 con los bytes subidos y devolvemos el puntero al
        # inicio para que el guardado a B2 no quede vacío.
        datos = archivo.read()
        archivo.seek(0)
        try:
            metadatos = validar_pkcs12(
                datos, serializer.validated_data["clave"], emisor
            )
        except CertificadoInvalido as exc:
            raise ErrorSolicitud(str(exc))

        try:
            # El `atomic` no es por agrupar —solo se escribe una fila—, sino
            # para poder seguir atendiendo la petición si el guardado falla.
            # Sin él, cualquier error a mitad del INSERT deja la transacción
            # marcada y la siguiente consulta muere con un
            # TransactionManagementError en vez de con su propio error: el .p12
            # se sube dentro del INSERT (`FileField.pre_save`), así que un B2
            # caído revienta justo ahí. El savepoint deshace eso y la respuesta
            # sale como el 502 que es.
            with transaction.atomic():
                # Las fechas de vigencia salen del .p12, no del cuerpo.
                serializer.save(**metadatos)
        except IntegrityError:
            # Dos cargas a la vez para el mismo emisor: ambas pasaron la guarda
            # de arriba y el índice único deja entrar solo a una. Sin esto, la
            # segunda sale como un 500.
            self._ya_tiene_certificado(emisor)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def _exigir_sin_certificado(self, emisor):
        """Rechaza la carga si el emisor ya tiene certificado."""
        if models.Certificado.objects.filter(emisor=emisor).exists():
            self._ya_tiene_certificado(emisor)

    @staticmethod
    def _ya_tiene_certificado(emisor):
        """Dice cuál hay que borrar; siempre lanza."""
        # Se relee para dar el id que se acaba de comprobar (o el que ganó la
        # carrera): el mensaje sirve de poco sin la ruta exacta a llamar.
        actual = models.Certificado.objects.filter(emisor=emisor).first()
        ruta = f"/api/emisores/certificado/{actual.id}/" if actual else ""
        raise ErrorSolicitud(
            f"El emisor {emisor.razon_social} ya tiene un certificado digital. "
            f"Cada emisor tiene uno solo: elimine el actual "
            f"(DELETE {ruta}) y vuelva a cargar. Al borrarlo se borra también "
            f"su .p12 del almacenamiento, así que tenga a mano el archivo nuevo."
        )
