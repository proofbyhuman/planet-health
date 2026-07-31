"""Rutas de inferencia con modelos de visión.

El resultado se devuelve con la forma de un `Modulo` del contrato v3, para que el
frontend lo dibuje con el mismo componente que los demás módulos y con la misma
insignia de procedencia. Un conteo de copas hecho por un modelo es tan `medido`
como una lectura de Open-Meteo: alguien lo midió, y hay que decir quién y con qué.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, UploadFile

from ..utils.inferencia import (
    TAMANO_MAXIMO_BYTES,
    EstadoTrabajo,
    Trabajo,
    motor_de_inferencia,
)
from ..models.procedencia import Fuente, Indicador, Modulo, Procedencia, indicador_no_disponible

router = APIRouter(prefix="/api/v1/inferencia", tags=["inferencia"])

TIPOS_ACEPTADOS = {"image/jpeg", "image/png", "image/tiff"}

LIMITACION_CANOPIA = (
    "DeepForest está entrenado con imágenes aéreas RGB tomadas desde arriba, del "
    "tipo que produce un dron o un vuelo fotogramétrico. Sobre una foto tomada "
    "desde el suelo, una imagen satelital de baja resolución o un paisaje sin "
    "árboles no detecta nada, y eso no significa que no haya vegetación. El "
    "conteo es de copas visibles en esta imagen, no de árboles de la parcela: el "
    "sotobosque y los árboles tapados por otros no aparecen."
)


def _modulo_desde_resultado(resultado: dict) -> Modulo:
    """Traduce la salida de DeepForest a un módulo del contrato."""
    fuente = Fuente(
        nombre=f"DeepForest ({resultado['modelo']})",
        url="https://deepforest.readthedocs.io",
        licencia="MIT",
        atribucion=(
            "Detección de copas con DeepForest (Weecology, University of Florida), "
            "modelo weecology/deepforest-tree."
        ),
    )

    indicadores = [
        Indicador(
            clave="copas_arboles",
            etiqueta="Copas de árboles detectadas",
            valor=resultado["conteo_copas"],
            procedencia=Procedencia.MEDIDO,
            fuente=fuente,
            periodo="la imagen cargada",
            limitaciones=LIMITACION_CANOPIA,
        )
    ]

    if resultado.get("puntaje_medio") is not None:
        indicadores.append(
            Indicador(
                clave="confianza_canopia",
                etiqueta="Confianza media de las detecciones",
                valor=resultado["puntaje_medio"],
                procedencia=Procedencia.ESTIMADO,
                fuente=fuente,
                metodo=(
                    f"Promedio del puntaje que el modelo asignó a cada una de las "
                    f"{resultado['conteo_copas']} detecciones conservadas, con umbral "
                    f"{resultado['umbral_puntaje']}. Se descartaron "
                    f"{resultado.get('detecciones_descartadas_por_umbral', 0)} por debajo "
                    f"del umbral."
                ),
                limitaciones=(
                    "Es la seguridad del modelo sobre lo que ve, no una medida de si "
                    "acertó. Un modelo puede estar muy seguro y equivocarse."
                ),
            )
        )
    else:
        indicadores.append(
            indicador_no_disponible(
                "confianza_canopia",
                "Confianza media de las detecciones",
                "No hubo detecciones sobre las que promediar.",
            )
        )

    return Modulo(
        clave="canopia",
        titulo="Canopia (DeepForest)",
        icono="🌲",
        indicadores=indicadores,
        limitaciones=LIMITACION_CANOPIA,
    )


def _respuesta_de_trabajo(trabajo: Trabajo) -> dict:
    cuerpo = trabajo.a_diccionario()
    if trabajo.estado is EstadoTrabajo.LISTO and trabajo.resultado:
        cuerpo["modulo"] = _modulo_desde_resultado(trabajo.resultado).model_dump(mode="json")
    return cuerpo


@router.post("/canopia", status_code=202, summary="Detectar copas de árboles en una imagen")
async def detectar_canopia(
    tareas: BackgroundTasks,
    imagen: Annotated[UploadFile, File(description="Imagen aérea RGB")],
    umbral: Annotated[float, Query(ge=0.0, le=1.0)] = 0.3,
) -> dict:
    """Encola una inferencia de DeepForest y devuelve el id del trabajo.

    Devuelve 202 y no el resultado porque la inferencia en CPU tarda medio minuto
    largo, y la primera vez —que descarga los pesos del modelo— bastante más.
    Dejar una petición HTTP abierta ese tiempo se corta sola en cualquier proxy.
    Se consulta el estado con `GET /api/v1/inferencia/{id}`.
    """
    disponible, motivo = motor_de_inferencia.disponible()
    if not disponible:
        # 503 y no 500: el servicio anda, esta capacidad concreta no está montada
        # en esta instalación. El motivo explica qué falta.
        raise HTTPException(503, motivo)

    if imagen.content_type not in TIPOS_ACEPTADOS:
        raise HTTPException(
            415,
            f"Tipo de archivo no aceptado: {imagen.content_type}. "
            f"Se admiten {', '.join(sorted(TIPOS_ACEPTADOS))}.",
        )

    destino = Path(tempfile.gettempdir()) / f"ph-{Path(imagen.filename or 'imagen').name}"
    with destino.open("wb") as archivo:
        copiados = 0
        while fragmento := await imagen.read(1024 * 1024):
            copiados += len(fragmento)
            if copiados > TAMANO_MAXIMO_BYTES:
                archivo.close()
                destino.unlink(missing_ok=True)
                raise HTTPException(
                    413,
                    f"La imagen pasa el máximo de {TAMANO_MAXIMO_BYTES // (1024 * 1024)} MB.",
                )
            archivo.write(fragmento)

    trabajo = motor_de_inferencia.crear()
    tareas.add_task(motor_de_inferencia.correr_canopia, trabajo, destino, umbral)
    return _respuesta_de_trabajo(trabajo)


@router.get("/{id_trabajo}", summary="Estado de una inferencia")
async def estado_de_trabajo(id_trabajo: str) -> dict:
    """Devuelve el estado y, si terminó, el módulo listo para dibujar."""
    trabajo = motor_de_inferencia.obtener(id_trabajo)
    if trabajo is None:
        raise HTTPException(
            404,
            f"No hay ningún trabajo con id {id_trabajo}. Los trabajos viven en memoria "
            f"y se pierden si el servidor se reinicia.",
        )
    return _respuesta_de_trabajo(trabajo)


@router.get("", summary="Capacidades de inferencia de esta instalación")
async def capacidades() -> dict:
    """Qué modelos puede correr este servidor y cuáles no.

    Se declara lo que falta en lugar de omitirlo, igual que los indicadores
    `no_disponible`: así queda a la vista qué se puede montar.
    """
    disponible, motivo = motor_de_inferencia.disponible()
    return {
        "canopia_deepforest": {
            "disponible": disponible,
            "motivo": motivo,
            "modelo": "weecology/deepforest-tree",
        },
        "fauna_megadetector": {
            "disponible": False,
            "motivo": (
                "PyTorch-Wildlife no está instalado ni evaluado en este proyecto. La "
                "versión anterior mostraba detecciones de MegaDetector que salían de "
                "Math.random()."
            ),
            "modelo": None,
        },
        "bioacustica": {
            "disponible": False,
            "motivo": "Sin modelo acústico evaluado todavía.",
            "modelo": None,
        },
    }
