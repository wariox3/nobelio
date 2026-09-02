"""Formato común de las trazas de emisión.

Las líneas que dejan `apps.dian.servicios` y la notificación se leen en el
servidor con `journalctl` y `grep`, no con un agregador. Eso pide dos cosas:
que los campos vayan siempre en el mismo orden y con el mismo nombre, y que un
campo que falta desaparezca en vez de aparecer vacío, para que un `grep
estado=RECHAZADO` no dependa de la suerte.

`campos` es todo lo que hace falta para eso. No hay una capa de "eventos" ni un
logger propio: cada módulo usa el suyo (`logging.getLogger(__name__)`), que es
lo que permite subir o bajar el nivel de una parte sin tocar el resto.
"""


def campos(**pares) -> str:
    """Formatea ``clave=valor`` separados por espacio, en el orden recibido.

    Los valores vacíos (``None`` o cadena vacía) se omiten: un ``track_id=`` a
    secas no dice nada y estorba al filtrar. El ``0`` y el ``False`` sí se
    escriben, porque ahí el valor es la información —``errores=0`` es
    justamente lo que se quiere ver—.

    Lo que lleva espacios se entrecomilla, de modo que un mensaje de error de la
    DIAN no parta la línea en campos que no existen.
    """
    partes = []
    for clave, valor in pares.items():
        if valor is None or valor == "":
            continue
        texto = str(valor)
        if " " in texto or '"' in texto:
            texto = '"{}"'.format(texto.replace('"', "'"))
        partes.append(f"{clave}={texto}")
    return " ".join(partes)
