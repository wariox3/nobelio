"""Utilidades para montar una nómina en las pruebas.

Se apoya en dos cosas que ya existen y no conviene duplicar: los catálogos
mínimos de `apps.documentos.tests_utils` —cuenta, tipos de identificación,
geografía, moneda— y `crear_nomina_prueba`, que es el mismo constructor que usa
el endpoint de habilitación. Probar contra él tiene la ventaja de que si la
nómina de habilitación deja de armarse bien, estas pruebas lo dicen.

Los catálogos propios de nómina (tipo de trabajador, periodo, tipo de contrato…)
no se crean aquí: los siembra la migración `catalogos.0006_datos_nomina`, así
que en la base de pruebas ya están.
"""
from apps.documentos.tests_utils import crear_catalogos_minimos, crear_certificado


def crear_catalogos_de_pago():
    """Forma y medio de pago, que la nómina necesita y las pruebas no tienen.

    Los catálogos propios de nómina los siembra `catalogos.0006_datos_nomina`,
    pero estos dos vienen del Genericode de facturación, que se carga con un
    comando de gestión y no por migración: en la base de pruebas no están.
    """
    from apps.catalogos.models import FormaPago, MedioPago

    forma, _ = FormaPago.objects.get_or_create(
        codigo="1", defaults={"nombre": "Contado"}
    )
    medio, _ = MedioPago.objects.get_or_create(
        codigo="10", defaults={"nombre": "Efectivo"}
    )
    return forma, medio


def crear_emisor_de_nomina(catalogos=None, *, nit="901192048"):
    """Emisor con certificado y software de **nómina** activo.

    El software es de tipo `NOMINA` y no de facturación: la DIAN habilita cada
    operación por separado, y el pipeline busca el activo *de su tipo*, así que
    uno de facturación no serviría para firmar una nómina.
    """
    from apps.emisores.models import Emisor, SoftwareDian

    c = catalogos or crear_catalogos_minimos()
    emisor = Emisor.objects.create(
        cuenta=c["cuenta"],
        razon_social="Empresa Demo SAS", nombre_comercial="Demo",
        tipo_identificacion=c["nit"], numero_identificacion=nit,
        digito_verificacion="1", tipo_organizacion=c["juridica"],
        pais=c["colombia"], departamento=c["antioquia"], municipio=c["medellin"],
        direccion="Calle 1 # 2-3", telefono="6041234567", correo="demo@empresa.co",
        ambiente_nomina=2,
    )
    software = SoftwareDian.objects.create(
        emisor=emisor, tipo=SoftwareDian.Tipo.NOMINA,
        identificador="56f2ae4e-9812-4fad-9255-08fcfcd5ccb0",
        pin="12345", test_set_id="set-de-pruebas-nomina",
    )
    certificado = crear_certificado(emisor)
    crear_catalogos_de_pago()
    return {
        "catalogos": c,
        "emisor": emisor,
        "software": software,
        "certificado": certificado,
    }


def crear_nomina(base=None, **kwargs):
    """Una nómina en borrador, con sus conceptos, lista para firmar.

    ``base`` es lo que devuelve `crear_emisor_de_nomina`; si no se pasa, se
    crea uno. ``kwargs`` van tal cual a `crear_nomina_prueba` (prefijo,
    consecutivo, periodo_inicio, periodo_fin).
    """
    from apps.emisores.servicios import crear_nomina_prueba

    base = base or crear_emisor_de_nomina()
    nomina = crear_nomina_prueba(base["emisor"], **kwargs)
    return nomina, base
