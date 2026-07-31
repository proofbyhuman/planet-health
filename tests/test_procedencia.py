"""El invariante central: no se puede emitir un número sin decir de dónde salió.

Si algún test de este archivo empieza a fallar porque "molesta", la respuesta
correcta es casi siempre arreglar la fuente que lo dispara, no relajar la regla.
Estas validaciones son lo único que impide que el proyecto vuelva a mostrar
`Math.random()` bajo el logo de una institución científica.
"""

from __future__ import annotations

import pytest
from contrato_informe.esquema_informe import VeredictoProhibidoError
from pydantic import ValidationError

from app.procedencia import (
    CoberturaDatos,
    Fuente,
    Indicador,
    Modulo,
    Procedencia,
    indicador_no_disponible,
)

FUENTE = Fuente(nombre="Open-Meteo", licencia="CC-BY-4.0")


def test_medido_con_fuente_se_construye():
    ind = Indicador(
        clave="precipitacion_365d",
        etiqueta="Precipitación (365 días)",
        valor=1034.3,
        unidad="mm",
        procedencia=Procedencia.MEDIDO,
        fuente=FUENTE,
    )
    assert ind.valor == 1034.3
    assert ind.procedencia is Procedencia.MEDIDO


def test_medido_sin_fuente_es_rechazado():
    """'Medido' significa que alguien lo midió: hay que poder decir quién."""
    with pytest.raises(ValueError, match="no cita fuente"):
        Indicador(clave="ph", etiqueta="pH", valor=6.4, procedencia=Procedencia.MEDIDO)


def test_estimado_sin_metodo_es_rechazado():
    """Un número derivado que no se puede reproducir no es un dato."""
    with pytest.raises(ValueError, match="no declara método"):
        Indicador(
            clave="shannon",
            etiqueta="Índice de Shannon",
            valor=4.45,
            procedencia=Procedencia.ESTIMADO,
            fuente=FUENTE,
        )


def test_no_disponible_con_valor_es_rechazado():
    """Este es el caso que el proyecto rellenaba con ruido pseudoaleatorio."""
    with pytest.raises(ValueError, match="no tiene valor de relleno"):
        Indicador(
            clave="ndvi",
            etiqueta="NDVI",
            valor=0.72,
            procedencia=Procedencia.NO_DISPONIBLE,
            motivo="no hay fuente",
        )


def test_no_disponible_sin_motivo_es_rechazado():
    with pytest.raises(ValueError, match="no dice por qué"):
        Indicador(clave="ndvi", etiqueta="NDVI", procedencia=Procedencia.NO_DISPONIBLE)


def test_valor_nulo_con_procedencia_afirmativa_es_rechazado():
    with pytest.raises(ValueError, match="la procedencia es no_disponible"):
        Indicador(
            clave="ph", etiqueta="pH", valor=None, procedencia=Procedencia.MEDIDO, fuente=FUENTE
        )


@pytest.mark.parametrize(
    "campo,texto",
    [
        ("limitaciones", "el suelo es apto para vivienda"),
        ("metodo", "se garantiza la exactitud del cálculo"),
        ("motivo", "la parcela fue aprobada"),
        ("etiqueta", "Aptitud del suelo"),
    ],
)
def test_la_guardia_contra_veredictos_alcanza_a_todo_el_texto(campo, texto):
    """Reutiliza `validar_texto_descriptivo()` de esquema_informe.py.

    Un informe descriptivo no emite juicios de aptitud, y el texto libre de un
    indicador llega a la pantalla igual que el número.

    Se espera `ValidationError` y no `VeredictoProhibidoError`: pydantic envuelve
    lo que levanta un validador. La prueba de que la guardia corrió es que el
    mensaje nombra la palabra de veredicto encontrada, que es el texto que escribe
    `validar_texto_descriptivo()` y nada más de esta capa produce.
    """
    kwargs = {
        "clave": "x",
        "etiqueta": "X",
        "valor": 1,
        "procedencia": Procedencia.MEDIDO,
        "fuente": FUENTE,
        campo: texto,
    }
    if campo == "motivo":
        kwargs.update(valor=None, procedencia=Procedencia.NO_DISPONIBLE)

    with pytest.raises(ValidationError) as excepcion:
        Indicador(**kwargs)
    assert "palabra de veredicto" in str(excepcion.value)
    assert f"indicador.x.{campo}" in str(excepcion.value)


def test_validation_error_de_pydantic_sigue_siendo_un_value_error():
    """Deja anclada la razón por la que el test de arriba no atrapa la excepción propia.

    Si una versión futura de pydantic dejara de derivar de ValueError, varios
    tests de este archivo pasarían a atrapar la excepción equivocada sin avisar.
    """
    assert issubclass(ValidationError, ValueError)
    assert issubclass(VeredictoProhibidoError, ValueError)


def test_atajo_de_no_disponible():
    ind = indicador_no_disponible("ndvi", "NDVI", "requiere credenciales de Copernicus")
    assert ind.valor is None
    assert ind.procedencia is Procedencia.NO_DISPONIBLE
    assert "Copernicus" in ind.motivo


def test_cobertura_cuenta_lo_que_hay():
    modulo = Modulo(
        clave="m",
        titulo="M",
        indicadores=[
            Indicador(
                clave="a", etiqueta="A", valor=1, procedencia=Procedencia.MEDIDO, fuente=FUENTE
            ),
            Indicador(
                clave="b", etiqueta="B", valor=2, procedencia=Procedencia.MEDIDO, fuente=FUENTE
            ),
            Indicador(
                clave="c",
                etiqueta="C",
                valor=3,
                procedencia=Procedencia.ESTIMADO,
                fuente=FUENTE,
                metodo="a + b",
            ),
            indicador_no_disponible("d", "D", "sin fuente"),
        ],
    )
    cobertura = CoberturaDatos.desde_modulos([modulo])
    assert cobertura.total == 4
    assert cobertura.medidos == 2
    assert cobertura.estimados == 1
    assert cobertura.no_disponibles == 1
    assert cobertura.pct_con_dato == 75


def test_cobertura_vacia_no_divide_por_cero():
    assert CoberturaDatos.desde_modulos([]).pct_con_dato == 0
