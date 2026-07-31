"""Rutas de registros firmados: crear, listar, descargar y verificar."""

from __future__ import annotations

import hashlib
from typing import Annotated, Any

from contrato_informe.esquema_informe import ahora_utc_iso, json_canonico
from contrato_informe.motor_firma import verificar_documento
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..almacen import AlmacenRegistros, RegistroDuplicado
from ..dependencias import obtener_almacen
from ..firma import gestor_de_firma

router = APIRouter(prefix="/api/v1", tags=["registros"])


class SolicitudDeRegistro(BaseModel):
    """Lo que manda el frontend para sellar un informe."""

    informe: dict[str, Any] = Field(description="Informe completo del contrato v3")
    observacion: str = Field(
        default="",
        max_length=2000,
        description="Nota de quien registra. Opcional.",
    )


def _identificador(informe: dict[str, Any]) -> str:
    """Identificador estable y reproducible del registro.

    Se deriva del contenido en lugar de sortearse. Con un UUID, apretar dos veces
    el botón de sellar generaría dos registros distintos del mismo informe y la
    cola de validación comunitaria se llenaría de duplicados que nadie puede
    distinguir. Derivándolo del contenido, el segundo intento choca contra la
    restricción de clave primaria y el almacén lo rechaza.
    """
    digest = hashlib.sha256(json_canonico(informe)).hexdigest()[:16]
    return f"PH-{digest}"


@router.post("/registros", status_code=201, summary="Sellar y registrar un informe")
async def crear_registro(
    solicitud: SolicitudDeRegistro,
    almacen: Annotated[AlmacenRegistros, Depends(obtener_almacen)],
) -> dict:
    """Firma un informe con Ed25519 y lo guarda.

    Devuelve el documento firmado completo, listo para guardar como .json y
    verificar con `repo_publico/verificar_informe.py` sin modificaciones.
    """
    informe = solicitud.informe
    coordenadas = informe.get("coordenadas") or {}
    lat, lon = coordenadas.get("lat"), coordenadas.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        raise HTTPException(422, "El informe no trae coordenadas válidas.")

    bloque = {
        **informe,
        "registrado_en": ahora_utc_iso(),
        "observacion": solicitud.observacion,
        "estado_validacion": "pendiente_comunidad",
    }
    id_registro = _identificador(bloque)
    documento = gestor_de_firma.firmar(bloque, id_registro)

    try:
        almacen.guardar(id_registro, documento, float(lat), float(lon), solicitud.observacion)
    except RegistroDuplicado:
        # Mismo contenido, mismo id: ya estaba. Se devuelve el que ya existe en
        # lugar de un error, porque desde el punto de vista de quien lo mandó el
        # resultado es el mismo y no se perdió nada.
        existente = almacen.obtener(id_registro)
        return JSONResponse(status_code=200, content={"ya_existia": True, "documento": existente})

    return {"ya_existia": False, "documento": documento}


@router.get("/registros", summary="Listar registros")
async def listar_registros(
    almacen: Annotated[AlmacenRegistros, Depends(obtener_almacen)],
    limite: Annotated[int, Query(ge=1, le=500)] = 50,
    lat_min: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lat_max: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lon_min: Annotated[float | None, Query(ge=-180, le=180)] = None,
    lon_max: Annotated[float | None, Query(ge=-180, le=180)] = None,
) -> dict:
    """Cola de validación comunitaria. Devuelve resúmenes, no los documentos."""
    recuadro = None
    limites = (lat_min, lat_max, lon_min, lon_max)
    if all(v is not None for v in limites):
        recuadro = (lat_min, lat_max, lon_min, lon_max)
    elif any(v is not None for v in limites):
        raise HTTPException(
            422, "Para filtrar por zona hacen falta los cuatro límites del recuadro."
        )

    return {"total": almacen.contar(), "registros": almacen.listar(limite, recuadro)}


@router.get("/registros/{id_registro}", summary="Descargar un registro firmado")
async def obtener_registro(
    id_registro: str,
    almacen: Annotated[AlmacenRegistros, Depends(obtener_almacen)],
) -> dict:
    """Devuelve el documento firmado tal como se firmó, byte por byte."""
    documento = almacen.obtener(id_registro)
    if documento is None:
        raise HTTPException(404, f"No hay ningún registro con id {id_registro}.")
    return documento


@router.get("/registros/{id_registro}/verificacion", summary="Verificar un registro")
async def verificar_registro(
    id_registro: str,
    almacen: Annotated[AlmacenRegistros, Depends(obtener_almacen)],
    huella_esperada: Annotated[
        str | None, Query(description="Huella tomada de una fuente externa")
    ] = None,
) -> dict:
    """Comprueba un registro y devuelve los tres niveles por separado.

    **Advertencia que viaja en la respuesta y no es un detalle.** Que el servidor
    verifique sus propias firmas prueba poco: si el servidor está comprometido,
    miente en la verificación igual que mintió en la firma. Esto sirve para
    mostrar el estado en la interfaz, no como prueba. La verificación que vale es
    la que hace quien recibe el documento, en su máquina, con
    `repo_publico/verificar_informe.py`, que no importa nada de este código.

    `verificar_documento()` de `motor_firma.py` ya separa los tres niveles y
    devuelve `clave_anclada: None` cuando no se le pasó huella externa, en lugar
    de dar por buena la autoría. Eso es lo que se expone tal cual.
    """
    documento = almacen.obtener(id_registro)
    if documento is None:
        raise HTTPException(404, f"No hay ningún registro con id {id_registro}.")

    resultado = verificar_documento(documento, huella_esperada)
    return {
        "id_registro": id_registro,
        "integridad_ok": resultado.integridad_ok,
        "firma_ok": resultado.firma_ok,
        "clave_anclada": resultado.clave_anclada,
        "huella_clave": resultado.huella_clave,
        "documento_intacto": resultado.documento_intacto,
        "resumen": resultado.resumen(),
        "mensajes": resultado.mensajes,
        "advertencia": (
            "Esta verificación la hizo el mismo servidor que firmó. Para una "
            "comprobación independiente, descargá el registro y corré "
            "verificar_informe.py en tu máquina."
        ),
    }


@router.get("/clave-publica", tags=["servicio"], summary="Clave pública del servidor")
async def clave_publica() -> dict:
    """Huella y clave pública con las que este servidor firma.

    Sirve para que alguien pueda anclar la verificación. Ojo: leer la huella de
    acá y compararla contra un documento firmado por este mismo servidor no
    prueba nada — las dos cosas salen de la misma fuente. El ancla vale cuando la
    huella se publica por un canal distinto, que es para lo que existe
    `CLAVES_PUBLICAS.md`.
    """
    return {
        "huella": gestor_de_firma.huella(),
        "clave_publica_hex": gestor_de_firma.clave_publica_hex(),
        "algoritmo": "Ed25519",
        "advertencia": (
            "Comparar esta huella contra un documento que firmó este mismo servidor "
            "no prueba autoría. El ancla tiene que venir de un canal independiente."
        ),
    }
