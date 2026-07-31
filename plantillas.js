/**
 * Clonado y llenado de las <template> de index.html.
 *
 * Es la pieza que sostiene la separación: el marcado vive en index.html y las
 * vistas solo lo clonan y le ponen texto. En todo js/vistas/ no hay una sola
 * cadena de HTML.
 *
 * Aparte de la prolijidad, hay un motivo de seguridad. Los datos que se pintan
 * vienen de servicios externos: los nombres de familia salen de GBIF, los
 * motivos y las atribuciones del backend. Todo se escribe con `textContent`, que
 * no interpreta marcado. La versión anterior armaba filas con `innerHTML` y
 * plantillas de cadena — la tabla del contrato de datos, por ejemplo—, así que
 * un nombre científico con un `<` en un registro de GBIF podía romper la tabla,
 * y algo peor podía inyectarse.
 */

/**
 * Clona una plantilla por id.
 *
 * @param {string} id - id del elemento <template>.
 * @returns {DocumentFragment}
 * @throws {Error} Si la plantilla no existe. Es un error de programación:
 *   significa que index.html y las vistas se desincronizaron.
 */
export function clonarPlantilla(id) {
  const plantilla = document.getElementById(id);
  if (!plantilla || !(plantilla instanceof HTMLTemplateElement)) {
    throw new Error(`Falta la plantilla "${id}" en index.html`);
  }
  return plantilla.content.cloneNode(true);
}

/**
 * Busca un elemento marcado con `data-campo="nombre"`.
 *
 * @param {ParentNode} raiz
 * @param {string} nombre
 * @returns {HTMLElement|null}
 */
export function campo(raiz, nombre) {
  return raiz.querySelector(`[data-campo="${nombre}"]`);
}

/**
 * Escribe texto en un campo. Si el texto viene vacío, oculta el campo.
 *
 * @param {ParentNode} raiz
 * @param {string} nombre
 * @param {string|number|null|undefined} texto
 * @returns {HTMLElement|null} El elemento, por si hay que seguir tocándolo.
 */
export function escribir(raiz, nombre, texto) {
  const elemento = campo(raiz, nombre);
  if (!elemento) return null;
  const contenido = texto === null || texto === undefined ? '' : String(texto);
  elemento.textContent = contenido;
  elemento.hidden = contenido === '';
  return elemento;
}

/**
 * Muestra u oculta un campo sin tocar su contenido.
 *
 * @param {ParentNode} raiz
 * @param {string} nombre
 * @param {boolean} visible
 */
export function mostrar(raiz, nombre, visible) {
  const elemento = campo(raiz, nombre);
  if (elemento) elemento.hidden = !visible;
}

/**
 * Vacía un contenedor.
 *
 * `replaceChildren()` en lugar de `innerHTML = ''`: no reinterpreta marcado y
 * suelta los nodos viejos de una.
 *
 * @param {HTMLElement} contenedor
 */
export function vaciar(contenedor) {
  contenedor.replaceChildren();
}

/**
 * Borra los `data-campo` de un componente ya terminado.
 *
 * **El error que esto evita, que ya se produjo.** Las plantillas se anidan: una
 * tarjeta de módulo tiene adentro varios indicadores, y tanto el módulo como
 * cada indicador tienen un campo llamado `limitaciones`. `campo()` usa
 * `querySelector`, que devuelve el primero en orden de documento y no distingue
 * niveles. Así que cuando `crearModulo()` insertaba los indicadores y después
 * escribía las limitaciones del módulo, el texto terminaba dentro del primer
 * indicador.
 *
 * El resultado no era un hueco cosmético: la tarjeta de Hidrología mostraba, en
 * el detalle del indicador de precipitación, la advertencia del módulo —"ningún
 * dato de este módulo describe agua subterránea"— en lugar de la que
 * corresponde a ERA5, que habla de la resolución de 25 km. Una limitación
 * puesta en el indicador equivocado es peor que ninguna: dice algo cierto sobre
 * otra cosa.
 *
 * Una vez que un componente está armado, sus marcadores ya cumplieron su
 * función. Borrarlos hace que ninguna consulta de un componente padre pueda
 * alcanzarlos, y cierra la clase entera de error en lugar de este caso puntual.
 *
 * @param {ParentNode} nodo - Componente terminado, antes de insertarlo.
 * @returns {ParentNode} El mismo nodo, para poder encadenar.
 */
export function sellarComponente(nodo) {
  for (const elemento of nodo.querySelectorAll('[data-campo]')) {
    elemento.removeAttribute('data-campo');
  }
  return nodo;
}
