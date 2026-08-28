"""Variantes de firma para acorralar el rechazo ZE02 de nómina. **TEMPORAL.**

La DIAN rechaza nuestras nóminas con ZE02 ("el valor de la Firma difiere del
calculado") mientras la firma verifica con tres implementaciones distintas,
incluida una comprobación sobre un documento de nómina que **ella misma aceptó**
y que descargamos de su catálogo. Ese documento aceptado es la referencia: cada
variante de aquí alinea con él un detalle más de los que todavía nos separan.

Se elige por documento, al emitir (``{"variante": "..."}``), para poder mandar
una por documento e identificar cuál pasa sin tener que editar y revertir el
firmador cada vez. En cuanto se sepa cuál era, este módulo desaparece y lo que
funcione se deja fijo.

Ninguna de las variantes cambia el significado del documento: son diferencias de
representación que, según el estándar, no deberían afectar a la validación —y
justamente por eso son las candidatas que quedan—.
"""

VARIANTES = {
    "ns-propios": (
        "ds:Signature declara su propio xmlns:ds, y xades:QualifyingProperties "
        "los suyos, en vez de heredarlos de la raíz. Es lo que hace el documento "
        "aceptado, y es la diferencia que importaría si el validador extrae el "
        "nodo y lo canonicaliza suelto, perdiendo los namespaces heredados."
    ),
    "decl-comillas": (
        "La declaración XML con comillas dobles, como el documento aceptado; "
        "lxml las emite simples. Queda fuera de la canonicalización, así que no "
        "puede alterar ningún digest, pero sí cambia los bytes transmitidos."
    ),
    "ref1-id": (
        "La referencia al KeyInfo lleva atributo Id, como en el aceptado; la "
        "nuestra solo tiene URI."
    ),
    "contenido": (
        "El cuerpo se alinea con el aceptado: Novedad siempre presente y sin "
        "Notas."
    ),
    "ref2-orden": (
        "En la referencia a SignedProperties, Type antes de URI, como en el "
        "aceptado. C14N ordena los atributos, así que no debería importar."
    ),
}

TODAS = tuple(VARIANTES)


def normalizar(valor) -> frozenset:
    """Convierte lo que llega por el API en el conjunto de variantes activas.

    Acepta ``"todas"``, una sola, o varias separadas por coma. Un nombre que no
    exista es un error y no un silencio: el objetivo de esto es saber con qué se
    firmó cada documento, y una variante mal escrita que no se aplique dejaría
    el experimento sin conclusión.
    """
    if not valor:
        return frozenset()
    if isinstance(valor, str):
        nombres = [v.strip() for v in valor.split(",") if v.strip()]
    else:
        nombres = list(valor)
    if nombres == ["todas"]:
        return frozenset(TODAS)
    desconocidas = [n for n in nombres if n not in VARIANTES]
    if desconocidas:
        raise ValueError(
            f"Variante(s) de firma desconocida(s): {', '.join(desconocidas)}. "
            f"Las que hay: {', '.join(TODAS)}, o 'todas'."
        )
    return frozenset(nombres)
