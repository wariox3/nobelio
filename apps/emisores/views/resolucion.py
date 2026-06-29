"""API de resoluciones de facturación."""
from datetime import date

import requests
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.catalogos.models import TipoFactura
from apps.dian import servicios as dian
from apps.dian import soap
from apps.emisores import models, serializers


def _a_fecha(valor: str):
    """Convierte una fecha de la DIAN (ISO, con o sin hora) a ``date``."""
    if not valor:
        return None
    try:
        return date.fromisoformat(valor[:10])
    except ValueError:
        return None


class ResolucionFacturacionViewSet(viewsets.ModelViewSet):
    queryset = models.ResolucionFacturacion.objects.select_related("emisor", "tipo_factura")
    serializer_class = serializers.ResolucionFacturacionSerializer

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
            return Response(
                {"emisor": "El parámetro 'emisor' es obligatorio."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        emisor = get_object_or_404(models.Emisor, pk=emisor_id)
        respuesta = self._consultar(emisor)
        if isinstance(respuesta, Response):
            return respuesta
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

        ``POST /api/emisores/resolucion/importar-dian/`` con
        ``{"emisor": <id>, "tipo_factura": <id>}``.

        Consulta GetNumberingRange y hace *upsert* de cada rango (clave por
        emisor + tipo_factura + prefijo + número de resolución), guardando la
        ``clave_tecnica`` en el servidor. No devuelve la clave técnica.
        """
        emisor = get_object_or_404(models.Emisor, pk=request.data.get("emisor"))
        tipo_factura = get_object_or_404(
            TipoFactura, pk=request.data.get("tipo_factura"),
        )

        respuesta = self._consultar(emisor)
        if isinstance(respuesta, Response):
            return respuesta

        # Si la DIAN no devuelve rangos, no hay nada que importar: devolvemos su
        # mensaje (p. ej. software sin prefijos asociados) en lugar de 0 cambios.
        if not respuesta.rangos:
            return Response(
                {"detail": respuesta.descripcion
                 or "La DIAN no devolvió rangos de numeración para este emisor."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        guardadas = []
        for r in respuesta.rangos:
            resolucion, _ = models.ResolucionFacturacion.objects.update_or_create(
                emisor=emisor,
                tipo_factura=tipo_factura,
                prefijo=r.prefijo,
                numero_resolucion=r.numero_resolucion,
                defaults={
                    "fecha_resolucion": _a_fecha(r.fecha_resolucion),
                    "rango_desde": r.rango_desde,
                    "rango_hasta": r.rango_hasta,
                    "vigente_desde": _a_fecha(r.vigente_desde),
                    "vigente_hasta": _a_fecha(r.vigente_hasta),
                    "clave_tecnica": r.clave_tecnica,
                    "activa": True,
                },
            )
            guardadas.append(resolucion)

        serializer = self.get_serializer(guardadas, many=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    # -- Internos -----------------------------------------------------------

    def _consultar(self, emisor):
        """Llama a la DIAN; devuelve un ``RespuestaRangos`` o una Response de error."""
        try:
            return dian.consultar_rangos_numeracion(emisor)
        except dian.ErrorEmision as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except requests.HTTPError as exc:
            # La DIAN suele responder 500 con un soap:Fault que explica la causa.
            fault = ""
            if exc.response is not None:
                fault = soap.extraer_fault(exc.response.content)
            return Response(
                {"detail": f"La DIAN rechazó la consulta: {fault or exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except requests.RequestException as exc:
            return Response(
                {"detail": f"Error al comunicarse con la DIAN: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
