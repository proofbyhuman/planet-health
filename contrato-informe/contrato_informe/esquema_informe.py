"""Contrato de datos del informe ambiental de parcela.

Este archivo es el **único lugar** donde está escrita la forma de un informe.
Todo lo demás del módulo (firma, PDF, fuentes de datos) depende de lo que hay acá.

Tres reglas de diseño que vienen de decisiones ya tomadas por el equipo:

1. **Sin veredicto.** No existe un campo "apto/no apto", "PASS/FAIL" ni equivalente.
   El informe describe condiciones con rangos y dice qué requiere confirmación.
   Para que esto no se rompa por descuido, `validar_texto_descriptivo()` rechaza
   activamente palabras de veredicto en los campos de texto libre.
2. **Nada de ubicaciones por defecto.** Si el partido o la provincia no se pudieron
   determinar de forma confiable, el valor es literalmente `"no determinado"`.
   Nunca un nombre de lugar fijo. (Esto corrige el bug del código viejo de cebada,
   que firmaba "Tres Arroyos" para cualquier coordenada.)
3. **Genérico, no cableado a un cultivo.** Las secciones son una lista abierta de
   componentes ambientales, no un diccionario fijo de índices de un cultivo.

El bloque que se firma criptográficamente es el diccionario que devuelve
`Informe.a_bloque_firmable()`, serializado con `json_canonico()`.
"""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

VERSION_ESQUEMA = "0.1.0"

#: Valor obligatorio cuando un dato de ubicación no se pudo determinar.
#: No se admite ningún otro valor por defecto.
NO_DETERMINADO = "no determinado"

ALCANCE_POR_DEFECTO = (
    "Informe ambiental descriptivo de gabinete, elaborado con datos públicos de "
    "observación remota para las coordenadas indicadas. Describe condiciones "
    "observadas y sus limitaciones. No constituye un Estudio de Impacto Ambiental, "
    "no es asesoramiento legal y no reemplaza ningún trámite ante autoridad "
    "competente. Puede ampliarse con relevamiento en terreno."
)

LIMITACIONES_POR_DEFECTO = (
    "Los datos provienen de sensores remotos y bases públicas, con resolución "
    "espacial y temporal propia de cada fuente: pueden no representar variaciones "
    "dentro de la parcela. La nubosidad frecuente en la Patagonia puede dejar "
    "períodos sin observación. Ningún resultado de este informe describe el "
    "subsuelo ni el agua subterránea."
)


class Componente(str, Enum):
    """Componentes ambientales que puede cubrir el informe."""

    AGUA = "agua"
    SUELO = "suelo"
    RIESGO_INCENDIO = "riesgo_incendio"
    BIODIVERSIDAD = "biodiversidad"
    BOSQUE_NATIVO = "bosque_nativo"
    OTRO = "otro"


class NivelConfianza(str, Enum):
    """Cuánto se puede apoyar el lector en el resultado de una sección."""

    ALTA = "alta"
    MEDIA = "media"
    BAJA = "baja"
    NO_ESTIMADA = "no_estimada"


class Verificacion(str, Enum):
    """Qué hace falta para confirmar lo que dice la sección."""

    NO_REQUIERE = "no_requiere_verificacion_adicional"
    TERRENO = "requiere_confirmacion_en_terreno"
    AUTORIDAD = "requiere_confirmacion_ante_autoridad_competente"
    TERRENO_Y_AUTORIDAD = "requiere_confirmacion_en_terreno_y_ante_autoridad"


# ---------------------------------------------------------------------------
# Guardia contra la semántica de veredicto
# ---------------------------------------------------------------------------

class VeredictoProhibidoError(ValueError):
    """Se intentó escribir un juicio de aptitud en un informe descriptivo."""


#: Palabras que convierten una descripción en un veredicto. La lista es corta a
#: propósito: no busca censurar el lenguaje, busca frenar el error puntual de
#: escribir "apto para vivienda" adentro de un PDF firmado.
TERMINOS_DE_VEREDICTO = (
    "apto", "apta", "aptos", "aptas",
    "inapto", "inapta", "inaptos", "inaptas",
    "aptitud",
    "aprobado", "aprobada", "aprueba",
    "reprobado", "rechazado", "rechazada",
    "habilitado", "habilitada", "habilita",
    "certificamos", "garantizamos", "garantiza", "garantizado",
    "pass", "fail",
)

_RE_VEREDICTO = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in TERMINOS_DE_VEREDICTO) + r")\b"
)


def _sin_acentos(texto: str) -> str:
    """Devuelve el texto en minúsculas y sin tildes, para comparar palabras."""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def validar_texto_descriptivo(texto: str, campo: str) -> str:
    """Rechaza textos que emitan un juicio de aptitud.

    Args:
        texto: contenido a validar.
        campo: nombre del campo, solo para que el error diga dónde está el problema.

    Returns:
        El mismo texto, si pasa.

    Raises:
        VeredictoProhibidoError: si aparece una palabra de veredicto.
    """
    if not texto:
        return texto
    hallado = _RE_VEREDICTO.search(_sin_acentos(texto))
    if hallado:
        raise VeredictoProhibidoError(
            f"El campo '{campo}' contiene la palabra de veredicto "
            f"'{hallado.group(1)}'. Este informe es descriptivo: reformulá la frase "
            f"como una descripción de condiciones y, si hace falta, marcá el campo "
            f"'verificacion' de la sección."
        )
    return texto


# ---------------------------------------------------------------------------
# Piezas del informe
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FuenteDatos:
    """Una fuente de datos citada, con su atribución y su licencia.

    La atribución no es decorativa: los datos Sentinel de Copernicus son libres
    para uso comercial pero **exigen** la leyenda "Copernicus Sentinel data [año]".
    """

    nombre: str
    coleccion: str = ""
    url: str = ""
    licencia: str = NO_DETERMINADO
    atribucion: str = ""
    fecha_consulta: str = NO_DETERMINADO

    def a_diccionario(self) -> dict[str, Any]:
        return {
            "nombre": self.nombre,
            "coleccion": self.coleccion,
            "url": self.url,
            "licencia": self.licencia,
            "atribucion": self.atribucion,
            "fecha_consulta": self.fecha_consulta,
        }


@dataclass(frozen=True)
class Indicador:
    """Un número (o un texto) observado, con su contexto.

    `rango_referencia` describe contra qué se lee el valor (por ejemplo
    "media regional 2015-2025: 0,45-0,60"). **No es un umbral de aprobación.**
    """

    nombre: str
    valor: Any
    unidad: str = ""
    periodo: str = ""
    rango_referencia: str = ""
    interpretacion: str = ""
    metodo: str = ""

    def __post_init__(self) -> None:
        validar_texto_descriptivo(self.interpretacion, f"indicador.{self.nombre}.interpretacion")
        validar_texto_descriptivo(self.rango_referencia, f"indicador.{self.nombre}.rango_referencia")

    def valor_legible(self) -> str:
        """Texto del valor listo para imprimir, incluido el caso 'sin dato'."""
        if self.valor is None:
            return "sin dato"
        if isinstance(self.valor, float):
            texto = f"{self.valor:.3f}".rstrip("0").rstrip(".")
        else:
            texto = str(self.valor)
        return f"{texto} {self.unidad}".strip()

    def a_diccionario(self) -> dict[str, Any]:
        return {
            "nombre": self.nombre,
            "valor": self.valor,
            "unidad": self.unidad,
            "periodo": self.periodo,
            "rango_referencia": self.rango_referencia,
            "interpretacion": self.interpretacion,
            "metodo": self.metodo,
        }


@dataclass(frozen=True)
class Ubicacion:
    """Dónde está la parcela.

    Regla dura: `partido`, `provincia` y `pais` valen `"no determinado"` mientras
    no haya una resolución confiable. Está prohibido un valor por defecto distinto.
    Ver `ubicacion.py` para cómo se completan estos campos.
    """

    lat: float
    lon: float
    partido: str = NO_DETERMINADO
    provincia: str = NO_DETERMINADO
    pais: str = NO_DETERMINADO
    fuente_ubicacion: str = "no consultada"
    radio_analisis_m: int = 0
    etiqueta_parcela: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.lat, (int, float)) or not isinstance(self.lon, (int, float)):
            raise ValueError("lat y lon deben ser números.")
        if not -90.0 <= float(self.lat) <= 90.0:
            raise ValueError(f"Latitud fuera de rango: {self.lat}")
        if not -180.0 <= float(self.lon) <= 180.0:
            raise ValueError(f"Longitud fuera de rango: {self.lon}")
        if self.radio_analisis_m < 0:
            raise ValueError("radio_analisis_m no puede ser negativo.")
        # Un campo administrativo vacío nunca queda vacío: queda "no determinado".
        for campo in ("partido", "provincia", "pais"):
            valor = getattr(self, campo)
            if valor is None or not str(valor).strip():
                object.__setattr__(self, campo, NO_DETERMINADO)

    def descripcion_administrativa(self) -> str:
        """Texto de una línea con el encuadre administrativo, honesto si falta."""
        partes = [self.partido, self.provincia, self.pais]
        if all(p == NO_DETERMINADO for p in partes):
            return NO_DETERMINADO
        return " / ".join(partes)

    def a_diccionario(self) -> dict[str, Any]:
        return {
            "lat": float(self.lat),
            "lon": float(self.lon),
            "partido": self.partido,
            "provincia": self.provincia,
            "pais": self.pais,
            "fuente_ubicacion": self.fuente_ubicacion,
            "radio_analisis_m": int(self.radio_analisis_m),
            "etiqueta_parcela": self.etiqueta_parcela,
        }


@dataclass
class SeccionInforme:
    """El resultado de un componente ambiental (agua, suelo, incendio, etc.)."""

    componente: Componente
    titulo: str
    resumen_descriptivo: str
    indicadores: list[Indicador] = field(default_factory=list)
    nivel_confianza: NivelConfianza = NivelConfianza.NO_ESTIMADA
    verificacion: Verificacion = Verificacion.TERRENO
    limitaciones: str = ""
    fuentes: list[FuenteDatos] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.componente = Componente(self.componente)
        self.nivel_confianza = NivelConfianza(self.nivel_confianza)
        self.verificacion = Verificacion(self.verificacion)
        validar_texto_descriptivo(self.resumen_descriptivo, f"seccion.{self.componente.value}.resumen_descriptivo")
        validar_texto_descriptivo(self.limitaciones, f"seccion.{self.componente.value}.limitaciones")

    def a_diccionario(self) -> dict[str, Any]:
        return {
            "componente": self.componente.value,
            "titulo": self.titulo,
            "resumen_descriptivo": self.resumen_descriptivo,
            "indicadores": [i.a_diccionario() for i in self.indicadores],
            "nivel_confianza": self.nivel_confianza.value,
            "verificacion": self.verificacion.value,
            "limitaciones": self.limitaciones,
            "fuentes": [f.a_diccionario() for f in self.fuentes],
        }


@dataclass
class Informe:
    """El informe completo. Es lo que se firma y lo que se imprime en PDF."""

    tipo_informe: str
    ubicacion: Ubicacion
    secciones: list[SeccionInforme] = field(default_factory=list)
    id_informe: str = field(default_factory=lambda: uuid.uuid4().hex)
    fecha_emision: str = field(default_factory=lambda: ahora_utc_iso())
    version_esquema: str = VERSION_ESQUEMA
    alcance: str = ALCANCE_POR_DEFECTO
    limitaciones_generales: str = LIMITACIONES_POR_DEFECTO
    solicitante: str = NO_DETERMINADO
    profesional_responsable: str = NO_DETERMINADO
    #: Marca de dato inventado. Si es True, el PDF sale con banda de advertencia.
    es_ficticio: bool = False

    def __post_init__(self) -> None:
        if not self.id_informe:
            raise ValueError("id_informe no puede estar vacío.")
        validar_texto_descriptivo(self.alcance, "informe.alcance")
        validar_texto_descriptivo(self.limitaciones_generales, "informe.limitaciones_generales")

    # -- Consultas -----------------------------------------------------------

    def fuentes_consolidadas(self) -> list[FuenteDatos]:
        """Todas las fuentes citadas por las secciones, sin repetir, ordenadas."""
        vistas: dict[tuple[str, str], FuenteDatos] = {}
        for seccion in self.secciones:
            for fuente in seccion.fuentes:
                vistas.setdefault((fuente.nombre, fuente.coleccion), fuente)
        return [vistas[c] for c in sorted(vistas)]

    def atribuciones(self) -> list[str]:
        """Leyendas de atribución obligatorias, sin repetir."""
        salida: list[str] = []
        for fuente in self.fuentes_consolidadas():
            if fuente.atribucion and fuente.atribucion not in salida:
                salida.append(fuente.atribucion)
        return salida

    def secciones_que_requieren_verificacion(self) -> list[SeccionInforme]:
        return [s for s in self.secciones if s.verificacion is not Verificacion.NO_REQUIERE]

    # -- Serialización -------------------------------------------------------

    def a_diccionario(self) -> dict[str, Any]:
        """Representación completa del informe como diccionario."""
        return {
            "version_esquema": self.version_esquema,
            "id_informe": self.id_informe,
            "tipo_informe": self.tipo_informe,
            "fecha_emision": self.fecha_emision,
            "es_ficticio": bool(self.es_ficticio),
            "solicitante": self.solicitante,
            "profesional_responsable": self.profesional_responsable,
            "alcance": self.alcance,
            "limitaciones_generales": self.limitaciones_generales,
            "ubicacion": self.ubicacion.a_diccionario(),
            "secciones": [s.a_diccionario() for s in self.secciones],
        }

    def a_bloque_firmable(self) -> dict[str, Any]:
        """El diccionario exacto sobre el que se calcula el hash y la firma.

        Hoy es idéntico a `a_diccionario()`. Existe como método aparte para poder
        excluir campos volátiles en el futuro sin tocar el motor de firma.
        """
        return self.a_diccionario()


# ---------------------------------------------------------------------------
# Utilidades compartidas
# ---------------------------------------------------------------------------

def ahora_utc_iso() -> str:
    """Fecha y hora actual en UTC, formato ISO 8601 con 'Z'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def json_canonico(bloque: dict[str, Any]) -> bytes:
    """Serializa un diccionario de forma reproducible, para hashear y firmar.

    Claves ordenadas, sin espacios y en UTF-8: el mismo contenido produce siempre
    los mismos bytes, en cualquier máquina. Si esto cambia, todas las firmas
    emitidas antes dejan de verificar.
    """
    return json.dumps(
        bloque, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def nuevo_id_informe() -> str:
    """Identificador único de un informe (UUID4 en hexadecimal, sin guiones).

    Se usa para el nombre de archivo y para el QR. Es lo que impide el bug de
    concurrencia del código viejo, donde todos los informes escribían y leían
    las mismas rutas fijas.
    """
    return uuid.uuid4().hex


# Firma de función que debe cumplir cualquier módulo de `fuentes_datos/`.
# Recibe la ubicación y devuelve una sección lista para entrar al informe.
CalculadorDeSeccion = Callable[[Ubicacion], SeccionInforme]
