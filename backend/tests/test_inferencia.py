"""Inferencia como subproceso.

Ninguno de estos tests carga torch ni corre un modelo: se simula el subproceso.
Correr DeepForest de verdad tarda medio minuto largo y baja pesos de la red, así
que no puede estar en una suite que tiene que correr en segundos y sin conexión.

Lo que sí se comprueba, y es lo importante de la fase:
`test_el_backend_no_importa_torch`. Si alguien un día importa deepforest dentro
de `app/`, ese test falla y explica por qué no se hace.
"""

from __future__ import annotations

import json
import sys

import pytest
from fastapi.testclient import TestClient

from app.inferencia import EstadoTrabajo, MotorDeInferencia
from app.main import app
from app.rutas.inferencia import _modulo_desde_resultado

RESULTADO_DEEPFOREST = {
    "conteo_copas": 55,
    "detecciones": [{"caja": [330.1, 342.6, 373.8, 391.7], "puntaje": 0.799, "clase": "Tree"}],
    "puntaje_medio": 0.535,
    "umbral_puntaje": 0.3,
    "detecciones_descartadas_por_umbral": 0,
    "modelo": "weecology/deepforest-tree",
}


@pytest.fixture
def api():
    return TestClient(app)


# ---------------------------------------------------------------------------
# El aislamiento entre entornos, que es el punto de toda la fase
# ---------------------------------------------------------------------------


def test_el_backend_no_importa_torch():
    """FastAPI nunca carga torch ni deepforest.

    El `pyproject.toml` de consultora_ambiental ya había decidido que esos
    paquetes viven en entornos separados y se invocan como programas externos, y
    dejaba anotado que *"ese supuesto todavía NO fue probado"*. Este test lo
    prueba y lo mantiene probado.

    Importar torch en el proceso del servidor costaría segundos de arranque y
    unos 2 GB de memoria, y ensuciaría el venv del repo DeepForest al que se le
    mandan PRs.
    """
    # `app.main` ya está importado por el módulo de arriba.
    assert "torch" not in sys.modules
    assert "deepforest" not in sys.modules
    assert "pytorch_lightning" not in sys.modules


def test_las_capacidades_declaran_lo_que_falta(api):
    """Los modelos que no están se declaran, no se omiten."""
    capacidades = api.get("/api/v1/inferencia").json()
    assert set(capacidades) == {"canopia_deepforest", "fauna_megadetector", "bioacustica"}
    assert capacidades["fauna_megadetector"]["disponible"] is False
    assert "Math.random()" in capacidades["fauna_megadetector"]["motivo"]


# ---------------------------------------------------------------------------
# Traducción del resultado al contrato
# ---------------------------------------------------------------------------


def test_el_conteo_de_copas_sale_como_medido():
    modulo = _modulo_desde_resultado(RESULTADO_DEEPFOREST)
    copas = next(i for i in modulo.indicadores if i.clave == "copas_arboles")
    assert copas.valor == 55
    assert copas.procedencia.value == "medido"
    assert copas.fuente.nombre.startswith("DeepForest")
    assert "MIT" in copas.fuente.licencia


def test_la_confianza_media_sale_como_estimado_con_su_metodo():
    """Es un promedio calculado por nosotros, no algo que el modelo informe."""
    modulo = _modulo_desde_resultado(RESULTADO_DEEPFOREST)
    confianza = next(i for i in modulo.indicadores if i.clave == "confianza_canopia")
    assert confianza.procedencia.value == "estimado"
    assert "Promedio del puntaje" in confianza.metodo
    assert "no una medida de si acertó" in confianza.limitaciones


def test_sin_detecciones_la_confianza_queda_sin_dato():
    """Cero detecciones no es confianza cero: no hay sobre qué promediar."""
    vacio = {**RESULTADO_DEEPFOREST, "conteo_copas": 0, "detecciones": [], "puntaje_medio": None}
    modulo = _modulo_desde_resultado(vacio)

    copas = next(i for i in modulo.indicadores if i.clave == "copas_arboles")
    assert copas.valor == 0
    assert copas.procedencia.value == "medido"

    confianza = next(i for i in modulo.indicadores if i.clave == "confianza_canopia")
    assert confianza.valor is None
    assert confianza.procedencia.value == "no_disponible"


def test_la_limitacion_dice_que_el_conteo_no_es_de_arboles():
    """Se cuentan copas visibles desde arriba, no árboles de la parcela."""
    modulo = _modulo_desde_resultado(RESULTADO_DEEPFOREST)
    assert "sotobosque" in modulo.limitaciones
    assert "imágenes aéreas RGB" in modulo.limitaciones


# ---------------------------------------------------------------------------
# El motor: cómo degrada
# ---------------------------------------------------------------------------


class ProcesoSimulado:
    """Reemplazo de asyncio.subprocess.Process para los tests."""

    def __init__(self, salida: bytes, errores: bytes = b""):
        self._salida, self._errores = salida, errores

    async def communicate(self):
        return self._salida, self._errores


def _simular_subproceso(monkeypatch, salida: bytes, errores: bytes = b""):
    async def falso_exec(*args, **kwargs):
        return ProcesoSimulado(salida, errores)

    monkeypatch.setattr("asyncio.create_subprocess_exec", falso_exec)


async def test_una_inferencia_exitosa_guarda_el_resultado(tmp_path, monkeypatch):
    _simular_subproceso(monkeypatch, json.dumps(RESULTADO_DEEPFOREST).encode())
    motor = MotorDeInferencia()
    trabajo = motor.crear()
    imagen = tmp_path / "img.png"
    imagen.write_bytes(b"png falso")

    await motor.correr_canopia(trabajo, imagen, 0.3)

    assert trabajo.estado is EstadoTrabajo.LISTO
    assert trabajo.resultado["conteo_copas"] == 55
    # La imagen subida se borra: son datos de campo de alguien, no basura del
    # servidor.
    assert not imagen.exists()


async def test_una_salida_vacia_es_un_fallo_con_el_stderr(tmp_path, monkeypatch):
    _simular_subproceso(monkeypatch, b"", b"ModuleNotFoundError: No module named 'deepforest'")
    motor = MotorDeInferencia()
    trabajo = motor.crear()
    imagen = tmp_path / "img.png"
    imagen.write_bytes(b"x")

    await motor.correr_canopia(trabajo, imagen, 0.3)

    assert trabajo.estado is EstadoTrabajo.FALLO
    assert "deepforest" in trabajo.error


async def test_una_salida_que_no_es_json_no_rompe_nada(tmp_path, monkeypatch):
    _simular_subproceso(monkeypatch, b"esto no es json")
    motor = MotorDeInferencia()
    trabajo = motor.crear()
    imagen = tmp_path / "img.png"
    imagen.write_bytes(b"x")

    await motor.correr_canopia(trabajo, imagen, 0.3)
    assert trabajo.estado is EstadoTrabajo.FALLO
    assert "no es JSON" in trabajo.error


async def test_el_error_del_script_llega_como_fallo(tmp_path, monkeypatch):
    _simular_subproceso(monkeypatch, json.dumps({"error": "No existe la imagen"}).encode())
    motor = MotorDeInferencia()
    trabajo = motor.crear()
    imagen = tmp_path / "img.png"
    imagen.write_bytes(b"x")

    await motor.correr_canopia(trabajo, imagen, 0.3)
    assert trabajo.estado is EstadoTrabajo.FALLO
    assert trabajo.error == "No existe la imagen"


async def test_se_toma_la_ultima_linea_si_alguna_libreria_escribe_en_stdout(tmp_path, monkeypatch):
    """Torch y lightning escriben avisos; el JSON tiene que sobrevivir igual."""
    salida = b"Aviso de alguna libreria\n" + json.dumps(RESULTADO_DEEPFOREST).encode()
    _simular_subproceso(monkeypatch, salida)
    motor = MotorDeInferencia()
    trabajo = motor.crear()
    imagen = tmp_path / "img.png"
    imagen.write_bytes(b"x")

    await motor.correr_canopia(trabajo, imagen, 0.3)
    assert trabajo.estado is EstadoTrabajo.LISTO
    assert trabajo.resultado["conteo_copas"] == 55


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


def test_un_trabajo_inexistente_da_404(api):
    respuesta = api.get("/api/v1/inferencia/inf-nada")
    assert respuesta.status_code == 404
    assert "se pierden si el servidor se reinicia" in respuesta.json()["detail"]


def test_se_rechaza_un_archivo_que_no_es_imagen(api, monkeypatch):
    monkeypatch.setattr(
        "app.rutas.inferencia.motor_de_inferencia.disponible", lambda: (True, "")
    )
    respuesta = api.post(
        "/api/v1/inferencia/canopia",
        files={"imagen": ("notas.txt", b"hola", "text/plain")},
    )
    assert respuesta.status_code == 415


def test_sin_entorno_de_deepforest_responde_503_y_no_500(api, monkeypatch):
    """El servicio anda; esta capacidad no está montada. Son cosas distintas."""
    monkeypatch.setattr(
        "app.rutas.inferencia.motor_de_inferencia.disponible",
        lambda: (False, "No se encontró el intérprete de Python del entorno de DeepForest"),
    )
    respuesta = api.post(
        "/api/v1/inferencia/canopia",
        files={"imagen": ("a.png", b"\x89PNG", "image/png")},
    )
    assert respuesta.status_code == 503
    assert "intérprete" in respuesta.json()["detail"]
