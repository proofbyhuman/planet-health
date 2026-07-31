/**
 * Service worker de Planet Health.
 *
 * Hasta ahora este archivo existía y **nadie lo registraba**: el modo offline
 * que el README pide como requisito central nunca se había activado. Se registra
 * desde `js/app.js`.
 *
 * Dos estrategias distintas, porque las cosas que cachea no se parecen en nada:
 *
 * - **Los archivos de la aplicación** (HTML, CSS, JS) van con caché primero pero
 *   revalidando por detrás: se responde al toque con lo guardado y en paralelo se
 *   pide la versión nueva para la próxima vez.
 * - **Las respuestas de la API** van con red primero y caché de respaldo. Un
 *   informe viejo sirve sin conexión, pero con señal hay que pedir el de ahora:
 *   servir siempre el cacheado dejaría a alguien mirando la humedad relativa de
 *   la semana pasada creyendo que es la de hoy.
 *
 * **Por qué revalidar y no caché a secas.** La primera versión de esto era caché
 * pura, y durante el desarrollo de la Fase 2 se corrigió un error de CSS que en
 * el navegador no aparecía: el service worker seguía sirviendo la hoja vieja.
 * Con caché pura, la única forma de que alguien reciba una corrección es que
 * cambie la constante VERSION de acá, y eso es una lista de pasos que en algún
 * despliegue se va a olvidar. Peor todavía en una herramienta que se instala en
 * el teléfono y se abre semanas después en el campo: una corrección de un dato
 * mal calculado tiene que llegar sola. Revalidando, la primera apertura con
 * señal ya baja lo nuevo y la siguiente lo usa.
 *
 * Lo que **no** se cachea son las respuestas de error. Guardar un 500 dejaría esa
 * coordenada rota hasta que se limpie la caché a mano.
 */

const VERSION = 'planet-health-v2';
const CACHE_APP = `${VERSION}-app`;
const CACHE_API = `${VERSION}-api`;

/**
 * Todo lo que hace falta para que la aplicación abra sin conexión.
 *
 * La lista anterior había quedado desactualizada: nombraba archivos que ya no
 * existen y le faltaban dos que sí. Si un archivo de acá no existe, `addAll()`
 * falla entero y no se cachea nada, así que conviene revisarla al mover
 * archivos.
 */
const ARCHIVOS_APP = [
  './',
  './index.html',
  './manifest.json',
  './css/styles.css',
  './js/app.js',
  './js/estado.js',
  './js/mapa.js',
  './js/plantillas.js',
  './js/servicios/api_cliente.js',
  './js/servicios/registros_api.js',
  './js/servicios/inferencia_api.js',
  './js/servicios/almacen_offline.js',
  './js/servicios/sello_local.js',
  './js/vistas/indicador.js',
  './js/vistas/informe.js',
  './js/vistas/modulo.js',
  './js/vistas/sello.js',
];

self.addEventListener('install', (evento) => {
  evento.waitUntil(
    caches
      .open(CACHE_APP)
      .then((cache) => cache.addAll(ARCHIVOS_APP))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (evento) => {
  evento.waitUntil(
    caches
      .keys()
      .then((claves) =>
        Promise.all(claves.filter((c) => !c.startsWith(VERSION)).map((c) => caches.delete(c)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (evento) => {
  const { request } = evento;

  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  // Leaflet viene de un CDN. Sin conexión no está, y el mapa no aparece; la
  // aplicación sigue andando con el formulario de coordenadas. No se cachea
  // desde acá para no guardar respuestas opacas de otro origen.
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith('/api/')) {
    evento.respondWith(redPrimeroConRespaldo(request));
    return;
  }

  evento.respondWith(cacheRevalidando(request));
});

/**
 * Caché primero, revalidando por detrás, para los archivos de la aplicación.
 *
 * Responde de inmediato con lo guardado —así abre rápido y abre sin señal— y en
 * paralelo pide la versión nueva y la deja lista para la próxima apertura.
 *
 * @param {Request} request
 * @returns {Promise<Response>}
 */
async function cacheRevalidando(request) {
  const cache = await caches.open(CACHE_APP);
  const guardado = await cache.match(request);

  const enRed = fetch(request)
    .then((respuesta) => {
      if (respuesta.ok) cache.put(request, respuesta.clone());
      return respuesta;
    })
    .catch(() => null);

  if (guardado) {
    // No se espera a la red: la actualización queda para la próxima carga.
    return guardado;
  }

  const reciente = await enRed;
  if (reciente) return reciente;

  // Una navegación sin conexión y sin caché cae en la portada, que sí está
  // cacheada, en lugar de mostrar el error del navegador.
  if (request.mode === 'navigate') {
    const portada = await cache.match('./index.html');
    if (portada) return portada;
  }
  return Response.error();
}

/**
 * Red primero con respaldo en caché, para la API.
 *
 * @param {Request} request
 * @returns {Promise<Response>}
 */
async function redPrimeroConRespaldo(request) {
  const cache = await caches.open(CACHE_API);

  try {
    const respuesta = await fetch(request);
    // Solo se guardan respuestas buenas: un 500 cacheado deja la coordenada rota.
    if (respuesta.ok) {
      cache.put(request, await conMarcaDeGuardado(respuesta.clone()));
    }
    return respuesta;
  } catch (error) {
    const guardado = await cache.match(request);
    if (guardado) return marcarComoServidoDeCache(guardado);
    throw error;
  }
}

/**
 * Agrega la fecha de guardado a una respuesta antes de cachearla.
 *
 * @param {Response} respuesta
 * @returns {Promise<Response>}
 */
async function conMarcaDeGuardado(respuesta) {
  const cabeceras = new Headers(respuesta.headers);
  cabeceras.set('x-ph-guardado-en', new Date().toISOString());
  return new Response(await respuesta.blob(), {
    status: respuesta.status,
    statusText: respuesta.statusText,
    headers: cabeceras,
  });
}

/**
 * Marca una respuesta que sale de la caché del dispositivo.
 *
 * **Por qué hace falta.** El backend marca `desde_cache` en cada fuente cuando el
 * dato sale de *su* caché de disco. Pero cuando la respuesta entera sale de la
 * caché de *este* service worker, el JSON de adentro sigue diciendo que fue una
 * consulta viva, porque lo era cuando se guardó. Sin esta marca, alguien sin
 * señal vería un informe con fecha y aspecto de recién hecho.
 *
 * Es el mismo criterio que se aplica en todo el proyecto: un dato viejo sirve, y
 * mucho, siempre que se sepa que es viejo.
 *
 * @param {Response} respuesta
 * @returns {Promise<Response>}
 */
async function marcarComoServidoDeCache(respuesta) {
  const cabeceras = new Headers(respuesta.headers);
  cabeceras.set('x-ph-desde-cache-local', 'si');
  return new Response(await respuesta.blob(), {
    status: respuesta.status,
    statusText: respuesta.statusText,
    headers: cabeceras,
  });
}
