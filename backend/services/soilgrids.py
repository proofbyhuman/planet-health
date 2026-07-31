"""Módulo de suelo: perfil edáfico real de SoilGrids (ISRIC).

**Qué reemplaza.** El pie de página de la versión anterior citaba SoilGrids entre
las fuentes del proyecto, y SoilGrids no se consultaba en ningún lado. Los valores
de pH, materia orgánica y NDVI salían de esto:

    const seed = Math.abs(Math.sin(lat * 12.9898 + lon * 78.233)) * 43758.5453;
    const normVal = seed - Math.floor(seed);
    const ph = parseFloat((6.2 + normVal * 1.4).toFixed(1));

Es la función de ruido pseudoaleatorio clásica de los shaders. Da un número
estable por coordenada —dos consultas al mismo punto devolvían el mismo pH, lo que
lo hacía parecer un dato— dentro de un rango elegido a mano para que resultara
verosímil. Cerca de la peor forma posible de estar equivocado: consistente.

**Dos cosas de la API que hay que hacer bien.**

1. Los valores vienen como enteros escalados. El pH llega como `64` y hay que
   dividirlo por el `d_factor` que la propia respuesta declara, que es 10, para
   llegar a 6,4. El factor se lee de la respuesta y no se cablea, porque ISRIC lo
   define por capa.
2. **`mean` puede venir en `null`**, y viene seguido. SoilGrids no tiene datos
   sobre cuerpos de agua, glaciares ni superficie urbana densa. La primera
   coordenada de prueba de este proyecto —el centro de Bariloche, sobre el
   Nahuel Huapi— devuelve `null` en las tres propiedades. Ese caso termina en
   `no_disponible` con el motivo escrito, que es exactamente el hueco que la
   versión anterior rellenaba con ruido.
"""

from __future__ import annotations

from ..procedencia import Fuente, Indicador, Modulo, Procedencia, indicador_no_disponible
from .cliente import ClienteFuentes, Respuesta

URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"

#: Capa superficial. Es la que le importa a quien planta, camina o toma una
#: muestra con pala: los primeros cinco centímetros.
PROFUNDIDAD = "0-5cm"

LICENCIA = "CC-BY-4.0"
ATRIBUCION = "Datos de suelo de SoilGrids, ISRIC — World Soil Information"

LIMITACION_MODELO = (
    "SoilGrids es un modelo de predicción global con celdas de 250 m, entrenado "
    "con perfiles de suelo observados. Es una estimación para la celda, no una "
    "muestra tomada en esta parcela: no reemplaza un análisis de laboratorio."
)

#: Qué se pide y cómo se presenta. La unidad de destino y el factor de escala se
#: leen de la respuesta; acá solo va lo que la API no dice.
PROPIEDADES: dict[str, dict[str, str]] = {
    "phh2o": {
        "clave": "ph_agua",
        "etiqueta": "pH en agua (0-5 cm)",
        "unidad": "",
        "limitaciones": LIMITACION_MODELO,
    },
    "soc": {
        "clave": "carbono_organico",
        "etiqueta": "Carbono orgánico (0-5 cm)",
        "unidad": "g/kg",
        "limitaciones": (
            "Es carbono orgánico del suelo, no materia orgánica. La conversión "
            "habitual multiplica por 1,724, pero ese factor varía mucho según el "
            "tipo de suelo, así que acá se informa lo que el modelo entrega. "
            + LIMITACION_MODELO
        ),
    },
    "clay": {
        "clave": "arcilla",
        "etiqueta": "Contenido de arcilla (0-5 cm)",
        "unidad": "%",
        "limitaciones": LIMITACION_MODELO,
    },
    "sand": {
        "clave": "arena",
        "etiqueta": "Contenido de arena (0-5 cm)",
        "unidad": "%",
        "limitaciones": LIMITACION_MODELO,
    },
}

MOTIVO_NDVI = (
    "El NDVI necesita imágenes satelitales Sentinel-2 o Landsat. Las APIs que las "
    "sirven procesadas piden credenciales (Copernicus Data Space, Sentinel Hub o "
    "Google Earth Engine) y el nivel gratuito de Earth Engine es de uso no "
    "comercial, lo que choca con una herramienta comunitaria abierta. Queda "
    "pendiente de resolver la vía de acceso."
)


def _fuente(respuesta: Respuesta) -> Fuente:
    return Fuente(
        nombre="SoilGrids (ISRIC)",
        url="https://soilgrids.org",
        licencia=LICENCIA,
        atribucion=ATRIBUCION,
        consultada_en=respuesta.consultada_en.isoformat(),
        desde_cache=respuesta.desde_cache,
    )


def _valor_de_capa(capa: dict) -> tuple[float | None, str]:
    """Extrae el valor medio de una capa, ya desescalado.

    Returns:
        `(valor, unidad)`. El valor es `None` si SoilGrids no tiene dato en ese
        punto, que es un caso normal y frecuente, no un error.
    """
    unidad_medida = capa.get("unit_measure") or {}
    factor = unidad_medida.get("d_factor") or 1
    unidad = str(unidad_medida.get("target_units") or "").strip()
    # El pH viene con target_units "-" porque es adimensional.
    if unidad == "-":
        unidad = ""

    for profundidad in capa.get("depths") or []:
        if profundidad.get("label") == PROFUNDIDAD:
            crudo = (profundidad.get("values") or {}).get("mean")
            if crudo is None:
                return None, unidad
            return float(crudo) / float(factor), unidad
    return None, unidad


async def obtener_modulo_suelo(cliente: ClienteFuentes, lat: float, lon: float) -> Modulo:
    """Arma el módulo de suelo. Nunca lanza."""
    respuesta = await cliente.obtener_json(
        fuente="soilgrids",
        url=URL,
        params=[
            ("lon", lon),
            ("lat", lat),
            *[("property", p) for p in PROPIEDADES],
            ("depth", PROFUNDIDAD),
            ("value", "mean"),
        ],
        clave_cache=f"perfil|{lat:.4f}|{lon:.4f}|{PROFUNDIDAD}",
    )

    indicadores: list[Indicador] = []

    if respuesta is None:
        motivo = "El servicio SoilGrids de ISRIC no respondió y no hay copia guardada."
        indicadores = [
            indicador_no_disponible(
                conf["clave"], conf["etiqueta"], motivo, conf["unidad"]
            )
            for conf in PROPIEDADES.values()
        ]
    else:
        fuente = _fuente(respuesta)
        capas = {
            capa.get("name"): capa
            for capa in (respuesta.datos.get("properties") or {}).get("layers") or []
        }

        for nombre_api, conf in PROPIEDADES.items():
            capa = capas.get(nombre_api)
            if capa is None:
                indicadores.append(
                    indicador_no_disponible(
                        conf["clave"],
                        conf["etiqueta"],
                        f"SoilGrids no devolvió la capa '{nombre_api}' para esta consulta.",
                        conf["unidad"],
                    )
                )
                continue

            valor, unidad_api = _valor_de_capa(capa)
            if valor is None:
                indicadores.append(
                    indicador_no_disponible(
                        conf["clave"],
                        conf["etiqueta"],
                        "SoilGrids no tiene datos de suelo en este punto. Suele pasar "
                        "sobre cuerpos de agua, glaciares, roca desnuda y superficie "
                        "urbana densa.",
                        conf["unidad"],
                    )
                )
                continue

            indicadores.append(
                Indicador(
                    clave=conf["clave"],
                    etiqueta=conf["etiqueta"],
                    valor=round(valor, 2),
                    unidad=conf["unidad"] or unidad_api,
                    procedencia=Procedencia.MEDIDO,
                    fuente=fuente,
                    periodo=f"profundidad {PROFUNDIDAD}",
                    limitaciones=conf["limitaciones"],
                )
            )

    # El NDVI queda declarado como hueco en lugar de omitirse: que la interfaz lo
    # muestre vacío y explicado es información, y deja visible qué falta hacer.
    indicadores.append(
        indicador_no_disponible("ndvi", "Cobertura vegetal (NDVI)", MOTIVO_NDVI)
    )

    return Modulo(
        clave="suelo",
        titulo="Suelo",
        icono="🌱",
        indicadores=indicadores,
        limitaciones=(
            "Ningún dato de este módulo describe contaminación, metales pesados ni "
            "el subsuelo por debajo de los primeros centímetros. Para eso hace falta "
            "muestreo en terreno y análisis de laboratorio."
        ),
    )
