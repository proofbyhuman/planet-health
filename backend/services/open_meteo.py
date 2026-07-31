"""Módulo de hidrología: precipitación, temperatura y humedad, desde Open-Meteo.

**Qué cambia respecto de la versión anterior.** `environmental_api.js` pedía el
pronóstico de 7 días, promediaba la precipitación diaria y multiplicaba por 365:

    const precipEstAnnual = Math.round(precipDaily * 365);

Eso no es la precipitación anual, es una semana de pronóstico estirada. Una semana
lluviosa da 2000 mm/año en un desierto y una seca da 50 mm/año en la selva. Para
Bariloche devolvía 720 mm; el acumulado real de los últimos 365 días medido por
ERA5 es 1034 mm.

Acá se consulta el **archivo de reanálisis ERA5**, que son datos observados y
consolidados, y se suma día por día. Es la misma API gratuita, otro endpoint.

**Y qué pasó con "Riesgo de Sequía".** La versión anterior lo derivaba de umbrales
globales fijos: menos de 350 mm era "Alto", más de 750 mm era "Bajo". Un umbral
único para todo el planeta no significa nada — 400 mm es sequía severa en una
pradera templada y es un año húmedo en el Monte. Además "riesgo" es un veredicto,
justo lo que `esquema_informe.py` prohíbe.

Se reemplaza por la **anomalía de precipitación**: cuánto se desvía el último año
respecto del promedio de esta misma coordenada en la última década. Es un número
reproducible, comparable contra sí mismo, y no juzga nada.
"""

from __future__ import annotations

import datetime as dt

from ..models.procedencia import Fuente, Indicador, Modulo, Procedencia, indicador_no_disponible
from .cliente import ClienteFuentes, Respuesta

URL_ARCHIVO = "https://archive-api.open-meteo.com/v1/archive"
URL_ACTUAL = "https://api.open-meteo.com/v1/forecast"

#: ERA5 se consolida con unos días de atraso. Pedir hasta ayer devuelve nulos al
#: final de la serie; seis días es el margen que la propia API recomienda.
ATRASO_ERA5_DIAS = 6

#: Desde cuándo se traen datos para calcular el promedio de referencia. Diez años
#: es un compromiso: suficiente para que un año extremo no domine el promedio, y
#: poco como para que la respuesta pese unos 60 kB y no varios megabytes.
ANIO_INICIO_REFERENCIA = 2015

LICENCIA = "CC-BY-4.0"
ATRIBUCION = "Datos meteorológicos de Open-Meteo.com (reanálisis ERA5, Copernicus / ECMWF)"

LIMITACION_REANALISIS = (
    "ERA5 es un modelo de reanálisis con celdas de unos 25 km: describe el "
    "comportamiento de la zona, no lo que cayó exactamente sobre esta parcela. "
    "En terreno de montaña la diferencia entre una ladera y la otra puede ser "
    "grande y el modelo no la distingue."
)


def _fuente(respuesta: Respuesta, coleccion: str) -> Fuente:
    return Fuente(
        nombre=f"Open-Meteo ({coleccion})",
        url="https://open-meteo.com",
        licencia=LICENCIA,
        atribucion=ATRIBUCION,
        consultada_en=respuesta.consultada_en.isoformat(),
        desde_cache=respuesta.desde_cache,
    )


def _serie_valida(valores: list, tiempos: list) -> tuple[list[float], list[str]]:
    """Descarta los días sin dato, conservando la correspondencia con las fechas."""
    limpios: list[float] = []
    fechas: list[str] = []
    for fecha, valor in zip(tiempos, valores, strict=False):
        if valor is not None:
            limpios.append(float(valor))
            fechas.append(fecha)
    return limpios, fechas


def _promedio_anual_de_referencia(
    precipitaciones: list[float], fechas: list[str], primer_anio_excluido: int
) -> tuple[float | None, list[int]]:
    """Precipitación media de los años calendario completos previos a la ventana medida.

    Args:
        primer_anio_excluido: primer año calendario que toca la ventana de 365
            días. Ese año y los posteriores quedan afuera del promedio.

    **Por qué se excluye el año en que empieza la ventana.** La ventana de 365
    días termina hoy y arranca a mitad del año pasado, así que ese año calendario
    está parcialmente *adentro* de lo que se quiere medir. Si entrara al promedio
    de referencia, el período se estaría comparando en parte contra sí mismo y
    toda anomalía saldría amortiguada hacia cero. Con la serie de prueba de este
    proyecto —últimos 365 días al doble del histórico— el solapamiento daba
    +92 % en lugar del +100 % real. Los dos conjuntos tienen que ser disjuntos.

    Solo entran años con al menos 360 días con dato: un año a medio cargar
    arrastraría el promedio hacia abajo y haría parecer húmedo cualquier año
    normal.

    Returns:
        El promedio y la lista de años que efectivamente se usaron. `(None, [])`
        si no quedó ningún año completo.
    """
    por_anio: dict[int, list[float]] = {}
    for fecha, valor in zip(fechas, precipitaciones, strict=True):
        anio = int(fecha[:4])
        if anio < primer_anio_excluido:
            por_anio.setdefault(anio, []).append(valor)

    completos = {a: v for a, v in por_anio.items() if len(v) >= 360}
    if not completos:
        return None, []
    totales = [sum(v) for v in completos.values()]
    return sum(totales) / len(totales), sorted(completos)


async def obtener_modulo_hidrologia(cliente: ClienteFuentes, lat: float, lon: float) -> Modulo:
    """Arma el módulo de hidrología. Nunca lanza: si falla, devuelve no disponibles."""
    hoy = dt.date.today()
    fin = hoy - dt.timedelta(days=ATRASO_ERA5_DIAS)
    inicio = dt.date(ANIO_INICIO_REFERENCIA, 1, 1)

    archivo = await cliente.obtener_json(
        fuente="open_meteo_archivo",
        url=URL_ARCHIVO,
        params={
            "latitude": lat,
            "longitude": lon,
            "start_date": inicio.isoformat(),
            "end_date": fin.isoformat(),
            "daily": "precipitation_sum,temperature_2m_mean",
            "timezone": "UTC",
        },
        clave_cache=f"archivo|{lat:.4f}|{lon:.4f}|{inicio}|{fin}",
    )

    actual = await cliente.obtener_json(
        fuente="open_meteo_actual",
        url=URL_ACTUAL,
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "relative_humidity_2m,temperature_2m",
            "timezone": "UTC",
        },
        clave_cache=f"actual|{lat:.4f}|{lon:.4f}",
    )

    indicadores: list[Indicador] = []

    if archivo is None:
        motivo = (
            "El archivo climático de Open-Meteo no respondió y no hay copia guardada "
            "de esta coordenada."
        )
        indicadores += [
            indicador_no_disponible("precipitacion_365d", "Precipitación (365 días)", motivo, "mm"),
            indicador_no_disponible(
                "temperatura_media_365d", "Temperatura media (365 días)", motivo, "°C"
            ),
            indicador_no_disponible(
                "anomalia_precipitacion", "Anomalía de precipitación", motivo, "%"
            ),
        ]
    else:
        indicadores += _indicadores_de_archivo(archivo, fin)

    if actual is None:
        indicadores.append(
            indicador_no_disponible(
                "humedad_relativa",
                "Humedad relativa actual",
                "El servicio de condiciones actuales de Open-Meteo no respondió.",
                "%",
            )
        )
    else:
        indicadores += _indicadores_actuales(actual)

    return Modulo(
        clave="hidrologia",
        titulo="Hidrología",
        icono="💧",
        indicadores=indicadores,
        limitaciones=(
            "Ningún dato de este módulo describe agua subterránea, napas ni calidad "
            "del agua. Solo describe lo que ocurre en la atmósfera y lo que llega al "
            "suelo como precipitación."
        ),
    )


def _indicadores_de_archivo(respuesta: Respuesta, fin: dt.date) -> list[Indicador]:
    """Traduce la serie diaria de ERA5 en indicadores."""
    fuente = _fuente(respuesta, "archivo ERA5")
    diario = respuesta.datos.get("daily") or {}
    tiempos = diario.get("time") or []

    precip, fechas_precip = _serie_valida(diario.get("precipitation_sum") or [], tiempos)
    temps, _ = _serie_valida(diario.get("temperature_2m_mean") or [], tiempos)

    if len(precip) < 365:
        motivo = (
            f"ERA5 devolvió {len(precip)} días con dato para esta coordenada y hacen "
            f"falta 365. Suele pasar sobre mar abierto o en latitudes polares."
        )
        return [
            indicador_no_disponible("precipitacion_365d", "Precipitación (365 días)", motivo, "mm"),
            indicador_no_disponible(
                "temperatura_media_365d", "Temperatura media (365 días)", motivo, "°C"
            ),
            indicador_no_disponible(
                "anomalia_precipitacion", "Anomalía de precipitación", motivo, "%"
            ),
        ]

    inicio_ventana = fin - dt.timedelta(days=364)
    periodo = f"{inicio_ventana.isoformat()} a {fin.isoformat()}"

    precip_365 = sum(precip[-365:])
    temp_365 = sum(temps[-365:]) / 365 if len(temps) >= 365 else None

    indicadores = [
        Indicador(
            clave="precipitacion_365d",
            etiqueta="Precipitación (365 días)",
            valor=round(precip_365, 1),
            unidad="mm",
            procedencia=Procedencia.MEDIDO,
            fuente=fuente,
            periodo=periodo,
            limitaciones=LIMITACION_REANALISIS,
        )
    ]

    if temp_365 is None:
        indicadores.append(
            indicador_no_disponible(
                "temperatura_media_365d",
                "Temperatura media (365 días)",
                "La serie de temperatura de ERA5 vino incompleta para esta coordenada.",
                "°C",
            )
        )
    else:
        indicadores.append(
            Indicador(
                clave="temperatura_media_365d",
                etiqueta="Temperatura media (365 días)",
                valor=round(temp_365, 1),
                unidad="°C",
                procedencia=Procedencia.MEDIDO,
                fuente=fuente,
                periodo=periodo,
                limitaciones=LIMITACION_REANALISIS,
            )
        )

    referencia, anios = _promedio_anual_de_referencia(
        precip, fechas_precip, inicio_ventana.year
    )
    if referencia is None or referencia <= 0:
        indicadores.append(
            indicador_no_disponible(
                "anomalia_precipitacion",
                "Anomalía de precipitación",
                "No hay años calendario completos en la serie para calcular un promedio "
                "de referencia en esta coordenada.",
                "%",
            )
        )
    else:
        anomalia = (precip_365 / referencia - 1) * 100
        indicadores.append(
            Indicador(
                clave="anomalia_precipitacion",
                etiqueta="Anomalía de precipitación",
                valor=round(anomalia, 1),
                unidad="%",
                procedencia=Procedencia.ESTIMADO,
                fuente=fuente,
                periodo=periodo,
                metodo=(
                    f"(acumulado de los últimos 365 días ÷ promedio anual de "
                    f"{anios[0]}–{anios[-1]} − 1) × 100. El promedio de referencia es "
                    f"{referencia:.0f} mm/año sobre {len(anios)} años calendario "
                    f"completos de la misma serie ERA5 y la misma coordenada."
                ),
                limitaciones=(
                    "Diez años es una serie corta para hablar de clima: describe cómo "
                    "viene este año contra la última década, no una tendencia climática. "
                    + LIMITACION_REANALISIS
                ),
            )
        )

    return indicadores


def _indicadores_actuales(respuesta: Respuesta) -> list[Indicador]:
    """Condiciones instantáneas del endpoint de pronóstico."""
    fuente = _fuente(respuesta, "condiciones actuales")
    actual = respuesta.datos.get("current") or {}
    hora = actual.get("time", "")

    indicadores: list[Indicador] = []
    humedad = actual.get("relative_humidity_2m")
    if humedad is None:
        indicadores.append(
            indicador_no_disponible(
                "humedad_relativa",
                "Humedad relativa actual",
                "Open-Meteo no devolvió humedad para esta coordenada.",
                "%",
            )
        )
    else:
        indicadores.append(
            Indicador(
                clave="humedad_relativa",
                etiqueta="Humedad relativa actual",
                valor=humedad,
                unidad="%",
                procedencia=Procedencia.MEDIDO,
                fuente=fuente,
                periodo=f"instantáneo, {hora} UTC" if hora else "instantáneo",
                limitaciones=(
                    "Es una lectura de un instante. No describe el régimen de humedad "
                    "de la parcela ni sirve para compararla con otra medida en otro "
                    "momento del día."
                ),
            )
        )
    return indicadores
