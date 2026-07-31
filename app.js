/**
 * Orquestación de Planet Health.
 *
 * Este archivo solo conecta piezas: escucha eventos, pide el informe y le dice a
 * las vistas que pinten. No arma HTML, no calcula nada y no sabe cómo se dibuja
 * un indicador.
 *
 * Antes era una clase de 260 líneas que hacía todo: inicializaba Leaflet,
 * consultaba Open-Meteo y GBIF, inventaba datos de DeepForest, calculaba
 * puntajes, escribía en catorce elementos por id y mostraba resultados con
 * `alert()`.
 */

import { MapaParcelas } from './mapa.js';
import {
  estado,
  guardarInforme,
  iniciarConsulta,
  limpiarInforme,
  problemaConCoordenada,
  terminarConsulta,
} from './estado.js';
import { vaciar } from './plantillas.js';
import { ErrorDeApi, consultarParcela } from './servicios/api_cliente.js';
import { sellarInforme, verificarRegistro } from './servicios/registros_api.js';
import { analizarCanopia, capacidadesDeInferencia } from './servicios/inferencia_api.js';
import { AlmacenOffline } from './servicios/almacen_offline.js';
import { crearModulo } from './vistas/modulo.js';
import { pintarAtribuciones, pintarCobertura, pintarEncabezado } from './vistas/informe.js';
import { pintarSello } from './vistas/sello.js';

/**
 * Punto de partida del mapa.
 *
 * Es solo el encuadre inicial de la vista, no un resultado: la aplicación
 * arranca sin ningún dato en pantalla y no consulta nada hasta que la persona
 * elige un lugar. La versión anterior lanzaba un análisis de Bariloche al cargar
 * y dejaba ese informe en pantalla como si fuera del visitante.
 */
const VISTA_INICIAL = { lat: -41.1335, lon: -71.3103 };

class AplicacionPlanetHealth {
  constructor() {
    this.elementos = {
      formulario: document.getElementById('formulario-coordenadas'),
      lat: document.getElementById('entrada-lat'),
      lon: document.getElementById('entrada-lon'),
      botonUbicacion: document.getElementById('boton-mi-ubicacion'),
      botonGuardar: document.getElementById('boton-guardar'),
      avisoGuardado: document.getElementById('aviso-guardado'),
      botonSellar: document.getElementById('boton-sellar'),
      observacion: document.getElementById('entrada-observacion'),
      panelSello: document.getElementById('panel-sello'),
      seccionCanopia: document.getElementById('seccion-canopia'),
      entradaImagen: document.getElementById('entrada-imagen'),
      botonCanopia: document.getElementById('boton-canopia'),
      avisoCanopia: document.getElementById('aviso-canopia'),
      cargador: document.getElementById('cargador'),
      error: document.getElementById('panel-error'),
      encabezado: document.getElementById('encabezado-informe'),
      modulos: document.getElementById('contenedor-modulos'),
      atribuciones: document.getElementById('panel-atribuciones'),
      cobertura: document.getElementById('panel-cobertura'),
      conexion: document.getElementById('insignia-conexion'),
      version: document.getElementById('pie-version'),
    };

    this.mapa = new MapaParcelas('mapa', (lat, lon) => this.analizar(lat, lon));
    this.iniciar();
  }

  iniciar() {
    this.mapa.iniciar(VISTA_INICIAL.lat, VISTA_INICIAL.lon);
    this.conectarEventos();
    this.actualizarConexion();
    this.registrarServiceWorker();
    this.comprobarCapacidadesDeInferencia();
  }

  /**
   * Muestra el panel de canopia solo si este servidor puede correr DeepForest.
   *
   * Ofrecer un botón que va a fallar es peor que no ofrecerlo: la persona sube
   * una foto, espera, y recibe un error que no puede resolver. Si el entorno no
   * está montado, el módulo de canopia se sigue viendo en el informe como
   * `no_disponible` con el motivo escrito, que es donde corresponde decirlo.
   */
  async comprobarCapacidadesDeInferencia() {
    try {
      const capacidades = await capacidadesDeInferencia();
      this.elementos.seccionCanopia.hidden = !capacidades.canopia_deepforest?.disponible;
    } catch {
      this.elementos.seccionCanopia.hidden = true;
    }
  }

  conectarEventos() {
    this.elementos.formulario.addEventListener('submit', (evento) => {
      evento.preventDefault();
      const lat = Number.parseFloat(this.elementos.lat.value);
      const lon = Number.parseFloat(this.elementos.lon.value);
      this.mapa.moverA(lat, lon);
      this.analizar(lat, lon);
    });

    this.elementos.botonUbicacion.addEventListener('click', () => this.usarMiUbicacion());
    this.elementos.botonGuardar.addEventListener('click', () => this.guardarEnDispositivo());
    this.elementos.botonSellar.addEventListener('click', () => this.sellarYRegistrar());

    this.elementos.entradaImagen.addEventListener('change', () => {
      this.elementos.botonCanopia.disabled = this.elementos.entradaImagen.files.length === 0;
    });
    this.elementos.botonCanopia.addEventListener('click', () => this.analizarImagen());

    window.addEventListener('online', () => this.actualizarConexion());
    window.addEventListener('offline', () => this.actualizarConexion());
  }

  /**
   * Consulta una coordenada y pinta el resultado.
   *
   * @param {number} lat
   * @param {number} lon
   */
  async analizar(lat, lon) {
    const problema = problemaConCoordenada(lat, lon);
    if (problema) {
      this.mostrarError(problema);
      return;
    }

    this.elementos.lat.value = lat.toFixed(5);
    this.elementos.lon.value = lon.toFixed(5);

    // Se limpia todo antes de consultar. Si la consulta falla, la pantalla queda
    // vacía y con el error: nunca con los números del lugar anterior.
    this.limpiarPantalla();
    this.mostrarCargando(true);

    const senal = iniciarConsulta();
    try {
      const informe = await consultarParcela(lat, lon, senal);
      guardarInforme(lat, lon, informe);
      this.pintarInforme(informe);
    } catch (error) {
      // Un aborto no es una falla: es que la persona pidió otra coordenada y esta
      // consulta ya no interesa. No se toca la pantalla, que la consulta nueva
      // está por pintarla.
      if (error.name === 'AbortError') return;
      this.mostrarError(
        error instanceof ErrorDeApi ? error.message : 'Ocurrió un problema inesperado al consultar.'
      );
    } finally {
      if (!senal.aborted) {
        this.mostrarCargando(false);
        terminarConsulta();
      }
    }
  }

  /**
   * @param {object} informe
   */
  pintarInforme(informe) {
    pintarEncabezado(this.elementos.encabezado, informe);
    pintarCobertura(this.elementos.cobertura, informe.cobertura);

    vaciar(this.elementos.modulos);
    for (const modulo of informe.modulos) {
      this.elementos.modulos.append(crearModulo(modulo));
    }

    pintarAtribuciones(this.elementos.atribuciones, informe);

    this.elementos.botonGuardar.disabled = false;
    // Sellar exige conexión: la firma la hace el servidor con su clave privada,
    // que no puede estar en el navegador de nadie.
    this.elementos.botonSellar.disabled = !navigator.onLine;
    this.elementos.version.textContent =
      `Contrato de datos v${informe.version_contrato} · informe generado el ` +
      new Date(informe.generado_en).toLocaleString('es-AR');
  }

  limpiarPantalla() {
    limpiarInforme();
    this.elementos.error.hidden = true;
    this.elementos.encabezado.hidden = true;
    this.elementos.atribuciones.hidden = true;
    this.elementos.avisoGuardado.hidden = true;
    this.elementos.botonGuardar.disabled = true;
    this.elementos.botonSellar.disabled = true;
    vaciar(this.elementos.encabezado);
    vaciar(this.elementos.modulos);
    vaciar(this.elementos.cobertura);
    vaciar(this.elementos.atribuciones);
    // El panel del sello se limpia con el resto: un registro firmado pertenece a
    // un informe concreto, y dejarlo en pantalla junto a otro informe daría a
    // entender que ese otro está firmado.
    vaciar(this.elementos.panelSello);
    this.elementos.version.textContent = '';
  }

  /**
   * @param {boolean} visible
   */
  mostrarCargando(visible) {
    this.elementos.cargador.hidden = !visible;
  }

  /**
   * @param {string} mensaje
   */
  mostrarError(mensaje) {
    this.limpiarPantalla();
    this.elementos.error.textContent = mensaje;
    this.elementos.error.hidden = false;
  }

  usarMiUbicacion() {
    if (!navigator.geolocation) {
      this.mostrarError('Este navegador no ofrece la ubicación del dispositivo.');
      return;
    }
    this.elementos.botonUbicacion.disabled = true;
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        this.elementos.botonUbicacion.disabled = false;
        this.mapa.moverA(coords.latitude, coords.longitude);
        this.analizar(coords.latitude, coords.longitude);
      },
      (error) => {
        this.elementos.botonUbicacion.disabled = false;
        this.mostrarError(
          error.code === error.PERMISSION_DENIED
            ? 'No diste permiso para acceder a la ubicación. Podés escribir la coordenada a mano.'
            : 'No se pudo obtener la ubicación del dispositivo.'
        );
      },
      { enableHighAccuracy: true, timeout: 15000 }
    );
  }

  async guardarEnDispositivo() {
    if (!estado.informe) return;
    const aviso = this.elementos.avisoGuardado;
    try {
      const guardado = await AlmacenOffline.guardarInforme(estado.informe, estado.lat, estado.lon);
      // Se dice exactamente qué se guardó y qué prueba el sello. La firma
      // Ed25519 del servidor llega en la Fase 3; hasta entonces esto es un hash
      // local, y decir otra cosa sería el mismo error que el cartel "Sello
      // Criptográfico Activo" de la versión anterior, que estaba siempre
      // encendido sobre un SHA-256 sin clave.
      aviso.textContent =
        `Guardado en este dispositivo. Huella de integridad local ${guardado.huella}. ` +
        'Detecta si el archivo se modifica después de guardarlo; no lleva firma todavía.';
      aviso.hidden = false;
    } catch {
      aviso.textContent = 'No se pudo guardar en este dispositivo.';
      aviso.hidden = false;
    }
  }

  /**
   * Manda una imagen a DeepForest y agrega el módulo de canopia al informe.
   */
  async analizarImagen() {
    const [imagen] = this.elementos.entradaImagen.files;
    if (!imagen) return;

    const boton = this.elementos.botonCanopia;
    const aviso = this.elementos.avisoCanopia;
    boton.disabled = true;
    aviso.hidden = false;
    aviso.classList.remove('aviso-error');
    aviso.textContent = 'Enviando la imagen…';

    const textos = {
      en_cola: 'En cola…',
      corriendo: 'Corriendo DeepForest. Puede tardar medio minuto.',
    };

    try {
      const trabajo = await analizarCanopia(imagen, (estado) => {
        aviso.textContent = textos[estado] ?? 'Procesando…';
      });

      // Se agrega como un módulo más, con la misma tarjeta y las mismas
      // insignias de procedencia que el resto. Un conteo hecho por un modelo es
      // tan `medido` como una lectura de Open-Meteo, y se muestra igual.
      const modulos = this.elementos.modulos;
      modulos.querySelector('[data-modulo="canopia"]')?.remove();
      const tarjeta = crearModulo(trabajo.modulo);
      tarjeta.querySelector('.module-card').dataset.modulo = 'canopia';
      modulos.append(tarjeta);

      const copas = trabajo.modulo.indicadores.find((i) => i.clave === 'copas_arboles');
      aviso.textContent = `Listo: ${copas.valor} copas detectadas. El módulo se agregó al informe.`;
    } catch (error) {
      aviso.classList.add('aviso-error');
      aviso.textContent =
        error instanceof ErrorDeApi ? error.message : 'No se pudo analizar la imagen.';
    } finally {
      boton.disabled = this.elementos.entradaImagen.files.length === 0;
    }
  }

  /**
   * Manda el informe a firmar al servidor y muestra el resultado.
   */
  async sellarYRegistrar() {
    if (!estado.informe) return;

    const boton = this.elementos.botonSellar;
    const textoOriginal = boton.textContent;
    boton.disabled = true;
    boton.textContent = 'Firmando…';

    try {
      const { documento, ya_existia: yaExistia } = await sellarInforme(
        estado.informe,
        this.elementos.observacion.value.trim()
      );
      const verificacion = await verificarRegistro(documento.firma.id_informe);
      pintarSello(this.elementos.panelSello, documento, verificacion);

      if (yaExistia) {
        // El id sale del contenido, así que sellar dos veces lo mismo devuelve
        // el registro que ya estaba en lugar de duplicarlo.
        this.elementos.avisoGuardado.textContent =
          'Este informe ya estaba registrado con esta misma observación. Se muestra el registro existente.';
        this.elementos.avisoGuardado.hidden = false;
      }
    } catch (error) {
      this.elementos.avisoGuardado.textContent =
        error instanceof ErrorDeApi ? error.message : 'No se pudo sellar el informe.';
      this.elementos.avisoGuardado.hidden = false;
    } finally {
      boton.textContent = textoOriginal;
      boton.disabled = !navigator.onLine || !estado.informe;
    }
  }

  actualizarConexion() {
    const insignia = this.elementos.conexion;
    const texto = insignia.querySelector('[data-campo="texto"]');
    const enLinea = navigator.onLine;
    insignia.classList.toggle('badge-online', enLinea);
    insignia.classList.toggle('badge-offline', !enLinea);
    texto.textContent = enLinea
      ? 'Con conexión'
      : 'Sin conexión — solo informes guardados en este dispositivo';
    // Al perder señal el botón de sellar se apaga solo: la firma es del servidor.
    this.elementos.botonSellar.disabled = !enLinea || !estado.informe;
  }

  /**
   * Registra el service worker.
   *
   * Nadie lo registraba: `sw.js` estaba escrito desde el principio y jamás se
   * activó, así que el modo offline que el README pide como requisito central
   * nunca existió.
   */
  registrarServiceWorker() {
    if (!('serviceWorker' in navigator)) return;
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('sw.js').catch((error) => {
        // Sin service worker la aplicación anda igual, solo que sin modo
        // offline. No es motivo para molestar a la persona con un error.
        console.warn('No se pudo registrar el service worker:', error);
      });
    });
  }
}

document.addEventListener('DOMContentLoaded', () => {
  new AplicacionPlanetHealth();
});
