# Revisión técnica y plan de trabajo

Revisión a fondo del proyecto hecha el **2026-09-01** sobre `main` (`7e3bd52`).
Recoge lo que se encontró leyendo el código, con dónde está cada cosa y por qué
importa, y termina con un plan por fases.

> **Método.** La revisión original fue por lectura: no se ejecutó la suite ni se
> emitió ningún documento contra la DIAN. Lo que se afirmaba sobre cobertura
> salía de inventariar los ficheros `tests_*.py`, y lo que se afirmaba sobre
> comportamiento, de seguir el código (incluido el de DRF donde hacía falta).
>
> **Desde el 2026-09-02 ya no es solo lectura.** Las fases 1 y 2 están
> aplicadas: cada apartado resuelto lleva su nota al pie con lo que se hizo y
> por qué, y la suite se ejecuta. Eso corrigió una afirmación de §C1 —el orden
> de la raíz sí tenía prueba— y destapó un hallazgo nuevo, **B9**. Sigue sin
> emitirse nada contra la DIAN.

---

## Tabla de contenido

- [Valoración general](#valoración-general)
- [A. Seguridad](#a-seguridad)
- [B. Corrección](#b-corrección)
- [C. Cobertura de pruebas](#c-cobertura-de-pruebas)
- [D. Operación y rendimiento](#d-operación-y-rendimiento)
- [E. Deuda y consistencia](#e-deuda-y-consistencia)
- [Plan de trabajo](#plan-de-trabajo)

---

## Valoración general

El núcleo DIAN —identificadores, UBL, firma XAdES, WS-Security— está por encima
de lo que se ve normalmente en este dominio: separa bien las tres composiciones
de hash (CUFE/CUDE, CUDS, CUNE), documenta el *porqué* de cada decisión
peligrosa y guarda el rastro de los rechazos que costaron descubrirlas (el ZE02
de `firma._declarar_contexto_heredado` es el mejor ejemplo). El alcance
multi-inquilino de `apps/seguridad/alcance.py` está pensado con cuidado, hasta
el detalle de que un id ajeno y uno inexistente respondan igual.

Las incidencias no están ahí. Están en tres sitios:

1. **El material criptográfico y los secretos** se guardan y se exponen con
   menos cuidado del que se pone en usarlos (§A).
2. **Las dos funcionalidades más nuevas** —nómina y documento equivalente
   P.O.S., unas 2.000 líneas entre modelo, constructores, serializers y API— no
   tienen **ni una sola prueba**, justo cuando el resto del proyecto sí las
   tiene (§C).
3. **El borde HTTP** acumula varios 500 evitables y no hay ninguna traza con la
   que investigarlos después (§B2, §D1).

Nada de esto compromete la corrección de lo que ya emite y la DIAN acepta. Todo
compromete lo que pasa el día que algo falle en producción.

> **Al 2026-09-02 las cuatro fases están aplicadas**, con tres excepciones
> deliberadas, cada una anotada en su apartado: **A2** (el PIN se queda visible,
> riesgo aceptado), **A4** (Zinc no sirve TLS, es trabajo fuera de este
> repositorio) y **D3** (el envío síncrono, a la espera de datos que medir). La
> nómina sigue sin representación gráfica (**E1**) y la auditoría de **A7**
> sigue pendiente. De 211 pruebas con 32 en rojo se pasó a **274 en verde**.

---

## A. Seguridad

### A1 · La clave del `.p12` se guarda en claro — **resuelto**

`apps/emisores/models/certificado.py:33`

```python
clave = models.CharField("clave del certificado", max_length=255)
```

El propio docstring del modelo lo reconoce («la clave debería cifrarse en
producción»), así que esto es una deuda conocida, no un descubrimiento. Lo que
cambia la severidad es el conjunto: un volcado de `bdnobelio` da la clave, y el
`.env` del mismo servidor da las credenciales B2 con las que bajar el `.p12`.
Con las dos mitades juntas se firma cualquier documento en nombre de cualquier
emisor de la plataforma.

Lo mínimo es cifrar la columna con una clave que no viva en la misma base:
`DJANGO_SECRET_KEY` no sirve (rota, y con ella se firman los JWT), hace falta
una `CERT_ENCRYPTION_KEY` propia. Fernet de `cryptography` —que ya es
dependencia— basta; el cambio es un descriptor en el modelo más una migración
de datos.

> **Arreglado el 2026-09-02.** `apps/utilidades/cifrado.py` añade un
> `ClaveCifradaField`: cifra al escribir y descifra al leer, así que los dos
> sitios que abren el `.p12` (`apps/dian/servicios.py:283` y `:439`) siguen
> haciendo `cert_modelo.clave` sin enterarse. Fernet con una
> `CERT_ENCRYPTION_KEY` propia, **sin `default`**: si falta, el proyecto no
> arranca, para que ningún despliegue con la variable mal escrita siga
> guardando en claro. La lectura tolera texto sin cifrar, de modo que una fila
> sin migrar no deja a un emisor sin poder firmar. La columna pasa a 512 —el
> token abulta más que la clave— y el serializer declara su `max_length=255`
> aparte, para que el contrato de entrada de la API no cambie.
>
> Se descartó `MultiFernet`: una sola clave, y el día que haya que rotarla se
> hace con un comando y las dos a mano.

### A2 · El PIN del software se devuelve por la API — **riesgo aceptado**

`apps/emisores/serializers/software.py:12`

El `pin` está en `fields` sin `write_only`, así que sale en el listado y en el
detalle de `/api/emisores/software/`. El commit que lo introdujo (`2636ad8`) lo
hizo «for clarity», y es entendible durante la habilitación, pero el PIN no es
un dato descriptivo: entra en la composición del CUDE, del CUDS, del CUNE y del
`sts:SoftwareSecurityCode`. Es el secreto que impide que un tercero fabrique
identificadores válidos para ese emisor.

Compárese con el criterio que sí se aplicó a sus vecinos: `Certificado.clave` y
`Certificado.archivo` son `write_only` (`serializers/certificado.py:21-26`) y
`Resolucion.clave_tecnica` también (`serializers/resolucion.py:12-17`), cada uno
con un comentario explicando que es sensible. El PIN es de la misma familia y
quedó fuera.

> **Decisión (2026-09-02): se deja como está.** MarioA reafirma la decisión del
> 2026-08-28 y el punto se cierra como riesgo aceptado, no como pendiente. El
> razonamiento que ya estaba escrito en el serializer sigue siendo el que
> manda: la contención del PIN es el alcance —`AlcanceEmisorMixin` limita el
> queryset a los emisores que alcanza quien pregunta, así que una cuenta nunca
> ve los softwares de otra— y tenerlo a la vista en el listado ahorra fricción
> durante la habilitación, que es cuando más se consulta. Lo que queda dicho, y
> es la razón de conservar el apartado, es el alcance del riesgo si esa
> contención falla: con el PIN se fabrican `SoftwareSecurityCode` y CUDE/CUDS/
> CUNE válidos de ese emisor. Si algún día se amplía quién puede leer
> `/api/emisores/software/` —una cuenta de solo lectura, un panel para el
> cliente final, un token de integración— hay que volver aquí antes.

### A3 · `CertificadoViewSet` cierra la puerta de entrada y deja la ventana abierta — **resuelto**

`apps/emisores/views/certificado.py:26-33`

`create` está bloqueado con un `MethodNotAllowed` que remite a `cargar/`, que es
donde se valida el `.p12` (`validar_pkcs12`) y donde se exige que B2 esté
configurado para no escribir material criptográfico en disco local. Pero la
clase sigue siendo un `ModelViewSet` completo: `PUT`, `PATCH` y `DELETE` siguen
publicados.

Un `PATCH /api/emisores/certificado/{id}/` con un `archivo` nuevo lo guarda sin
pasar por ninguna de las dos comprobaciones —caería al `default_storage` local
si B2 no está—, y un `PATCH` de `emisor` mueve el certificado a otro emisor del
alcance, o reactiva con `activo=true` uno que se jubiló. Todo el trabajo de
`cargar/` se rodea con una petición.

> **Arreglado el 2026-09-02.** La clase deja de ser un `ModelViewSet` y se
> compone con los mixins justos: `List`, `Retrieve`, `Destroy` y `GenericViewSet`,
> más la acción `cargar`. Sin `update` ni `partial_update` declarados, el router
> no publica `PUT` ni `PATCH`. Se conservó el `create` que lanza
> `MethodNotAllowed` —aunque el router ya no lo enrutaría por sí solo— para que
> un `POST` siga respondiendo un 405 que dice a dónde ir. No hay edición de
> certificados: el `alias` se fija al subir y `activo` lo gobierna `cargar`, que
> jubila los anteriores del emisor.

### A4 · La notificación al adquiriente viaja por HTTP plano — **bloqueado fuera**

`config/settings/base.py:116` y `apps/utilidades/zinc.py:23`

```python
ZINC_URL_BASE = env("ZINC_URL_BASE", default="http://zinc.semantica.com.co")
```

Por ahí van, en base64 dentro de un JSON, el zip completo con el
AttachedDocument firmado, el PDF, los adjuntos del emisor y la dirección de
correo del adquiriente (`payload_zinc`, `servicios/notificacion.py`). Son datos
fiscales de terceros. Si Zinc tiene `https`, es un cambio de una línea en el
default; si no lo tiene, la conversación es con Zinc.

> **Comprobado el 2026-09-02: no lo tiene.** `zinc.semantica.com.co` responde
> 200 por el puerto 80 y no acepta conexión en el 443. No hay nada que cambiar
> aquí: `ZINC_URL_BASE` ya es una variable de entorno, así que el día que la
> pasarela sirva TLS basta con apuntarla a `https://` sin tocar código. El
> punto queda abierto y **fuera de este repositorio**: es trabajo en Zinc.

### A5 · `crear-habilitacion` y sus vecinas son un oráculo de ids — **resuelto**

`apps/emisores/views/emisor.py:138-144`

```python
try:
    return Emisor.objects.get(pk=identificador)
except (Emisor.DoesNotExist, TypeError, ValueError):
    raise ErrorSolicitud("El emisor indicado no existe.")
```

Y después `exigir_alcance(request, emisor)`, que lanza 403. Es decir: un emisor
de otra cuenta responde **403** y uno inexistente responde **400**, así que
cualquier credencial puede enumerar qué ids de emisor existen en la plataforma.

Es exactamente lo que `RelacionDelAlcance` evita en el resto del proyecto, y su
docstring lo explica mejor que este informe: «un id ajeno y un id inexistente se
distinguen por el mensaje de error, y eso convierte al endpoint en un oráculo».
Aquí basta con buscar dentro de `emisores_permitidos(request)` y responder
siempre lo mismo.

### A6 · `crear-habilitacion` escribe datos del sandbox en cualquier emisor — **resuelto**

`apps/emisores/views/emisor.py:177-190`

El endpoint hace un `update_or_create` de una resolución con prefijo `SETP`,
número `18760000001`, rango `990000000–995000000` y la clave técnica
`fc8eac42…` **escritas a pelo en el código**. Son los datos del set de
habilitación de la DIAN, no los del emisor. El endpoint no mira el ambiente, así
que llamarlo sobre un emisor ya en producción le mete una resolución falsa
marcada `activa=True` en su tabla de numeración, que es justo la que
`_resolucion_por_numero` consulta al crear documentos.

Como mínimo tendría que negarse cuando `emisor.ambiente_facturacion == 1`. Mejor
aún: separarlo del `EmisorViewSet` y dejarlo como comando de gestión, que es lo
que realmente es.

### A7 · Sin throttling ni auditoría — **parcial**

`REST_FRAMEWORK` no define `DEFAULT_THROTTLE_CLASSES`, así que una API Key
—credencial de larga duración, sin caducidad por defecto— puede llamar sin
límite a `enviar/`, que gasta cupo del Set de Pruebas y consecutivos de archivo.
Y no queda constancia de nada: ver §D1.

---

## B. Corrección

### B1 · `PUT /api/documentos/documento/{id}/` responde 500 — **resuelto**

`apps/documentos/serializers/documento.py:660`

`DocumentoCrearSerializer.update()` hace `pop` de `adquiriente` y de `pos`, pero
**no de `detalles`**, y después llama a `super().update()`. Lo primero que hace
`ModelSerializer.update` es `raise_errors_on_nested_writes`, que revienta con un
`AssertionError` cuando un campo serializer anidado está en `validated_data` y
es una relación del modelo. `detalles` cumple las dos condiciones.

Como `AssertionError` no la maneja el `exception_handler` de DRF, sale un 500 con
traceback en vez de un error de negocio. Un `PATCH` sin `detalles` funciona; un
`PUT` (que los exige, porque el campo es obligatorio) no puede funcionar nunca.

El contraste con la nómina es lo que confirma que es un olvido y no una decisión:
`NominaCrearSerializer` sí hace `validated_data.pop("conceptos", None)` en su
`update` (`apps/nomina/serializers/nomina.py:435`).

La decisión de fondo es qué debe hacer un `PUT` con las líneas: reemplazarlas
enteras (coherente con la semántica de `PUT`) o rechazarlas explícitamente en un
borrador. Cualquiera de las dos es mejor que el 500.

### B2 · Los filtros de la query string producen 500 con basura — **resuelto**

- `apps/documentos/views/documento.py:78` → `qs.filter(emisor=emisor)`
- `apps/nomina/views/nomina.py:59-63` → `emisor` y `empleado`
- `apps/emisores/views/certificado.py:22-23` → `emisor`

`Emisor.pk` es un entero, así que `?emisor=abc` llega a `IntegerField.get_prep_value`
y lanza `ValueError`, que DRF tampoco maneja: 500 con traceback.

El proyecto ya resolvió este mismo problema una vez y dejó escrito el
razonamiento: `RelacionDelAlcance.to_internal_value` existe precisamente porque
«un id mal formado hace que Django lance su propio `ValidationError` … sin esto
sale un 500 con traceback». Falta aplicar el mismo criterio a los filtros de
listado, que es el otro sitio donde entra un id sin validar.

### B3 · `emitir` y `enviar` no bloquean el documento — **resuelto**

`apps/dian/servicios.py:292` y `:525`, llamados desde
`apps/documentos/views/documento.py:118` y `:131`.

Ninguno de los dos toma la fila con `select_for_update`. Dos `POST /emitir/`
simultáneos sobre el mismo documento pasan los dos la comprobación de estado
(`borrador`), construyen dos XML, calculan dos CUFE distintos —la hora de emisión
se refija en cada uno— y firman los dos; el segundo pisa a `cufe_cude` y a
`xml_archivo`, y como B2 va con `file_overwrite=False` quedan **dos XML firmados
en el bucket** y solo uno referenciado.

Dos `POST /enviar/` simultáneos son peores: el documento sale dos veces a la
DIAN, gasta dos consecutivos de archivo (`_nombre_archivo_envio` reserva uno por
envío, deliberadamente) y el segundo vuelve como regla 90.

No es teórico para el P.O.S.: el propio `ConsecutivoArchivoDocumentoEquivalente`
justifica su `select_for_update` diciendo que «en un punto de venta los envíos
concurrentes son la norma, no la excepción». El razonamiento vale igual un nivel
más arriba.

### B4 · La nómina no reconoce el «procesado anteriormente» al enviar — **resuelto**

`apps/dian/servicios.py:894` frente a `:577`

El envío de documentos hace `if respuesta.es_valido or _ya_procesado(respuesta)`.
El de nóminas hace solo `if respuesta.es_valido`. La consulta
(`actualizar_estado_nomina`) sí usa `_ya_procesado`, así que la asimetría está
únicamente en el envío.

Consecuencia: reenviar una nómina que la DIAN ya tiene la deja en `rechazado`
—con la regla 90 como único error— en vez de `aceptado`, y hay que pasar por
`consultar` para arreglarlo. Dado que `_ya_procesado` está escrita con cuidado
para no dar por buena una regla 90 que venga acompañada de rechazos reales, no
hay motivo para no usarla en los dos sitios.

### B5 · `Resolucion.consecutivo_actual` no lo actualiza nadie — **resuelto**

`apps/emisores/models/resolucion.py:29` y `:65`

El campo y su `siguiente_consecutivo` no se leen ni se escriben en ningún punto
del pipeline (comprobado con `grep`: las únicas apariciones son la definición, el
serializer que lo expone y funciones homónimas de nómina, que son otra cosa). La
numeración de facturas la lleva **entera el ERP**: el `consecutivo` llega en el
cuerpo y lo único que se comprueba es que quepa en el rango
(`_errores_de_numeracion`).

Que sea así es defendible. El problema es que el campo es de escritura en
`ResolucionSerializer` y se llama «consecutivo actual», así que promete un
control de numeración que no existe. Un integrador que confíe en él se llevará
números repetidos. O se implementa la reserva (con `select_for_update`, como los
consecutivos de archivo) o se marca `read_only` y se documenta que la numeración
es del ERP.

### B6 · `crear_nota_ajuste` de nómina puede duplicar consecutivo — **resuelto**

`apps/nomina/servicios.py:56-61`

```python
ultimo = Nomina.objects.filter(...).aggregate(ultimo=Max("consecutivo"))["ultimo"]
return (ultimo or 0) + 1
```

Sin bloqueo. Dos notas de ajuste creadas a la vez para el mismo emisor y prefijo
obtienen el mismo número; la restricción única `(emisor, prefijo, consecutivo)`
salva la integridad, pero como `apps/nomina/views/nomina.py:110` solo captura
`ValueError`, el `IntegrityError` sale como 500. Es poco probable —las notas de
ajuste no se crean en ráfaga— y por eso es baja, pero el arreglo es una línea.

### B7 · `?search=` no hace nada en documentos — **resuelto**

`apps/documentos/views/documento.py:40` declara `filters.SearchFilter` y la clase
no define `search_fields`, así que DRF devuelve el queryset intacto: el parámetro
se ignora en silencio. `NominaViewSet` (`nomina/views/nomina.py:33`) y `EmisorViewSet` sí los declaran.
Faltan los obvios: `numero`, `cufe_cude`, `adquiriente__razon_social`,
`adquiriente__numero_identificacion`.

### B8 · La titularidad del certificado se comprueba por subcadena — **resuelto**

`apps/emisores/servicios/certificado_validacion.py:97`

```python
if nit_emisor and nit_emisor not in _identificadores_del_certificado(cert):
```

Compara dígitos contra dígitos sin delimitar, así que el NIT `900123` valida
contra un certificado emitido a `8900123456`. Es un falso positivo poco probable
pero real; comparar contra la lista de identificadores en vez de contra su
concatenación lo cierra.

### B9 · Los listados salen sin orden, y el comentario dice que sí lo tienen — **resuelto**

`apps/documentos/views/documento.py:36-45,68-74` y
`apps/nomina/views/nomina.py:55`

Hallado el 2026-09-02 al escribir las pruebas de nómina: DRF avisa con
`UnorderedObjectListWarning` al paginar. Los dos viewsets anotan el conteo de
errores en la acción `list`:

```python
qs = qs.prefetch_related(None).annotate(total_errores=Count("errores"))
```

y **`annotate` con un agregado descarta el `Meta.ordering` del modelo** —Django
lo hace a propósito, para no meter los campos de orden en el `GROUP BY`—. Se
comprueba en una línea: el queryset base da `ordered = True` y el anotado,
`False`; en el SQL no queda `ORDER BY`.

O sea que el listado paginado sale en el orden que quiera PostgreSQL, que no
está obligado a ser el mismo entre dos consultas: **una fila puede repetirse en
dos páginas y otra no aparecer en ninguna**. Es justo lo que rompe un ERP que
recorre las páginas para sincronizar.

Lo que lo vuelve una trampa es el comentario que hay encima en `documento.py`:
«Sin el parámetro manda el orden del modelo: lo más reciente primero». Es lo que
uno esperaría y no es lo que pasa, así que nadie va a ir a mirarlo.

El arreglo es un `order_by` explícito después del `annotate`, o un `ordering` en
la vista para que `OrderingFilter` lo imponga. Está emparentado con **D4** —el
índice del orden por defecto—, que daba por hecho que ese orden se aplicaba.

---

## C. Cobertura de pruebas

### C1 · Nómina y documento equivalente P.O.S. no tienen ninguna prueba — **resuelto**

Inventario de `tests_*.py` por app:

| App | Ficheros | Métodos `test_` |
|-----|---------:|----------------:|
| `dian` | 7 | 66 |
| `documentos` | 3 | 32 |
| `emisores` | 5 | 34 |
| `seguridad` | 3 | 36 |
| `utilidades` | 3 | 16 |
| `catalogos`, `cuentas`, `nucleo` | 3 | 23 |
| **`nomina`** | **0** | **0** |

Y `documento_equivalente` / `DocumentoPOS` no aparecen en ningún test de ninguna
app. Las dos funcionalidades sin cubrir son, juntas, lo más nuevo y lo que más
lógica propia trae:

- `apps/dian/nomina.py` (781 líneas), `apps/nomina/serializers/nomina.py` (449),
  `apps/nomina/models/nomina.py` (312), `apps/nomina/views/nomina.py` (204),
  `apps/nomina/servicios.py` (141).
- `ConstructorDocumentoEquivalentePOS` y las dos notas de ajuste DE
  (`apps/dian/ubl.py:1120-1319`, tres clases), `DocumentoPOS`, `_validar_pos`,
  `ConsecutivoArchivoDocumentoEquivalente`, `nombre_archivo_documento_equivalente`.

Hay además dos cosas concretas que solo una prueba puede sostener:

- **El CUNE no tiene vector oficial.** Lo dice el propio código
  (`identificadores.py`, `calcular_cune`): el ejemplo del anexo no reproduce su
  propio hash. Lo único que respalda hoy esa función es que la DIAN aceptó una
  nómina el 2026-08-30. Una prueba de regresión que fije la composición contra
  ese documento aceptado convierte un hecho irrepetible en una red de seguridad.
- **El orden de las declaraciones de la raíz** es lo que costó el ZE02.
  ~~No hay prueba que lo fije.~~ **Corrección (2026-09-02): sí la hay.**
  `apps.dian.tests_firma.OrdenDeclaracionesNominaTests` lo comprueba contra la
  ejemplificación oficial con tres casos, y el docstring de `_raiz` la nombra.
  La revisión miró el inventario por app —`apps/nomina/` no tenía ficheros de
  prueba— y dio por ausente algo que vivía en `apps/dian/`.

> El README dice hoy «Todo el pipeline está cubierto por pruebas» (línea 12).
> Con nómina y P.O.S. dentro del pipeline, ya no es cierto.

> **Arreglado el 2026-09-02.** 43 pruebas nuevas, de 211 a 254, y la suite
> entera en verde.
>
> **Nómina** (`apps/nomina/tests_xml.py`, `tests_api.py`, `tests_utils.py`, 19):
> la composición del CUNE campo a campo, el XML firmado contra el XSD oficial,
> y el ciclo de la API —emitir, enviar y consultar— con un cliente SOAP falso,
> incluido el camino asincrónico: el envío al Set de Pruebas no trae veredicto y
> es `consultar` quien lo aplica.
>
> Sobre el CUNE, el ancla es la que decía este apartado: se comprobó que
> `calcular_cune` reproduce **exactamente** el CUNE guardado de **NESETP6**, la
> nómina que la DIAN aceptó el 2026-08-30. Ese caso no se puede commitear —en el
> CUNE entra el PIN del software, que es un secreto—, así que la prueba fija la
> composición armándola a mano en el orden del anexo. Es lo que se rompería si
> alguien reordena los campos o cambia un formato, que es el riesgo real.
>
> **P.O.S.** (`apps/documentos/tests_pos.py`, 13): las tres extensiones propias
> con sus literales acentuados (`UbicaciónCaja`, `CódigoVenta`, que la DIAN
> compara al pie de la letra), la nomenclatura del numeral 8.13.5 —que sí tiene
> ejemplo oficial, `ds08001972680002000000001`, y la prueba lo reproduce— y el
> consecutivo hexadecimal por emisor y año.
>
> Escribiéndolas salió **B9**: los dos listados paginan sin orden.

### C2 · Se perdieron las pruebas de habilitación y no se repusieron — **resuelto**

`ff3eb34` borró `apps/dian/tests_set_pruebas.py` y
`apps/emisores/tests_habilitar.py` al retirar el `HabilitarSerializer`. Fue
coherente —las pruebas eran de lo que se quitaba—, pero lo que ocupó su lugar
(`crear_habilitacion`, §A6) nació sin ninguna. Es el endpoint que decide con qué
software y con qué resolución se emite todo lo demás.

> **Arreglado el 2026-09-02.** `apps/emisores/tests_habilitacion.py`, 10 casos.
> No es una restauración: el fichero borrado probaba una API que ya no existe
> (`ResolucionFacturacion`, `emitir_set_pruebas`), así que se escribieron contra
> el endpoint de ahora. Cubren el camino feliz —registra el software y siembra
> la resolución del Set de Pruebas—, que repetirlo no duplica nada, que jubila
> el software anterior **del mismo tipo** sin tocar el de la otra operación, y
> los cortes: sin certificado, con el certificado vencido, sin PIN, y que la
> resolución no se siembra si el software no valida.
>
> De paso quedó a la vista una fragilidad: `crear_habilitacion` hace un
> `TipoFactura.objects.get(codigo="01")` directo, así que sobre una base sin el
> catálogo cargado responde 500 en vez de decir qué falta. En un despliegue real
> el catálogo está; entra en el barrido de **E3**.

### C3 · Sin integración continua — **resuelto**

No hay `.github/`, ni ningún otro runner. Nada garantiza que la suite pase antes
de un `git pull` en producción, y `actualizar.sh` no la ejecuta: hace `git pull`,
`migrate` y arranca. Un `workflow` que corra `python manage.py test` con
`config.settings.test` contra un PostgreSQL de servicio es media hora de trabajo.

> **Arreglado el 2026-09-02.** `.github/workflows/pruebas.yml`, en cada push a
> `main` y en cada pull request: PostgreSQL 16 de servicio —hace falta de
> verdad, porque la suite usa restricciones y `select_for_update` que SQLite no
> reproduce—, `manage.py check`, la suite, y un `makemigrations --check` que
> detecta el modelo cambiado sin su migración, que es lo que pasa las pruebas y
> rompe el despliegue.

---

## D. Operación y rendimiento

### D1 · No hay registro de nada — **resuelto**

En las ~16.500 líneas de `apps/` hay **una sola** llamada a un logger
(`apps/nucleo/api.py:79`, para los fallos de B2), y `settings` no define
`LOGGING`. No queda constancia de:

- quién emitió o envió qué documento, y cuándo;
- qué se le mandó a la DIAN y qué respondió (el XML crudo se guarda por
  documento en B2, pero no hay una línea de tiempo consultable);
- los 500 de §B1 y §B2, que sin traza son irreproducibles;
- las notificaciones enviadas a los adquirientes.

Para un servicio que emite documentos con efectos fiscales, esto es lo que más
va a doler el primer día que un cliente pregunte «¿esta factura salió?». Un
`LOGGING` con formato estructurado y una línea por transición de estado del
documento cubre el 90 % del valor.

> **Arreglado el 2026-09-02.** `LOGGING` en `base.py` manda todo a stdout, que
> recoge journald a través de la unidad de systemd: la aplicación no abre
> ficheros, no rota nada y no necesita permisos sobre `/var/log`. El formato es
> `clave=valor` (`apps/nucleo/registro.campos`), pensado para `grep` y no para
> un agregador, y omite los campos vacíos para que un `grep estado=RECHAZADO`
> no dependa de la suerte.
>
> Siete líneas, una por transición: `documento.firmado`, `documento.enviado` y
> `documento.estado_actualizado`, sus tres gemelas de nómina, y
> `documento.notificado`. El nivel lo decide el resultado —un rechazo de la
> DIAN sale como `WARNING`, no como `ERROR`: la DIAN contestó y el documento
> quedó bien anotado, lo que falla es el contenido—. Del rechazo se registran
> el número de errores y solo el primero; el detalle sigue en la respuesta
> guardada. En la notificación se registra **cuántos** destinatarios, no
> cuáles: la dirección del adquiriente es dato de un tercero y el log se lee
> con menos cuidado que la base.
>
> Se añadió también `django.request`, que es donde Django deja los 500 de §B1
> y §B2 y que hasta ahora se perdían.

### D2 · Cada petición con API Key cuesta un PBKDF2 y una escritura — **resuelto**

`apps/seguridad/autenticacion.py:74-82`

```python
if not llave.verificar_secreto(secreto):   # check_password -> PBKDF2, ~600k iteraciones
    ...
llave.registrar_uso()                       # UPDATE en cada petición
```

`check_password` usa el hasher por defecto de Django, dimensionado para
contraseñas humanas de baja entropía. El secreto de una `LlaveApi` son 40
caracteres de `get_random_string`: no necesita estiramiento de clave, necesita
comparación en tiempo constante. Tal como está, cada petición del ERP consume
del orden de 100 ms de CPU **antes** de tocar la lógica, y con `gthread` eso
compite por el GIL con todo lo demás.

`registrar_uso()` añade un `UPDATE` por petición sobre una fila caliente. Sirve
para saber si una llave sigue viva; con una resolución de un minuto sobra.

El cambio recomendado es el patrón habitual: guardar `sha256(secreto)` (o un
HMAC con `SECRET_KEY`) y comparar con `secrets.compare_digest`. Es compatible con
el diseño actual —el secreto ya no se puede recuperar— y no obliga a rotar las
llaves si se hace con migración perezosa.

### D3 · El envío a la DIAN ocurre dentro de la petición HTTP — **abierto, a la espera de datos**

`POST /enviar/` llama a `enviar_a_dian`, que hace el `requests.post` con
`timeout=60` (`soap.py:531`). Con `--workers 3 --threads 4`, el techo son 12
envíos simultáneos; el decimotercero espera. Y el cliente HTTP del ERP tiene que
mantener abierta una conexión durante todo ese tiempo, con el timeout de nginx a
150 s por encima.

Funciona para el volumen de factura. No funciona para el volumen que justifica
el documento equivalente P.O.S., que es donde se van a ver los picos. La salida
natural es una cola (`enviar/` encola y responde 202, un worker envía y aplica el
estado), reutilizando `actualizar_estado`, que ya está escrito para eso. Es la
única incidencia de este informe que es un cambio de arquitectura y no un
arreglo, y por eso va en la última fase.

### D4 · El orden por defecto del listado no tiene índice — **resuelto**

`Documento.Meta.ordering = ["-fecha_emision", "-consecutivo"]`, y no hay
`indexes`. Con la tabla pequeña da igual; con un año de tiquetes P.O.S. cada
listado paginado ordena en memoria. Un índice compuesto
`(emisor, -fecha_emision, -consecutivo)` cubre el listado tal como lo consulta la
API (`emisor` es el filtro que siempre está por el alcance).

---

## E. Deuda y consistencia

- **E1 · Representación gráfica.** ~~(resuelto el 2026-09-02 salvo la nómina, que sigue sin PDF)~~ `apps/dian/representacion.py` está escrita para
  la factura y se usa para todo: el título del PDF dice `Factura {numero}`
  (línea 74) y el pie dice «Representación gráfica de la factura electrónica de
  venta» (línea 204) incluso en una nota crédito, un documento soporte o un
  tiquete P.O.S. Además el QR solo va en la última página (el anexo lo pide en
  todas), las retenciones del documento soporte no aparecen en los totales, y
  **la nómina no tiene representación gráfica en absoluto**, aunque es el
  documento que hay que entregarle al trabajador. El README ya reconoce los dos
  primeros puntos; los otros dos no.
- **E2 · Código de diagnóstico en producción.** ~~(resuelto el 2026-09-02: pasa a `settings`)~~ `soap.DIRECTORIO_CAPTURA`
  (`soap.py:127-146`) está marcado «**TEMPORAL**» y vuelca a disco los sobres
  SOAP transmitidos, que llevan el documento firmado y el certificado. Está
  desactivado por defecto y `.gitignore` cubre sus salidas, así que es seguro;
  pero cumplió su función (el ZE02 está resuelto) y ahora es un interruptor de
  exfiltración esperando a que alguien lo active por error. Si se conserva, que
  sea con un setting y no con un global reasignable desde cualquier import.
- **E3 · `crear_habilitacion` desentona.** ~~(resuelto el 2026-09-02: reescrito con la guarda de ambiente de §A6 y una respuesta que dice qué hizo)~~ `apps/emisores/views/emisor.py:146-192`:
  docstring vacío, mensajes sin acentos y con mayúsculas erráticas («EL
  certificado esta vencido»), un `Response({}, status=200)` que no dice qué pasó,
  y datos del sandbox incrustados. Rodeado de código que documenta hasta el
  último criterio, se nota. Vale la pena reescribirlo con el estilo del resto.
- **E4 · Scaffolding vacío.** ~~(resuelto el 2026-09-02: los cuatro ficheros borrados)~~ `apps/dian/views.py`, `apps/dian/models.py` y
  `apps/nucleo/views.py` son los tres ficheros de `startapp` sin tocar (`from
  django.shortcuts import render` y un comentario). `apps/emisores/tests.py`
  también está vacío.
- **E5 · Documentación desfasada.** ~~(resuelto el 2026-09-02: los tres puntos)~~ Tres puntos concretos:
  - `README.md:12` «Todo el pipeline está cubierto por pruebas» (ver §C1).
  - `README.md:94` da `DIAN_POLICY_HASH` como «*(vacío — configúralo)*», pero
    `settings/base.py:214` ya trae el hash real por defecto.
  - `servicios/notificacion.py:5` y `:118` dicen que «el envío por correo todavía
    no está implementado» y hablan de «el día que exista»; está implementado
    desde entonces, en el mismo fichero (`enviar_notificacion`).
- **E6 · Dos módulos grandes.** ~~(resuelto el 2026-09-02: `ubl` es un paquete por familia y la nómina salió de `servicios.py`)~~ `apps/dian/ubl.py` (1.340 líneas, once
  constructores) y `apps/dian/servicios.py` (989, con la mitad de nómina pegada
  al final tras un separador de comentarios). Ninguno está mal escrito —la
  jerarquía de constructores es correcta y los comentarios sostienen la lectura—,
  pero `ubl.py` ya es el sitio donde hay que buscarlo todo. La partición natural
  es por familia: factura y notas, documento soporte, documento equivalente,
  attached document.

---

## Plan de trabajo

Cuatro fases, ordenadas por *lo que pasa si no se hace*. Las tres primeras son
arreglos acotados; la cuarta es el único cambio estructural.

### Fase 1 — Cerrar la exposición de secretos

Es lo primero porque es lo único que, si se materializa, no se puede deshacer.

1. ~~`SoftwareDian.pin` a `write_only`.~~ **Descartado el 2026-09-02**: el PIN
   se queda visible y **A2** se cierra como riesgo aceptado; el porqué y la
   condición que obligaría a revisarlo están en el apartado. **(A2)**
2. ~~Recortar `CertificadoViewSet`.~~ **Hecho el 2026-09-02**: `list`,
   `retrieve`, `destroy` y `cargar`; `update`/`partial_update` fuera. **(A3)**
3. ~~Cifrar `Certificado.clave`.~~ **Hecho el 2026-09-02**: `ClaveCifradaField`
   con `CERT_ENCRYPTION_KEY` propia, migración `0026` y entradas en
   `.env.example` y en `docs/despliegue.md`. **(A1)**
4. ~~Pasar Zinc a `https`.~~ **No se puede desde aquí**: la pasarela no escucha
   en 443 (comprobado el 2026-09-02). Queda como trabajo en Zinc; cuando sirva
   TLS es solo cambiar `ZINC_URL_BASE`. **(A4)**

### Fase 2 — Poner red bajo lo que ya funciona

Sin esto, cualquier arreglo posterior se hace a ciegas sobre los dos módulos que
más se van a tocar.

1. ~~`LOGGING` y una línea por transición de estado.~~ **Hecho el 2026-09-02**:
   `LOGGING` a stdout (journald) y siete líneas —firmado, enviado y estado
   actualizado, por duplicado para documento y nómina, más la notificación.
   **(D1)**
2. ~~Pruebas de `apps/nomina`.~~ **Hecho el 2026-09-02** (19). **(C1)**
3. ~~Pruebas del documento equivalente P.O.S.~~ **Hecho el 2026-09-02** (13). **(C1)**
4. ~~Pruebas de `crear-habilitacion`.~~ **Hecho el 2026-09-02** (10). **(C2)**
5. ~~Workflow de CI.~~ **Hecho el 2026-09-02**:
   `.github/workflows/pruebas.yml`. **(C3)**

> **Paso previo no planeado: la suite estaba en 32 rojos de 211.** No era código
> roto sino pruebas que no habían seguido a tres cambios deliberados
> (`SoftwareDian.tipo` obligatorio, `codigo_postal` obligatorio, el DS pasado a
> `CUDS-SHA384`), más dos que habían dejado de probar lo que decían. Se
> repararon antes de escribir ninguna nueva: sobre una suite roja, un fallo
> nuevo no se distingue del ruido.
>
> La regla de no escribir pruebas durante el diseño se retiró el 2026-09-02, que
> es lo que permitió abordar esta fase sin esperar a que el P.O.S. cerrara
> habilitación.

### Fase 3 — Los 500 y las asimetrías · **hecha el 2026-09-02**

Los doce puntos, más **B9**, que salió escribiendo las pruebas de la fase 2.
262 pruebas en verde.

1. ~~`DocumentoCrearSerializer.update` con `detalles`.~~ **400 explícito.** Las
   líneas no se editan: quien corrige un borrador lo borra y lo rehace. Editarlas
   obligaría a reemplazarlas y recalcular los tres totales, y no compensa. **(B1)**
2. ~~Filtros de la query string.~~ `apps.nucleo.api.entero_de_query` en los cinco
   sitios que filtran por id. Se eligió **400 y no ignorar el valor**: un filtro
   que no se aplica devuelve más filas de las pedidas, y eso se descubre tarde y
   mal. **(B2)**
3. ~~`select_for_update` en `emitir` y `enviar`.~~ Un `_bloquear` en cada viewset,
   dentro de `transaction.atomic`. Mantiene la transacción abierta durante la
   llamada SOAP, que es el precio de que el envío sea de uno en uno. **(B3)**
4. ~~`_ya_procesado` en `enviar_nomina_a_dian`.~~ Un reenvío tras un corte de red
   dejaba la nómina en `rechazado` con un error que no habla de su contenido. **(B4)**
5. ~~`Resolucion.consecutivo_actual`.~~ **Retirado** (migración `0027`), junto con
   la propiedad `siguiente_consecutivo`, que tampoco llamaba nadie. La numeración
   la lleva el ERP; un contador que siempre marca cero invita a fiarse de él. **(B5)**
6. ~~Consecutivo de la nota de ajuste.~~ Bloqueo de la última fila —`select_for_update`
   no admite agregados— y captura del `IntegrityError` en la vista. **(B6)**
7. ~~`search_fields`.~~ Número, CUFE, y NIT y razón social del receptor. **(B7)**
8. ~~NIT por subcadena.~~ Ahora se comparan las rachas de dígitos enteras, con el
   dígito de verificación admitido pegado. Con dos pruebas nuevas. **(B8)**
9. ~~Oráculo de ids y datos del sandbox.~~ `_emisor_del_cuerpo` busca dentro del
   alcance, así que un id ajeno y uno inexistente responden igual;
   `crear-habilitacion` rechaza a un emisor que ya está en producción, devuelve
   qué hizo en vez de `{}`, y quedó reescrito con el estilo del resto. **(A5, A6, E3)**
10. ~~Throttling.~~ 300/hora por credencial y 30/hora anónimo, ajustables por
    entorno. Hizo falta un throttle propio: el de DRF construye su clave con
    `request.user.pk` y el principal de una API Key no es un modelo. **(A7, parcial:
    la auditoría sigue pendiente)**
11. ~~Barrido de documentación.~~ Los tres puntos de **E5** y el scaffolding vacío
    de **E4** (cuatro ficheros de `startapp` sin tocar). **(E4, E5)**
12. ~~Orden de los listados.~~ `order_by` explícito tras el `annotate`. **(B9)**

### Fase 4 — Preparar el volumen del P.O.S. · **hecha el 2026-09-02, salvo D3**

Se abordó sin esperar a la habilitación ni al tráfico, por decisión de MarioA.
Eso deja **D3 fuera**: es el único punto cuya decisión depende de datos que aún
no existen. 274 pruebas en verde.

1. ~~PBKDF2 en `LlaveApi`.~~ **SHA-256**, no HMAC: el secreto lo genera el
   servidor con ~238 bits, así que encarecer cada intento no protege de nada y lo
   paga el ERP legítimo en cada petición. Migración perezosa —el hash viejo se
   verifica y se reescribe al primer uso—, así que ninguna integración rota su
   llave. `registrar_uso()` pasa a escribir como mucho cada 5 minutos. **(D2)**
2. ~~Índice del listado.~~ `(emisor, -fecha, -consecutivo)` en documentos y en
   nóminas. Con 20 filas no mide nada; se crea ahora precisamente porque hacerlo
   sobre una tabla con volumen bloquea las escrituras. **(D4)**
3. **D3 sigue abierto**, y es el único. Antes de elegir entre la cola y subir
   hilos de gunicorn hay que medir, y ya se puede: el logging de D1 deja una
   línea por envío. Lo que hace falta saber es **cuánto tarda un envío real**
   (percentil 95) y **cuántos concurrentes** hay en hora punta. Con eso, si la
   cola no aporta, sobra con gunicorn y no se mete una pieza de infraestructura
   nueva ni se cambia el contrato del ERP. **(D3)**
4. ~~Representación gráfica.~~ El título del PDF y el pie llevan el nombre del
   tipo —decían «Factura» en una nota crédito—, el identificador se rotula según
   su tipo (CUFE, CUDE o **CUDS**, leído del constructor UBL para que el papel no
   pueda contradecir al XML), el QR va en **todas** las páginas y las retenciones
   del documento soporte aparecen en los totales, con su neto a girar. **La
   nómina sigue sin representación gráfica**, por decisión de alcance: es un
   diseño nuevo, no un arreglo. **(E1, parcial)**
5. ~~Partir los dos módulos grandes.~~ `ubl.py` (1.340 líneas) es ahora un
   paquete por familia —`base`, `factura`, `soporte`, `adjunto`, `equivalente`—
   y la mitad de nómina de `servicios.py` vive en `servicios_nomina.py` (1.069 →
   772 + 355). **La superficie pública no cambió**: ambos reexportan todo, así
   que `from apps.dian import ubl` y `servicios.generar_y_firmar_nomina` valen
   igual. **(E6)**
6. ~~`DIRECTORIO_CAPTURA`.~~ Deja de ser un global reasignable desde cualquier
   import —lo que volcaba es el documento firmado y el certificado— y pasa a
   `settings.DIAN_DIRECTORIO_CAPTURA`, que sale del entorno. **(E2)**
