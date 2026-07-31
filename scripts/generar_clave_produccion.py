#!/usr/bin/env python3
"""Genera el par de claves Ed25519 con el que el servidor firma los registros.

    python scripts/generar_clave_produccion.py

Pide la contraseña por teclado y no la muestra. Se hace así, y no pasándola como
argumento, porque un argumento queda en el historial de la terminal y en la lista
de procesos de la máquina.

Al terminar imprime lo que hay que copiar al panel del servicio de hosting y lo
que hay que publicar. **La clave privada no se publica nunca y no entra al
repositorio**: el `.gitignore` excluye `claves/`, `*.pem` y `*.key`. Una clave
privada que llegó al historial de git no se puede borrar, solo se puede rotar.
"""

from __future__ import annotations

import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contrato_informe.motor_firma import (  # noqa: E402
    ErrorDeFirma,
    cargar_clave_privada,
    generar_par_de_claves,  # noqa: E402
    huella_de_clave,
)

DESTINO = Path(__file__).resolve().parent.parent / "claves"
RUTA_PRIVADA = DESTINO / "produccion_privada.pem"
RUTA_PUBLICA = DESTINO / "produccion_publica.pem"


def main() -> int:
    if RUTA_PRIVADA.exists():
        print(f"Ya existe {RUTA_PRIVADA}.")
        print("No se sobrescribe una clave: si hay que rotarla, movela a mano primero.")
        return 1

    contrasena = getpass("Contraseña para la clave privada: ")
    if len(contrasena) < 12:
        print("Muy corta. Usá al menos 12 caracteres: esta clave firma documentos.")
        return 1
    if contrasena != getpass("Repetila: "):
        print("No coinciden.")
        return 1

    try:
        generar_par_de_claves(RUTA_PRIVADA, RUTA_PUBLICA, passphrase=contrasena.encode("utf-8"))
    except ErrorDeFirma as error:
        print(f"No se pudo generar el par: {error}")
        return 1

    huella = huella_de_clave(cargar_clave_privada(RUTA_PRIVADA, contrasena.encode()).public_key())

    print()
    print(f"Par generado en {DESTINO}")
    print()
    print("1. En el panel del servicio de hosting, cargá dos variables de entorno:")
    print()
    print("   PLANET_HEALTH_CLAVE_PRIVADA_PEM  = el contenido completo de")
    print(f"                                      {RUTA_PRIVADA.name}")
    print("   PLANET_HEALTH_PASSPHRASE         = la contraseña que acabás de poner")
    print()
    print("2. Publicá esta huella en el repositorio, en CLAVES_PUBLICAS.md:")
    print()
    print(f"       {huella}")
    print()
    print("   Es lo único que permite comprobar quién emitió un informe. Sin una")
    print("   copia publicada por fuera del documento, la verificación no puede")
    print("   decir nada sobre el emisor: cualquiera puede firmar con una clave")
    print("   propia y pasar los otros dos niveles.")
    print()
    print("3. No subas el .pem a ningún lado. Ya está excluido del repositorio.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
