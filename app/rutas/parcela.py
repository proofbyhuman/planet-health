"""Ruta de consulta de una parcela."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ..contrato import construir_informe
from ..dependencias import obtener_cliente
from ..fuentes.cliente import ClienteFuentes

router = APIRouter(prefix="/api/v1", tags=["parcela"])


@router.get("/parcela", summary="Condiciones ambientales de una coordenada")
async def consultar_parcela(
    cliente: Annotated[ClienteFuentes, Depends(obtener_cliente)],
    lat: Annotated[float, Query(ge=-90, le=90, description="Latitud en grados decimales (WGS84)")],
    lon: Annotated[
        float, Query(ge=-180, le=180, description="Longitud en grados decimales (WGS84)")
    ],
) -> dict:
    """Devuelve el informe completo de la coordenada.

    Cada indicador viaja con su procedencia (`medido`, `estimado`, `simulado` o
    `no_disponible`), su fuente y sus limitaciones. Los indicadores sin fuente
    disponible salen con valor `null` y el motivo escrito: la respuesta nunca
    trae un valor de relleno.

    Los rangos de latitud y longitud los valida FastAPI, así que una coordenada
    imposible devuelve 422 antes de tocar ninguna fuente externa.
    """
    return await construir_informe(cliente, lat, lon)
