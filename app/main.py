"""Punto de entrada del backend de Planet Health.

Se levanta con:

    backend/.venv/Scripts/python.exe -m uvicorn app.main:app --reload

Este módulo **no importa torch ni deepforest**, a propósito. Los modelos de visión
corren como proceso aparte (ver `rutas/inferencia.py` cuando exista), siguiendo la
decisión que ya había tomado `consultora_ambiental` en su pyproject: *"esos dos
viven en entornos de Python separados y los adaptadores los invocan desde afuera,
como programas externos"*. Gracias a eso el servidor arranca en milisegundos en
vez de esperar los segundos que tarda torch en cargarse.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .contrato import VERSION_CONTRATO
from .dependencias import DIRECTORIO_FRONTEND
from .rutas import inferencia, parcela, registros

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="Planet Health API",
    version="0.1.0",
    summary="Condiciones ambientales de una coordenada, con la procedencia de cada dato.",
    description=(
        "Toda respuesta declara de dónde salió cada número. Un indicador sin fuente "
        "sale con valor `null` y el motivo escrito; nunca con un valor de relleno."
    ),
)

# En desarrollo el frontend se abre con Live Server o similar en otro puerto. En
# producción lo sirve este mismo proceso, así que es del mismo origen y el CORS
# no interviene. La variable existe para el caso en que alguien aloje el frontend
# aparte; se listan orígenes concretos y nunca "*", porque la API acepta POST que
# crean registros firmados.
ORIGENES = [
    origen.strip()
    for origen in os.environ.get(
        "PLANET_HEALTH_ORIGENES",
        "http://localhost:5500,http://127.0.0.1:5500,http://localhost:8000",
    ).split(",")
    if origen.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGENES,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(parcela.router)
app.include_router(registros.router)
app.include_router(inferencia.router)


@app.get("/api/v1/salud", tags=["servicio"], summary="Estado del servicio")
async def salud() -> dict:
    """Comprueba que el proceso está vivo.

    No consulta las fuentes externas: responder que el servicio anda no es lo
    mismo que responder que Open-Meteo anda, y mezclarlo haría que un problema
    ajeno pareciera una caída propia.
    """
    return {"estado": "ok", "version_contrato": VERSION_CONTRATO}


# El frontend va montado último, en la raíz, para no tapar las rutas de /api.
if DIRECTORIO_FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=DIRECTORIO_FRONTEND, html=True), name="frontend")
