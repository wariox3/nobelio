"""Catálogos DIAN en la memoria de cada proceso.

Crear un documento valida una decena de ids de catálogo —tipo de
identificación, país, municipio, moneda, forma y medio de pago, unidad,
tributo…—, y cada uno era una consulta. Con la base fuera del servidor, cada
consulta es un viaje por la red, y los catálogos casi nunca cambian: solo los
escribe `cargar_catalogos`, con `update_or_create`, y no se borran.

Así que cada proceso guarda la tabla entera la primera vez que la necesita y la
vuelve a leer cuando vence (`CATALOGOS_EN_MEMORIA_SEGUNDOS`). Lo que eso deja:

- **Un id nuevo** —cargado después de leer la tabla— no está en memoria y se
  busca en la base: funciona desde el primer momento.
- **Un cambio en una fila existente** —otro nombre, otro código— tarda como
  mucho ese tiempo en verse. `actualizar.sh` para el servicio antes de
  `cargar_catalogos` y lo levanta después, así que en un despliegue la memoria
  empieza limpia; tras un `cargar_catalogos` suelto, `systemctl restart
  nobelio` o esperar a que venza.

Se entrega una copia de la fila y no la guardada: la misma instancia pasaría
por varias peticiones y varios hilos a la vez, y cualquier caché que Django le
colgara a una se vería en todas.

Con `0` no se guarda nada y cada id vuelve a la base. Es como corre la suite,
para que un catálogo creado en una prueba y deshecho al terminarla no siga
vivo en la memoria de la siguiente; las pruebas de este módulo lo encienden.
"""
import copy
import threading
import time

from django.conf import settings

from apps.nucleo.serializers import RelacionMemorizada

from .models.base import ElementoCatalogo

_tablas = {}
_candado = threading.Lock()


def es_catalogo(modelo):
    return issubclass(modelo, ElementoCatalogo)


def buscar(modelo, pk):
    """Copia de la fila ``pk`` de ``modelo``, o ``None`` si no está en memoria.

    ``None`` no quiere decir que no exista —puede ser nueva, o estar la memoria
    apagada—: quien llama la busca entonces en la base.
    """
    segundos = settings.CATALOGOS_EN_MEMORIA_SEGUNDOS
    if not segundos:
        return None
    filas = _filas(modelo, segundos)
    fila = filas.get(pk)
    return copy.copy(fila) if fila is not None else None


def olvidar():
    """Vacía la memoria de este proceso. Solo tiene sentido en pruebas."""
    with _candado:
        _tablas.clear()


def _filas(modelo, segundos):
    ahora = time.monotonic()
    entrada = _tablas.get(modelo)
    if entrada is None or entrada[0] <= ahora:
        with _candado:
            # Otro hilo pudo leerla mientras este esperaba el candado.
            entrada = _tablas.get(modelo)
            if entrada is None or entrada[0] <= ahora:
                filas = {fila.pk: fila for fila in modelo._default_manager.all()}
                entrada = (ahora + segundos, filas)
                _tablas[modelo] = entrada
    return entrada[1]


class RelacionDeCatalogo(RelacionMemorizada):
    """Relación por id que resuelve los catálogos desde la memoria del proceso.

    Solo cuando el queryset del campo es la tabla entera: si algún campo lo
    acotara, la memoria —que guarda todas las filas— aceptaría ids que el
    campo rechaza. Lo que no es un id con forma de entero, lo que no está en
    memoria y lo que no es catálogo sigue el camino de siempre, con la memoria
    por petición de `RelacionMemorizada` y los errores de DRF.
    """

    def to_internal_value(self, data):
        queryset = self.get_queryset()
        if (
            es_catalogo(queryset.model)
            and not queryset.query.where
            # `True` es un `int` para Python y DRF lo rechaza: que lo diga DRF.
            and isinstance(data, (int, str)) and not isinstance(data, bool)
        ):
            try:
                pk = int(data)
            except ValueError:
                pk = None
            if pk is not None:
                fila = buscar(queryset.model, pk)
                if fila is not None:
                    return fila
        return super().to_internal_value(data)
