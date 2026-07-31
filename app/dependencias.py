"""Configuración y dependencias inyectables.

Un solo lugar donde se decide dónde vive la caché y qué cliente HTTP se usa, para
que los tests puedan reemplazar el cliente por uno con transporte simulado sin
tocar las rutas.
"""

from __future__ import annotations

import os
from pathlib import Path

from .almacen import AlmacenRegistros
from .fuentes.cache import CacheEnDisco
from .fuentes.cliente import ClienteFuentes

#: Raíz del backend. La caché queda al lado del código y está en el .gitignore.
RAIZ = Path(__file__).resolve().parent.parent

DIRECTORIO_CACHE = Path(os.environ.get("PLANET_HEALTH_CACHE", RAIZ / "datos_cache"))

#: Base de los registros firmados. También excluida de git: contiene datos de
#: campo de personas, no código.
RUTA_BD = Path(os.environ.get("PLANET_HEALTH_BD", RAIZ / "registros.db"))

#: Carpeta del frontend que sirve el backend en desarrollo.
DIRECTORIO_FRONTEND = RAIZ.parent / "frontend"

_cliente_singleton: ClienteFuentes | None = None
_almacen_singleton: AlmacenRegistros | None = None


def obtener_cliente() -> ClienteFuentes:
    """Dependencia de FastAPI: el cliente de fuentes externas.

    Se sobreescribe en los tests con `app.dependency_overrides`.
    """
    global _cliente_singleton
    if _cliente_singleton is None:
        _cliente_singleton = ClienteFuentes(CacheEnDisco(DIRECTORIO_CACHE))
    return _cliente_singleton


def obtener_almacen() -> AlmacenRegistros:
    """Dependencia de FastAPI: el almacén de registros firmados."""
    global _almacen_singleton
    if _almacen_singleton is None:
        _almacen_singleton = AlmacenRegistros(RUTA_BD)
    return _almacen_singleton
