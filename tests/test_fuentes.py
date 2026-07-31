"""Las fuentes externas, con respuestas simuladas. Ninguna prueba toca la red."""

from __future__ import annotations

import datetime as dt
import math

import pytest
from conftest import cargar_respuesta, construir_cliente, respondedor

from app.fuentes.gbif import equitatividad_pielou, indice_shannon, obtener_modulo_biodiversidad
from app.fuentes.open_meteo import obtener_modulo_hidrologia
from app.fuentes.soilgrids import obtener_modulo_suelo
from app.procedencia import Procedencia


def indicador(modulo, clave):
    return next(i for i in modulo.indicadores if i.clave == clave)


# ---------------------------------------------------------------------------
# Shannon: la fórmula que la versión anterior no calculaba
# ---------------------------------------------------------------------------


def test_shannon_de_una_sola_especie_es_cero():
    """Sin variedad no hay información. Es el caso que la fórmula vieja daba 1,55."""
    assert indice_shannon([100]) == 0.0


def test_shannon_maximo_con_abundancias_parejas():
    """Con S especies igualmente abundantes, H' = ln(S) exactamente."""
    assert indice_shannon([10, 10, 10, 10]) == pytest.approx(math.log(4))


def test_shannon_distingue_equidad_y_la_formula_vieja_no():
    """El punto de todo el asunto.

    Dos comunidades con las mismas cuatro especies y los mismos 400 registros:
    una repartida pareja, la otra dominada por una. Shannon las separa. La
    fórmula anterior, `1.5 + nEspecies * 0.05`, daba 1,70 en ambas porque solo
    miraba cuántas especies había.
    """
    pareja = indice_shannon([100, 100, 100, 100])
    dominada = indice_shannon([397, 1, 1, 1])
    assert pareja > dominada
    assert dominada < 0.2

    formula_vieja = lambda n: 1.5 + min(n, 50) * 0.05  # noqa: E731
    assert formula_vieja(4) == formula_vieja(4)  # ciega a la diferencia


def test_shannon_ignora_ceros():
    assert indice_shannon([10, 10, 0, 0]) == pytest.approx(math.log(2))


def test_pielou_no_esta_definida_con_una_especie():
    """Devuelve None en lugar de cero: no es 'nada parejo', es 'no aplica'."""
    assert equitatividad_pielou(0.0, 1) is None


def test_pielou_vale_uno_con_abundancias_parejas():
    h = indice_shannon([5, 5, 5])
    assert equitatividad_pielou(h, 3) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# SoilGrids
# ---------------------------------------------------------------------------


async def test_soilgrids_desescala_con_el_factor_de_la_respuesta(cache_temporal):
    """El pH llega como 64 y hay que dividirlo por el d_factor que declara ISRIC."""
    cliente = construir_cliente(
        cache_temporal, respondedor({"isric.org": cargar_respuesta("soilgrids_tierra")})
    )
    modulo = await obtener_modulo_suelo(cliente, -34.6, -60.0)

    ph = indicador(modulo, "ph_agua")
    assert ph.procedencia is Procedencia.MEDIDO
    assert ph.valor == 6.4  # crudo 64, d_factor 10

    carbono = indicador(modulo, "carbono_organico")
    assert carbono.valor == 27.2
    assert carbono.unidad == "g/kg"


async def test_soilgrids_sin_dato_no_inventa_nada(cache_temporal):
    """Sobre el lago Nahuel Huapi, ISRIC devuelve `mean: null` en las tres capas.

    Es exactamente el hueco que `calculateSoilMetrics()` rellenaba con
    `Math.sin(lat*12.9898 + lon*78.233)`.
    """
    cliente = construir_cliente(
        cache_temporal, respondedor({"isric.org": cargar_respuesta("soilgrids_agua")})
    )
    modulo = await obtener_modulo_suelo(cliente, -41.1335, -71.3103)

    ph = indicador(modulo, "ph_agua")
    assert ph.valor is None
    assert ph.procedencia is Procedencia.NO_DISPONIBLE
    assert "cuerpos de agua" in ph.motivo


async def test_soilgrids_caido_degrada_a_no_disponible(cache_temporal):
    cliente = construir_cliente(cache_temporal, respondedor({"isric.org": 503}))
    modulo = await obtener_modulo_suelo(cliente, -34.6, -60.0)
    assert all(i.procedencia is Procedencia.NO_DISPONIBLE for i in modulo.indicadores)
    assert all(i.valor is None for i in modulo.indicadores)


async def test_ndvi_siempre_declarado_como_hueco(cache_temporal):
    """Aparece aunque no haya dato: deja a la vista qué falta construir."""
    cliente = construir_cliente(
        cache_temporal, respondedor({"isric.org": cargar_respuesta("soilgrids_tierra")})
    )
    modulo = await obtener_modulo_suelo(cliente, -34.6, -60.0)
    ndvi = indicador(modulo, "ndvi")
    assert ndvi.procedencia is Procedencia.NO_DISPONIBLE
    assert "Sentinel" in ndvi.motivo


# ---------------------------------------------------------------------------
# GBIF
# ---------------------------------------------------------------------------


def _gbif_simulado(total: int, especies: list[int], familias: list[int]) -> dict:
    return {
        "count": total,
        "facets": [
            {
                "field": "SPECIES_KEY",
                "counts": [{"name": str(1000 + i), "count": c} for i, c in enumerate(especies)],
            },
            {
                "field": "FAMILY_KEY",
                "counts": [{"name": str(2000 + i), "count": c} for i, c in enumerate(familias)],
            },
        ],
    }


async def test_gbif_calcula_shannon_sobre_abundancias_reales(cache_temporal):
    cuerpo = _gbif_simulado(400, [100, 100, 100, 100], [400])
    cliente = construir_cliente(
        cache_temporal,
        respondedor(
            {
                "occurrence/search": cuerpo,
                "v1/species/": {"family": "Nothofagaceae"},
            }
        ),
    )
    modulo = await obtener_modulo_biodiversidad(cliente, -41.0, -71.0)

    shannon = indicador(modulo, "indice_shannon")
    assert shannon.valor == pytest.approx(round(math.log(4), 2))
    assert shannon.procedencia is Procedencia.ESTIMADO
    assert "−Σ pᵢ·ln(pᵢ)" in shannon.metodo
    assert "observadores" in shannon.limitaciones


async def test_gbif_sin_registros_no_finge_biodiversidad(cache_temporal):
    cliente = construir_cliente(
        cache_temporal, respondedor({"occurrence/search": _gbif_simulado(0, [], [])})
    )
    modulo = await obtener_modulo_biodiversidad(cliente, 0.0, 0.0)

    assert indicador(modulo, "ocurrencias").valor == 0
    for clave in ("riqueza_especies", "indice_shannon", "equitatividad"):
        assert indicador(modulo, clave).procedencia is Procedencia.NO_DISPONIBLE


async def test_gbif_declara_cuando_la_lista_viene_truncada(cache_temporal):
    """Con 1200 especies GBIF corta la faceta y la riqueza pasa a ser un piso."""
    from app.fuentes.gbif import TOPE_FACETAS

    cuerpo = _gbif_simulado(90_000, [50] * TOPE_FACETAS, [90_000])
    cliente = construir_cliente(
        cache_temporal,
        respondedor({"occurrence/search": cuerpo, "v1/species/": {"family": "Asteraceae"}}),
    )
    modulo = await obtener_modulo_biodiversidad(cliente, -41.0, -71.0)

    assert "piso, no un total" in indicador(modulo, "riqueza_especies").limitaciones
    assert "tope" in indicador(modulo, "indice_shannon").metodo


async def test_gbif_con_respuesta_real_del_oceano(cache_temporal):
    """Captura real del Pacífico abierto: 17 ocurrencias, 2 especies."""
    cliente = construir_cliente(
        cache_temporal,
        respondedor(
            {
                "occurrence/search": cargar_respuesta("gbif_oceano"),
                "v1/species/": {"family": "Rhodospirillaceae"},
            }
        ),
    )
    modulo = await obtener_modulo_biodiversidad(cliente, -30.0, -120.0)
    assert indicador(modulo, "ocurrencias").valor == 17
    assert indicador(modulo, "riqueza_especies").valor == 2


async def test_gbif_caido_degrada_entero(cache_temporal):
    cliente = construir_cliente(cache_temporal, respondedor({"gbif.org": 500}))
    modulo = await obtener_modulo_biodiversidad(cliente, -41.0, -71.0)
    assert all(i.procedencia is Procedencia.NO_DISPONIBLE for i in modulo.indicadores)


# ---------------------------------------------------------------------------
# Open-Meteo
# ---------------------------------------------------------------------------


def _archivo_simulado(mm_por_dia_historico: float, mm_por_dia_ultimo_anio: float) -> dict:
    """Serie diaria desde 2015-01-01 hasta hoy menos seis días.

    Los últimos 365 días llevan un valor distinto del resto, para poder verificar
    que la anomalía compara la ventana correcta contra el promedio correcto.
    """
    fin = dt.date.today() - dt.timedelta(days=6)
    inicio = dt.date(2015, 1, 1)
    dias = (fin - inicio).days + 1
    fechas = [(inicio + dt.timedelta(days=i)).isoformat() for i in range(dias)]
    precip = [mm_por_dia_historico] * (dias - 365) + [mm_por_dia_ultimo_anio] * 365
    return {
        "daily": {
            "time": fechas,
            "precipitation_sum": precip,
            "temperature_2m_mean": [10.0] * dias,
        },
        "daily_units": {"precipitation_sum": "mm", "temperature_2m_mean": "°C"},
    }


async def test_precipitacion_es_la_suma_real_no_una_extrapolacion(cache_temporal):
    """2 mm por día durante 365 días son 730 mm, sumados día por día."""
    cliente = construir_cliente(
        cache_temporal,
        respondedor(
            {
                "archive-api": _archivo_simulado(2.0, 2.0),
                "v1/forecast": {
                    "current": {"relative_humidity_2m": 65, "time": "2026-07-29T12:00"}
                },
            }
        ),
    )
    modulo = await obtener_modulo_hidrologia(cliente, -41.0, -71.0)

    precip = indicador(modulo, "precipitacion_365d")
    assert precip.valor == pytest.approx(730.0)
    assert precip.procedencia is Procedencia.MEDIDO


async def test_anomalia_compara_el_ultimo_anio_contra_el_promedio(cache_temporal):
    """Últimos 365 días al doble del histórico: la anomalía tiene que dar +100 %."""
    cliente = construir_cliente(
        cache_temporal,
        respondedor(
            {
                "archive-api": _archivo_simulado(2.0, 4.0),
                "v1/forecast": {"current": {"relative_humidity_2m": 65, "time": ""}},
            }
        ),
    )
    modulo = await obtener_modulo_hidrologia(cliente, -41.0, -71.0)

    anomalia = indicador(modulo, "anomalia_precipitacion")
    assert anomalia.valor == pytest.approx(100.0, abs=1.0)
    assert anomalia.procedencia is Procedencia.ESTIMADO
    assert "promedio anual" in anomalia.metodo


async def test_no_hay_riesgo_de_sequia_como_veredicto(cache_temporal):
    """El módulo describe con números; no clasifica en Alto / Moderado / Bajo."""
    cliente = construir_cliente(
        cache_temporal,
        respondedor(
            {
                "archive-api": _archivo_simulado(0.1, 0.1),
                "v1/forecast": {"current": {"relative_humidity_2m": 20, "time": ""}},
            }
        ),
    )
    modulo = await obtener_modulo_hidrologia(cliente, -25.0, -69.0)
    valores = [str(i.valor) for i in modulo.indicadores]
    assert not any(v in valores for v in ("Alto", "Moderado", "Bajo", "Saludable", "Vulnerable"))


async def test_serie_corta_no_produce_indicadores(cache_temporal):
    """Sobre mar abierto ERA5 puede devolver pocos días. Mejor hueco que promedio falso."""
    corto = {
        "daily": {
            "time": ["2026-01-01", "2026-01-02"],
            "precipitation_sum": [1.0, 2.0],
            "temperature_2m_mean": [10.0, 11.0],
        }
    }
    cliente = construir_cliente(
        cache_temporal,
        respondedor(
            {
                "archive-api": corto,
                "v1/forecast": {"current": {"relative_humidity_2m": 70, "time": ""}},
            }
        ),
    )
    modulo = await obtener_modulo_hidrologia(cliente, -60.0, -140.0)
    assert indicador(modulo, "precipitacion_365d").procedencia is Procedencia.NO_DISPONIBLE
    assert "365" in indicador(modulo, "precipitacion_365d").motivo
