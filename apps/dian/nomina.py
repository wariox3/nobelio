"""
Generación del XML del documento soporte de pago de nómina electrónica.

Vive aquí, junto a ``ubl.py``, porque es el mismo oficio —armar el XML que se le
manda a la DIAN— pero **no comparte nada con él**: la nómina no es UBL. Su raíz
y su namespace son propios, la información va en atributos en vez de en
elementos con texto, y no hay extensiones DIAN, ni resolución, ni impuestos, ni
adquiriente. Lo único común es la firma: el ``ext:UBLExtensions`` vacío que deja
este módulo es el que rellena ``apps/dian/firma``.

Referencia: Anexo Técnico Nómina Electrónica v1.0 (Res. 000013/2021), resumen en
``docs/anexo-nomina.md``.
"""
from __future__ import annotations

from decimal import Decimal

from lxml import etree

from apps.dian import identificadores as ident

# --- Namespaces -------------------------------------------------------------
NS_NOMINA = "dian:gov:co:facturaelectronica:NominaIndividual"
NS_NOMINA_AJUSTE = "dian:gov:co:facturaelectronica:NominaIndividualDeAjuste"

NS = {
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
    "xades": "http://uri.etsi.org/01903/v1.3.2#",
    "xades141": "http://uri.etsi.org/01903/v1.4.1#",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

# Literal exacto de @Version (regla NIE022): es el equivalente del ProfileID.
VERSION_NOMINA = "V1.0: Documento Soporte de Pago de Nómina Electrónica"

# El de la nota de ajuste (NIAE022), **sin** los espacios que lo rodean en la
# tabla de reglas del anexo: allí aparece como `" V1.0: … "` y en la tabla de
# campos sin ellos. Se emite sin espacios porque emitir el `ProfileID` de la
# nota de ajuste del documento soporte *con* el espacio que traía su anexo costó
# un rechazo NSAD03; si la DIAN rechaza este por NIAE022, se prueba con ellos.
VERSION_NOTA_AJUSTE = (
    "V1.0: Nota de Ajuste de Documento Soporte de Pago de Nómina Electrónica"
)

# Tipo de XML (numeral 5.5.7): identifica el perfil y entra en el CUNE.
TIPO_XML_NOMINA = "102"
TIPO_XML_NOTA_AJUSTE = "103"

# @Idioma del lugar de generación (ISO 639, numeral 5.3.1).
IDIOMA_ESPANOL = "es"

CERO = Decimal("0")


def _q(prefijo: str, etiqueta: str) -> etree.QName:
    return etree.QName(NS[prefijo], etiqueta)


def _sub(padre, etiqueta, texto=None, **atributos):
    """Crea un hijo con texto y atributos opcionales.

    El namespace lo hereda del padre en vez de fijarlo: la nómina y su nota de
    ajuste tienen namespaces distintos y el cuerpo se arma con el mismo código,
    así que colgar del padre es lo que hace que la nota no salga con la mitad de
    los elementos en el namespace equivocado.

    Los atributos en ``None`` no se emiten: el XSD los declara opcionales y
    mandarlos vacíos es un rechazo, no una omisión.
    """
    elem = etree.SubElement(padre, etree.QName(etree.QName(padre).namespace, etiqueta))
    if texto is not None:
        elem.text = str(texto)
    for clave, valor in atributos.items():
        if valor is not None:
            elem.set(clave, str(valor))
    return elem


def _valor(monto) -> str:
    """Formatea un monto para el XML.

    Usa el mismo formateo que el CUNE (truncado a dos decimales) a propósito:
    si el XML dijera un número y el hash se hubiera calculado con otro, la DIAN
    rechazaría el documento por CUNE mal calculado.
    """
    return ident.formatear_valor(monto if monto is not None else CERO)


def _fecha(valor) -> str | None:
    return valor.isoformat() if valor is not None else None


def _booleano(valor) -> str:
    """El XSD los declara ``xsd:boolean``; la DIAN los escribe en minúscula."""
    return "true" if valor else "false"


class ConstructorNominaXML:
    """Arma el ``NominaIndividual`` de una nómina ya liquidada.

    No calcula nada: los totales y los conceptos vienen del modelo. Lo único que
    deriva es el CUNE, que depende de esos totales y del PIN del software.
    """

    nombre_raiz = "NominaIndividual"
    ns_raiz = NS_NOMINA
    version = VERSION_NOMINA
    tipo_xml = TIPO_XML_NOMINA

    def __init__(self, nomina, *, software, ambiente: int, pin: str = ""):
        self.doc = nomina
        self.software = software
        self.ambiente = ambiente
        self.pin = pin or (software.pin if software else "")
        self.empleado = nomina.empleado
        self.emisor = nomina.emisor
        self.conceptos = list(nomina.conceptos.all())

    # -- API pública --------------------------------------------------------

    def calcular_identificador(self) -> str:
        """CUNE: los dos totales de la nómina y el tipo de XML, sin impuestos."""
        return ident.calcular_cune(
            numero_documento=self.doc.numero,
            fecha=self.doc.fecha_generacion,
            hora=self.doc.hora_generacion,
            valor_devengado=self.doc.total_devengados,
            valor_deduccion=self.doc.total_deducciones,
            valor_total=self.doc.total_comprobante,
            nit_empleador=self.emisor.numero_identificacion,
            documento_empleado=self.empleado.numero_documento,
            tipo_xml=self.tipo_xml,
            pin_software=self.pin,
            tipo_ambiente=self.ambiente,
        )

    def construir(self) -> etree._Element:
        cune = self.doc.cune or self.calcular_identificador()
        self.cune = cune

        nsmap = {**NS, None: self.ns_raiz}
        raiz = etree.Element(etree.QName(self.ns_raiz, self.nombre_raiz), nsmap=nsmap)
        raiz.set(
            _q("xsi", "schemaLocation"),
            f"{self.ns_raiz} {self.nombre_raiz}ElectronicaXSD.xsd",
        )
        # El XSD lo declara obligatorio aunque no signifique nada para nosotros.
        raiz.set("SchemaLocation", "")

        self._extensiones(raiz)
        self._cuerpo(raiz, cune)
        return raiz

    def generar_xml(self) -> bytes:
        return etree.tostring(
            self.construir(), xml_declaration=True, encoding="UTF-8", standalone=False
        )

    # -- Secciones ----------------------------------------------------------

    def _extensiones(self, raiz):
        """``ext:UBLExtensions`` vacío: la firma XAdES la añade el firmador.

        Mientras esté vacío el XML no valida contra el XSD (exige una extensión
        como mínimo); valida el firmado, que es el que se envía. Las
        ejemplificaciones oficiales tienen el mismo hueco.
        """
        etree.SubElement(raiz, _q("ext", "UBLExtensions"))

    def _cuerpo(self, raiz, cune):
        """Los elementos en el orden que fija el XSD, que es el del anexo."""
        self._novedad(raiz)
        self._periodo(raiz)
        self._numero_secuencia(raiz)
        self._lugar_generacion(raiz)
        self._proveedor(raiz)
        _sub(raiz, "CodigoQR", self._url_qr(cune))
        self._informacion_general(raiz, cune)
        if self.doc.notas:
            _sub(raiz, "Notas", self.doc.notas)
        self._empleador(raiz)
        self._trabajador(raiz)
        self._pago(raiz)
        self._fechas_pagos(raiz)
        self._devengados(raiz)
        self._deducciones(raiz)
        if self.doc.redondeo:
            _sub(raiz, "Redondeo", _valor(self.doc.redondeo))
        _sub(raiz, "DevengadosTotal", _valor(self.doc.total_devengados))
        _sub(raiz, "DeduccionesTotal", _valor(self.doc.total_deducciones))
        _sub(raiz, "ComprobanteTotal", _valor(self.doc.total_comprobante))

    def _novedad(self, raiz):
        """Cambio contractual: apunta al CUNE donde estaba el dato anterior."""
        if not self.doc.novedad:
            return
        _sub(raiz, "Novedad", _booleano(True), CUNENov=self.doc.cune_novedad or None)

    def _periodo(self, raiz):
        _sub(
            raiz, "Periodo",
            FechaIngreso=_fecha(self.empleado.fecha_ingreso),
            FechaRetiro=_fecha(self.doc.fecha_retiro),
            FechaLiquidacionInicio=_fecha(self.doc.fecha_liquidacion_inicio),
            FechaLiquidacionFin=_fecha(self.doc.fecha_liquidacion_fin),
            TiempoLaborado=self.doc.tiempo_laborado,
            FechaGen=_fecha(self.doc.fecha_generacion),
        )

    def _numero_secuencia(self, raiz, *, con_codigo_trabajador=True):
        _sub(
            raiz, "NumeroSecuenciaXML",
            CodigoTrabajador=(self.doc.codigo_trabajador or None)
            if con_codigo_trabajador else None,
            Prefijo=self.doc.prefijo or None,
            Consecutivo=self.doc.consecutivo,
            Numero=self.doc.numero,
        )

    def _lugar_generacion(self, raiz):
        """Dónde se generó el XML: la sede del emisor."""
        _sub(
            raiz, "LugarGeneracionXML",
            Pais=self.emisor.pais.codigo,
            DepartamentoEstado=self.emisor.departamento.codigo,
            MunicipioCiudad=self.emisor.municipio.codigo,
            Idioma=IDIOMA_ESPANOL,
        )

    def _proveedor(self, raiz):
        """``ProveedorXML``: quién generó el XML. Con software propio, el emisor."""
        _sub(
            raiz, "ProveedorXML",
            RazonSocial=self.emisor.razon_social,
            NIT=self.emisor.numero_identificacion,
            DV=self.emisor.digito_verificacion or "0",
            SoftwareID=self.software.identificador,
            SoftwareSC=ident.calcular_codigo_seguridad_software(
                id_software=self.software.identificador,
                pin=self.pin,
                numero_documento=self.doc.numero,
            ),
        )

    def _informacion_general(self, raiz, cune, *, reducida=False):
        """El bloque ``Eliminar`` lo lleva recortado: sin periodo ni moneda."""
        atributos = {
            "Version": self.version,
            "Ambiente": self.ambiente,
            "TipoXML": self.tipo_xml,
            "CUNE": cune,
            "EncripCUNE": ident.SCHEME_NAME_CUNE,
            "FechaGen": _fecha(self.doc.fecha_generacion),
            "HoraGen": ident.formatear_hora(self.doc.hora_generacion),
        }
        if not reducida:
            atributos.update({
                "PeriodoNomina": self.doc.periodo_nomina.codigo,
                "TipoMoneda": self.doc.moneda.codigo,
                "TRM": _valor(self.doc.trm) if self.doc.trm is not None else None,
            })
        _sub(raiz, "InformacionGeneral", **atributos)

    def _empleador(self, raiz):
        _sub(
            raiz, "Empleador",
            RazonSocial=self.emisor.razon_social,
            NIT=self.emisor.numero_identificacion,
            DV=self.emisor.digito_verificacion or "0",
            Pais=self.emisor.pais.codigo,
            DepartamentoEstado=self.emisor.departamento.codigo,
            MunicipioCiudad=self.emisor.municipio.codigo,
            Direccion=self.emisor.direccion,
        )

    def _trabajador(self, raiz):
        """La identidad sale del empleado; las condiciones, de la nómina.

        Es el reparto del modelo: en el maestro un cambio es una corrección y
        debe propagarse; en las condiciones es un hecho nuevo y el documento se
        quedó con las suyas al crearse.
        """
        emp, doc = self.empleado, self.doc
        _sub(
            raiz, "Trabajador",
            TipoTrabajador=doc.tipo_trabajador.codigo,
            SubTipoTrabajador=doc.subtipo_trabajador.codigo,
            AltoRiesgoPension=_booleano(doc.alto_riesgo_pension),
            TipoDocumento=emp.tipo_identificacion.codigo,
            NumeroDocumento=emp.numero_documento,
            PrimerApellido=emp.primer_apellido,
            SegundoApellido=emp.segundo_apellido,
            PrimerNombre=emp.primer_nombre,
            OtrosNombres=emp.otros_nombres or None,
            LugarTrabajoPais=doc.lugar_trabajo_pais.codigo,
            LugarTrabajoDepartamentoEstado=doc.lugar_trabajo_departamento.codigo,
            LugarTrabajoMunicipioCiudad=doc.lugar_trabajo_municipio.codigo,
            LugarTrabajoDireccion=doc.lugar_trabajo_direccion,
            SalarioIntegral=_booleano(doc.salario_integral),
            TipoContrato=doc.tipo_contrato.codigo,
            Sueldo=_valor(doc.sueldo),
            CodigoTrabajador=doc.codigo_trabajador or None,
        )

    def _pago(self, raiz):
        doc = self.doc
        _sub(
            raiz, "Pago",
            Forma=doc.forma_pago.codigo,
            Metodo=doc.medio_pago.codigo,
            Banco=doc.banco or None,
            TipoCuenta=doc.get_tipo_cuenta_display() if doc.tipo_cuenta else None,
            NumeroCuenta=doc.numero_cuenta or None,
        )

    def _fechas_pagos(self, raiz):
        fechas = _sub(raiz, "FechasPagos")
        _sub(fechas, "FechaPago", _fecha(self.doc.fecha_pago))

    def _url_qr(self, cune) -> str:
        subdominio = "catalogo-vpfe-hab" if self.ambiente == 2 else "catalogo-vpfe"
        return f"https://{subdominio}.dian.gov.co/document/searchqr?documentkey={cune}"

    # -- Devengados y deducciones -------------------------------------------

    def _por_concepto(self, grupo):
        """Agrupa las filas del grupo por concepto, conservando su orden."""
        indice = {}
        for fila in self.conceptos:
            if fila.grupo == grupo:
                indice.setdefault(fila.concepto, []).append(fila)
        return indice

    def _devengados(self, raiz):
        from apps.nomina.models import NominaConcepto as NC

        filas = self._por_concepto(NC.Grupo.DEVENGADO)
        dev = _sub(raiz, "Devengados")

        # `Basico` es obligatorio: si no viene, sale en ceros antes que un XML
        # que el XSD rechaza por estructura.
        basico = (filas.get(NC.Concepto.BASICO) or [None])[0]
        _sub(
            dev, "Basico",
            DiasTrabajados=int(basico.cantidad) if basico and basico.cantidad else 0,
            SueldoTrabajado=_valor(basico.valor if basico else CERO),
        )

        for fila in filas.get(NC.Concepto.AUXILIO_TRANSPORTE, []):
            _sub(dev, "Transporte", AuxilioTransporte=_valor(fila.valor))
        for fila in filas.get(NC.Concepto.VIATICOS, []):
            _sub(dev, "Transporte",
                 ViaticoManuAlojS=_valor(fila.valor),
                 ViaticoManuAlojNS=_valor(fila.valor_no_salarial))

        for concepto, contenedor, etiqueta in (
            (NC.Concepto.HED, "HEDs", "HED"),
            (NC.Concepto.HEN, "HENs", "HEN"),
            (NC.Concepto.HRN, "HRNs", "HRN"),
            (NC.Concepto.HEDDF, "HEDDFs", "HEDDF"),
            (NC.Concepto.HRDDF, "HRDDFs", "HRDDF"),
            (NC.Concepto.HENDF, "HENDFs", "HENDF"),
            (NC.Concepto.HRNDF, "HRNDFs", "HRNDF"),
        ):
            self._horas_extra(dev, filas.get(concepto, []), contenedor, etiqueta)

        comunes = filas.get(NC.Concepto.VACACIONES_COMUNES, [])
        compensadas = filas.get(NC.Concepto.VACACIONES_COMPENSADAS, [])
        if comunes or compensadas:
            vac = _sub(dev, "Vacaciones")
            for fila in comunes:
                _sub(vac, "VacacionesComunes",
                     FechaInicio=_fecha(fila.fecha_inicio),
                     FechaFin=_fecha(fila.fecha_fin),
                     Cantidad=int(fila.cantidad or 0), Pago=_valor(fila.valor))
            for fila in compensadas:
                _sub(vac, "VacacionesCompensadas",
                     Cantidad=int(fila.cantidad or 0), Pago=_valor(fila.valor))

        for fila in filas.get(NC.Concepto.PRIMA, [])[:1]:
            _sub(dev, "Primas",
                 Cantidad=int(fila.cantidad or 0), Pago=_valor(fila.valor),
                 PagoNS=_valor(fila.valor_no_salarial)
                 if fila.valor_no_salarial is not None else None)

        # `Cesantias` es un solo elemento con el pago y sus intereses: dos filas
        # nuestras que aquí se juntan.
        cesantias = (filas.get(NC.Concepto.CESANTIAS) or [None])[0]
        intereses = (filas.get(NC.Concepto.INTERESES_CESANTIAS) or [None])[0]
        if cesantias or intereses:
            _sub(dev, "Cesantias",
                 Pago=_valor(cesantias.valor if cesantias else CERO),
                 Porcentaje=_valor(intereses.porcentaje if intereses else CERO),
                 PagoIntereses=_valor(intereses.valor if intereses else CERO))

        incapacidades = filas.get(NC.Concepto.INCAPACIDAD, [])
        if incapacidades:
            cont = _sub(dev, "Incapacidades")
            for fila in incapacidades:
                _sub(cont, "Incapacidad",
                     FechaInicio=_fecha(fila.fecha_inicio),
                     FechaFin=_fecha(fila.fecha_fin),
                     Cantidad=int(fila.cantidad or 0),
                     Tipo=fila.tipo_incapacidad or NC.TipoIncapacidad.COMUN,
                     Pago=_valor(fila.valor))

        licencias = [
            (NC.Concepto.LICENCIA_MP, "LicenciaMP", True),
            (NC.Concepto.LICENCIA_REMUNERADA, "LicenciaR", True),
            (NC.Concepto.LICENCIA_NO_REMUNERADA, "LicenciaNR", False),
        ]
        if any(filas.get(c) for c, _, _ in licencias):
            cont = _sub(dev, "Licencias")
            for concepto, etiqueta, con_pago in licencias:
                for fila in filas.get(concepto, []):
                    _sub(cont, etiqueta,
                         FechaInicio=_fecha(fila.fecha_inicio),
                         FechaFin=_fecha(fila.fecha_fin),
                         Cantidad=int(fila.cantidad or 0),
                         Pago=_valor(fila.valor) if con_pago else None)

        self._lista(dev, filas.get(NC.Concepto.BONIFICACION, []), "Bonificaciones",
                    "Bonificacion", "BonificacionS", "BonificacionNS")
        self._lista(dev, filas.get(NC.Concepto.AUXILIO, []), "Auxilios",
                    "Auxilio", "AuxilioS", "AuxilioNS")

        huelgas = filas.get(NC.Concepto.HUELGA_LEGAL, [])
        if huelgas:
            cont = _sub(dev, "HuelgasLegales")
            for fila in huelgas:
                _sub(cont, "HuelgaLegal",
                     FechaInicio=_fecha(fila.fecha_inicio),
                     FechaFin=_fecha(fila.fecha_fin),
                     Cantidad=int(fila.cantidad or 0))

        otros = filas.get(NC.Concepto.OTRO_CONCEPTO, [])
        if otros:
            cont = _sub(dev, "OtrosConceptos")
            for fila in otros:
                _sub(cont, "OtroConcepto",
                     DescripcionConcepto=fila.descripcion,
                     ConceptoS=_valor(fila.valor),
                     ConceptoNS=_valor(fila.valor_no_salarial)
                     if fila.valor_no_salarial is not None else None)

        # Los dos siguientes tienen los dos atributos obligatorios, así que la
        # parte que no venga sale en ceros.
        ordinarias = filas.get(NC.Concepto.COMPENSACION_ORDINARIA, [])
        extraordinarias = filas.get(NC.Concepto.COMPENSACION_EXTRAORDINARIA, [])
        if ordinarias or extraordinarias:
            cont = _sub(dev, "Compensaciones")
            _sub(cont, "Compensacion",
                 CompensacionO=_valor(sum((f.valor for f in ordinarias), CERO)),
                 CompensacionE=_valor(sum((f.valor for f in extraordinarias), CERO)))

        bonos = filas.get(NC.Concepto.BONO_EPCTV, [])
        alimentacion = filas.get(NC.Concepto.BONO_EPCTV_ALIMENTACION, [])
        if bonos or alimentacion:
            cont = _sub(dev, "BonoEPCTVs")
            bono = (bonos or [None])[0]
            alim = (alimentacion or [None])[0]
            _sub(cont, "BonoEPCTV",
                 PagoS=_valor(bono.valor) if bono else None,
                 PagoNS=_valor(bono.valor_no_salarial) if bono else None,
                 PagoAlimentacionS=_valor(alim.valor) if alim else None,
                 PagoAlimentacionNS=_valor(alim.valor_no_salarial) if alim else None)

        self._valores(dev, filas.get(NC.Concepto.COMISION, []), "Comisiones", "Comision")
        self._valores(dev, filas.get(NC.Concepto.PAGO_TERCERO, []), "PagosTerceros",
                      "PagoTercero")
        self._valores(dev, filas.get(NC.Concepto.ANTICIPO, []), "Anticipos", "Anticipo")

        for concepto, etiqueta in (
            (NC.Concepto.DOTACION, "Dotacion"),
            (NC.Concepto.APOYO_SOSTENIMIENTO, "ApoyoSost"),
            (NC.Concepto.TELETRABAJO, "Teletrabajo"),
            (NC.Concepto.BONIFICACION_RETIRO, "BonifRetiro"),
            (NC.Concepto.INDEMNIZACION, "Indemnizacion"),
            (NC.Concepto.REINTEGRO, "Reintegro"),
        ):
            self._total(dev, filas.get(concepto, []), etiqueta)

    def _deducciones(self, raiz):
        from apps.nomina.models import NominaConcepto as NC

        filas = self._por_concepto(NC.Grupo.DEDUCCION)
        ded = _sub(raiz, "Deducciones")

        # Salud y pensión son obligatorias en el XSD.
        for concepto, etiqueta in (
            (NC.Concepto.SALUD, "Salud"),
            (NC.Concepto.FONDO_PENSION, "FondoPension"),
        ):
            fila = (filas.get(concepto) or [None])[0]
            _sub(ded, etiqueta,
                 Porcentaje=_valor(fila.porcentaje if fila else CERO),
                 Deduccion=_valor(fila.valor if fila else CERO))

        solidaridad = (filas.get(NC.Concepto.FONDO_SP) or [None])[0]
        subsistencia = (filas.get(NC.Concepto.FONDO_SP_SUBSISTENCIA) or [None])[0]
        if solidaridad or subsistencia:
            _sub(ded, "FondoSP",
                 Porcentaje=_valor(solidaridad.porcentaje) if solidaridad else None,
                 DeduccionSP=_valor(solidaridad.valor) if solidaridad else None,
                 PorcentajeSub=_valor(subsistencia.porcentaje) if subsistencia else None,
                 DeduccionSub=_valor(subsistencia.valor) if subsistencia else None)

        sindicatos = filas.get(NC.Concepto.SINDICATO, [])
        if sindicatos:
            cont = _sub(ded, "Sindicatos")
            for fila in sindicatos:
                _sub(cont, "Sindicato",
                     Porcentaje=_valor(fila.porcentaje), Deduccion=_valor(fila.valor))

        publicas = filas.get(NC.Concepto.SANCION_PUBLICA, [])
        privadas = filas.get(NC.Concepto.SANCION_PRIVADA, [])
        if publicas or privadas:
            cont = _sub(ded, "Sanciones")
            _sub(cont, "Sancion",
                 SancionPublic=_valor(sum((f.valor for f in publicas), CERO)),
                 SancionPriv=_valor(sum((f.valor for f in privadas), CERO)))

        libranzas = filas.get(NC.Concepto.LIBRANZA, [])
        if libranzas:
            cont = _sub(ded, "Libranzas")
            for fila in libranzas:
                _sub(cont, "Libranza",
                     Descripcion=fila.descripcion, Deduccion=_valor(fila.valor))

        self._valores(ded, filas.get(NC.Concepto.PAGO_TERCERO, []), "PagosTerceros",
                      "PagoTercero")
        self._valores(ded, filas.get(NC.Concepto.ANTICIPO, []), "Anticipos", "Anticipo")
        self._valores(ded, filas.get(NC.Concepto.OTRA_DEDUCCION, []),
                      "OtrasDeducciones", "OtraDeduccion")

        for concepto, etiqueta in (
            (NC.Concepto.PENSION_VOLUNTARIA, "PensionVoluntaria"),
            (NC.Concepto.RETENCION_FUENTE, "RetencionFuente"),
            (NC.Concepto.AFC, "AFC"),
            (NC.Concepto.COOPERATIVA, "Cooperativa"),
            (NC.Concepto.EMBARGO_FISCAL, "EmbargoFiscal"),
            (NC.Concepto.PLAN_COMPLEMENTARIOS, "PlanComplementarios"),
            (NC.Concepto.EDUCACION, "Educacion"),
            (NC.Concepto.REINTEGRO, "Reintegro"),
            (NC.Concepto.DEUDA, "Deuda"),
        ):
            self._total(ded, filas.get(concepto, []), etiqueta)

    # -- Formas repetidas ---------------------------------------------------

    def _horas_extra(self, padre, filas, contenedor, etiqueta):
        """Un contenedor con una entrada por tramo de horas."""
        if not filas:
            return
        cont = _sub(padre, contenedor)
        for fila in filas:
            _sub(cont, etiqueta,
                 HoraInicio=fila.hora_inicio.isoformat() if fila.hora_inicio else None,
                 HoraFin=fila.hora_fin.isoformat() if fila.hora_fin else None,
                 Cantidad=int(fila.cantidad or 0),
                 Porcentaje=_valor(fila.porcentaje),
                 Pago=_valor(fila.valor))

    def _lista(self, padre, filas, contenedor, etiqueta, attr_s, attr_ns):
        """Contenedor de conceptos partidos en parte salarial y no salarial."""
        if not filas:
            return
        cont = _sub(padre, contenedor)
        for fila in filas:
            _sub(cont, etiqueta, **{
                attr_s: _valor(fila.valor),
                attr_ns: (_valor(fila.valor_no_salarial)
                          if fila.valor_no_salarial is not None else None),
            })

    def _valores(self, padre, filas, contenedor, etiqueta):
        """Contenedor de elementos que solo llevan un importe como texto."""
        if not filas:
            return
        cont = _sub(padre, contenedor)
        for fila in filas:
            _sub(cont, etiqueta, _valor(fila.valor))

    def _total(self, padre, filas, etiqueta):
        """Concepto que se emite una sola vez, con el importe como texto."""
        if not filas:
            return
        _sub(padre, etiqueta, _valor(sum((f.valor for f in filas), CERO)))


class ConstructorNotaAjusteNomina(ConstructorNominaXML):
    """Nota de ajuste de nómina (``NominaIndividualDeAjuste``, tipo 103).

    No corrige por diferencias como las notas de factura: o **reemplaza** el
    documento anterior repitiéndolo entero, o lo **elimina**. Eso decide qué
    bloque se emite —``Reemplazar`` o ``Eliminar``— y de ahí que el reemplazo
    reutilice tal cual el cuerpo de la nómina: es el mismo documento otra vez,
    dentro de otro elemento y apuntando al que sustituye.

    El ``Eliminar`` es mucho más corto: solo la cabecera, sin trabajador, ni
    devengados, ni deducciones, ni totales.
    """

    nombre_raiz = "NominaIndividualDeAjuste"
    ns_raiz = NS_NOMINA_AJUSTE
    version = VERSION_NOTA_AJUSTE
    tipo_xml = TIPO_XML_NOTA_AJUSTE

    def _cuerpo(self, raiz, cune):
        from apps.nomina.models import Nomina

        _sub(raiz, "TipoNota", self.doc.tipo_nota or Nomina.TipoNota.REEMPLAZAR)
        if self.doc.tipo_nota == Nomina.TipoNota.ELIMINAR:
            self._eliminar(raiz, cune)
        else:
            self._reemplazar(raiz, cune)

    def _novedad(self, raiz):
        """La nota de ajuste no admite ``Novedad``: el XSD no lo define."""

    def _reemplazar(self, raiz, cune):
        bloque = _sub(raiz, "Reemplazar")
        self._predecesor(bloque, "ReemplazandoPredecesor")
        super()._cuerpo(bloque, cune)

    def _eliminar(self, raiz, cune):
        bloque = _sub(raiz, "Eliminar")
        self._predecesor(bloque, "EliminandoPredecesor")
        self._numero_secuencia(bloque, con_codigo_trabajador=False)
        self._lugar_generacion(bloque)
        self._proveedor(bloque)
        _sub(bloque, "CodigoQR", self._url_qr(cune))
        self._informacion_general(bloque, cune, reducida=True)
        if self.doc.notas:
            _sub(bloque, "Notas", self.doc.notas)
        self._empleador(bloque)

    def _predecesor(self, bloque, etiqueta):
        """Los tres datos del documento que se ajusta, tomados de él mismo."""
        anterior = self.doc.nomina_predecesora
        if anterior is None:
            raise ValueError(
                "La nota de ajuste no indica qué nómina ajusta "
                "(`nomina_predecesora`)."
            )
        _sub(
            bloque, etiqueta,
            NumeroPred=anterior.numero,
            CUNEPred=anterior.cune,
            FechaGenPred=_fecha(anterior.fecha_generacion),
        )


def constructor_nomina_para(nomina, **kwargs) -> ConstructorNominaXML:
    """Devuelve el constructor que le toca según el tipo de XML."""
    from apps.nomina.models import Nomina

    clase = (
        ConstructorNotaAjusteNomina
        if nomina.tipo_xml == Nomina.TipoXML.AJUSTE
        else ConstructorNominaXML
    )
    return clase(nomina, **kwargs)


def generar_xml_nomina(nomina, *, software, ambiente, pin="") -> bytes:
    """Atajo: XML (sin firmar) de una nómina o de su nota de ajuste."""
    return constructor_nomina_para(
        nomina, software=software, ambiente=ambiente, pin=pin
    ).generar_xml()
