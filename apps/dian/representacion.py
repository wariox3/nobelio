"""
Representación gráfica (PDF) de los documentos electrónicos DIAN.

Genera un PDF con los datos obligatorios del documento y el código QR exigido
por el Anexo Técnico v1.9 (sección 11.7): el QR codifica la URL de consulta del
documento en el catálogo de la DIAN y debe medir al menos 2 cm.

Usa reportlab (PDF) y qrcode (código bidimensional).
"""
from __future__ import annotations

import io

import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from apps.dian.identificadores import formatear_hora


def url_consulta_dian(cufe: str, ambiente: int) -> str:
    """URL de consulta del documento en el catálogo DIAN (contenido del QR)."""
    subdominio = "catalogo-vpfe-hab" if ambiente == 2 else "catalogo-vpfe"
    return f"https://{subdominio}.dian.gov.co/document/searchqr?documentkey={cufe}"


def generar_qr_png(texto: str, *, tamano_cm: float = 4.0) -> bytes:
    """Genera un PNG con el código QR del texto dado."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(texto)
    qr.make(fit=True)
    imagen = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    return buffer.getvalue()


def _moneda(valor, codigo="COP") -> str:
    return f"$ {valor:,.2f} {codigo}"


class GeneradorPDF:
    """Construye la representación gráfica en PDF de un documento."""

    def __init__(self, documento, *, ambiente: int):
        self.doc = documento
        self.ambiente = ambiente
        self.estilos = getSampleStyleSheet()
        self.estilos.add(ParagraphStyle("Mini", fontSize=7, leading=9))
        self.estilos.add(ParagraphStyle("MiniBold", fontSize=7, leading=9, fontName="Helvetica-Bold"))
        self.estilos.add(ParagraphStyle("TituloDoc", fontSize=13, leading=16, fontName="Helvetica-Bold"))

    def generar(self) -> bytes:
        buffer = io.BytesIO()
        pdf = SimpleDocTemplate(
            buffer, pagesize=letter,
            leftMargin=1.5 * cm, rightMargin=1.5 * cm,
            topMargin=1.5 * cm, bottomMargin=1.5 * cm,
            title=f"Factura {self.doc.numero}",
        )
        elementos = []
        elementos += self._encabezado()
        elementos.append(Spacer(1, 0.4 * cm))
        elementos += self._partes()
        elementos.append(Spacer(1, 0.3 * cm))
        elementos.append(self._tabla_lineas())
        elementos.append(Spacer(1, 0.3 * cm))
        elementos += self._totales()
        elementos.append(Spacer(1, 0.4 * cm))
        elementos += self._pie_qr()
        pdf.build(elementos)
        return buffer.getvalue()

    # -- Secciones ----------------------------------------------------------

    def _encabezado(self):
        emisor = self.doc.emisor
        info_emisor = Paragraph(
            f"<b>{emisor.razon_social}</b><br/>"
            f"NIT: {emisor.numero_identificacion}"
            f"{'-' + emisor.digito_verificacion if emisor.digito_verificacion else ''}<br/>"
            f"{emisor.direccion}<br/>"
            f"{emisor.municipio.nombre if emisor.municipio else ''}"
            f"{', ' + emisor.telefono if emisor.telefono else ''}",
            self.estilos["Mini"],
        )
        validacion = ""
        if self.doc.fecha_validacion:
            validacion = (
                "<br/>Validación DIAN: "
                f"{self.doc.fecha_validacion.strftime('%Y-%m-%d %H:%M:%S')}"
            )
        info_doc = Paragraph(
            f"<b>{self.doc.documento_tipo.nombre.upper()}</b><br/>"
            f"<b>No. {self.doc.numero}</b><br/>"
            f"Fecha: {self.doc.fecha_emision.isoformat()}<br/>"
            f"Hora: {formatear_hora(self.doc.hora_emision)}"
            f"{validacion}",
            self.estilos["Mini"],
        )
        tabla = Table([[info_emisor, info_doc]], colWidths=[11 * cm, 7 * cm])
        tabla.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
        ]))
        return [tabla]

    def _partes(self):
        adq = self.doc.adquiriente
        resolucion = self.doc.resolucion
        adquirente = Paragraph(
            f"<b>Adquiriente:</b> {adq.razon_social}<br/>"
            f"Identificación: {adq.numero_identificacion}"
            f"{'-' + adq.digito_verificacion if adq.digito_verificacion else ''}<br/>"
            f"{adq.direccion}",
            self.estilos["Mini"],
        )
        pago = Paragraph(
            f"<b>Moneda:</b> {self.doc.moneda.codigo}<br/>"
            f"<b>Forma de pago:</b> {self.doc.forma_pago.nombre if self.doc.forma_pago else 'N/A'}<br/>"
            f"<b>Medio de pago:</b> {self.doc.medio_pago.nombre if self.doc.medio_pago else 'N/A'}"
            + (f"<br/><b>Resolución DIAN:</b> {resolucion.numero_resolucion}" if resolucion else ""),
            self.estilos["Mini"],
        )
        tabla = Table([[adquirente, pago]], colWidths=[11 * cm, 7 * cm])
        tabla.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
        ]))
        return [tabla]

    def _tabla_lineas(self):
        encabezados = ["#", "Descripción", "Cant.", "Und.", "Vr. Unitario", "Dscto.", "Total"]
        filas = [encabezados]
        for linea in self.doc.detalles.all():
            filas.append([
                str(linea.numero_linea),
                Paragraph(linea.descripcion, self.estilos["Mini"]),
                f"{linea.cantidad:g}",
                linea.unidad_medida.codigo,
                _moneda(linea.valor_unitario, self.doc.moneda.codigo),
                _moneda(linea.descuento, self.doc.moneda.codigo),
                _moneda(linea.valor_total, self.doc.moneda.codigo),
            ])
        tabla = Table(filas, colWidths=[0.8 * cm, 7.2 * cm, 1.5 * cm, 1.2 * cm, 2.6 * cm, 2 * cm, 2.7 * cm])
        tabla.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#243b53")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f4f8")]),
        ]))
        return tabla

    def _totales(self):
        m = self.doc.moneda.codigo
        filas = [
            ["Valor bruto:", _moneda(self.doc.valor_bruto, m)],
            ["Descuentos:", _moneda(self.doc.total_descuentos, m)],
            ["Total impuestos:", _moneda(self.doc.total_impuestos, m)],
            ["TOTAL A PAGAR:", _moneda(self.doc.total_a_pagar, m)],
        ]
        tabla = Table(filas, colWidths=[4 * cm, 4 * cm], hAlign="RIGHT")
        tabla.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("LINEABOVE", (0, -1), (-1, -1), 0.5, colors.black),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        return [tabla]

    def _pie_qr(self):
        cufe = self.doc.cufe_cude
        url = url_consulta_dian(cufe, self.ambiente)
        qr_png = generar_qr_png(url)
        imagen = Image(io.BytesIO(qr_png), width=4 * cm, height=4 * cm)

        info = Paragraph(
            f"<b>CUFE/CUDE:</b><br/><font size=6>{cufe}</font><br/><br/>"
            "Representación gráfica de la factura electrónica de venta.<br/>"
            "Documento generado conforme a la Resolución DIAN 000165 de 2023.<br/>"
            f"Validar en: <font size=6>{url}</font>",
            self.estilos["Mini"],
        )
        tabla = Table([[imagen, info]], colWidths=[4.5 * cm, 13.5 * cm])
        tabla.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        return [tabla]


def generar_pdf(documento, *, ambiente: int) -> bytes:
    """Genera el PDF de la representación gráfica de un documento."""
    return GeneradorPDF(documento, ambiente=ambiente).generar()
