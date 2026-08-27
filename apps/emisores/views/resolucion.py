"""API de resoluciones de numeración."""
from datetime import date

import requests
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.catalogos.models import TipoFactura
from apps.dian import servicios as dian
from apps.dian import soap
from apps.emisores import models, serializers
from apps.nucleo.api import ErrorPasarela, ErrorSolicitud
from apps.seguridad.alcance import AlcanceEmisorMixin, exigir_alcance

# GetNumberingRange solo devuelve numeración de facturación de venta.
CODIGO_FACTURA_VENTA = "01"


def _a_fecha(valor: str):
    """Convierte una fecha de la DIAN (ISO, con o sin hora) a ``date``."""
    if not valor:
        return None
    try:
        return date.fromisoformat(valor[:10])
    except ValueError:
        return None


class ResolucionViewSet(AlcanceEmisorMixin, viewsets.ModelViewSet):
    queryset = models.Resolucion.objects.select_related("emisor", "tipo_factura")
    serializer_class = serializers.ResolucionSerializer

    def get_queryset(self):
        """Permite filtrar por emisor: ``/api/emisores/resolucion/?emisor=<id>``."""
        qs = super().get_queryset()
        emisor = self.request.query_params.get("emisor")
        return qs.filter(emisor=emisor) if emisor else qs

    @action(detail=False, methods=["get"], url_path="consulta-dian")
    def consulta_dian(self, request):
        """Consulta en la DIAN los rangos de numeración del emisor (GetNumberingRange).

        ``GET /api/emisores/resolucion/consulta-dian/?emisor=<id>``

        Solo para previsualizar: devuelve los rangos autorizados sin persistir.
        La clave técnica no se expone (es sensible); se indica únicamente si
        está presente. Para guardarla usa ``importar-dian``.
        """
        emisor_id = request.query_params.get("emisor")
        if not emisor_id:
            raise ValidationError({"emisor": "El parámetro 'emisor' es obligatorio."})
        emisor = get_object_or_404(models.Emisor, pk=emisor_id)
        exigir_alcance(request, emisor)
        respuesta = self._consultar(emisor)
        datos = [
            {
                "prefijo": r.prefijo,
                "numero_resolucion": r.numero_resolucion,
                "fecha_resolucion": _a_fecha(r.fecha_resolucion),
                "rango_desde": r.rango_desde,
                "rango_hasta": r.rango_hasta,
                "vigente_desde": _a_fecha(r.vigente_desde),
                "vigente_hasta": _a_fecha(r.vigente_hasta),
                "tiene_clave_tecnica": bool(r.clave_tecnica),
            }
            for r in respuesta.rangos
        ]
        # Incluimos el mensaje de la DIAN: si rangos viene vacío explica el motivo
        # (p. ej. "No registra prefijos asociados al código de software: ...").
        return Response({
            "codigo": respuesta.codigo,
            "descripcion": respuesta.descripcion,
            "rangos": datos,
        })

    @action(detail=False, methods=["post"], url_path="importar-dian")
    def importar_dian(self, request):
        """Importa los rangos de la DIAN y crea/actualiza las resoluciones.

        ``POST /api/emisores/resolucion/importar-dian/`` con ``{"emisor": <id>}``.

        Consulta GetNumberingRange y crea solo las resoluciones que el emisor
        todavía no tiene (se comparan prefijo + número de resolución), guardando
        la ``clave_tecnica`` en el servidor. Las que ya existen no se tocan: si
        la DIAN cambió un rango se edita a mano. No devuelve la clave técnica.

        Todo lo importado entra como factura de venta (tipo ``01``), que es la
        numeración que devuelve esta operación.
        """
        emisor = get_object_or_404(models.Emisor, pk=request.data.get("emisor"))
        exigir_alcance(request, emisor)
        tipo_factura = get_object_or_404(TipoFactura, codigo=CODIGO_FACTURA_VENTA)

        respuesta = self._consultar(emisor)

        # Si la DIAN no devuelve rangos, no hay nada que importar: devolvemos su
        # mensaje (p. ej. software sin prefijos asociados) en lugar de 0 cambios.
        if not respuesta.rangos:
            raise ErrorSolicitud(
                respuesta.descripcion
                or "La DIAN no devolvió rangos de numeración para este emisor."
            )

        # Se mira el emisor entero, sin filtrar por tipo de factura: una misma
        # resolución no se importa dos veces por estar registrada bajo otro tipo.
        existentes = set(
            models.Resolucion.objects
            .filter(emisor=emisor)
            .values_list("prefijo", "numero_resolucion")
        )
        nuevos = [
            r for r in respuesta.rangos
            if (r.prefijo or "", r.numero_resolucion) not in existentes
        ]

        # Esta acción persiste sin pasar por el serializer, así que la regla de
        # numeración se comprueba aquí: si el NIT ya numera con ese prefijo y
        # resolución en otra cuenta, no se importa nada.
        for r in nuevos:
            ocupada = models.resolucion_activa_en_otra_cuenta(
                emisor, prefijo=r.prefijo or "", numero_resolucion=r.numero_resolucion
            )
            if ocupada is not None:
                raise ErrorSolicitud(models.mensaje_resolucion_ocupada(ocupada))

        creadas = [
            models.Resolucion.objects.create(
                emisor=emisor,
                tipo_factura=tipo_factura,
                prefijo=r.prefijo,
                numero_resolucion=r.numero_resolucion,
                fecha_resolucion=_a_fecha(r.fecha_resolucion),
                rango_desde=r.rango_desde,
                rango_hasta=r.rango_hasta,
                vigente_desde=_a_fecha(r.vigente_desde),
                vigente_hasta=_a_fecha(r.vigente_hasta),
                clave_tecnica=r.clave_tecnica,
                activa=True,
            )
            for r in nuevos
        ]

        serializer = self.get_serializer(creadas, many=True)
        codigo = status.HTTP_201_CREATED if creadas else status.HTTP_200_OK
        return Response(serializer.data, status=codigo)

    # -- Internos -----------------------------------------------------------

    def _consultar(self, emisor):
        """Llama a la DIAN; devuelve un ``RespuestaRangos`` o lanza la excepción."""
        try:
            return dian.consultar_rangos_numeracion(emisor)
        except dian.ErrorEmision as exc:
            raise ErrorSolicitud(str(exc))
        except requests.HTTPError as exc:
            # La DIAN suele responder 500 con un soap:Fault que explica la causa.
            fault = ""
            if exc.response is not None:
                fault = soap.extraer_fault(exc.response.content)
            raise ErrorPasarela(f"La DIAN rechazó la consulta: {fault or exc}")
        except requests.RequestException as exc:
            raise ErrorPasarela(f"Error al comunicarse con la DIAN: {exc}")
