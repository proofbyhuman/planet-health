"""Cliente HTTP compartido por todas las fuentes externas.

Concentra en un solo lugar tres cosas que si no se repiten mal en cada módulo:
la caché, el tiempo de espera y qué pasa cuando el servicio no responde.

**La regla de degradación**, que es lo importante de este archivo:

1. Hay caché fresca → se devuelve, sin tocar la red.
2. No hay → se consulta. Si responde, se guarda y se devuelve.
3. La consulta falla → se busca caché vencida. Si hay, se devuelve **marcada**
   como vieja, con su fecha.
4. No hay nada → se devuelve `None`.

El paso 4 es el que importa. `None` sube hasta la ruta y termina en indicadores
`no_disponible` con el motivo escrito. En ningún punto de esta cadena hay un
valor por defecto: si el dato no está, la respuesta dice que no está.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from .cache import CacheEnDisco

_log = logging.getLogger(__name__)

#: Corto a propósito. Quedarse sin un módulo del informe es un resultado
#: aceptable; dejar a alguien esperando en el campo con mala señal, no.
ESPERA_S = 20.0

#: Identificarse es parte de usar bien una API pública gratuita: si el proyecto
#: hace algo mal, los operadores tienen a quién escribirle antes de bloquear.
AGENTE_USUARIO = (
    "PlanetHealth/0.1 (herramienta comunitaria de salud ecologica; "
    "https://github.com/ramiroguevara/planet-health)"
)


@dataclass(frozen=True)
class Respuesta:
    """Lo que devolvió una fuente, más de dónde salió."""

    datos: Any
    #: Verdadero si viene de la caché en lugar de una consulta nueva.
    desde_cache: bool
    #: Cuándo se consultó realmente a la fuente. Es lo que se muestra en la
    #: interfaz cuando el dato es viejo.
    consultada_en: datetime
    #: Verdadero si la caché estaba vencida y se sirvió igual porque la fuente no
    #: respondió. La interfaz lo destaca.
    vencida: bool = False


class ClienteFuentes:
    """Hace consultas HTTP con caché y degradación explícita."""

    def __init__(self, cache: CacheEnDisco, cliente_http: httpx.AsyncClient | None = None) -> None:
        self._cache = cache
        # Se puede inyectar un cliente con transporte simulado: es lo que usan
        # los tests para no depender de la red.
        self._http = cliente_http

    @property
    def cache(self) -> CacheEnDisco:
        """La caché, para quien necesite guardar algo que no sea una consulta HTTP propia.

        Lo usa la resolución de ubicación, que corre dentro de `contrato_informe` con
        `requests` y no pasa por `obtener_json()`.
        """
        return self._cache

    async def _hacer_consulta(self, url: str, params: Any) -> Any:
        if self._http is not None:
            respuesta = await self._http.get(url, params=params)
            respuesta.raise_for_status()
            return respuesta.json()

        async with httpx.AsyncClient(
            timeout=ESPERA_S, headers={"User-Agent": AGENTE_USUARIO, "Accept": "application/json"}
        ) as cliente:
            respuesta = await cliente.get(url, params=params)
            respuesta.raise_for_status()
            return respuesta.json()

    async def obtener_json(
        self,
        fuente: str,
        url: str,
        params: Any,
        clave_cache: str,
    ) -> Respuesta | None:
        """Consulta una fuente aplicando la regla de degradación del encabezado.

        Args:
            fuente: nombre corto para agrupar la caché y elegir el TTL.
            url: endpoint.
            params: parámetros de la consulta (dict o lista de tuplas, porque
                SoilGrids y GBIF repiten claves).
            clave_cache: identifica unívocamente esta consulta. Tiene que incluir
                todo lo que la distingue de otra: coordenada, fechas, propiedades.

        Returns:
            La `Respuesta`, o `None` si no hay forma de conseguir el dato.
        """
        entrada = self._cache.leer(fuente, clave_cache)
        if entrada is not None:
            return Respuesta(entrada.datos, desde_cache=True, consultada_en=entrada.guardado_en)

        try:
            datos = await self._hacer_consulta(url, params)
        except Exception as error:  # noqa: BLE001 - cualquier falla degrada igual
            _log.warning("Falló la consulta a %s (%s): %s", fuente, type(error).__name__, error)
            vieja = self._cache.leer_vencida(fuente, clave_cache)
            if vieja is not None:
                _log.info(
                    "Se sirve caché vencida de %s, guardada el %s.", fuente, vieja.guardado_en
                )
                return Respuesta(
                    vieja.datos,
                    desde_cache=True,
                    consultada_en=vieja.guardado_en,
                    vencida=True,
                )
            return None

        self._cache.escribir(fuente, clave_cache, datos)
        return Respuesta(datos, desde_cache=False, consultada_en=datetime.now().astimezone())
