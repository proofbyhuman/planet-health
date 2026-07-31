"""Las garantías que este paquete tiene que sostener.

El más importante es `test_los_bytes_canonicos_estan_clavados`. Si esa
serialización cambia, **todas las firmas emitidas hasta ese momento dejan de
verificar**, en todos los proyectos que usen este paquete, y sin ningún aviso.
Es la razón por la que este código se extrajo a un paquete único en lugar de
copiarse.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from contrato_informe import (
    NO_DETERMINADO,
    Componente,
    ErrorDeFirma,
    Informe,
    SeccionInforme,
    Ubicacion,
    VeredictoProhibidoError,
    armar_documento_firmado,
    firmar_informe,
    generar_par_de_claves,
    huella_de_clave,
    json_canonico,
    resolver_ubicacion,
    validar_texto_descriptivo,
    verificar_documento,
)


@pytest.fixture
def clave(tmp_path):
    from contrato_informe import cargar_clave_privada

    privada, _ = generar_par_de_claves(
        tmp_path / "p.pem", tmp_path / "pub.pem", permitir_sin_cifrar=True
    )
    return cargar_clave_privada(privada)


@pytest.fixture
def informe():
    return Informe(
        tipo_informe="prueba",
        ubicacion=Ubicacion(lat=-41.1335, lon=-71.3103),
        secciones=[
            SeccionInforme(
                componente=Componente.AGUA,
                titulo="Hidrología",
                resumen_descriptivo="Precipitación acumulada de 1034 mm en los últimos 365 días.",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Serialización canónica: lo que no se puede romper nunca
# ---------------------------------------------------------------------------


def test_los_bytes_canonicos_estan_clavados():
    """Un caso conocido, con su resultado exacto escrito a mano.

    Si este test falla, no se arregla cambiando el valor esperado: se revisa qué
    tocó la serialización y se revierte. Cambiarla invalida todas las firmas
    emitidas antes.
    """
    entrada = {"b": 2, "a": {"z": [3, 1], "y": "ñandú"}, "c": None}
    assert json_canonico(entrada) == b'{"a":{"y":"\xc3\xb1and\xc3\xba","z":[3,1]},"b":2,"c":null}'


def test_el_orden_de_las_claves_no_cambia_los_bytes():
    uno = {"alfa": 1, "beta": {"x": 1, "a": 2}}
    otro = {"beta": {"a": 2, "x": 1}, "alfa": 1}
    assert json_canonico(uno) == json_canonico(otro)


def test_coincide_con_la_serializacion_del_verificador_independiente():
    """`verificar_informe.py` reimplementa esto sin importar el paquete.

    Es a propósito: quien recibe un documento no tiene que instalar ni confiar en
    el software de quien lo emitió. Pero entonces las dos implementaciones tienen
    que coincidir byte por byte, y eso se comprueba acá.
    """

    def como_el_verificador(dato):
        return json.dumps(
            dato, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")

    for caso in [
        {"a": 1},
        {"texto": "acentuación y ñ"},
        {"anidado": {"lista": [1, {"b": 2, "a": 1}]}},
        {"nulo": None, "verdadero": True, "flotante": 0.5},
    ]:
        assert json_canonico(caso) == como_el_verificador(caso)


# ---------------------------------------------------------------------------
# La regla de no inventar
# ---------------------------------------------------------------------------


def test_sin_resolutor_la_ubicacion_queda_sin_determinar():
    ubicacion = resolver_ubicacion(-41.1335, -71.3103)
    assert ubicacion.partido == NO_DETERMINADO
    assert ubicacion.provincia == NO_DETERMINADO
    assert ubicacion.lat == -41.1335  # la coordenada sí es real


@pytest.mark.parametrize(
    "resolutor",
    [
        lambda lat, lon: (_ for _ in ()).throw(RuntimeError("servicio caído")),
        lambda lat, lon: {},
        lambda lat, lon: "esto no es un diccionario",
        lambda lat, lon: {"partido": "", "provincia": "   ", "pais": "N/A"},
    ],
    ids=["falla", "vacío", "mal formado", "rellenos típicos"],
)
def test_ningun_resolutor_roto_produce_un_lugar_inventado(resolutor):
    """El bug del código viejo: escribía "TRES ARROYOS" para cualquier coordenada."""
    ubicacion = resolver_ubicacion(-41.0, -71.0, resolutor)
    assert ubicacion.descripcion_administrativa() == NO_DETERMINADO


def test_la_fuente_registra_que_paso():
    """No alcanza con "no determinado": hay que poder saber por qué."""
    def falla(lat, lon):
        raise TimeoutError("sin respuesta")

    assert "TimeoutError" in resolver_ubicacion(-41.0, -71.0, falla).fuente_ubicacion
    assert resolver_ubicacion(-41.0, -71.0).fuente_ubicacion == "no consultada"


def test_una_coordenada_imposible_se_rechaza():
    with pytest.raises(ValueError, match="Latitud fuera de rango"):
        Ubicacion(lat=999, lon=0)


# ---------------------------------------------------------------------------
# La guardia contra veredictos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    ["el suelo es apto para vivienda", "PASS", "certificamos la calidad", "parcela habilitada"],
)
def test_se_rechaza_un_juicio_de_aptitud(texto):
    with pytest.raises(VeredictoProhibidoError):
        validar_texto_descriptivo(texto, "prueba")


def test_una_descripcion_normal_pasa():
    validar_texto_descriptivo(
        "La precipitación acumulada fue de 1034 mm, por encima de la media de la década.",
        "prueba",
    )


def test_la_guardia_alcanza_a_las_secciones_del_informe():
    with pytest.raises(VeredictoProhibidoError):
        SeccionInforme(
            componente=Componente.SUELO,
            titulo="Suelo",
            resumen_descriptivo="El suelo resulta apto para el cultivo.",
        )


# ---------------------------------------------------------------------------
# Firma y verificación, en tres niveles
# ---------------------------------------------------------------------------


def test_firmar_y_verificar_da_los_tres_niveles(clave, informe):
    documento = armar_documento_firmado(informe, firmar_informe(informe, clave))

    sin_ancla = verificar_documento(documento)
    assert sin_ancla.integridad_ok is True
    assert sin_ancla.firma_ok is True
    # Lo importante: sin huella externa no se afirma nada sobre la autoría.
    assert sin_ancla.clave_anclada is None
    assert sin_ancla.documento_intacto is True

    con_ancla = verificar_documento(documento, huella_de_clave(clave.public_key()))
    assert con_ancla.clave_anclada is True


def test_una_huella_ajena_no_ancla(clave, informe):
    documento = armar_documento_firmado(informe, firmar_informe(informe, clave))
    resultado = verificar_documento(documento, "AAAA-BBBB-CCCC-DDDD")
    assert resultado.clave_anclada is False
    assert resultado.documento_intacto is True  # intacto, pero no de quien se espera


def test_modificar_una_letra_rompe_integridad_y_firma(clave, informe):
    documento = armar_documento_firmado(informe, firmar_informe(informe, clave))
    documento["informe"]["secciones"][0]["titulo"] = "Hidrologia"  # sin tilde

    resultado = verificar_documento(documento)
    assert resultado.integridad_ok is False
    assert resultado.firma_ok is False
    assert resultado.documento_intacto is False


def test_un_documento_mal_formado_no_explota(clave):
    resultado = verificar_documento({"cualquier": "cosa"})
    assert resultado.documento_intacto is False
    assert "no tiene la forma esperada" in resultado.mensajes[0]


def test_lo_que_se_firma_son_los_bytes_del_informe_no_su_hash(clave, informe):
    """Ed25519 ya hashea internamente. El SHA-256 se guarda solo para mostrarlo."""
    bloque = firmar_informe(informe, clave)
    mensaje = json_canonico(informe.a_bloque_firmable())

    assert bloque.hash_sha256 == hashlib.sha256(mensaje).hexdigest()
    publica = Ed25519PublicKey.from_public_bytes(bytes.fromhex(bloque.clave_publica_hex))
    publica.verify(bytes.fromhex(bloque.firma_hex), mensaje)  # no lanza

    with pytest.raises(InvalidSignature):
        publica.verify(bytes.fromhex(bloque.firma_hex), bloque.hash_sha256.encode())


def test_la_huella_son_cuatro_grupos_de_cuatro(clave):
    import re

    assert re.fullmatch(r"[0-9A-F]{4}(-[0-9A-F]{4}){3}", huella_de_clave(clave.public_key()))


# ---------------------------------------------------------------------------
# Claves
# ---------------------------------------------------------------------------


def test_no_se_genera_una_clave_sin_cifrar_por_descuido(tmp_path):
    """Hay que pedirlo explícitamente."""
    with pytest.raises(ErrorDeFirma, match="passphrase"):
        generar_par_de_claves(tmp_path / "a.pem", tmp_path / "b.pem")


def test_no_se_sobrescribe_un_par_de_claves_existente(tmp_path):
    rutas = (tmp_path / "a.pem", tmp_path / "b.pem")
    generar_par_de_claves(*rutas, permitir_sin_cifrar=True)
    with pytest.raises(ErrorDeFirma, match="No se sobrescriben claves"):
        generar_par_de_claves(*rutas, permitir_sin_cifrar=True)


def test_una_clave_cifrada_no_se_abre_sin_la_contrasena(tmp_path):
    from contrato_informe import cargar_clave_privada

    privada, _ = generar_par_de_claves(
        tmp_path / "a.pem", tmp_path / "b.pem", passphrase=b"secreta"
    )
    assert cargar_clave_privada(privada, b"secreta") is not None
    with pytest.raises(ErrorDeFirma):
        cargar_clave_privada(privada)


# ---------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------


def test_el_informe_no_tiene_puntaje_ni_veredicto(informe):
    """Regla de diseño: describe condiciones, no emite un apto/no apto."""
    diccionario = informe.a_diccionario()
    for prohibido in ("puntaje", "score", "apto", "resultado", "estado_general"):
        assert prohibido not in diccionario


def test_es_ficticio_viaja_en_el_bloque_firmado(informe):
    """La bandera se firma junto con el resto: no se puede quitar sin romper la firma."""
    assert informe.a_bloque_firmable()["es_ficticio"] is False
    informe.es_ficticio = True
    assert informe.a_bloque_firmable()["es_ficticio"] is True
