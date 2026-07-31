"""Módulo de biodiversidad: registros de ocurrencia de GBIF.

**El índice de Shannon.** La versión anterior lo calculaba así:

    shannon: (1.5 + Math.min(speciesSet.size, 50) * 0.05).toFixed(2)

Eso no es el índice de Shannon. Es una función lineal del número de especies con
un piso de 1,5, que además saturaba a las 50 especies. Shannon mide *equidad*:
cien registros repartidos entre diez especies dan un valor alto; cien registros
donde noventa y siete son de una sola especie dan un valor bajo. El conteo de
especies no distingue esos dos casos y la fórmula anterior tampoco.

Acá se calcula la definición real, H' = −Σ pᵢ · ln(pᵢ), sobre las abundancias que
devuelve GBIF por especie usando facetas.

**La limitación grande, que va escrita en la respuesta.** Las ocurrencias de GBIF
son registros de observación, no un muestreo sistemático. Miden dónde miró la
gente tanto como qué hay. Un parque urbano al lado de una universidad acumula
decenas de miles de registros y un valle idéntico sin observadores queda casi
vacío. Un Shannon bajo puede significar poca diversidad o pocos naturalistas.
Por eso el indicador es `ESTIMADO`, no `MEDIDO`, y lleva la advertencia adosada.
"""

from __future__ import annotations

import asyncio
import math

from ..models.procedencia import Fuente, Indicador, Modulo, Procedencia, indicador_no_disponible
from .cliente import ClienteFuentes, Respuesta

URL_OCURRENCIAS = "https://api.gbif.org/v1/occurrence/search"
URL_ESPECIE = "https://api.gbif.org/v1/species"

#: Medio lado del recuadro de búsqueda, en grados. 0,1° son unos 11 km en
#: latitud; en longitud se achica con el coseno de la latitud, y eso está dicho
#: en el método del indicador en vez de fingir que es un círculo de 11 km.
DELTA_GRADOS = 0.1

#: Cuántas especies pide como máximo la faceta. GBIF trunca a este número: si se
#: alcanza, el índice se calcula sobre las más registradas y la cobertura real
#: queda declarada en el método.
TOPE_FACETAS = 1200

#: Cuántos nombres de familia se resuelven para mostrar. Cada uno es una consulta
#: extra, cacheada 90 días porque un nombre científico no cambia.
FAMILIAS_A_NOMBRAR = 5

LICENCIA = "CC-BY-4.0"
ATRIBUCION = (
    "Datos de biodiversidad de GBIF.org (Global Biodiversity Information Facility). "
    "Las ocurrencias provienen de múltiples conjuntos de datos con licencias propias."
)

SESGO_DE_OBSERVACION = (
    "Los registros de GBIF reflejan dónde hubo observadores, no solo qué especies "
    "hay. Una zona sin registros puede estar poco explorada en lugar de poco "
    "poblada, y un valor bajo no describe el estado del ecosistema."
)


def _fuente(respuesta: Respuesta) -> Fuente:
    return Fuente(
        nombre="GBIF",
        url="https://www.gbif.org",
        licencia=LICENCIA,
        atribucion=ATRIBUCION,
        consultada_en=respuesta.consultada_en.isoformat(),
        desde_cache=respuesta.desde_cache,
    )


def _facetas(datos: dict, campo: str) -> list[dict]:
    """Saca la lista de conteos de una faceta. GBIF devuelve el campo en mayúsculas."""
    for faceta in datos.get("facets") or []:
        if faceta.get("field", "").upper() == campo.upper():
            return faceta.get("counts") or []
    return []


def indice_shannon(abundancias: list[int]) -> float:
    """H' = −Σ pᵢ · ln(pᵢ) sobre las abundancias dadas.

    Args:
        abundancias: cantidad de registros de cada especie. Los ceros se ignoran
            (ln(0) no existe y una especie ausente no aporta información).

    Returns:
        El índice en nats. 0 si hay una sola especie o ninguna.
    """
    positivas = [a for a in abundancias if a > 0]
    total = sum(positivas)
    if total == 0 or len(positivas) < 2:
        return 0.0
    return -sum((a / total) * math.log(a / total) for a in positivas)


def equitatividad_pielou(shannon: float, riqueza: int) -> float | None:
    """J' = H' / ln(S). Cuán parejo está repartido, entre 0 y 1.

    Devuelve `None` con menos de dos especies, donde la equitatividad no está
    definida en lugar de valer cero.
    """
    if riqueza < 2:
        return None
    return shannon / math.log(riqueza)


async def obtener_modulo_biodiversidad(
    cliente: ClienteFuentes, lat: float, lon: float
) -> Modulo:
    """Arma el módulo de biodiversidad. Nunca lanza."""
    rango_lat = f"{lat - DELTA_GRADOS},{lat + DELTA_GRADOS}"
    rango_lon = f"{lon - DELTA_GRADOS},{lon + DELTA_GRADOS}"
    ancho_km = 2 * DELTA_GRADOS * 111.32 * math.cos(math.radians(lat))
    alto_km = 2 * DELTA_GRADOS * 111.32
    recuadro = f"recuadro de {ancho_km:.0f} × {alto_km:.0f} km centrado en la coordenada"

    respuesta = await cliente.obtener_json(
        fuente="gbif",
        url=URL_OCURRENCIAS,
        params=[
            ("decimalLatitude", rango_lat),
            ("decimalLongitude", rango_lon),
            ("limit", "0"),
            ("facet", "speciesKey"),
            ("facet", "familyKey"),
            ("facetLimit", str(TOPE_FACETAS)),
        ],
        clave_cache=f"ocurrencias|{lat:.4f}|{lon:.4f}|{DELTA_GRADOS}",
    )

    if respuesta is None:
        motivo = "La API de GBIF no respondió y no hay copia guardada de esta coordenada."
        return Modulo(
            clave="biodiversidad",
            titulo="Biodiversidad",
            icono="🦋",
            indicadores=[
                indicador_no_disponible("ocurrencias", "Registros de ocurrencia", motivo),
                indicador_no_disponible("riqueza_especies", "Especies distintas", motivo),
                indicador_no_disponible("indice_shannon", "Índice de Shannon", motivo),
                indicador_no_disponible("equitatividad", "Equitatividad de Pielou", motivo),
                indicador_no_disponible("familias", "Familias más registradas", motivo),
            ],
            limitaciones=SESGO_DE_OBSERVACION,
        )

    fuente = _fuente(respuesta)
    total = respuesta.datos.get("count") or 0
    especies = _facetas(respuesta.datos, "SPECIES_KEY")
    familias = _facetas(respuesta.datos, "FAMILY_KEY")

    indicadores: list[Indicador] = [
        Indicador(
            clave="ocurrencias",
            etiqueta="Registros de ocurrencia",
            valor=total,
            procedencia=Procedencia.MEDIDO,
            fuente=fuente,
            periodo=recuadro,
            limitaciones=SESGO_DE_OBSERVACION,
        )
    ]

    if total == 0 or not especies:
        motivo = (
            f"GBIF no tiene registros de ocurrencia en el {recuadro}. La zona puede "
            f"estar inexplorada por la red de observadores."
        )
        indicadores += [
            indicador_no_disponible("riqueza_especies", "Especies distintas", motivo),
            indicador_no_disponible("indice_shannon", "Índice de Shannon", motivo),
            indicador_no_disponible("equitatividad", "Equitatividad de Pielou", motivo),
            indicador_no_disponible("familias", "Familias más registradas", motivo),
        ]
        return Modulo(
            clave="biodiversidad",
            titulo="Biodiversidad",
            icono="🦋",
            indicadores=indicadores,
            limitaciones=SESGO_DE_OBSERVACION,
        )

    abundancias = [int(c["count"]) for c in especies]
    riqueza = len(abundancias)
    truncado = riqueza >= TOPE_FACETAS
    cubierto = sum(abundancias)
    pct_cubierto = round(100 * cubierto / total) if total else 0

    nota_truncado = (
        f" GBIF devolvió el tope de {TOPE_FACETAS} especies, así que el índice se "
        f"calculó sobre las más registradas, que reúnen el {pct_cubierto} % de las "
        f"ocurrencias. El valor real sería algo mayor."
        if truncado
        else ""
    )

    indicadores.append(
        Indicador(
            clave="riqueza_especies",
            etiqueta="Especies distintas",
            valor=riqueza,
            procedencia=Procedencia.MEDIDO,
            fuente=fuente,
            periodo=recuadro,
            limitaciones=(
                (
                    f"Es un piso, no un total: GBIF cortó la lista en {TOPE_FACETAS}. "
                    if truncado
                    else ""
                )
                + SESGO_DE_OBSERVACION
            ),
        )
    )

    shannon = indice_shannon(abundancias)
    indicadores.append(
        Indicador(
            clave="indice_shannon",
            etiqueta="Índice de Shannon",
            valor=round(shannon, 2),
            unidad="nats",
            procedencia=Procedencia.ESTIMADO,
            fuente=fuente,
            periodo=recuadro,
            metodo=(
                f"H' = −Σ pᵢ·ln(pᵢ), donde pᵢ es la proporción de registros de cada "
                f"una de las {riqueza} especies del {recuadro}, sobre {cubierto} "
                f"ocurrencias." + nota_truncado
            ),
            limitaciones=SESGO_DE_OBSERVACION,
        )
    )

    pielou = equitatividad_pielou(shannon, riqueza)
    if pielou is None:
        indicadores.append(
            indicador_no_disponible(
                "equitatividad",
                "Equitatividad de Pielou",
                "Hace falta más de una especie registrada para que la equitatividad "
                "esté definida.",
            )
        )
    else:
        indicadores.append(
            Indicador(
                clave="equitatividad",
                etiqueta="Equitatividad de Pielou",
                valor=round(pielou, 2),
                procedencia=Procedencia.ESTIMADO,
                fuente=fuente,
                periodo=recuadro,
                metodo=(
                    f"J' = H' / ln(S), con H' = {shannon:.2f} y S = {riqueza} especies. "
                    f"Va de 0 a 1: cerca de 1 los registros están repartidos parejo "
                    f"entre las especies, cerca de 0 unas pocas concentran casi todo."
                ),
                limitaciones=SESGO_DE_OBSERVACION,
            )
        )

    nombres = await _nombrar_familias(cliente, familias[:FAMILIAS_A_NOMBRAR])
    if nombres:
        indicadores.append(
            Indicador(
                clave="familias",
                etiqueta="Familias más registradas",
                valor=nombres,
                procedencia=Procedencia.MEDIDO,
                fuente=fuente,
                periodo=recuadro,
                limitaciones=(
                    f"Son las {len(nombres)} familias con más registros de las "
                    f"{len(familias)} presentes, ordenadas por cantidad de "
                    f"observaciones. " + SESGO_DE_OBSERVACION
                ),
            )
        )
    else:
        indicadores.append(
            indicador_no_disponible(
                "familias",
                "Familias más registradas",
                "No se pudieron resolver los nombres científicos de las familias en el "
                "servicio de taxonomía de GBIF.",
            )
        )

    return Modulo(
        clave="biodiversidad",
        titulo="Biodiversidad",
        icono="🦋",
        indicadores=indicadores,
        limitaciones=SESGO_DE_OBSERVACION,
    )


async def _nombrar_familias(cliente: ClienteFuentes, facetas: list[dict]) -> list[str]:
    """Traduce claves numéricas de familia a nombres científicos.

    Las consultas van en paralelo y cada una se cachea 90 días. Si alguna falla se
    omite esa familia: es preferible una lista corta a un nombre inventado.
    """
    if not facetas:
        return []

    async def resolver(clave_taxon: str) -> str | None:
        respuesta = await cliente.obtener_json(
            fuente="gbif_taxon",
            url=f"{URL_ESPECIE}/{clave_taxon}",
            params=None,
            clave_cache=f"taxon|{clave_taxon}",
        )
        if respuesta is None:
            return None
        nombre = respuesta.datos.get("family") or respuesta.datos.get("scientificName")
        return str(nombre) if nombre else None

    resultados = await asyncio.gather(
        *(resolver(str(f["name"])) for f in facetas), return_exceptions=True
    )
    return [r for r in resultados if isinstance(r, str) and r]
