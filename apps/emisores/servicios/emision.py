"""¿Está el emisor en condiciones de emitir?

Un documento no sirve de nada si al llegar a `emitir/` no hay con qué firmarlo,
así que la pregunta se responde ya al **crearlo**: es un error de datos maestros
(falta cargar el .p12, o venció) y cuanto antes se vea, menos documentos
huérfanos quedan en borrador.

La misma regla la usa el pipeline de firma (`apps.dian.servicios`), para que lo
que se promete al crear sea exactamente lo que se exige al emitir.
"""
from django.utils import timezone

MENSAJE_EMISOR_INACTIVO = "El emisor está inactivo; no puede emitir documentos."
MENSAJE_SIN_CERTIFICADO = "El emisor no tiene un certificado digital activo."


def certificado_activo(emisor):
    """El certificado activo del emisor (el más reciente), o ``None``.

    El orden lo pone ``Certificado.Meta.ordering = ["-creado_en"]``: cargar uno
    nuevo jubila el anterior, así que el primero es siempre el vigente.
    """
    return emisor.certificados.filter(activo=True).first()


def motivo_no_puede_emitir(emisor, fecha=None):
    """Explica por qué el emisor no puede emitir, o ``None`` si sí puede.

    Se comprueba contra ``fecha`` (hoy por defecto) y no contra la fecha de
    emisión del documento: lo que importa es que el certificado esté vigente en
    el momento de firmar, que es ahora.

    Las vigencias nulas no bloquean: se rellenan solas desde el propio .p12 al
    cargarlo (ver ``validar_pkcs12``), y un certificado antiguo sin fechas no
    debe dejar a un emisor sin poder facturar.
    """
    if not emisor.activo:
        return MENSAJE_EMISOR_INACTIVO

    certificado = certificado_activo(emisor)
    if certificado is None:
        return MENSAJE_SIN_CERTIFICADO

    fecha = fecha or timezone.localdate()
    if certificado.vigente_hasta and certificado.vigente_hasta < fecha:
        return (
            f"El certificado digital del emisor venció el "
            f"{certificado.vigente_hasta}. Cargue uno vigente en "
            f"/api/emisores/certificado/cargar/ antes de emitir."
        )
    if certificado.vigente_desde and certificado.vigente_desde > fecha:
        return (
            f"El certificado digital del emisor no rige hasta el "
            f"{certificado.vigente_desde}."
        )
    return None
