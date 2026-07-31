"""Procedencia de cada dato: de dónde salió y cuánto se puede apoyar el lector en él.

Este archivo existe por un motivo concreto. La versión anterior de Planet Health
mostraba `Puma concolor` con 91 % de confianza bajo el logo de Microsoft
PyTorch-Wildlife, y el número venía de `Math.random()`. También mostraba pH y
materia orgánica citando SoilGrids en el pie de página, calculados con
`Math.sin(lat*12.9898 + lon*78.233)`, que es un hash de la coordenada.

Es el mismo error que `contrato_informe/ubicacion.py` ya documentó del código viejo
de cebada, que escribía "TRES ARROYOS" para cualquier coordenada y después firmaba
ese dato falso: *el error no fue de criptografía sino de diseño, había un valor
por defecto donde no debía haber ninguno*.

La defensa acá no es una convención ni un comentario pidiendo cuidado: es que el
modelo `Indicador` **no se puede construir** sin declarar la procedencia, y cada
procedencia exige lo que hace falta para sostenerla. Un indicador `MEDIDO` sin
fuente, o `ESTIMADO` sin método, o `NO_DISPONIBLE` con un valor adentro, revienta
en tiempo de validación y nunca llega a la respuesta HTTP.

Se apoya en dos piezas de `contrato_informe.esquema_informe` en lugar de
reimplementarlas: `NO_DETERMINADO` y `validar_texto_descriptivo()`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from contrato_informe.esquema_informe import validar_texto_descriptivo
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Procedencia(StrEnum):
    """De dónde viene el valor de un indicador.

    El orden de la enumeración va de más a menos apoyo empírico, y así se ordenan
    también las insignias en la interfaz.
    """

    #: Consultado a una fuente externa. El valor es lo que la fuente respondió,
    #: convertido de unidades si hizo falta, sin ninguna otra transformación.
    MEDIDO = "medido"

    #: Derivado por cálculo a partir de datos medidos. Obliga a declarar `metodo`:
    #: si el lector no puede reproducir la cuenta, el número no sirve.
    ESTIMADO = "estimado"

    #: Generado, inventado o de demostración. Solo puede aparecer en una respuesta
    #: marcada `es_ficticio=True`, y la interfaz la muestra con banda de
    #: advertencia. Es la única puerta por la que puede entrar un dato no real, y
    #: está cerrada por defecto.
    SIMULADO = "simulado"

    #: No hay fuente todavía. El valor es `None` y hay que decir por qué.
    #: Un módulo sin datos se muestra vacío y explicado, nunca relleno.
    NO_DISPONIBLE = "no_disponible"


class Fuente(BaseModel):
    """Atribución de una fuente de datos externa.

    Réplica deliberada de `contrato_informe.esquema_informe.FuenteDatos`: la
    atribución no es decorativa. Open-Meteo pide cita bajo CC-BY-4.0 y SoilGrids
    bajo CC-BY-4.0; GBIF pide citar los conjuntos de datos consultados.
    """

    model_config = ConfigDict(frozen=True)

    nombre: str
    url: str = ""
    licencia: str = "no determinado"
    atribucion: str = ""
    consultada_en: str = ""
    #: Verdadero si el valor salió de la caché en disco y no de una consulta nueva.
    desde_cache: bool = False


class Indicador(BaseModel):
    """Un dato observable, con todo lo que hace falta para poder creerle.

    Las reglas de validación son la razón de ser de la clase. Ver
    `_verificar_coherencia()`.
    """

    model_config = ConfigDict(frozen=True)

    #: Identificador estable, en snake_case. La interfaz lo usa como clave; no se
    #: renombra sin migrar el frontend.
    clave: str
    #: Texto que ve la persona.
    etiqueta: str
    #: `None` siempre que la procedencia sea NO_DISPONIBLE, y solo en ese caso.
    valor: float | int | str | list[str] | None = None
    unidad: str = ""
    procedencia: Procedencia
    fuente: Fuente | None = None
    #: Cómo se calculó. Obligatorio para ESTIMADO.
    metodo: str = ""
    #: Por qué falta. Obligatorio para NO_DISPONIBLE.
    motivo: str = ""
    #: Qué no se puede concluir de este número. Se muestra junto al valor.
    limitaciones: str = ""
    #: Período que cubre la observación, por ejemplo "2025-07-30 a 2026-07-29".
    periodo: str = ""

    @model_validator(mode="after")
    def _verificar_coherencia(self) -> Indicador:
        """Impide construir un indicador que no se pueda sostener.

        Raises:
            ValueError: si la combinación de procedencia, valor y respaldo es
                incoherente. Es a propósito un error de programación, no una
                respuesta HTTP 4xx: significa que una fuente está mal escrita.
        """
        p = self.procedencia

        if p is Procedencia.NO_DISPONIBLE:
            if self.valor is not None:
                raise ValueError(
                    f"El indicador '{self.clave}' es no_disponible pero trae el valor "
                    f"{self.valor!r}. Un dato que no existe no tiene valor de relleno: "
                    f"o hay fuente y es medido, o el valor es None."
                )
            if not self.motivo.strip():
                raise ValueError(
                    f"El indicador '{self.clave}' es no_disponible y no dice por qué. "
                    f"La interfaz muestra ese motivo: sin él la persona ve un hueco "
                    f"sin explicación."
                )
        else:
            if self.valor is None:
                raise ValueError(
                    f"El indicador '{self.clave}' tiene procedencia '{p.value}' y valor "
                    f"None. Si no hay valor, la procedencia es no_disponible."
                )

        if p is Procedencia.MEDIDO and self.fuente is None:
            raise ValueError(
                f"El indicador '{self.clave}' se declara medido y no cita fuente. "
                f"'Medido' significa que alguien lo midió: hay que decir quién."
            )

        if p is Procedencia.ESTIMADO and not self.metodo.strip():
            raise ValueError(
                f"El indicador '{self.clave}' se declara estimado y no declara método. "
                f"Un número derivado que no se puede reproducir no es un dato, es una "
                f"afirmación."
            )

        # La guardia contra veredictos de esquema_informe.py, aplicada a todo el
        # texto libre que llega a la pantalla. Frena que un "apto para vivienda"
        # se cuele en un método o una limitación.
        for campo in ("etiqueta", "metodo", "motivo", "limitaciones"):
            validar_texto_descriptivo(getattr(self, campo), f"indicador.{self.clave}.{campo}")

        return self


class Modulo(BaseModel):
    """Un componente ambiental: hidrología, suelo o biodiversidad.

    No lleva puntaje. Ver la nota sobre `salud_global` en `contrato.py`.
    """

    model_config = ConfigDict(frozen=True)

    clave: str
    titulo: str
    icono: str = ""
    indicadores: list[Indicador] = Field(default_factory=list)
    #: Qué no cubre este módulo. Se muestra al pie de la tarjeta.
    limitaciones: str = ""

    def cuenta_por_procedencia(self) -> dict[str, int]:
        """Cuántos indicadores hay de cada procedencia."""
        cuenta = {p.value: 0 for p in Procedencia}
        for indicador in self.indicadores:
            cuenta[indicador.procedencia.value] += 1
        return cuenta


class CoberturaDatos(BaseModel):
    """Cuánto se sabe realmente de esta parcela.

    Reemplaza al `salud_global: 82` / "Buena Salud Ambiental" de la versión
    anterior, que era un promedio ponderado con pesos elegidos a mano (0,35 /
    0,35 / 0,30) sobre indicadores en unidades distintas, presentado con dos
    cifras de precisión y traducido a un veredicto verbal.

    `esquema_informe.py` prohíbe exactamente eso: *"No existe un campo apto/no
    apto, PASS/FAIL ni equivalente. El informe describe condiciones con rangos y
    dice qué requiere confirmación."*

    Esto, en cambio, es una cuenta verificable: de los indicadores que el sistema
    sabe pedir, cuántos pudo conseguir. Le dice a la persona algo cierto y útil
    —cuánto de esta parcela está efectivamente observado— y mejora solo a medida
    que se suman fuentes reales.
    """

    model_config = ConfigDict(frozen=True)

    total: int
    medidos: int
    estimados: int
    simulados: int
    no_disponibles: int

    @property
    def pct_con_dato(self) -> int:
        """Porcentaje de indicadores que tienen algún valor respaldado."""
        if self.total == 0:
            return 0
        return round(100 * (self.medidos + self.estimados) / self.total)

    @classmethod
    def desde_modulos(cls, modulos: list[Modulo]) -> CoberturaDatos:
        """Suma la cobertura de todos los módulos."""
        acumulado: dict[str, int] = {p.value: 0 for p in Procedencia}
        for modulo in modulos:
            for clave, valor in modulo.cuenta_por_procedencia().items():
                acumulado[clave] += valor
        return cls(
            total=sum(acumulado.values()),
            medidos=acumulado[Procedencia.MEDIDO.value],
            estimados=acumulado[Procedencia.ESTIMADO.value],
            simulados=acumulado[Procedencia.SIMULADO.value],
            no_disponibles=acumulado[Procedencia.NO_DISPONIBLE.value],
        )

    def a_diccionario(self) -> dict[str, Any]:
        return {**self.model_dump(), "pct_con_dato": self.pct_con_dato}


def indicador_no_disponible(
    clave: str,
    etiqueta: str,
    motivo: str,
    unidad: str = "",
) -> Indicador:
    """Atajo para el caso más frecuente y más importante: todavía no hay dato.

    Existe para que declarar un hueco sea más corto que inventarlo.
    """
    return Indicador(
        clave=clave,
        etiqueta=etiqueta,
        valor=None,
        unidad=unidad,
        procedencia=Procedencia.NO_DISPONIBLE,
        motivo=motivo,
    )
