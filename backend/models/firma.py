"""Firma Ed25519 de los registros, reutilizando `contrato_informe.motor_firma`.

**Qué reemplaza.** La interfaz anterior mostraba el cartel «Sello Criptográfico
Activo» y la leyenda «Ed25519 / SHA-256», y en el código no había ninguna firma:
solo un hash SHA-256 que, además, no cubría los datos (ver el comentario de
`ordenarClaves()` en `frontend/js/servicios/sello_local.js`). Acá la firma es
real.

**Por qué no se usa `firmar_informe()` tal cual.** Esa función recibe un
`Informe`, el dataclass de `esquema_informe.py`, cuyas secciones tienen
indicadores con `nombre`, `valor`, `unidad`, `metodo` — pero no `procedencia`.
Convertir el contrato v3 a esa forma perdería justamente el campo que este
proyecto existe para sostener. Se firma el contrato v3 como está.

Eso no rompe nada aguas abajo: `repo_publico/verificar_informe.py` es genérico,
hashea `documento["informe"]` sea cual sea su estructura interna, y solo exige la
forma exterior `{"informe": ..., "firma": {...}}`. Todo lo criptográfico —
`json_canonico`, el hash, la huella, el bloque de firma — sale de `motor_firma`
sin modificar, para que las dos implementaciones no puedan divergir.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any

from contrato_informe.esquema_informe import ahora_utc_iso, json_canonico
from contrato_informe.motor_firma import (
    ALGORITMO,
    BloqueFirma,
    ErrorDeFirma,
    cargar_clave_privada,
    clave_publica_hex,
    generar_par_de_claves,
    huella_de_clave,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_log = logging.getLogger(__name__)

#: Dónde viven las claves. El .gitignore excluye *.pem, *.key y claves/ enteros:
#: una clave privada que entra al historial de git no se puede borrar, solo se
#: puede rotar.
DIRECTORIO_CLAVES = Path(
    os.environ.get("PLANET_HEALTH_CLAVES", Path(__file__).resolve().parent.parent / "claves")
)
RUTA_PRIVADA = DIRECTORIO_CLAVES / "planet_health_privada.pem"
RUTA_PUBLICA = DIRECTORIO_CLAVES / "planet_health_publica.pem"

#: Contraseña de la clave privada, si está cifrada.
VARIABLE_PASSPHRASE = "PLANET_HEALTH_PASSPHRASE"

#: La clave privada en PEM, directamente en el entorno.
#:
#: Hace falta para desplegar en cualquier servicio con disco efímero, que son
#: casi todos los planes gratuitos. Sin esto, el servidor genera una clave nueva
#: en cada despliegue, y entonces **todos los registros firmados antes dejan de
#: anclar**: la huella publicada ya no coincide con ninguna clave viva. Alguien
#: que verificara un informe de la semana pasada vería fallar el nivel 3 sin que
#: nadie haya manipulado nada.
#:
#: El valor va como secreto en el panel del servicio, nunca en el repositorio.
VARIABLE_CLAVE_PEM = "PLANET_HEALTH_CLAVE_PRIVADA_PEM"


class GestorDeFirma:
    """Tiene la clave privada del servidor y firma registros con ella."""

    def __init__(self) -> None:
        self._clave: Ed25519PrivateKey | None = None

    def _obtener_clave(self) -> Ed25519PrivateKey:
        """Carga la clave, generando una de desarrollo la primera vez.

        **Sobre generar una clave sin contraseña automáticamente.** Suena a
        atajo peligroso y conviene decir por qué no lo es acá: una clave que
        nadie publicó no prueba nada sobre el emisor, y el verificador lo dice
        con todas las letras en su nivel 3. Un registro firmado con esta clave de
        desarrollo pasa integridad y firma, y **falla el ancla**, que es
        exactamente el resultado correcto.

        Lo que sí sería un error es publicar la huella de una clave sin
        contraseña en `CLAVES_PUBLICAS.md`. Para producción hay que generar el
        par a mano con passphrase y recién ahí publicar la huella.
        """
        if self._clave is not None:
            return self._clave

        passphrase = os.environ.get(VARIABLE_PASSPHRASE)
        clave_bytes = passphrase.encode("utf-8") if passphrase else None

        # Primero el entorno: es lo que se usa en producción, donde el disco es
        # efímero y una clave en archivo no sobrevive al despliegue.
        pem = os.environ.get(VARIABLE_CLAVE_PEM)
        if pem:
            try:
                clave = serialization.load_pem_private_key(
                    pem.replace("\\n", "\n").encode("utf-8"), password=clave_bytes
                )
            except Exception as error:  # noqa: BLE001
                raise ErrorDeFirma(
                    f"No se pudo leer la clave privada de la variable {VARIABLE_CLAVE_PEM}: "
                    f"{error}. Si la clave está cifrada, falta {VARIABLE_PASSPHRASE}."
                ) from error
            if not isinstance(clave, Ed25519PrivateKey):
                raise ErrorDeFirma(f"{VARIABLE_CLAVE_PEM} no contiene una clave Ed25519.")
            _log.info(
                "Clave de firma cargada desde el entorno. Huella: %s",
                huella_de_clave(clave.public_key()),
            )
            self._clave = clave
            return self._clave

        if not RUTA_PRIVADA.exists():
            generar_par_de_claves(
                RUTA_PRIVADA,
                RUTA_PUBLICA,
                passphrase=clave_bytes,
                permitir_sin_cifrar=clave_bytes is None,
            )
            self._clave = cargar_clave_privada(RUTA_PRIVADA, clave_bytes)
            if clave_bytes is None:
                _log.warning(
                    "Se generó una clave Ed25519 SIN CONTRASEÑA en %s porque no está "
                    "definida la variable %s. Sirve para desarrollo. Su huella (%s) NO "
                    "debe publicarse en CLAVES_PUBLICAS.md: mientras no esté publicada, "
                    "los registros que firme fallan el ancla externa, que es lo correcto.",
                    RUTA_PRIVADA,
                    VARIABLE_PASSPHRASE,
                    self.huella(),
                )
            return self._clave

        self._clave = cargar_clave_privada(RUTA_PRIVADA, clave_bytes)
        return self._clave

    def huella(self) -> str:
        """Huella corta de la clave pública, en el formato de CLAVES_PUBLICAS.md."""
        return huella_de_clave(self._obtener_clave().public_key())

    def clave_publica_hex(self) -> str:
        """Los 32 bytes de la clave pública, en hexadecimal."""
        return clave_publica_hex(self._obtener_clave().public_key())

    def firmar(self, bloque: dict[str, Any], id_registro: str) -> dict[str, Any]:
        """Firma un bloque de datos y devuelve el documento completo.

        Se firman los bytes canónicos del bloque, no su hash: Ed25519 ya hashea
        internamente. El SHA-256 se guarda aparte porque es lo que se muestra
        para comparar a ojo. Es la misma decisión que documenta
        `motor_firma.firmar_informe()`, replicada acá para un diccionario.

        Args:
            bloque: lo que se firma. Va tal cual bajo la clave "informe".
            id_registro: identificador estable del registro.

        Returns:
            `{"informe": ..., "firma": {...}}`, la forma que espera
            `repo_publico/verificar_informe.py`.
        """
        clave = self._obtener_clave()
        mensaje = json_canonico(bloque)
        publica = clave.public_key()

        bloque_firma = BloqueFirma(
            id_informe=id_registro,
            algoritmo=ALGORITMO,
            hash_sha256=hashlib.sha256(mensaje).hexdigest(),
            firma_hex=clave.sign(mensaje).hex(),
            clave_publica_hex=clave_publica_hex(publica),
            huella_clave=huella_de_clave(publica),
            fecha_firma=ahora_utc_iso(),
        )
        return {"informe": bloque, "firma": bloque_firma.a_diccionario()}


#: Instancia única. La clave se carga la primera vez que se firma algo, no al
#: importar: así el backend arranca aunque las claves no estén, y solo falla la
#: ruta que las necesita.
gestor_de_firma = GestorDeFirma()

__all__ = ["ErrorDeFirma", "GestorDeFirma", "gestor_de_firma"]
