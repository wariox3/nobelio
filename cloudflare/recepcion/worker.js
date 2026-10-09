// Email Worker `nobelio-recepcion`: recibe los correos de <nit>@recepcion.<dominio>,
// guarda el MIME crudo en R2 y se lo publica a nobelio en POST /recepcion/inbound.
// El contrato (cabeceras, códigos) está en docs/recepcion.md y en
// apps/recepcion/views/inbound.py.
//
// Necesita:
//   - RAW: vinculación al bucket R2 (nobelio-inbound-raw).
//   - NOBELIO_URL: variable, p. ej. https://api.rededoc.uk/recepcion/inbound
//   - INBOUND_TOKEN: secreto, el mismo valor que en /opt/nobelio/.env.

// Esperas antes de cada reintento del POST, en milisegundos.
const ESPERAS = [1000, 3000, 9000];

export default {
  async email(message, env, ctx) {
    // Entero en memoria y no por stream: R2 exige conocer el largo del cuerpo,
    // y el stream de `message.raw` no lo trae. Email Routing no entrega más
    // de 25 MB, así que cabe de sobra.
    const raw = await new Response(message.raw).arrayBuffer();

    // Carpeta por fecha UTC: solo organiza (docs/recepcion.md, "Carpeta en R2").
    const fecha = new Date().toISOString().slice(0, 10);
    const clave = `${fecha}/${crypto.randomUUID()}.eml`;
    await env.RAW.put(clave, raw, {
      httpMetadata: { contentType: "message/rfc822" },
      customMetadata: { to: message.to, from: message.from },
    });

    const peticion = {
      method: "POST",
      headers: {
        "Content-Type": "message/rfc822",
        "Authorization": `Bearer ${env.INBOUND_TOKEN}`,
        "X-Envelope-To": message.to,
        "X-Envelope-From": message.from,
        "X-Raw-Key": clave,
      },
      body: raw,
    };

    // nobelio es idempotente por el SHA-256 del body: reintentar no duplica.
    // Solo se reintentan la red y los 5xx; un 4xx (token, cabeceras, tamaño)
    // no se arregla repitiendo.
    let ultimo = "";
    for (let intento = 0; intento <= ESPERAS.length; intento++) {
      if (intento > 0) {
        await new Promise((r) => setTimeout(r, ESPERAS[intento - 1]));
      }
      try {
        const respuesta = await fetch(env.NOBELIO_URL, peticion);
        if (respuesta.ok) {
          console.log(`recepcion.ok ${respuesta.status} to=${message.to} key=${clave}`);
          return;
        }
        ultimo = `${respuesta.status} ${await respuesta.text()}`;
        if (respuesta.status < 500) {
          break;
        }
      } catch (error) {
        ultimo = String(error);
      }
    }

    // El MIME ya quedó en R2: se puede volver a publicar a mano con la clave
    // (docs/recepcion.md, "Montaje en Cloudflare").
    console.error(`recepcion.fallo to=${message.to} key=${clave} ${ultimo}`);
    throw new Error(`nobelio no registró el correo (${clave}): ${ultimo}`);
  },
};
