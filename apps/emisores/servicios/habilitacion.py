from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.catalogos.models import Moneda, Tributo, UnidadMedida
from apps.documentos.models import (
    Adquiriente,
    Documento,
    DocumentoDetalle,
    DocumentoDetalleImpuesto,
    DocumentoEstado,
    DocumentoTipo,
)

@transaction.atomic
def crear_documento_habilitacion(emisor, resolucion, consecutivo=None):
    if consecutivo is None:
        consecutivo = 990000000

    valor = Decimal("1000.00")
    iva = Decimal("190.00")
    borrador = DocumentoEstado.objects.get(nombre=DocumentoEstado.Nombre.BORRADOR)
    moneda = Moneda.objects.get(codigo="COP")
    unidad = UnidadMedida.objects.get(codigo="94")
    tributo_iva = Tributo.objects.get(codigo="01")

    # --- Factura ------------------------------------------------------------
    factura = Documento.objects.create(
        documento_tipo=DocumentoTipo.objects.get(
            codigo=DocumentoTipo.Codigo.FACTURA_VENTA
        ),
        estado=borrador,
        emisor=emisor,
        ambiente=Documento.Ambiente.PRUEBAS,
        resolucion=resolucion,
        moneda=moneda,
        prefijo=resolucion.prefijo,
        consecutivo=consecutivo,
        fecha_emision=timezone.localdate(),
        hora_emision=timezone.localtime().time(),
        observaciones="Documento del Set de Pruebas (habilitación).",
        valor_bruto=valor,
        total_impuestos=iva,
        total_a_pagar=valor + iva,
    )

    adquiriente = Adquiriente.objects.create(
        documento=factura,
        razon_social=emisor.razon_social,
        tipo_identificacion=emisor.tipo_identificacion,
        numero_identificacion=emisor.numero_identificacion,
        tipo_organizacion=emisor.tipo_organizacion,
        pais=emisor.pais,
        departamento=emisor.departamento,
        municipio=emisor.municipio,
        direccion=emisor.direccion,
        correo=emisor.correo,
        telefono=emisor.telefono,
    )
    adquiriente.responsabilidades.set(emisor.responsabilidades.all())

    detalle = DocumentoDetalle.objects.create(
        documento=factura,
        numero_linea=1,
        descripcion="Servicio de prueba",
        cantidad=Decimal("1"),
        unidad_medida=unidad,
        valor_unitario=valor,
        valor_total=valor,
    )
    DocumentoDetalleImpuesto.objects.create(
        detalle=detalle,
        tributo=tributo_iva,
        tarifa=Decimal("19.00"),
        base_gravable=valor,
        valor=iva,
    )

    # --- Nota crédito -------------------------------------------------------
    # Anula la factura entera (mismos importes) y la referencia por
    # `documento_referencia`, de donde el UBL saca DiscrepancyResponse y
    # BillingReference (ver apps.dian.ubl._ConstructorNotaUBL).
    nota_credito = Documento.objects.create(
        documento_tipo=DocumentoTipo.objects.get(
            codigo=DocumentoTipo.Codigo.NOTA_CREDITO
        ),
        estado=borrador,
        emisor=emisor,
        ambiente=Documento.Ambiente.PRUEBAS,
        resolucion=resolucion,
        documento_referencia=factura,
        moneda=moneda,
        prefijo=resolucion.prefijo,
        consecutivo=consecutivo,
        fecha_emision=timezone.localdate(),
        hora_emision=timezone.localtime().time(),
        observaciones="Nota crédito del Set de Pruebas (habilitación).",
        valor_bruto=valor,
        total_impuestos=iva,
        total_a_pagar=valor + iva,
    )

    adquiriente_nota = Adquiriente.objects.create(
        documento=nota_credito,
        razon_social=emisor.razon_social,
        tipo_identificacion=emisor.tipo_identificacion,
        numero_identificacion=emisor.numero_identificacion,
        tipo_organizacion=emisor.tipo_organizacion,
        pais=emisor.pais,
        departamento=emisor.departamento,
        municipio=emisor.municipio,
        direccion=emisor.direccion,
        correo=emisor.correo,
        telefono=emisor.telefono,
    )
    adquiriente_nota.responsabilidades.set(emisor.responsabilidades.all())

    detalle_nota = DocumentoDetalle.objects.create(
        documento=nota_credito,
        numero_linea=1,
        descripcion="Servicio de prueba",
        cantidad=Decimal("1"),
        unidad_medida=unidad,
        valor_unitario=valor,
        valor_total=valor,
    )
    DocumentoDetalleImpuesto.objects.create(
        detalle=detalle_nota,
        tributo=tributo_iva,
        tarifa=Decimal("19.00"),
        base_gravable=valor,
        valor=iva,
    )
