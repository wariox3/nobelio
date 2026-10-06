# Listas de RADIAN

La DIAN no publica en Genericode los eventos del adquiriente vigentes:

- `Eventos-2.1.gc`, de la *Caja de herramientas RADIAN 1.1*, solo trae los de
  título valor (035–051).
- `EventoDocumento-2.1.gc` y `TiposEventos.gc`, de la caja de factura, están
  desactualizadas: el 030 aparece como «Solicitación de Corrección» y no
  traen el 034.

`EventoRadian.gc` se transcribió de los ejemplos oficiales de la caja RADIAN
(`apps/dian/datos/ejemplos/radian/`): el código es su `cbc:ResponseCode` y el
nombre su `cbc:Description`, que va tal cual al XML del evento.

| Código | Id | Nombre |
|--------|----|--------|
| 030 | 30 | Acuse de recibo de Factura Electrónica de Venta |
| 031 | 31 | Reclamo de la Factura Electrónica de Venta |
| 032 | 32 | Recibo del bien y/o prestación del servicio |
| 033 | 33 | Aceptación expresa |

El id es el código como número. Solo los cuatro del adquiriente: el 034 es del
facturador y los de título valor quedan fuera (`docs/recepcion.md`).

Los conceptos del reclamo (031) son la lista oficial `Concepto de Reclamo.gc`,
en la carpeta padre.
