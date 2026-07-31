"""La caché y la regla de degradación del cliente."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from conftest import construir_cliente, marcar_como_vieja, respondedor

from app.fuentes.cache import TTL_POR_FUENTE
from app.fuentes.cliente import ClienteFuentes

URL = "https://ejemplo.test/datos"


def test_leer_lo_que_no_existe_devuelve_none(cache_temporal):
    assert cache_temporal.leer("gbif", "inexistente") is None


def test_escribir_y_leer(cache_temporal):
    cache_temporal.escribir("gbif", "k", {"count": 42})
    entrada = cache_temporal.leer("gbif", "k")
    assert entrada is not None
    assert entrada.datos == {"count": 42}
    assert entrada.edad < timedelta(seconds=30)


def test_una_entrada_vencida_no_se_devuelve(cache_temporal):
    cache_temporal.escribir("gbif", "k", {"count": 42})
    marcar_como_vieja(cache_temporal, "gbif", "k", dias=2)  # TTL de gbif: 24 h
    assert cache_temporal.leer("gbif", "k") is None
    assert cache_temporal.leer_vencida("gbif", "k") is not None


def test_el_ttl_de_soilgrids_aguanta_un_mes(cache_temporal):
    """El perfil de suelo no cambia entre dos clics, ni entre dos semanas."""
    cache_temporal.escribir("soilgrids", "k", {"ph": 6.4})
    marcar_como_vieja(cache_temporal, "soilgrids", "k", dias=20)
    assert cache_temporal.leer("soilgrids", "k") is not None
    marcar_como_vieja(cache_temporal, "soilgrids", "k", dias=40)
    assert cache_temporal.leer("soilgrids", "k") is None


def test_un_archivo_corrupto_se_trata_como_ausencia(cache_temporal):
    """Una caché rota no puede romper una consulta que la red sí puede resolver."""
    cache_temporal.escribir("gbif", "k", {"count": 1})
    ruta = cache_temporal._ruta("gbif", "k")  # noqa: SLF001
    ruta.write_text("{ esto no es json", encoding="utf-8")
    assert cache_temporal.leer("gbif", "k") is None


def test_claves_distintas_no_colisionan(cache_temporal):
    cache_temporal.escribir("gbif", "lat|-41.1|lon|-71.3", {"x": 1})
    cache_temporal.escribir("gbif", "lat|-41.2|lon|-71.3", {"x": 2})
    assert cache_temporal.leer("gbif", "lat|-41.1|lon|-71.3").datos == {"x": 1}
    assert cache_temporal.leer("gbif", "lat|-41.2|lon|-71.3").datos == {"x": 2}


def test_toda_fuente_usada_tiene_un_ttl_declarado():
    """Que nadie caiga en el TTL por defecto sin haberlo decidido."""
    usadas = {
        "open_meteo_actual",
        "open_meteo_archivo",
        "gbif",
        "gbif_taxon",
        "soilgrids",
        "georef_ar",
    }
    assert usadas <= set(TTL_POR_FUENTE)


# ---------------------------------------------------------------------------
# La regla de degradación del cliente
# ---------------------------------------------------------------------------


async def test_la_segunda_consulta_sale_de_la_cache(cache_temporal):
    llamadas = {"n": 0}

    def manejador(request: httpx.Request) -> httpx.Response:
        llamadas["n"] += 1
        return httpx.Response(200, json={"v": llamadas["n"]})

    cliente = ClienteFuentes(
        cache_temporal, httpx.AsyncClient(transport=httpx.MockTransport(manejador))
    )

    primera = await cliente.obtener_json("gbif", URL, None, "k")
    segunda = await cliente.obtener_json("gbif", URL, None, "k")

    assert llamadas["n"] == 1
    assert primera.desde_cache is False
    assert segunda.desde_cache is True
    assert segunda.datos == {"v": 1}


async def test_si_la_fuente_cae_se_sirve_cache_vencida_marcada(cache_temporal):
    """Un dato viejo con su fecha es más útil que un hueco, si viaja rotulado."""
    sano = construir_cliente(cache_temporal, respondedor({"ejemplo.test": {"v": "original"}}))
    await sano.obtener_json("gbif", URL, None, "k")
    marcar_como_vieja(cache_temporal, "gbif", "k", dias=5)

    caido = construir_cliente(cache_temporal, respondedor({"ejemplo.test": 503}))
    respuesta = await caido.obtener_json("gbif", URL, None, "k")

    assert respuesta is not None
    assert respuesta.datos == {"v": "original"}
    assert respuesta.vencida is True
    assert respuesta.desde_cache is True


async def test_sin_cache_y_sin_red_devuelve_none(cache_temporal):
    """El `None` que termina siendo un indicador `no_disponible` con motivo."""
    cliente = construir_cliente(cache_temporal, respondedor({"ejemplo.test": 503}))
    assert await cliente.obtener_json("gbif", URL, None, "k") is None


async def test_un_error_de_red_degrada_igual_que_un_500(cache_temporal):
    def explota(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin ruta al host")

    cliente = ClienteFuentes(
        cache_temporal, httpx.AsyncClient(transport=httpx.MockTransport(explota))
    )
    assert await cliente.obtener_json("gbif", URL, None, "k") is None


@pytest.mark.parametrize("codigo", [400, 401, 429, 500, 503])
async def test_ningun_codigo_de_error_se_guarda_como_dato(cache_temporal, codigo):
    """Cachear un 429 dejaría la coordenada rota durante todo el TTL."""
    cliente = construir_cliente(cache_temporal, respondedor({"ejemplo.test": codigo}))
    await cliente.obtener_json("gbif", URL, None, "k")
    assert cache_temporal.leer("gbif", "k") is None
    assert cache_temporal.leer_vencida("gbif", "k") is None
