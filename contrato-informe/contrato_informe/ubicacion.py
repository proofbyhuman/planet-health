"""Resolución del encuadre administrativo (partido / provincia) de una coordenada.

**Por qué este archivo existe aparte.** El código viejo de `aspr-cof-cebada` escribía
"TRES ARROYOS" como ubicación de cualquier coordenada que se le pasara, y después
firmaba criptográficamente ese dato falso. El error no fue de criptografía sino de
diseño: había un valor por defecto donde no debía haber ninguno.

La regla acá es una sola y no se negocia:

> Si no hay una respuesta confiable, el campo dice `"no determinado"`.
> Nunca un nombre de lugar de relleno, nunca el último que funcionó,
> nunca una constante del código.

Lo que está implementado y probado: la lógica de decisión — sin resolutor, o con un
resolutor que falla, que devuelve algo vacío o que devuelve algo mal formado, el
resultado siempre es `"no determinado"`.

Desde el 28/07/2026 también está implementada la consulta real al servicio de
normalización geográfica del Estado argentino, en `resolutor_georef_ar()`. No se
usa por defecto: hay que pasarla explícitamente a `resolver_ubicacion()`, para que
quede a la vista de quien emite el informe que se consultó un servicio externo.
"""

from __future__ import annotations

from typing import Any, Callable

from .esquema_informe import NO_DETERMINADO, Ubicacion

#: Un resolutor recibe (lat, lon) y devuelve un diccionario con las claves
#: 'partido', 'provincia' y 'pais' (las que pueda). Cualquier otra cosa se descarta.
Resolutor = Callable[[float, float], dict[str, Any]]

_CLAVES_ADMINISTRATIVAS = ("partido", "provincia", "pais")


def resolver_ubicacion(
    lat: float,
    lon: float,
    resolutor: Resolutor | None = None,
    radio_analisis_m: int = 0,
    etiqueta_parcela: str = "",
) -> Ubicacion:
    """Arma una `Ubicacion` a partir de coordenadas, sin inventar nada.

    Args:
        lat: latitud en grados decimales (WGS84).
        lon: longitud en grados decimales (WGS84).
        resolutor: función opcional que traduce coordenadas a división
            administrativa. Si es `None`, no se consulta nada y los campos
            administrativos quedan en "no determinado".
        radio_analisis_m: radio del entorno analizado alrededor del punto, en metros.
        etiqueta_parcela: nombre interno de la parcela, si el solicitante dio uno.

    Returns:
        Una `Ubicacion` con lat/lon siempre reales, y campos administrativos
        completados **solo** si el resolutor devolvió texto utilizable.

    Notas:
        Cualquier excepción del resolutor se traga a propósito: un servicio caído
        no puede transformarse en un dato falso dentro de un documento firmado.
        Queda registrado en `fuente_ubicacion`.
    """
    datos: dict[str, Any] = {}
    fuente = "no consultada"

    if resolutor is not None:
        try:
            respuesta = resolutor(lat, lon)
        except Exception as error:  # noqa: BLE001 - a propósito: nada puede romper el informe
            fuente = f"consulta fallida ({type(error).__name__})"
        else:
            if isinstance(respuesta, dict):
                datos = respuesta
                fuente = str(
                    respuesta.get("fuente") or getattr(resolutor, "__name__", "resolutor")
                )
            else:
                fuente = "consulta con respuesta inválida"

    campos = {clave: _valor_limpio(datos.get(clave)) for clave in _CLAVES_ADMINISTRATIVAS}

    if resolutor is not None and all(v == NO_DETERMINADO for v in campos.values()):
        fuente = f"{fuente} — sin resultado utilizable"

    return Ubicacion(
        lat=float(lat),
        lon=float(lon),
        partido=campos["partido"],
        provincia=campos["provincia"],
        pais=campos["pais"],
        fuente_ubicacion=fuente,
        radio_analisis_m=int(radio_analisis_m),
        etiqueta_parcela=etiqueta_parcela,
    )


def _valor_limpio(valor: Any) -> str:
    """Convierte lo que devolvió el resolutor en texto usable, o en 'no determinado'.

    Descarta `None`, cadenas vacías, espacios en blanco y los rellenos típicos de
    los servicios de geocodificación ("null", "N/A", "-", "unknown").
    """
    if valor is None:
        return NO_DETERMINADO
    texto = str(valor).strip()
    if not texto:
        return NO_DETERMINADO
    if texto.lower() in {"null", "none", "n/a", "na", "-", "--", "unknown", "desconocido"}:
        return NO_DETERMINADO
    return texto


#: Endpoint de ubicación del servicio de normalización geográfica del Estado
#: argentino (IGN / datos.gob.ar). Es público y no pide credenciales.
URL_GEOREF = "https://apis.datos.gob.ar/georef/api/ubicacion"

#: Segundos de espera. Corto a propósito: quedarse sin encuadre administrativo es
#: un resultado aceptable del informe, colgarse esperando no lo es.
ESPERA_GEOREF_S = 10


def resolutor_georef_ar(lat: float, lon: float) -> dict[str, Any]:
    """Resolutor contra el servicio público de normalización geográfica argentino.

    Consulta el endpoint de ubicación de la API georef y traduce su respuesta a las
    claves que espera `resolver_ubicacion()`.

    Args:
        lat: latitud en grados decimales (WGS84).
        lon: longitud en grados decimales (WGS84).

    Returns:
        `{"partido": ..., "provincia": ..., "pais": "Argentina", "fuente": ...}` si
        el punto cae en territorio argentino y el servicio respondió. **Un
        diccionario vacío en cualquier otro caso** — punto fuera del país, servicio
        caído, respuesta con otro formato. Nunca un valor de relleno: es la regla
        dura de este módulo.

    Nota sobre el nombre del campo:
        En la Patagonia la unidad equivalente al "partido" bonaerense se llama
        **departamento**, y es lo que devuelve el servicio. El campo de nuestro
        esquema se llama `partido` por herencia; el contenido puede ser un
        departamento y eso es correcto.
    """
    try:
        import requests

        respuesta = requests.get(
            URL_GEOREF,
            params={"lat": float(lat), "lon": float(lon)},
            timeout=ESPERA_GEOREF_S,
            headers={"Accept": "application/json"},
        )
        respuesta.raise_for_status()
        cuerpo = respuesta.json()
    except Exception:  # noqa: BLE001 - a propósito: cualquier falla es "no determinado"
        return {}

    if not isinstance(cuerpo, dict):
        return {}
    ubicacion = cuerpo.get("ubicacion")
    if not isinstance(ubicacion, dict):
        return {}

    provincia = _nombre_anidado(ubicacion.get("provincia"))
    if not provincia:
        # Sin provincia el punto está fuera de la Argentina, o el servicio no supo
        # ubicarlo. En los dos casos no hay nada que afirmar.
        return {}

    partido = _nombre_anidado(ubicacion.get("departamento")) or _nombre_anidado(
        ubicacion.get("municipio")
    )

    return {
        "partido": partido,
        "provincia": provincia,
        "pais": "Argentina",
        "fuente": "georef-ar (IGN / datos.gob.ar)",
    }


def _nombre_anidado(bloque: Any) -> str:
    """Saca el campo `nombre` de un bloque de la respuesta de georef."""
    if not isinstance(bloque, dict):
        return ""
    return str(bloque.get("nombre") or "").strip()
