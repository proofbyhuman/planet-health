/**
 * El mapa de Leaflet, separado del resto de la aplicación.
 *
 * Todo esto vivía dentro de `PlanetHealthApp.initMap()`, mezclado con el manejo
 * del formulario, el renderizado y las llamadas a las APIs. Acá el mapa solo
 * sabe mostrar una posición y avisar cuando la persona elige otra: no conoce el
 * backend ni el contrato de datos.
 */

const CAPA_TESELAS = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
const ATRIBUCION_MAPA = '&copy; OpenStreetMap contributors &copy; CARTO';

export class MapaParcelas {
  /**
   * @param {string} idContenedor - id del div que aloja el mapa.
   * @param {(lat: number, lon: number) => void} alElegirCoordenada
   */
  constructor(idContenedor, alElegirCoordenada) {
    this.alElegirCoordenada = alElegirCoordenada;
    this.mapa = null;
    this.marcador = null;
    this.contenedor = document.getElementById(idContenedor);
  }

  /**
   * Inicializa el mapa.
   *
   * @param {number} lat
   * @param {number} lon
   * @returns {boolean} `false` si Leaflet no cargó, para que la aplicación siga
   *   funcionando con el formulario de coordenadas. Sin conexión, el CDN de
   *   Leaflet no responde y el mapa no aparece: eso no puede dejar inutilizable
   *   una herramienta pensada para usarse en el campo.
   */
  iniciar(lat, lon) {
    if (!window.L || !this.contenedor) return false;

    this.mapa = L.map(this.contenedor, { zoomControl: false }).setView([lat, lon], 9);
    L.control.zoom({ position: 'topright' }).addTo(this.mapa);
    L.tileLayer(CAPA_TESELAS, {
      attribution: ATRIBUCION_MAPA,
      subdomains: 'abcd',
      maxZoom: 19,
    }).addTo(this.mapa);

    const icono = L.divIcon({ className: 'chincheta-parcela', iconSize: [24, 24], iconAnchor: [12, 12] });
    this.marcador = L.marker([lat, lon], { icon: icono, draggable: true }).addTo(this.mapa);

    this.mapa.on('click', (evento) => {
      const { lat: nuevaLat, lng } = evento.latlng;
      this.moverA(nuevaLat, lng);
      this.alElegirCoordenada(nuevaLat, lng);
    });

    this.marcador.on('dragend', () => {
      const posicion = this.marcador.getLatLng();
      this.alElegirCoordenada(posicion.lat, posicion.lng);
    });

    return true;
  }

  /**
   * Mueve la chincheta sin disparar una consulta.
   *
   * @param {number} lat
   * @param {number} lon
   */
  moverA(lat, lon) {
    if (!this.mapa) return;
    this.marcador.setLatLng([lat, lon]);
    this.mapa.panTo([lat, lon]);
  }
}
