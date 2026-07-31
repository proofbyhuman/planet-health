"""Ejecución de modelos de visión como procesos aparte.

FastAPI nunca importa torch ni deepforest. Los invoca con el intérprete del
entorno donde viven y lee su JSON por stdout. Ver el encabezado de
`inferencia/canopia_deepforest.py` para los tres motivos.

**Los trabajos viven en memoria.** Un reinicio del servidor los pierde. Es
suficiente mientras la inferencia sea una consulta puntual de alguien mirando la
pantalla, y evita meter una cola de tareas —Redis, Celery— en un proyecto que
tiene que poder levantarse en la máquina de cualquiera. Si algún día se encolan
lotes de imágenes de cámaras trampa, esto hay que reemplazarlo, y el lugar es
este archivo solo.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

RAIZ_BACKEND = Path(__file__).resolve().parent.parent
GUION_CANOPIA = RAIZ_BACKEND / "inferencia" / "canopia_deepforest.py"

#: Intérprete del entorno donde está instalado DeepForest. Configurable porque en
#: otra máquina el repo no está en el Escritorio de nadie.
PYTHON_DEEPFOREST = Path(
    os.environ.get(
        "PLANET_HEALTH_PYTHON_DEEPFOREST",
        Path.home() / "Desktop" / "DeepForest" / ".venv312" / "Scripts" / "python.exe",
    )
)

#: Techo de espera. La primera corrida baja los pesos del modelo y en CPU tarda
#: bastante; medido en esta máquina, unos 100 s la primera vez y unos 35 s
#: después. Cinco minutos deja margen para una conexión lenta sin dejar un
#: proceso colgado para siempre.
ESPERA_MAXIMA_S = 300

#: Tamaño máximo de imagen aceptado. Una foto de celular ronda los 5 MB.
TAMANO_MAXIMO_BYTES = 25 * 1024 * 1024


class EstadoTrabajo(StrEnum):
    EN_COLA = "en_cola"
    CORRIENDO = "corriendo"
    LISTO = "listo"
    FALLO = "fallo"


@dataclass
class Trabajo:
    """Una inferencia pedida."""

    id: str
    estado: EstadoTrabajo = EstadoTrabajo.EN_COLA
    creado_en: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    resultado: dict[str, Any] | None = None
    error: str | None = None

    def a_diccionario(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "estado": self.estado.value,
            "creado_en": self.creado_en,
            "resultado": self.resultado,
            "error": self.error,
        }


class MotorDeInferencia:
    """Lanza los subprocesos y guarda el estado de cada trabajo."""

    def __init__(self) -> None:
        self._trabajos: dict[str, Trabajo] = {}

    def disponible(self) -> tuple[bool, str]:
        """Si se puede correr inferencia acá, y por qué no si no se puede.

        Returns:
            `(disponible, motivo)`. El motivo va tal cual a la respuesta HTTP y a
            la pantalla, así que dice qué falta y cómo resolverlo.
        """
        if not PYTHON_DEEPFOREST.is_file():
            return False, (
                f"No se encontró el intérprete de Python del entorno de DeepForest en "
                f"{PYTHON_DEEPFOREST}. Se configura con la variable de entorno "
                f"PLANET_HEALTH_PYTHON_DEEPFOREST."
            )
        if not GUION_CANOPIA.is_file():
            return False, f"Falta el script de inferencia en {GUION_CANOPIA}."
        return True, ""

    def obtener(self, id_trabajo: str) -> Trabajo | None:
        return self._trabajos.get(id_trabajo)

    def crear(self) -> Trabajo:
        trabajo = Trabajo(id=f"inf-{uuid.uuid4().hex[:12]}")
        self._trabajos[trabajo.id] = trabajo
        return trabajo

    async def correr_canopia(self, trabajo: Trabajo, ruta_imagen: Path, umbral: float) -> None:
        """Corre DeepForest sobre una imagen y guarda el resultado en el trabajo.

        No propaga excepciones: cualquier problema queda como `estado: fallo` con
        el motivo escrito. Un modelo que no corre es un indicador `no_disponible`,
        no una caída del servidor.
        """
        trabajo.estado = EstadoTrabajo.CORRIENDO
        try:
            proceso = await asyncio.create_subprocess_exec(
                str(PYTHON_DEEPFOREST),
                str(GUION_CANOPIA),
                str(ruta_imagen),
                "--umbral",
                str(umbral),
                stdout=asyncio.subprocess.PIPE,
                # stderr aparte: torch y lightning escriben barras de progreso y
                # avisos ahí. Si se mezclaran con stdout romperían el JSON.
                stderr=asyncio.subprocess.PIPE,
                # PyTorch Lightning crea una carpeta `lightning_logs/` en el
                # directorio de trabajo apenas instancia un Trainer, sin
                # preguntar. Corriendo desde `backend/` la iba llenando de
                # `version_N/` en el árbol del repositorio. Todas las rutas que
                # se le pasan son absolutas, así que el directorio de trabajo no
                # afecta al resultado y se lo manda al temporal del sistema.
                cwd=tempfile.gettempdir(),
            )
            salida, errores = await asyncio.wait_for(
                proceso.communicate(), timeout=ESPERA_MAXIMA_S
            )
        except TimeoutError:
            trabajo.estado = EstadoTrabajo.FALLO
            trabajo.error = (
                f"La inferencia pasó los {ESPERA_MAXIMA_S} segundos y se cortó. La "
                f"primera corrida descarga los pesos del modelo y es la más lenta."
            )
            return
        except Exception as error:  # noqa: BLE001
            trabajo.estado = EstadoTrabajo.FALLO
            trabajo.error = f"No se pudo lanzar el proceso de inferencia: {error}"
            return
        finally:
            ruta_imagen.unlink(missing_ok=True)

        texto = salida.decode("utf-8", errors="replace").strip()
        if not texto:
            detalle = errores.decode("utf-8", errors="replace").strip()[-400:]
            trabajo.estado = EstadoTrabajo.FALLO
            trabajo.error = f"El proceso de inferencia no devolvió nada. Último error: {detalle}"
            _log.error("Inferencia sin salida. stderr: %s", detalle)
            return

        try:
            # El script escribe un único objeto JSON, pero alguna librería podría
            # colar una línea en stdout antes. Se toma la última línea que parsee.
            datos = json.loads(texto.splitlines()[-1])
        except json.JSONDecodeError:
            trabajo.estado = EstadoTrabajo.FALLO
            trabajo.error = "El proceso de inferencia devolvió algo que no es JSON."
            _log.error("Salida no interpretable: %s", texto[:400])
            return

        if "error" in datos:
            trabajo.estado = EstadoTrabajo.FALLO
            trabajo.error = datos["error"]
            return

        trabajo.estado = EstadoTrabajo.LISTO
        trabajo.resultado = datos


motor_de_inferencia = MotorDeInferencia()
