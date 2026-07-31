"""Andamiaje común de los tests.

Ninguna prueba toca la red. Las fuentes externas se sirven con
`httpx.MockTransport` y la caché va a un directorio temporal, así que la suite
corre igual sin internet y no ensucia `datos_cache/`.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

# El backend se importa como `app.*` desde la raíz de backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.cache import CacheEnDisco  # noqa: E402
from backend.services.cliente import ClienteFuentes  # noqa: E402

DIRECTORIO_RESPUESTAS = Path(__file__).parent / "respuestas"


def cargar_respuesta(nombre: str) -> dict:
    """Lee una respuesta de ejemplo capturada de la API real."""
    return json.loads((DIRECTORIO_RESPUESTAS / f"{nombre}.json").read_text(encoding="utf-8"))


@pytest.fixture
def cache_temporal(tmp_path: Path) -> CacheEnDisco:
    return CacheEnDisco(tmp_path / "cache")


def construir_cliente(cache: CacheEnDisco, manejador) -> ClienteFuentes:
    """Cliente de fuentes con transporte simulado.

    Args:
        manejador: función que recibe una `httpx.Request` y devuelve una
            `httpx.Response`. Es donde cada test decide qué contesta cada API.
    """
    transporte = httpx.MockTransport(manejador)
    return ClienteFuentes(cache, httpx.AsyncClient(transport=transporte))


def respondedor(rutas: dict[str, object], por_defecto: int = 404):
    """Arma un manejador que despacha por fragmento de URL.

    Args:
        rutas: fragmento de URL → cuerpo JSON, o un entero para responder ese
            código de estado. Se usa el primer fragmento que aparezca en la URL.
        por_defecto: qué responder si ningún fragmento coincide.
    """

    def manejador(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for fragmento, cuerpo in rutas.items():
            if fragmento in url:
                if isinstance(cuerpo, int):
                    return httpx.Response(cuerpo, json={"error": "simulado"})
                return httpx.Response(200, json=cuerpo)
        return httpx.Response(por_defecto, json={"error": f"sin ruta simulada para {url}"})

    return manejador


def sembrar_ubicacion(cache: CacheEnDisco, lat: float, lon: float, **campos) -> None:
    """Deja resuelta la ubicación administrativa en la caché.

    Evita que los tests del contrato salgan a consultar el servicio del IGN, que
    es sincrónico, usa `requests` y no pasa por el transporte simulado.
    """
    base = {
        "lat": lat,
        "lon": lon,
        "partido": "no determinado",
        "provincia": "no determinado",
        "pais": "no determinado",
        "fuente_ubicacion": "sembrada en el test",
        "radio_analisis_m": 11132,
        "etiqueta_parcela": "",
    }
    cache.escribir("georef_ar", f"ubicacion|{lat:.4f}|{lon:.4f}", {**base, **campos})


def marcar_como_vieja(cache: CacheEnDisco, fuente: str, clave: str, dias: int) -> None:
    """Retrasa la fecha de una entrada de caché para forzar su vencimiento."""
    ruta = cache._ruta(fuente, clave)  # noqa: SLF001 - inspección deliberada en test
    crudo = json.loads(ruta.read_text(encoding="utf-8"))
    viejo = datetime.now(UTC).timestamp() - dias * 86400
    crudo["guardado_en"] = datetime.fromtimestamp(viejo, tz=UTC).isoformat()
    ruta.write_text(json.dumps(crudo, ensure_ascii=False), encoding="utf-8")
