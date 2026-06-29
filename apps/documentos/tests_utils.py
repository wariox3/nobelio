"""Utilidades para construir catálogos y documentos en las pruebas."""
from datetime import date, time
from decimal import Decimal

from apps.catalogos import models as cat


def crear_catalogos_minimos():
    """Crea los registros de catálogo mínimos para las pruebas y los devuelve.

    Incluye una ``cuenta`` (tenant) de conveniencia para crear emisores, aunque
    no sea un catálogo en sentido estricto.
    """
    from apps.cuentas.models import Cuenta

    cuenta = Cuenta.objects.create(nombre="Cuenta Demo")
    nit = cat.TipoIdentificacion.objects.create(codigo="31", nombre="NIT")
    juridica = cat.TipoOrganizacion.objects.create(codigo="1", nombre="Persona Jurídica")
    colombia = cat.Pais.objects.create(codigo="CO", nombre="Colombia")
    antioquia = cat.Departamento.objects.create(codigo="05", nombre="Antioquia")
    medellin = cat.Municipio.objects.create(
        codigo="05001", nombre="Medellín", departamento=antioquia
    )
    cop = cat.Moneda.objects.create(codigo="COP", nombre="Peso colombiano")
    unidad = cat.UnidadMedida.objects.create(codigo="94", nombre="Unidad")
    iva = cat.Tributo.objects.create(codigo="01", nombre="IVA")
    return {
        "cuenta": cuenta,
        "nit": nit,
        "juridica": juridica,
        "colombia": colombia,
        "antioquia": antioquia,
        "medellin": medellin,
        "cop": cop,
        "unidad": unidad,
        "iva": iva,
    }


def crear_documento_factura(catalogos=None):
    """Crea un documento de factura completo (emisor, software, resolución,
    adquirente, documento, línea e impuesto) y devuelve un dict con todo.

    Reutilizable por las pruebas de UBL, firma, PDF y API.
    """
    from apps.catalogos.models import TipoFactura
    from apps.documentos import models as doc
    from apps.emisores.models import Emisor, ResolucionFacturacion, SoftwareDian

    c = catalogos or crear_catalogos_minimos()

    emisor = Emisor.objects.create(
        cuenta=c["cuenta"],
        razon_social="Empresa Demo SAS", nombre_comercial="Demo",
        tipo_identificacion=c["nit"], numero_identificacion="700085371",
        digito_verificacion="1", tipo_organizacion=c["juridica"],
        pais=c["colombia"], departamento=c["antioquia"], municipio=c["medellin"],
        direccion="Calle 1 # 2-3", telefono="6041234567", correo="demo@empresa.co",
    )
    software = SoftwareDian.objects.create(
        emisor=emisor, identificador="56f2ae4e-9812-4fad-9255-08fcfcd5ccb0",
        pin="12345", id_proveedor="700085371",
    )
    tipo_factura, _ = TipoFactura.objects.get_or_create(
        codigo="01", defaults={"nombre": "Factura de Venta"}
    )
    resolucion = ResolucionFacturacion.objects.create(
        emisor=emisor, tipo_factura=tipo_factura, numero_resolucion="18760000001",
        fecha_resolucion=date(2019, 1, 19), prefijo="SETP",
        rango_desde=990000000, rango_hasta=995000000,
        clave_tecnica="693ff6f2a553c3646a063436fd4dd9ded0311471",
        vigente_desde=date(2019, 1, 19), vigente_hasta=date(2030, 1, 19),
    )
    adquirente = doc.Adquiriente.objects.create(
        razon_social="Cliente Demo", tipo_identificacion=c["nit"],
        numero_identificacion="800199436", digito_verificacion="6",
        tipo_organizacion=c["juridica"], pais=c["colombia"],
        departamento=c["antioquia"], municipio=c["medellin"], direccion="Cra 4 # 5-6",
    )
    documento = doc.Documento.objects.create(
        tipo=doc.Documento.Tipo.FACTURA_VENTA, emisor=emisor,
        resolucion=resolucion, adquiriente=adquirente, prefijo="SETP",
        consecutivo=990000129, numero="323200000129",
        fecha_emision=date(2019, 1, 16), hora_emision=time(10, 53, 10),
        moneda=c["cop"], valor_bruto=Decimal("1500000.00"),
        total_impuestos=Decimal("285000.00"), total_a_pagar=Decimal("1785000.00"),
    )
    linea = doc.DocumentoDetalle.objects.create(
        documento=documento, numero_linea=1, descripcion="Producto demo",
        codigo_producto="DEMO-1", cantidad=Decimal("1"), unidad_medida=c["unidad"],
        valor_unitario=Decimal("1500000"), valor_total=Decimal("1500000.00"),
    )
    doc.DocumentoDetalleImpuesto.objects.create(
        detalle=linea, tributo=c["iva"], base_gravable=Decimal("1500000.00"),
        tarifa=Decimal("19.00"), valor=Decimal("285000.00"),
    )
    return {
        "catalogos": c, "emisor": emisor, "software": software,
        "resolucion": resolucion, "adquirente": adquirente, "documento": documento,
    }
