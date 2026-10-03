"""
Recepción de facturas de proveedores.

Cada emisor reenvía su buzón de compras a ``<nit>@recepcion.rededoc.co``; un
Email Worker de Cloudflare guarda el MIME crudo en R2 y lo publica aquí.
"""
