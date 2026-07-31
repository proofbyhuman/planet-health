"""Registros firmados: almacén, firma Ed25519 y compatibilidad con el verificador.

El test que importa de verdad es
`test_el_verificador_independiente_valida_un_registro`: reimplementa la lógica de
`repo_publico/verificar_informe.py` sin importar nada del proyecto, igual que
haría alguien que recibe un documento y no confía en nuestro software. Si ese
test falla, el registro dejó de ser verificable por terceros, que es lo único que
hace útil a la firma.
"""

from __future__ import annotations

import hashlib
import json
import re

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.testclient import TestClient

from app.almacen import AlmacenRegistros, RegistroDuplicado
from app.dependencias import obtener_almacen
from app.firma import GestorDeFirma
from app.main import app

INFORME_MINIMO = {
    "version_contrato": "3.0.0",
    "coordenadas": {"lat": -41.1335, "lon": -71.3103},
    "modulos": [
        {
            "clave": "hidrologia",
            "titulo": "Hidrología",
            "indicadores": [
                {
                    "clave": "precipitacion_365d",
                    "etiqueta": "Precipitación (365 días)",
                    "valor": 1034.3,
                    "unidad": "mm",
                    "procedencia": "medido",
                }
            ],
        }
    ],
}


@pytest.fixture
def gestor(tmp_path, monkeypatch):
    """Un gestor de firma con claves propias en un directorio temporal."""
    monkeypatch.setattr("app.firma.RUTA_PRIVADA", tmp_path / "privada.pem")
    monkeypatch.setattr("app.firma.RUTA_PUBLICA", tmp_path / "publica.pem")
    monkeypatch.delenv("PLANET_HEALTH_PASSPHRASE", raising=False)
    return GestorDeFirma()


@pytest.fixture
def almacen(tmp_path):
    return AlmacenRegistros(tmp_path / "registros.db")


@pytest.fixture
def api(almacen):
    app.dependency_overrides[obtener_almacen] = lambda: almacen
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# El criterio de aceptación de la fase
# ---------------------------------------------------------------------------


def json_canonico_independiente(dato: object) -> bytes:
    """Copia literal de `json_canonico()` de verificar_informe.py.

    Se reimplementa a propósito en lugar de importarla. Ese script es autónomo
    para que quien recibe un documento no tenga que instalar ni confiar en el
    software de quien lo emitió; si el test importara nuestra serialización, un
    cambio en ella pasaría desapercibido y rompería a todos los verificadores de
    afuera sin que nada avisara acá.
    """
    return json.dumps(dato, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def test_el_verificador_independiente_valida_un_registro(gestor):
    """Los tres niveles de verificar_informe.py, sin importar nada del proyecto."""
    documento = gestor.firmar(INFORME_MINIMO, "PH-prueba")

    assert set(documento) == {"informe", "firma"}
    firma = documento["firma"]
    assert firma["algoritmo"] == "Ed25519 + SHA-256"

    mensaje = json_canonico_independiente(documento["informe"])

    # Nivel 1: integridad.
    assert hashlib.sha256(mensaje).hexdigest() == firma["hash_sha256"]

    # Nivel 2: firma. Se verifica sobre los bytes del informe, no sobre el hash:
    # Ed25519 hashea internamente.
    clave = Ed25519PublicKey.from_public_bytes(bytes.fromhex(firma["clave_publica_hex"]))
    clave.verify(bytes.fromhex(firma["firma_hex"]), mensaje)

    # Nivel 3: ancla. La huella del documento es la que se compara contra la
    # publicada por fuera.
    assert firma["huella_clave"] == gestor.huella()


def test_una_modificacion_rompe_las_dos_comprobaciones(gestor):
    documento = gestor.firmar(INFORME_MINIMO, "PH-prueba")
    documento["informe"]["modulos"][0]["indicadores"][0]["valor"] = 99999

    mensaje = json_canonico_independiente(documento["informe"])
    assert hashlib.sha256(mensaje).hexdigest() != documento["firma"]["hash_sha256"]

    clave = Ed25519PublicKey.from_public_bytes(
        bytes.fromhex(documento["firma"]["clave_publica_hex"])
    )
    with pytest.raises(InvalidSignature):
        clave.verify(bytes.fromhex(documento["firma"]["firma_hex"]), mensaje)


def test_la_huella_tiene_el_formato_de_claves_publicas_md(gestor):
    """Cuatro grupos de cuatro dígitos hexadecimales, para compararla a ojo.

    Se comprueba con una expresión regular y no con `isupper()`: un grupo puede
    salir todo numérico —"8183"— y `isupper()` devuelve False cuando no hay
    ninguna letra en la cadena.
    """
    huella = gestor.huella()
    assert re.fullmatch(r"[0-9A-F]{4}(-[0-9A-F]{4}){3}", huella), huella


# ---------------------------------------------------------------------------
# Almacén
# ---------------------------------------------------------------------------


def test_un_registro_firmado_no_se_sobrescribe(almacen, gestor):
    """Misma regla que guardar_documento_firmado() en motor_firma.py."""
    documento = gestor.firmar(INFORME_MINIMO, "PH-1")
    almacen.guardar("PH-1", documento, -41.0, -71.0)
    with pytest.raises(RegistroDuplicado):
        almacen.guardar("PH-1", documento, -41.0, -71.0)


def test_el_documento_vuelve_byte_por_byte(almacen, gestor):
    """Si el almacén reordenara o normalizara algo, la firma dejaría de verificar."""
    documento = gestor.firmar(INFORME_MINIMO, "PH-1")
    almacen.guardar("PH-1", documento, -41.0, -71.0)

    recuperado = almacen.obtener("PH-1")
    assert json_canonico_independiente(recuperado["informe"]) == json_canonico_independiente(
        documento["informe"]
    )
    assert recuperado["firma"] == documento["firma"]


def test_obtener_lo_que_no_existe(almacen):
    assert almacen.obtener("PH-inexistente") is None


def test_listar_filtra_por_recuadro(almacen, gestor):
    for i, (lat, lon) in enumerate([(-41.0, -71.0), (-34.0, -60.0), (10.0, 20.0)]):
        almacen.guardar(f"PH-{i}", gestor.firmar(INFORME_MINIMO, f"PH-{i}"), lat, lon)

    todos = almacen.listar()
    assert len(todos) == 3

    patagonia = almacen.listar(recuadro=(-42.0, -40.0, -72.0, -70.0))
    assert [r["id"] for r in patagonia] == ["PH-0"]


def test_el_almacen_no_expone_forma_de_borrar():
    """No hay borrado ni actualización, y es deliberado."""
    metodos = {m for m in dir(AlmacenRegistros) if not m.startswith("_")}
    assert metodos == {"guardar", "obtener", "listar", "contar"}


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


def test_crear_registro_devuelve_el_documento_firmado(api):
    respuesta = api.post("/api/v1/registros", json={"informe": INFORME_MINIMO})
    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["ya_existia"] is False
    assert cuerpo["documento"]["firma"]["algoritmo"] == "Ed25519 + SHA-256"


def test_registrar_dos_veces_lo_mismo_no_duplica(api):
    """El id sale del contenido: dos clics en el botón no ensucian la cola."""
    primero = api.post("/api/v1/registros", json={"informe": INFORME_MINIMO})
    segundo = api.post("/api/v1/registros", json={"informe": INFORME_MINIMO})

    assert primero.status_code == 201
    assert segundo.status_code == 200
    assert segundo.json()["ya_existia"] is True
    assert api.get("/api/v1/registros").json()["total"] == 1


def test_observaciones_distintas_son_registros_distintos(api):
    api.post("/api/v1/registros", json={"informe": INFORME_MINIMO, "observacion": "una"})
    api.post("/api/v1/registros", json={"informe": INFORME_MINIMO, "observacion": "otra"})
    assert api.get("/api/v1/registros").json()["total"] == 2


def test_un_informe_sin_coordenadas_se_rechaza(api):
    assert api.post("/api/v1/registros", json={"informe": {"modulos": []}}).status_code == 422


def test_descargar_un_registro_inexistente(api):
    assert api.get("/api/v1/registros/PH-nada").status_code == 404


def test_verificacion_separa_los_tres_niveles(api):
    id_registro = api.post("/api/v1/registros", json={"informe": INFORME_MINIMO}).json()[
        "documento"
    ]["firma"]["id_informe"]

    sin_ancla = api.get(f"/api/v1/registros/{id_registro}/verificacion").json()
    assert sin_ancla["integridad_ok"] is True
    assert sin_ancla["firma_ok"] is True
    # Lo importante: sin huella externa NO dice que la autoría esté probada.
    assert sin_ancla["clave_anclada"] is None
    assert "servidor que firmó" in sin_ancla["advertencia"]

    con_ancla = api.get(
        f"/api/v1/registros/{id_registro}/verificacion",
        params={"huella_esperada": sin_ancla["huella_clave"]},
    ).json()
    assert con_ancla["clave_anclada"] is True

    con_ancla_ajena = api.get(
        f"/api/v1/registros/{id_registro}/verificacion",
        params={"huella_esperada": "AAAA-BBBB-CCCC-DDDD"},
    ).json()
    assert con_ancla_ajena["clave_anclada"] is False


def test_filtrar_por_zona_exige_los_cuatro_limites(api):
    assert api.get("/api/v1/registros", params={"lat_min": -42}).status_code == 422
