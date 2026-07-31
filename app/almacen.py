"""Guardado de los registros firmados, en SQLite.

SQLite y no un motor de base de datos aparte porque el proyecto tiene que poder
levantarse en la máquina de cualquiera que quiera colaborar, y en un servidor
chico: un archivo y ninguna dependencia.

**Los registros no se modifican ni se borran.** No hay UPDATE ni DELETE en este
archivo, y es a propósito. Es la misma regla que `guardar_documento_firmado()` en
`motor_firma.py`, que se niega a sobrescribir: *"un informe firmado nunca se
sobrescribe: si hay que corregirlo, se emite uno nuevo con otro id"*. Un registro
firmado que se puede editar no vale nada, porque la firma dejaría de verificar y
lo único que se lograría es tener basura sin poder saber qué decía antes.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ESQUEMA = """
CREATE TABLE IF NOT EXISTS registros (
    id             TEXT PRIMARY KEY,
    creado_en      TEXT NOT NULL,
    lat            REAL NOT NULL,
    lon            REAL NOT NULL,
    huella_clave   TEXT NOT NULL,
    hash_sha256    TEXT NOT NULL,
    -- El documento firmado entero, tal como se entrega. Se guarda serializado y
    -- no desarmado en columnas porque cualquier cambio en la forma rompería la
    -- firma: estos bytes son exactamente lo que se firmó.
    documento      TEXT NOT NULL,
    estado_validacion TEXT NOT NULL DEFAULT 'pendiente_comunidad',
    observacion    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_registros_fecha ON registros(creado_en DESC);
CREATE INDEX IF NOT EXISTS idx_registros_lugar ON registros(lat, lon);
"""


class RegistroDuplicado(Exception):
    """Ya existe un registro con ese id. No se sobrescribe."""


class AlmacenRegistros:
    """Acceso a la tabla de registros firmados."""

    def __init__(self, ruta_bd: Path | str) -> None:
        self.ruta_bd = Path(ruta_bd)
        self.ruta_bd.parent.mkdir(parents=True, exist_ok=True)
        with self._conexion() as conexion:
            conexion.executescript(ESQUEMA)

    @contextmanager
    def _conexion(self):
        conexion = sqlite3.connect(self.ruta_bd)
        conexion.row_factory = sqlite3.Row
        try:
            yield conexion
            conexion.commit()
        finally:
            conexion.close()

    def guardar(
        self,
        id_registro: str,
        documento: dict[str, Any],
        lat: float,
        lon: float,
        observacion: str = "",
    ) -> None:
        """Guarda un documento firmado.

        Raises:
            RegistroDuplicado: si el id ya existe. No se sobrescribe nunca.
        """
        firma = documento["firma"]
        with self._conexion() as conexion:
            try:
                conexion.execute(
                    "INSERT INTO registros "
                    "(id, creado_en, lat, lon, huella_clave, hash_sha256, documento, observacion) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        id_registro,
                        firma["fecha_firma"],
                        lat,
                        lon,
                        firma["huella_clave"],
                        firma["hash_sha256"],
                        json.dumps(documento, ensure_ascii=False),
                        observacion,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise RegistroDuplicado(
                    f"Ya existe un registro con id {id_registro}. Los registros firmados "
                    f"no se sobrescriben: si hay que corregirlo, se emite otro."
                ) from error

    def obtener(self, id_registro: str) -> dict[str, Any] | None:
        """Devuelve el documento firmado, o `None` si no existe."""
        with self._conexion() as conexion:
            fila = conexion.execute(
                "SELECT documento FROM registros WHERE id = ?", (id_registro,)
            ).fetchone()
        return json.loads(fila["documento"]) if fila else None

    def listar(
        self,
        limite: int = 50,
        recuadro: tuple[float, float, float, float] | None = None,
    ) -> list[dict[str, Any]]:
        """Lista registros, del más nuevo al más viejo.

        Devuelve el resumen, no el documento entero: la lista de la cola de
        validación comunitaria puede tener miles de entradas y nadie necesita
        bajarse todas las firmas para elegir una.

        Args:
            limite: cuántos como máximo.
            recuadro: `(lat_min, lat_max, lon_min, lon_max)` para filtrar por zona.
        """
        consulta = (
            "SELECT id, creado_en, lat, lon, huella_clave, hash_sha256, "
            "estado_validacion, observacion FROM registros"
        )
        parametros: list[Any] = []
        if recuadro:
            consulta += " WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?"
            parametros.extend(recuadro)
        consulta += " ORDER BY creado_en DESC LIMIT ?"
        parametros.append(limite)

        with self._conexion() as conexion:
            filas = conexion.execute(consulta, parametros).fetchall()
        return [dict(f) for f in filas]

    def contar(self) -> int:
        with self._conexion() as conexion:
            return conexion.execute("SELECT COUNT(*) AS n FROM registros").fetchone()["n"]
