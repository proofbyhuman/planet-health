/**
 * Estado de la aplicación en un solo lugar, y control de consultas superpuestas.
 *
 * **La carrera que esto arregla.** En la versión anterior, cada clic en el mapa
 * llamaba a `analyzeLocation()` sin cancelar la consulta anterior. Haciendo dos
 * clics seguidos —cosa normal buscando un punto— salían dos análisis en paralelo
 * y pintaba el que terminara último, que no es necesariamente el último que se
 * pidió: si la primera consulta era lenta y la segunda rápida, la pantalla
 * terminaba mostrando los datos del primer punto con la chincheta clavada en el
 * segundo. Sin ninguna señal de que no correspondían.
 *
 * Para una herramienta cuyo propósito es decir cómo está *este* lugar, mostrar
 * los datos de otro es de los peores errores posibles. Acá cada consulta nueva
 * aborta la anterior.
 */

/** @type {{lat: number|null, lon: number|null, informe: object|null}} */
export const estado = {
  lat: null,
  lon: null,
  informe: null,
};

/** @type {AbortController|null} */
let consultaEnCurso = null;

/**
 * Abre una consulta nueva y cancela la que estuviera corriendo.
 *
 * @returns {AbortSignal}
 */
export function iniciarConsulta() {
  if (consultaEnCurso) consultaEnCurso.abort();
  consultaEnCurso = new AbortController();
  return consultaEnCurso.signal;
}

/** Marca que terminó la consulta en curso. */
export function terminarConsulta() {
  consultaEnCurso = null;
}

/**
 * Valida una coordenada antes de gastar una consulta.
 *
 * El backend valida igual y devuelve 422; esto es para dar un mensaje inmediato
 * y no cargar al servidor con lo que ya se sabe mal.
 *
 * @param {number} lat
 * @param {number} lon
 * @returns {string|null} El problema, o `null` si la coordenada sirve.
 */
export function problemaConCoordenada(lat, lon) {
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    return 'Hay que escribir dos números: latitud y longitud.';
  }
  if (lat < -90 || lat > 90) return 'La latitud va de -90 a 90 grados.';
  if (lon < -180 || lon > 180) return 'La longitud va de -180 a 180 grados.';
  return null;
}

/**
 * Guarda el informe recibido.
 *
 * @param {number} lat
 * @param {number} lon
 * @param {object} informe
 */
export function guardarInforme(lat, lon, informe) {
  estado.lat = lat;
  estado.lon = lon;
  estado.informe = informe;
}

/** Borra el informe en pantalla. Se usa al empezar una consulta nueva. */
export function limpiarInforme() {
  estado.informe = null;
}
