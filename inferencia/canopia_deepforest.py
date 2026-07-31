#!/usr/bin/env python3
"""Detección de copas de árboles con DeepForest. Corre en OTRO entorno de Python.

**Este archivo no lo importa el backend.** Se ejecuta como programa aparte, con
el intérprete del entorno donde vive DeepForest, y se comunica por stdout con
JSON. Es la única forma en que Planet Health toca torch.

Hay tres motivos y ninguno es de estilo:

1. **El pyproject de `consultora_ambiental` ya tomó esta decisión**, y textual:
   *"NO se declaran acá deepforest, torch ni pytorch-wildlife: según la decisión
   de diseño (b) de la propuesta, esos dos viven en entornos de Python separados
   y los adaptadores los invocan desde afuera, como programas externos. Ese
   supuesto todavía NO fue probado."* Acá queda probado.
2. **El repo DeepForest de esta máquina está instalado en modo editable y se le
   mandan PRs upstream.** Meterle fastapi y uvicorn a su venv para que comparta
   proceso con el backend contaminaría el entorno donde se corren sus tests.
3. **Importar torch cuesta segundos y unos 2 GB.** El backend arranca en
   milisegundos y responde `/api/v1/parcela` en 74 ms justamente porque nunca lo
   carga. Solo lo paga quien pide una inferencia.

Uso:

    python canopia_deepforest.py <ruta_imagen> [--umbral 0.3]

Escribe en stdout un único objeto JSON. Cualquier error también sale como JSON
por stdout, con `"error"`, para que quien invoca no tenga que interpretar un
traceback. Los mensajes de progreso de torch y lightning van a stderr y no
ensucian la salida.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: Modelo publicado por Weecology para detección de copas en imágenes aéreas RGB.
MODELO = "weecology/deepforest-tree"


def detectar_copas(ruta_imagen: Path, umbral: float) -> dict:
    """Corre DeepForest sobre una imagen y devuelve las detecciones.

    Args:
        ruta_imagen: imagen aérea RGB.
        umbral: puntaje mínimo para conservar una detección.

    Returns:
        Diccionario serializable con el conteo, las cajas y la versión del modelo.
    """
    # Los imports van adentro de la función a propósito: si la imagen no existe,
    # se falla en un milisegundo en vez de después de cargar torch.
    from deepforest import main

    modelo = main.deepforest()
    modelo.load_model(MODELO)

    predicciones = modelo.predict_image(path=str(ruta_imagen))

    if predicciones is None or len(predicciones) == 0:
        return {
            "conteo_copas": 0,
            "detecciones": [],
            "umbral_puntaje": umbral,
            "modelo": MODELO,
            "nota": (
                "El modelo no detectó ninguna copa. Está entrenado con imágenes "
                "aéreas RGB de vegetación; sobre una foto tomada desde el suelo, "
                "una captura de pantalla o un terreno sin árboles no encuentra nada."
            ),
        }

    filtradas = predicciones[predicciones["score"] >= umbral]

    detecciones = [
        {
            "caja": [
                round(float(fila["xmin"]), 1),
                round(float(fila["ymin"]), 1),
                round(float(fila["xmax"]), 1),
                round(float(fila["ymax"]), 1),
            ],
            "puntaje": round(float(fila["score"]), 3),
            "clase": str(fila.get("label", "Tree")),
        }
        for _, fila in filtradas.iterrows()
    ]

    puntajes = [d["puntaje"] for d in detecciones]
    return {
        "conteo_copas": len(detecciones),
        "detecciones": detecciones,
        "puntaje_medio": round(sum(puntajes) / len(puntajes), 3) if puntajes else None,
        "umbral_puntaje": umbral,
        "detecciones_descartadas_por_umbral": int(len(predicciones) - len(filtradas)),
        "modelo": MODELO,
    }


def main() -> int:
    analizador = argparse.ArgumentParser(description="Detecta copas de árboles con DeepForest.")
    analizador.add_argument("imagen", type=Path)
    analizador.add_argument(
        "--umbral",
        type=float,
        default=0.3,
        help="Puntaje mínimo de una detección para contarla (0 a 1).",
    )
    argumentos = analizador.parse_args()

    if not argumentos.imagen.is_file():
        print(json.dumps({"error": f"No existe la imagen {argumentos.imagen}"}))
        return 1

    try:
        resultado = detectar_copas(argumentos.imagen, argumentos.umbral)
    except Exception as error:  # noqa: BLE001 - se traduce a JSON para quien invoca
        print(
            json.dumps(
                {
                    "error": f"{type(error).__name__}: {error}",
                    "detalle": (
                        "La descarga de los pesos del modelo necesita conexión la "
                        "primera vez. Después queda en la caché de Hugging Face."
                    ),
                }
            )
        )
        return 1

    # ensure_ascii=False para que los textos en castellano viajen legibles.
    print(json.dumps(resultado, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
