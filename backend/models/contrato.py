"""Ensamblado de la respuesta: junta los módulos y arma el contrato que ve el frontend.

**Sobre el puntaje que ya no está.** La versión anterior coronaba la pantalla con
un círculo grande: `salud_global: 82`, y abajo "Buena Salud Ambiental". Salía de
esto:

    const globalScore = Math.round((waterScore * 0.35) + (soilScore * 0.35) + (bioScore * 0.30));

Un promedio ponderado de milímetros de lluvia, unidades de pH y cantidad de
especies, con pesos elegidos a mano y sin justificación, presentado como un número
sobre 100 y traducido a un juicio verbal. `esquema_informe.py` lo prohíbe de
frente: *"No existe un campo apto/no apto, PASS/FAIL ni equivalente. El informe
describe condiciones con rangos y dice qué requiere confirmación."*

Y hay un motivo más concreto que la coherencia con el otro proyecto. Ese número
era la parte de la pantalla que más se iba a citar y a captar de pantalla, y la
que menos significaba. Alguien podía decidir mudarse o comprar una parcela mirando
un 82 que era una suma de peras con manzanas.

En su lugar va `cobertura`: de los indicadores que el sistema sabe pedir, cuántos
consiguió. Es verificable, no juzga nada y responde algo que la persona sí
necesita saber antes de confiar en la pantalla — cuánto de esta parcela está
efectivamente observado.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from contrato_informe.esquema_informe import NO_DETERMINADO, Ubicacion
from contrato_informe.ubicacion import resolutor_georef_ar, resolver_ubicacion

from ..services.cliente import ClienteFuentes
from ..services.gbif import obtener_modulo_biodiversidad
from ..services.open_meteo import obtener_modulo_hidrologia
from ..services.soilgrids import obtener_modulo_suelo
from .procedencia import CoberturaDatos, Modulo, Procedencia, indicador_no_disponible

_log = logging.getLogger(__name__)

VERSION_CONTRATO = "3.0.0"

#: Cuánto se espera al servicio de normalización geográfica del IGN antes de
#: seguir sin encuadre administrativo. Medido en este proyecto, el servicio
#: responde entre 2,7 s y 22,9 s según el momento: el techo lo pone la persona
#: esperando en el campo, no el servicio.
ESPERA_UBICACION_S = 8.0

ALCANCE = (
    "Descripción de condiciones ambientales de una coordenada, armada con datos "
    "públicos de observación remota y registros abiertos de biodiversidad. "
    "Describe lo que las fuentes consultadas informan sobre la zona y dice "
    "explícitamente qué no pudo determinar. No es un estudio de impacto ambiental, "
    "no es asesoramiento profesional y no reemplaza un relevamiento en terreno."
)

LIMITACIONES_GENERALES = (
    "Todas las fuentes tienen una resolución espacial mucho más gruesa que una "
    "parcela: entre 250 m y 25 km según el dato. Describen el entorno, no el "
    "terreno exacto. Ninguna describe el subsuelo, el agua subterránea ni la "
    "presencia de contaminantes. Los registros de biodiversidad reflejan además "
    "dónde hubo observadores. Cada indicador lleva su propia procedencia y sus "
    "limitaciones: conviene leerlas antes de usar un número para decidir algo."
)

MOTIVO_SIN_INFERENCIA = (
    "Requiere ejecutar un modelo de visión por computadora sobre una imagen "
    "cargada por la persona. El backend todavía no expone ese endpoint."
)


async def _ubicacion_administrativa(
    cliente: ClienteFuentes, lat: float, lon: float
) -> dict[str, Any]:
    """Resuelve partido, provincia y país reutilizando `contrato_informe.ubicacion`.

    `resolver_ubicacion()` es síncrona y usa `requests`, así que va a un hilo para
    no bloquear el bucle de eventos mientras espera al servicio del IGN. Por eso
    tampoco puede pasar por `cliente.obtener_json()` y la caché se aplica a mano
    sobre el resultado ya resuelto.

    Sin caché este paso costaba 2,7 s en cada consulta y era el más lento de todos
    con diferencia, incluso cuando el resto salía de disco en centésimas.

    El resolutor cubre solo la Argentina. Fuera del país devuelve un diccionario
    vacío y los campos quedan en "no determinado", que es la regla dura de ese
    módulo y acá se respeta tal cual.
    """
    clave = f"ubicacion|{lat:.4f}|{lon:.4f}"
    guardada = cliente.cache.leer("georef_ar", clave)
    if guardada is not None:
        return guardada.datos

    resolver = asyncio.to_thread(
        resolver_ubicacion,
        lat,
        lon,
        resolutor_georef_ar,
        int(0.1 * 111320),  # radio equivalente al recuadro de búsqueda de GBIF
    )
    try:
        ubicacion = await asyncio.wait_for(resolver, timeout=ESPERA_UBICACION_S)
    except TimeoutError:
        # El propio `ubicacion.py` dice cuál es la conducta correcta acá:
        # "quedarse sin encuadre administrativo es un resultado aceptable del
        # informe, colgarse esperando no lo es". Su timeout interno son 10 s por
        # operación de socket, que en la práctica se estiran a más de 20 s.
        _log.warning(
            "georef-ar no respondió en %s s para (%s, %s). El encuadre queda sin determinar.",
            ESPERA_UBICACION_S,
            lat,
            lon,
        )
        # Se arma la Ubicacion a mano en vez de llamar a `resolver_ubicacion()`
        # con `resolutor=None`: ese atajo dejaría `fuente_ubicacion` en "no
        # consultada", y sí se consultó — lo que pasó es que no contestó a
        # tiempo. Son dos situaciones distintas y quien lea el informe después
        # tiene que poder distinguirlas.
        return Ubicacion(
            lat=lat,
            lon=lon,
            fuente_ubicacion=(
                f"georef-ar (IGN / datos.gob.ar) — consulta cancelada tras "
                f"{ESPERA_UBICACION_S:.0f} s sin respuesta"
            ),
            radio_analisis_m=int(0.1 * 111320),
        ).a_diccionario()

    resuelta = ubicacion.a_diccionario()

    # Solo se guarda una resolución que haya dado algo. Cachear 90 días un "no
    # determinado" que vino de un servicio caído convertiría una falla pasajera
    # en un hueco permanente para esa coordenada.
    if resuelta.get("pais") != NO_DETERMINADO:
        cliente.cache.escribir("georef_ar", clave, resuelta)

    return resuelta


def _modulo_observacion_directa() -> Modulo:
    """Los tres indicadores que dependen de modelos de visión, declarados vacíos.

    Aparecen en la respuesta en lugar de omitirse. Es una decisión: la interfaz
    muestra la tarjeta con los huecos y el motivo, y así queda a la vista qué
    falta construir en vez de dar a entender que el análisis está completo.

    Es también el reemplazo directo de `deepforest_adapter.js` y
    `wildlife_adapter.js`, que devolvían `Math.random()` bajo los nombres de
    Weecology y Microsoft.
    """
    return Modulo(
        clave="observacion_directa",
        titulo="Observación directa",
        icono="🔭",
        indicadores=[
            indicador_no_disponible(
                "copas_arboles",
                "Copas de árboles detectadas",
                "Requiere correr DeepForest sobre una imagen aérea de la parcela. "
                + MOTIVO_SIN_INFERENCIA,
            ),
            indicador_no_disponible(
                "fauna_detectada",
                "Fauna en cámara trampa",
                "Requiere correr MegaDetector sobre una imagen de cámara trampa. "
                "PyTorch-Wildlife todavía no está instalado ni evaluado en este "
                "proyecto. " + MOTIVO_SIN_INFERENCIA,
            ),
            indicador_no_disponible(
                "bioacustica",
                "Identificación bioacústica",
                "Requiere correr un modelo acústico sobre una grabación de ambiente. "
                + MOTIVO_SIN_INFERENCIA,
            ),
        ],
        limitaciones=(
            "Estos indicadores describen lo que se ve y se oye en un archivo que "
            "carga la persona, no lo que hay en la parcela: dependen por completo de "
            "dónde apuntó la cámara y cuándo."
        ),
    )


async def construir_informe(cliente: ClienteFuentes, lat: float, lon: float) -> dict[str, Any]:
    """Consulta todas las fuentes en paralelo y arma la respuesta completa.

    Ninguna fuente puede hacer fallar el informe: cada módulo degrada por su cuenta
    a indicadores `no_disponible`. Si se cayeran las tres, la respuesta sale igual,
    con todos los huecos explicados y `cobertura.pct_con_dato` en 0.
    """
    hidrologia, suelo, biodiversidad, ubicacion = await asyncio.gather(
        obtener_modulo_hidrologia(cliente, lat, lon),
        obtener_modulo_suelo(cliente, lat, lon),
        obtener_modulo_biodiversidad(cliente, lat, lon),
        _ubicacion_administrativa(cliente, lat, lon),
    )

    modulos = [hidrologia, suelo, biodiversidad, _modulo_observacion_directa()]
    cobertura = CoberturaDatos.desde_modulos(modulos)

    # Un solo indicador simulado contamina el informe entero y lo marca. Hoy no hay
    # ninguno: el camino quedó abierto y cerrado a la vez, para que si alguien
    # agrega un dato de demostración tenga que hacerlo a la vista.
    es_ficticio = cobertura.simulados > 0

    # Se recorre en orden y se descartan repetidas en vez de usar un set: las
    # leyendas de atribución se muestran tal cual al pie de la pantalla y el
    # orden tiene que ser estable entre dos consultas iguales.
    atribuciones: list[str] = []
    for modulo in modulos:
        for indicador in modulo.indicadores:
            atribucion = indicador.fuente.atribucion if indicador.fuente else ""
            if atribucion and atribucion not in atribuciones:
                atribuciones.append(atribucion)

    return {
        "version_contrato": VERSION_CONTRATO,
        "generado_en": datetime.now(UTC).isoformat(),
        "es_ficticio": es_ficticio,
        "coordenadas": {"lat": lat, "lon": lon},
        "ubicacion": ubicacion,
        "cobertura": cobertura.a_diccionario(),
        "modulos": [m.model_dump(mode="json") for m in modulos],
        "alcance": ALCANCE,
        "limitaciones_generales": LIMITACIONES_GENERALES,
        "atribuciones": atribuciones,
        "procedencias_posibles": [p.value for p in Procedencia],
    }
