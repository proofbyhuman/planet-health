"""Motor de firma e integridad de informes: Ed25519 + SHA-256.

Es la única pieza del repo viejo `aspr-cof-cebada` que se reutiliza en su lógica.
Acá está reescrita para ser genérica (firma cualquier `Informe`, no un diccionario
de cebada) y con tres correcciones respecto del original:

1. **Nombres de archivo únicos por informe.** El original escribía siempre en
   `bloque_firmado.json` y `certificado_cebada_001.pdf`: dos informes generados a la
   vez se pisaban entre sí y un cliente podía recibir el documento de otro.
   Acá cada archivo lleva el `id_informe` (UUID) y la fecha, y guardar nunca
   sobrescribe un archivo existente.
2. **Clave privada cifrada por defecto.** Se puede generar sin cifrar, pero hay que
   pedirlo explícitamente.
3. **Honestidad sobre qué prueba la firma.** Verificar un documento contra la clave
   pública que viaja adentro del mismo documento demuestra que el documento no fue
   editado *después* de firmarse, y nada más: quien pueda fabricar el documento
   puede fabricar también el par de claves. Por eso `verificar_documento()`
   distingue tres cosas distintas (integridad, firma válida, clave anclada) y
   avisa cuando no hay ancla externa.

Además, dos límites que conviene tener escritos:

- Esto **no** es firma digital en el sentido de la Ley 25.506 argentina. Es prueba
  de integridad. El encuadre legal quedó pendiente para Fable.
- La firma no dice nada sobre si el contenido del informe es correcto. Dice que
  nadie lo tocó después de emitido.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature

from .esquema_informe import Informe, ahora_utc_iso, json_canonico

ALGORITMO = "Ed25519 + SHA-256"
VERSION_BLOQUE_FIRMA = "0.1.0"

ADVERTENCIA_ANCLA = (
    "La clave pública incluida en este documento permite comprobar que el informe "
    "no fue modificado después de firmarse. NO prueba, por sí sola, quién lo emitió: "
    "para eso hay que comparar la huella de la clave contra una copia publicada por "
    "la consultora fuera de este documento."
)


class ErrorDeFirma(Exception):
    """Problema al firmar, cargar claves o guardar un documento firmado."""


# ---------------------------------------------------------------------------
# Claves
# ---------------------------------------------------------------------------

def generar_par_de_claves(
    ruta_privada: Path | str,
    ruta_publica: Path | str,
    passphrase: bytes | None = None,
    permitir_sin_cifrar: bool = False,
) -> tuple[Path, Path]:
    """Genera un par Ed25519 y lo escribe en disco en formato PEM.

    Args:
        ruta_privada: dónde guardar la clave privada (extensión `.pem` o `.key`,
            que el `.gitignore` del proyecto ya excluye de git).
        ruta_publica: dónde guardar la clave pública.
        passphrase: contraseña para cifrar la clave privada en disco.
        permitir_sin_cifrar: hay que ponerlo en True a propósito para guardar la
            clave privada sin cifrar. Existe para pruebas, no para producción.

    Returns:
        Las dos rutas escritas.

    Raises:
        ErrorDeFirma: si falta la passphrase y no se pidió explícitamente guardar
            sin cifrar, o si alguno de los archivos ya existe.
    """
    ruta_privada = Path(ruta_privada)
    ruta_publica = Path(ruta_publica)

    for ruta in (ruta_privada, ruta_publica):
        if ruta.exists():
            raise ErrorDeFirma(
                f"Ya existe {ruta}. No se sobrescriben claves: movelas o borralas a mano."
            )

    if passphrase is None and not permitir_sin_cifrar:
        raise ErrorDeFirma(
            "Falta la passphrase de la clave privada. Si de verdad querés guardarla "
            "sin cifrar (solo para pruebas), pasá permitir_sin_cifrar=True."
        )

    clave_privada = Ed25519PrivateKey.generate()
    cifrado = (
        serialization.BestAvailableEncryption(passphrase)
        if passphrase
        else serialization.NoEncryption()
    )

    ruta_privada.parent.mkdir(parents=True, exist_ok=True)
    ruta_publica.parent.mkdir(parents=True, exist_ok=True)

    ruta_privada.write_bytes(
        clave_privada.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=cifrado,
        )
    )
    ruta_publica.write_bytes(
        clave_privada.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return ruta_privada, ruta_publica


def cargar_clave_privada(ruta: Path | str, passphrase: bytes | None = None) -> Ed25519PrivateKey:
    """Lee una clave privada Ed25519 en PEM desde disco."""
    try:
        clave = serialization.load_pem_private_key(Path(ruta).read_bytes(), password=passphrase)
    except Exception as error:  # noqa: BLE001
        raise ErrorDeFirma(f"No se pudo leer la clave privada de {ruta}: {error}") from error
    if not isinstance(clave, Ed25519PrivateKey):
        raise ErrorDeFirma(f"{ruta} no es una clave Ed25519.")
    return clave


def cargar_clave_publica(ruta: Path | str) -> Ed25519PublicKey:
    """Lee una clave pública Ed25519 en PEM desde disco."""
    try:
        clave = serialization.load_pem_public_key(Path(ruta).read_bytes())
    except Exception as error:  # noqa: BLE001
        raise ErrorDeFirma(f"No se pudo leer la clave pública de {ruta}: {error}") from error
    if not isinstance(clave, Ed25519PublicKey):
        raise ErrorDeFirma(f"{ruta} no es una clave Ed25519.")
    return clave


def clave_publica_hex(clave: Ed25519PublicKey) -> str:
    """Los 32 bytes crudos de la clave pública, en hexadecimal."""
    return clave.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()


def huella_de_clave(clave_publica: Ed25519PublicKey | str) -> str:
    """Huella corta y legible de una clave pública, para comparar a ojo.

    Formato: 4 grupos de 4 caracteres, por ejemplo `A1B2-C3D4-E5F6-0718`.
    Es lo que se publica fuera del documento para que un tercero pueda anclar la
    verificación a algo que no venga dentro del propio PDF.
    """
    if isinstance(clave_publica, Ed25519PublicKey):
        crudo = clave_publica.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    else:
        crudo = bytes.fromhex(clave_publica)
    digest = hashlib.sha256(crudo).hexdigest().upper()[:16]
    return "-".join(digest[i:i + 4] for i in range(0, 16, 4))


# ---------------------------------------------------------------------------
# Hash y firma
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BloqueFirma:
    """Lo que se agrega al informe al firmarlo. No contiene datos del informe."""

    id_informe: str
    algoritmo: str
    hash_sha256: str
    firma_hex: str
    clave_publica_hex: str
    huella_clave: str
    fecha_firma: str
    version_bloque_firma: str = VERSION_BLOQUE_FIRMA
    advertencia: str = ADVERTENCIA_ANCLA

    def a_diccionario(self) -> dict[str, Any]:
        return {
            "id_informe": self.id_informe,
            "algoritmo": self.algoritmo,
            "hash_sha256": self.hash_sha256,
            "firma_hex": self.firma_hex,
            "clave_publica_hex": self.clave_publica_hex,
            "huella_clave": self.huella_clave,
            "fecha_firma": self.fecha_firma,
            "version_bloque_firma": self.version_bloque_firma,
            "advertencia": self.advertencia,
        }


def calcular_hash(bloque: dict[str, Any]) -> str:
    """SHA-256 en hexadecimal del bloque serializado de forma canónica."""
    return hashlib.sha256(json_canonico(bloque)).hexdigest()


def firmar_informe(informe: Informe, clave_privada: Ed25519PrivateKey) -> BloqueFirma:
    """Firma un informe y devuelve su bloque de firma.

    Se firman los bytes canónicos de `informe.a_bloque_firmable()`, no el hash:
    Ed25519 ya hashea internamente. El SHA-256 se guarda aparte porque es lo que
    se muestra en el PDF y en el QR para comparar a ojo.
    """
    bloque = informe.a_bloque_firmable()
    mensaje = json_canonico(bloque)
    firma = clave_privada.sign(mensaje)
    publica = clave_privada.public_key()
    return BloqueFirma(
        id_informe=informe.id_informe,
        algoritmo=ALGORITMO,
        hash_sha256=hashlib.sha256(mensaje).hexdigest(),
        firma_hex=firma.hex(),
        clave_publica_hex=clave_publica_hex(publica),
        huella_clave=huella_de_clave(publica),
        fecha_firma=ahora_utc_iso(),
    )


def armar_documento_firmado(informe: Informe, bloque_firma: BloqueFirma) -> dict[str, Any]:
    """Junta informe + firma en el diccionario que se guarda en disco."""
    return {
        "informe": informe.a_bloque_firmable(),
        "firma": bloque_firma.a_diccionario(),
    }


# ---------------------------------------------------------------------------
# Verificación
# ---------------------------------------------------------------------------

@dataclass
class ResultadoVerificacion:
    """Resultado de verificar un documento firmado.

    Tres cosas distintas que el código viejo mezclaba en un solo "válido":

    - `integridad_ok`: el hash guardado coincide con el contenido actual.
    - `firma_ok`: la firma verifica contra la clave pública del documento.
    - `clave_anclada`: la clave pública coincide con una huella de confianza
      provista desde afuera. `None` si no se pasó ninguna huella (y entonces la
      verificación no dice nada sobre la autoría).
    """

    integridad_ok: bool = False
    firma_ok: bool = False
    clave_anclada: bool | None = None
    huella_clave: str = ""
    mensajes: list[str] = field(default_factory=list)

    @property
    def documento_intacto(self) -> bool:
        """True si el documento no fue modificado después de firmarse."""
        return self.integridad_ok and self.firma_ok

    def resumen(self) -> str:
        partes = [
            f"integridad: {'ok' if self.integridad_ok else 'FALLA'}",
            f"firma: {'ok' if self.firma_ok else 'FALLA'}",
        ]
        if self.clave_anclada is None:
            partes.append("clave: sin ancla externa (no se comprobó autoría)")
        else:
            partes.append(f"clave: {'coincide con el ancla' if self.clave_anclada else 'NO coincide con el ancla'}")
        return " | ".join(partes)


def verificar_documento(
    documento: dict[str, Any],
    huella_de_confianza: str | None = None,
) -> ResultadoVerificacion:
    """Verifica un documento firmado tal como salió de `armar_documento_firmado()`.

    Args:
        documento: diccionario con las claves "informe" y "firma".
        huella_de_confianza: huella de la clave pública de la consultora, obtenida
            por fuera del documento. Si no se pasa, la verificación no puede decir
            nada sobre quién firmó.

    Returns:
        `ResultadoVerificacion` con los tres chequeos separados y los mensajes.
    """
    resultado = ResultadoVerificacion()

    informe = documento.get("informe")
    firma = documento.get("firma")
    if not isinstance(informe, dict) or not isinstance(firma, dict):
        resultado.mensajes.append("El documento no tiene la forma esperada (falta 'informe' o 'firma').")
        return resultado

    mensaje = json_canonico(informe)
    hash_calculado = hashlib.sha256(mensaje).hexdigest()
    resultado.huella_clave = str(firma.get("huella_clave", ""))

    if hash_calculado == firma.get("hash_sha256"):
        resultado.integridad_ok = True
    else:
        resultado.mensajes.append(
            "El contenido del informe no coincide con el hash firmado: fue modificado "
            "después de la emisión, o se serializó con otra versión del esquema."
        )

    try:
        clave = Ed25519PublicKey.from_public_bytes(bytes.fromhex(str(firma.get("clave_publica_hex", ""))))
        clave.verify(bytes.fromhex(str(firma.get("firma_hex", ""))), mensaje)
        resultado.firma_ok = True
    except InvalidSignature:
        resultado.mensajes.append("La firma no verifica contra la clave pública del documento.")
    except Exception as error:  # noqa: BLE001
        resultado.mensajes.append(f"No se pudo verificar la firma: {type(error).__name__}: {error}")

    if huella_de_confianza:
        resultado.clave_anclada = (
            resultado.huella_clave.strip().upper() == huella_de_confianza.strip().upper()
        )
        if not resultado.clave_anclada:
            resultado.mensajes.append(
                "La huella de la clave del documento no coincide con la huella de "
                "confianza: el documento puede estar bien formado pero no lo firmó "
                "quien se espera."
            )
    else:
        resultado.mensajes.append(ADVERTENCIA_ANCLA)

    return resultado


# ---------------------------------------------------------------------------
# Nombres de archivo y guardado (corrección del bug de concurrencia)
# ---------------------------------------------------------------------------

_RE_NO_SEGURO = re.compile(r"[^A-Za-z0-9_-]+")


def nombre_archivo_informe(informe: Informe, extension: str) -> str:
    """Nombre de archivo único e irrepetible para un informe.

    Formato: `[FICTICIO_]informe_<tipo>_<id12>_<fecha>.<ext>`

    El `id_informe` es un UUID: dos informes generados en el mismo segundo, para
    la misma coordenada, en procesos distintos, nunca comparten nombre. Ese era el
    bug de concurrencia del código viejo.
    """
    extension = extension.lstrip(".")
    tipo = _RE_NO_SEGURO.sub("-", informe.tipo_informe).strip("-").lower()[:40] or "informe"
    fecha = _RE_NO_SEGURO.sub("", informe.fecha_emision)[:15] or "sinfecha"
    prefijo = "FICTICIO_" if informe.es_ficticio else ""
    return f"{prefijo}informe_{tipo}_{informe.id_informe[:12]}_{fecha}.{extension}"


def guardar_documento_firmado(
    documento: dict[str, Any],
    directorio: Path | str,
    nombre_archivo: str,
) -> Path:
    """Escribe el documento firmado como JSON. Nunca pisa un archivo existente."""
    directorio = Path(directorio)
    directorio.mkdir(parents=True, exist_ok=True)
    destino = directorio / nombre_archivo
    if destino.exists():
        raise ErrorDeFirma(
            f"Ya existe {destino}. Un informe firmado nunca se sobrescribe: "
            f"si hay que corregirlo, se emite uno nuevo con otro id."
        )
    destino.write_text(
        json.dumps(documento, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return destino


def cargar_documento_firmado(ruta: Path | str) -> dict[str, Any]:
    """Lee un documento firmado desde disco."""
    return json.loads(Path(ruta).read_text(encoding="utf-8"))
