"""Aviso al emisor de que la DIAN aceptó su set de pruebas.

Cuando la DIAN confirma que el set de pruebas de un software quedó aceptado
(``apps.dian.servicios._marcar_habilitacion_superada``), sale un correo por
Zinc al **correo del emisor** (``Emisor.correo``). Uno por operación —
facturación, nómina y documento equivalente se habilitan por separado— y solo
la primera vez: quien llama lo pide únicamente cuando la bandera del software
pasa de falso a verdadero.

Solo con ``ENV=prod``. En ``dev`` y ``test`` la base puede tener emisores
reales, y un set de pruebas cerrado desde ahí le mandaría el aviso a un cliente
desde un entorno que no es el suyo. Queda en el log.

Va con la marca RedEDoc, de Semántica Digital: los colores salen del «Manual de
Marca RedDoc», los mismos del frontend (``neon/src/styles/custom.css``). El
nombre se pinta con texto y no con una imagen: Gmail no muestra SVG.

Lo que viene del emisor (razón social) se escapa antes de meterlo en el HTML.
"""
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.html import escape

from apps.nucleo.entorno import es_produccion
from apps.utilidades.zinc import Zinc, ZincError

logger = logging.getLogger(__name__)

APLICACION = "nobelio"

# Colores de marca.
OXFORD = "#13263c"
OCEANO = "#009ef7"
VERDE = "#5cc361"
E_MARCA = "#00c48c"  # la «E» del nombre
GRIS_TEXTO = "#55677e"
GRIS_SUAVE = "#e6ecf3"
FONDO = "#f4f7fa"
FUENTE = "Roboto,'Helvetica Neue',Arial,sans-serif"

# El nombre de cada operación tal como lo lee el emisor, por tipo de software.
OPERACIONES = {
    "facturacion": "Facturación electrónica",
    "nomina": "Nómina electrónica",
    "documento_equivalente": "Documento equivalente P.O.S.",
}


def nombre_operacion(software) -> str:
    return OPERACIONES.get(software.tipo, software.get_tipo_display())


def _nombre_marca(color_red) -> str:
    return (
        f'<span style="color:{color_red}">Red</span>'
        f'<span style="color:{E_MARCA}">E</span>'
        f'<span style="color:{OCEANO}">Doc</span>'
    )


def _filas(software, emisor, aceptado_en):
    nit = emisor.numero_identificacion
    if emisor.digito_verificacion:
        nit = f"{nit}-{emisor.digito_verificacion}"
    return [
        ("Empresa", emisor.razon_social),
        ("NIT", nit),
        ("Operación", nombre_operacion(software)),
        ("Software ID", software.identificador),
        ("Set de pruebas", software.test_set_id or "—"),
        ("Aceptado el", timezone.localtime(aceptado_en).strftime("%d/%m/%Y %I:%M %p")),
    ]


def _pasos(operacion):
    return [
        f"Los documentos de {operacion.lower()} ya no van al set de pruebas: "
        "se envían a producción.",
        "Asocia en el portal de la DIAN los prefijos y la resolución de numeración "
        "de producción a este software.",
        "Emite el primer documento real y confirma que la DIAN lo acepta.",
    ]


def cuerpo_html(software, emisor, aceptado_en) -> str:
    """El cuerpo del aviso: encabezado de marca, datos y qué sigue."""
    operacion = nombre_operacion(software)
    tabla = "".join(
        "<tr>"
        f'<td style="padding:10px 0;color:{GRIS_TEXTO};font-size:13px;'
        f'border-bottom:1px solid {GRIS_SUAVE};width:38%">{escape(campo)}</td>'
        f'<td style="padding:10px 0;color:{OXFORD};font-size:14px;font-weight:500;'
        f'border-bottom:1px solid {GRIS_SUAVE};word-break:break-all">{escape(valor)}</td>'
        "</tr>"
        for campo, valor in _filas(software, emisor, aceptado_en)
    )
    pasos = "".join(
        "<tr>"
        '<td style="vertical-align:top;padding:0 10px 10px 0">'
        f'<div style="width:22px;height:22px;line-height:22px;border-radius:11px;'
        f'background:{OCEANO};color:#ffffff;font-size:12px;font-weight:700;'
        f'text-align:center">{numero}</div></td>'
        f'<td style="vertical-align:top;padding:2px 0 10px;color:{OXFORD};'
        f'font-size:14px;line-height:1.5">{escape(paso)}</td>'
        "</tr>"
        for numero, paso in enumerate(_pasos(operacion), 1)
    )
    return f"""
<div style="margin:0;padding:32px 12px;background:{FONDO};font-family:{FUENTE}">
  <table role="presentation" cellspacing="0" cellpadding="0" style="width:100%;max-width:580px;margin:0 auto;border-collapse:collapse">
    <tr><td style="background:{OXFORD};border-radius:10px 10px 0 0;padding:22px 32px">
      <span style="font-size:26px;font-weight:700;letter-spacing:-0.5px">{_nombre_marca("#ffffff")}</span>
      <div style="color:#b9c6d6;font-size:12px;margin-top:2px">Facturación electrónica · Habilitación DIAN</div>
    </td></tr>
    <tr><td style="height:4px;background:{OCEANO};line-height:4px;font-size:0">&nbsp;</td></tr>
    <tr><td style="background:#ffffff;padding:32px">
      <div style="display:inline-block;background:#e4f6e5;color:#1e7a24;font-size:12px;font-weight:700;padding:6px 12px;border-radius:14px;letter-spacing:.3px">✓ HABILITACIÓN COMPLETA</div>
      <h1 style="margin:16px 0 8px;color:{OXFORD};font-size:22px;line-height:1.3;font-weight:700">¡La DIAN aceptó tu set de pruebas!</h1>
      <p style="margin:0 0 24px;color:{GRIS_TEXTO};font-size:15px;line-height:1.6">
        <strong style="color:{OXFORD}">{escape(emisor.razon_social)}</strong> terminó la habilitación de
        <strong style="color:{OXFORD}">{escape(operacion.lower())}</strong>. Desde ahora puede emitir documentos con validez ante la DIAN.
      </p>
      <table role="presentation" cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse;margin-bottom:28px;border-top:3px solid {VERDE}">
        {tabla}
      </table>
      <div style="color:{OXFORD};font-size:16px;font-weight:700;margin-bottom:14px">¿Qué sigue?</div>
      <table role="presentation" cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse">
        {pasos}
      </table>
    </td></tr>
    <tr><td style="background:#ffffff;border-radius:0 0 10px 10px;border-top:1px solid {GRIS_SUAVE};padding:18px 32px">
      <p style="margin:0;color:{GRIS_TEXTO};font-size:12px;line-height:1.6">
        Este aviso lo envía RedEDoc automáticamente cuando la DIAN confirma la habilitación. No hace falta responderlo.
      </p>
    </td></tr>
    <tr><td style="padding:18px 32px;text-align:center;color:#8496ad;font-size:12px;line-height:1.6">
      <span style="font-weight:700">{_nombre_marca(OXFORD)}</span>
      es un servicio de <a href="https://www.semantica.com.co" style="color:{OCEANO};text-decoration:none">Semántica Digital S.A.S.</a>
    </td></tr>
  </table>
</div>
"""


def payload_zinc(software, emisor, aceptado_en) -> dict:
    """Arma el cuerpo que espera Zinc en ``/api/correo/html``."""
    return {
        "correo": emisor.correo,
        "asunto": f"✅ Set de pruebas aceptado · {emisor.razon_social}",
        "contenido": cuerpo_html(software, emisor, aceptado_en),
        "nombreRemitente": getattr(settings, "ZINC_NOMBRE_REMITENTE", APLICACION),
        "aplicacion": APLICACION,
    }


def enviar_aviso_set_aceptado(software, *, zinc=None) -> bool:
    """Envía el aviso al correo del emisor. Devuelve si salió.

    **No propaga el fallo**: la habilitación ya quedó marcada cuando esto se
    llama, y un mal momento de la pasarela no puede tumbar la emisión ni la
    consulta que la detectó. Queda en el log. Sin correo en el emisor, o fuera
    de ``ENV=prod``, no se envía.

    El cliente se puede inyectar para las pruebas.
    """
    emisor = software.emisor
    if not es_produccion():
        logger.info(
            "Aviso de set de pruebas aceptado omitido (ENV=%s): software %s, emisor %s",
            settings.ENV, software.pk, emisor.pk,
        )
        return False
    if not emisor.correo:
        logger.warning(
            "El emisor %s no tiene correo: no sale el aviso de set de pruebas aceptado",
            emisor.pk,
        )
        return False
    cliente = zinc or Zinc()
    try:
        respuesta = cliente.correo_html(
            payload_zinc(software, emisor, software.actualizado_en or timezone.now())
        )
    except ZincError:
        logger.exception(
            "No se pudo enviar el aviso de set de pruebas aceptado del software %s",
            software.pk,
        )
        return False
    if respuesta.get("error"):
        logger.error(
            "Zinc rechazó el aviso de set de pruebas aceptado del software %s: %s",
            software.pk, respuesta,
        )
        return False
    return True


def avisar_al_confirmar(software):
    """Programa el aviso para cuando la transacción haya confirmado.

    Si la marca hiciera rollback, no saldría el aviso de una habilitación que
    no quedó guardada. Fuera de una transacción, ``on_commit`` lo ejecuta en el
    acto.
    """
    transaction.on_commit(lambda: enviar_aviso_set_aceptado(software))
