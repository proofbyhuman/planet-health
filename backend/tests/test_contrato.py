"""El informe completo y la ruta HTTP, con todas las fuentes simuladas."""

from __future__ import annotations

import datetime as dt

import pytest
from conftest import (
    cargar_respuesta,
    construir_cliente,
    respondedor,
    sembrar_ubicacion,
)
from fastapi.testclient import TestClient

from app.contrato import VERSION_CONTRATO, construir_informe
from app.dependencias import obtener_cliente
from app.main import app
from app.procedencia import Procedencia

LAT, LON = -34.6, -60.0


def _archivo_completo() -> dict:
    fin = dt.date.today() - dt.timedelta(days=6)
    inicio = dt.date(2015, 1, 1)
    dias = (fin - inicio).days + 1
    return {
        "daily": {
            "time": [(inicio + dt.timedelta(days=i)).isoformat() for i in range(dias)],
            "precipitation_sum": [2.5] * dias,
            "temperature_2m_mean": [16.0] * dias,
        }
    }


def _todas_las_fuentes_sanas() -> dict:
    return {
        "archive-api": _archivo_completo(),
        "v1/forecast": {"current": {"relative_humidity_2m": 68, "time": "2026-07-29T12:00"}},
        "isric.org": cargar_respuesta("soilgrids_tierra"),
        "occurrence/search": {
            "count": 900,
            "facets": [
                {
                    "field": "SPECIES_KEY",
                    "counts": [{"name": str(i), "count": 300} for i in range(3)],
                },
                {"field": "FAMILY_KEY", "counts": [{"name": "9", "count": 900}]},
            ],
        },
        "v1/species/": {"family": "Poaceae"},
    }


@pytest.fixture
def cliente_sano(cache_temporal):
    sembrar_ubicacion(
        cache_temporal, LAT, LON, partido="Chacabuco", provincia="Buenos Aires", pais="Argentina"
    )
    return construir_cliente(cache_temporal, respondedor(_todas_las_fuentes_sanas()))


# ---------------------------------------------------------------------------
# El invariante que protege todo lo demás
# ---------------------------------------------------------------------------


async def test_todo_indicador_declara_procedencia(cliente_sano):
    """Ningún número llega al frontend sin decir de dónde salió.

    Si alguien agrega un indicador nuevo y se olvida de la procedencia, este test
    falla antes de que el dato aparezca en pantalla.
    """
    informe = await construir_informe(cliente_sano, LAT, LON)

    validas = {p.value for p in Procedencia}
    total = 0
    for modulo in informe["modulos"]:
        for indicador in modulo["indicadores"]:
            total += 1
            assert indicador["procedencia"] in validas, indicador["clave"]
    assert total == informe["cobertura"]["total"]
    assert total > 0


async def test_todo_valor_presente_tiene_respaldo(cliente_sano):
    """Un valor no nulo trae fuente si es medido, o método si es estimado."""
    informe = await construir_informe(cliente_sano, LAT, LON)
    for modulo in informe["modulos"]:
        for ind in modulo["indicadores"]:
            if ind["procedencia"] == Procedencia.MEDIDO.value:
                assert ind["fuente"] is not None, ind["clave"]
                assert ind["valor"] is not None, ind["clave"]
            elif ind["procedencia"] == Procedencia.ESTIMADO.value:
                assert ind["metodo"].strip(), ind["clave"]
            elif ind["procedencia"] == Procedencia.NO_DISPONIBLE.value:
                assert ind["valor"] is None, ind["clave"]
                assert ind["motivo"].strip(), ind["clave"]


async def test_no_hay_puntaje_global_ni_veredicto(cliente_sano):
    """El `salud_global: 82` / "Buena Salud Ambiental" no vuelve por la ventana."""
    informe = await construir_informe(cliente_sano, LAT, LON)
    assert "salud_global" not in informe
    assert "estado_general" not in informe
    for modulo in informe["modulos"]:
        assert "score" not in modulo
        assert "puntaje" not in modulo


async def test_el_informe_no_es_ficticio_si_no_hay_simulados(cliente_sano):
    informe = await construir_informe(cliente_sano, LAT, LON)
    assert informe["cobertura"]["simulados"] == 0
    assert informe["es_ficticio"] is False


# ---------------------------------------------------------------------------
# Degradación
# ---------------------------------------------------------------------------


async def test_con_todas_las_fuentes_caidas_el_informe_sale_igual(cache_temporal):
    """Nada de esto puede tumbar la respuesta: sale entera, con los huecos explicados."""
    sembrar_ubicacion(cache_temporal, LAT, LON)
    cliente = construir_cliente(
        cache_temporal,
        respondedor({"archive-api": 503, "v1/forecast": 503, "isric.org": 503, "gbif.org": 503}),
    )
    informe = await construir_informe(cliente, LAT, LON)

    assert informe["cobertura"]["pct_con_dato"] == 0
    assert informe["cobertura"]["medidos"] == 0
    todos = [i for m in informe["modulos"] for i in m["indicadores"]]
    assert all(i["valor"] is None for i in todos)
    assert all(i["motivo"].strip() for i in todos)


async def test_una_fuente_caida_no_arrastra_a_las_otras(cache_temporal):
    """Que ISRIC esté caído no puede dejar sin datos a hidrología ni a biodiversidad."""
    sembrar_ubicacion(cache_temporal, LAT, LON)
    fuentes = {**_todas_las_fuentes_sanas(), "isric.org": 503}
    cliente = construir_cliente(cache_temporal, respondedor(fuentes))
    informe = await construir_informe(cliente, LAT, LON)

    por_clave = {m["clave"]: m for m in informe["modulos"]}
    hidro = [i["procedencia"] for i in por_clave["hidrologia"]["indicadores"]]
    suelo = [i["procedencia"] for i in por_clave["suelo"]["indicadores"]]
    assert Procedencia.MEDIDO.value in hidro
    assert set(suelo) == {Procedencia.NO_DISPONIBLE.value}


async def test_fuera_de_argentina_la_ubicacion_queda_sin_determinar(cache_temporal):
    """La regla dura de `ubicacion.py`, verificada de punta a punta."""
    sembrar_ubicacion(cache_temporal, -30.0, -120.0)
    cliente = construir_cliente(cache_temporal, respondedor(_todas_las_fuentes_sanas()))
    informe = await construir_informe(cliente, -30.0, -120.0)
    assert informe["ubicacion"]["provincia"] == "no determinado"
    assert informe["ubicacion"]["pais"] == "no determinado"


async def test_las_atribuciones_no_se_repiten(cliente_sano):
    """Open-Meteo aporta cuatro indicadores y su leyenda tiene que aparecer una vez."""
    informe = await construir_informe(cliente_sano, LAT, LON)
    assert len(informe["atribuciones"]) == len(set(informe["atribuciones"]))
    assert any("Open-Meteo" in a for a in informe["atribuciones"])


async def test_los_modulos_de_vision_estan_declarados_como_huecos(cliente_sano):
    """Reemplazo de deepforest_adapter.js y wildlife_adapter.js.

    No se omiten: aparecen vacíos y explicados, para que la interfaz muestre qué
    falta en lugar de dar a entender que el análisis está completo.
    """
    informe = await construir_informe(cliente_sano, LAT, LON)
    vision = next(m for m in informe["modulos"] if m["clave"] == "observacion_directa")
    claves = {i["clave"] for i in vision["indicadores"]}
    assert claves == {"copas_arboles", "fauna_detectada", "bioacustica"}
    assert all(i["procedencia"] == Procedencia.NO_DISPONIBLE.value for i in vision["indicadores"])


# ---------------------------------------------------------------------------
# La ruta HTTP
# ---------------------------------------------------------------------------


@pytest.fixture
def api(cache_temporal):
    sembrar_ubicacion(
        cache_temporal, LAT, LON, partido="Chacabuco", provincia="Buenos Aires", pais="Argentina"
    )
    cliente = construir_cliente(cache_temporal, respondedor(_todas_las_fuentes_sanas()))
    app.dependency_overrides[obtener_cliente] = lambda: cliente
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_salud_responde_sin_consultar_fuentes(api):
    respuesta = api.get("/api/v1/salud")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"estado": "ok", "version_contrato": VERSION_CONTRATO}


def test_parcela_responde_el_contrato_completo(api):
    respuesta = api.get("/api/v1/parcela", params={"lat": LAT, "lon": LON})
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["version_contrato"] == VERSION_CONTRATO
    assert cuerpo["coordenadas"] == {"lat": LAT, "lon": LON}
    assert {m["clave"] for m in cuerpo["modulos"]} == {
        "hidrologia",
        "suelo",
        "biodiversidad",
        "observacion_directa",
    }


@pytest.mark.parametrize(
    "lat,lon", [(999, 0), (0, 999), (-91, 0), (0, 181), ("abc", 0)]
)
def test_coordenadas_imposibles_se_rechazan_antes_de_consultar_nada(api, lat, lon):
    """FastAPI valida los rangos: 422 sin tocar ninguna fuente externa."""
    assert api.get("/api/v1/parcela", params={"lat": lat, "lon": lon}).status_code == 422


def test_faltan_parametros(api):
    assert api.get("/api/v1/parcela").status_code == 422
