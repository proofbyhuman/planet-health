"""Caché en disco de las respuestas de las fuentes externas.

Tres motivos, en orden de importancia:

1. **Cortesía con servicios públicos gratuitos.** Open-Meteo, GBIF y SoilGrids
   sostienen esto sin cobrar. Cada clic en el mapa disparando tres consultas
   nuevas es abuso, y termina en un bloqueo por IP que deja sin herramienta a
   toda la comunidad.
2. **Modo offline honesto.** El README pide que la app sirva en el campo sin
   conexión. Un dato guardado con su fecha se puede mostrar como "consultado el
   30/07"; sin la fecha habría que elegir entre no mostrar nada o mentir.
3. **Velocidad.** El perfil de suelo de una coordenada no cambia entre dos clics.

El TTL lo fija cada fuente según cuánto cambia el dato, no según cuánto queremos
que dure la caché. Ver `TTL_POR_FUENTE`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

#: Cuánto vale una respuesta antes de volver a preguntar. Elegido por la física
#: del dato: el clima de hoy cambia por hora, el suelo tarda siglos.
TTL_POR_FUENTE: dict[str, timedelta] = {
    "open_meteo_actual": timedelta(hours=1),      # condiciones instantáneas
    "open_meteo_archivo": timedelta(days=7),      # reanálisis ERA5 ya consolidado
    "gbif": timedelta(hours=24),                  # se suman registros a diario
    "soilgrids": timedelta(days=30),              # el perfil de suelo no se mueve
    "gbif_taxon": timedelta(days=90),             # nombres científicos
    "georef_ar": timedelta(days=90),              # límites administrativos
}

#: Si una fuente no está en el diccionario de arriba se usa esto, corto a
#: propósito: es preferible una consulta de más que servir un dato viejo sin
#: haberlo decidido.
TTL_POR_DEFECTO = timedelta(hours=1)


@dataclass(frozen=True)
class EntradaCache:
    """Una respuesta guardada, con la fecha en que se consultó de verdad."""

    datos: Any
    guardado_en: datetime

    @property
    def edad(self) -> timedelta:
        return datetime.now(UTC) - self.guardado_en

    def esta_vencida(self, ttl: timedelta) -> bool:
        return self.edad > ttl


class CacheEnDisco:
    """Caché de archivos JSON, una carpeta por fuente.

    No usa memoria de proceso a propósito: el backend puede reiniciarse en el
    medio de una jornada de campo sin perder lo que ya consultó.
    """

    def __init__(self, directorio: Path | str) -> None:
        self.directorio = Path(directorio)

    def _ruta(self, fuente: str, clave: str) -> Path:
        # La clave puede ser larga y traer caracteres que no van en un nombre de
        # archivo (coordenadas negativas, comas, URLs). El hash la vuelve segura
        # y de largo fijo.
        digest = hashlib.sha256(clave.encode("utf-8")).hexdigest()[:32]
        return self.directorio / fuente / f"{digest}.json"

    def leer(self, fuente: str, clave: str) -> EntradaCache | None:
        """Devuelve la entrada si existe y no venció. `None` en cualquier otro caso.

        Un archivo corrupto o ilegible se trata como ausencia: se vuelve a
        consultar. Nunca se propaga la excepción, porque una caché rota no puede
        romper una consulta que en realidad se puede resolver por red.
        """
        ruta = self._ruta(fuente, clave)
        if not ruta.exists():
            return None
        try:
            crudo = json.loads(ruta.read_text(encoding="utf-8"))
            entrada = EntradaCache(
                datos=crudo["datos"],
                guardado_en=datetime.fromisoformat(crudo["guardado_en"]),
            )
        except Exception as error:  # noqa: BLE001 - una caché rota no rompe nada
            _log.warning("Entrada de caché ilegible en %s (%s). Se ignora.", ruta, error)
            return None

        ttl = TTL_POR_FUENTE.get(fuente, TTL_POR_DEFECTO)
        if entrada.esta_vencida(ttl):
            return None
        return entrada

    def leer_vencida(self, fuente: str, clave: str) -> EntradaCache | None:
        """Devuelve la entrada aunque haya vencido, o `None` si no existe.

        Es el último recurso cuando la fuente externa no responde: un dato viejo
        rotulado con su fecha es más útil que un hueco, siempre que la fecha
        viaje con él hasta la pantalla. Quien llama es responsable de marcarlo.
        """
        ruta = self._ruta(fuente, clave)
        if not ruta.exists():
            return None
        try:
            crudo = json.loads(ruta.read_text(encoding="utf-8"))
            return EntradaCache(
                datos=crudo["datos"],
                guardado_en=datetime.fromisoformat(crudo["guardado_en"]),
            )
        except Exception:  # noqa: BLE001
            return None

    def escribir(self, fuente: str, clave: str, datos: Any) -> None:
        """Guarda una respuesta con la marca de tiempo de ahora.

        Si el disco falla (permisos, disco lleno) se registra y se sigue: no
        poder cachear es un problema de rendimiento, no de corrección.
        """
        ruta = self._ruta(fuente, clave)
        try:
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(
                json.dumps(
                    {
                        "guardado_en": datetime.now(UTC).isoformat(),
                        "clave_original": clave,
                        "datos": datos,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError as error:
            _log.warning("No se pudo escribir la caché en %s: %s", ruta, error)
