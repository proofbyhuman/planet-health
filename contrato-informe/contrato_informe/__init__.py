"""Contrato de datos, sello de integridad y regla de no inventar, para informes ambientales.

Tres piezas que resuelven un problema que aparece en cualquier herramienta que
informe sobre el ambiente, y que es más de diseño que de programación: **qué
hacer cuando no se sabe algo**.

- `esquema_informe` — la forma de un informe descriptivo, con serialización
  canónica reproducible, el valor obligatorio `"no determinado"`, la bandera
  `es_ficticio` y una guardia que rechaza activamente palabras de veredicto
  ("apto", "garantizamos") en los textos.
- `motor_firma` — Ed25519 + SHA-256, y una verificación que separa las tres cosas
  que se suelen confundir en un solo "válido": integridad, firma y clave anclada
  a una huella publicada por fuera del documento.
- `ubicacion` — resolución del encuadre administrativo de una coordenada, con la
  regla dura de que una consulta fallida devuelve `"no determinado"` y nunca un
  nombre de lugar de relleno.

## De dónde sale esto

Se escribió dentro de `consultora_ambiental` y se extrajo para que lo pueda usar
también Planet Health, que es software libre. La razón de la extracción no fue
solo de licencia: los bytes que produce `json_canonico()` **tienen que ser
idénticos** en todo lo que firme o verifique estos documentos. Dos copias del
mismo código terminan divergiendo, y el día que divergen todas las firmas
emitidas antes dejan de verificar sin que nada avise.

El origen se nota en las decisiones. Cada regla de acá viene de un error concreto
del código anterior, y está documentada en el módulo que la corrige. El más claro
está en `ubicacion.py`: había un valor por defecto donde no debía haber ninguno, y
el sistema firmaba criptográficamente un dato falso.

## Qué prueba la firma

Que nadie tocó el documento después de emitirlo. **Nada** sobre si su contenido es
correcto: un sello opera sobre bytes y no sabe qué dicen. Y no es firma digital en
los términos de la Ley 25.506 argentina, que exige un certificador licenciado.

## Uso

    from contrato_informe import Informe, Ubicacion, firmar_informe, verificar_documento
"""

from .esquema_informe import (
    ALCANCE_POR_DEFECTO,
    LIMITACIONES_POR_DEFECTO,
    NO_DETERMINADO,
    VERSION_ESQUEMA,
    Componente,
    FuenteDatos,
    Indicador,
    Informe,
    NivelConfianza,
    SeccionInforme,
    Ubicacion,
    VeredictoProhibidoError,
    Verificacion,
    ahora_utc_iso,
    json_canonico,
    nuevo_id_informe,
    validar_texto_descriptivo,
)
from .motor_firma import (
    ALGORITMO,
    BloqueFirma,
    ErrorDeFirma,
    ResultadoVerificacion,
    armar_documento_firmado,
    calcular_hash,
    cargar_clave_privada,
    cargar_clave_publica,
    cargar_documento_firmado,
    clave_publica_hex,
    firmar_informe,
    generar_par_de_claves,
    guardar_documento_firmado,
    huella_de_clave,
    nombre_archivo_informe,
    verificar_documento,
)
from .ubicacion import resolutor_georef_ar, resolver_ubicacion

__version__ = "0.1.0"

__all__ = [
    "ALCANCE_POR_DEFECTO",
    "ALGORITMO",
    "LIMITACIONES_POR_DEFECTO",
    "NO_DETERMINADO",
    "VERSION_ESQUEMA",
    "BloqueFirma",
    "Componente",
    "ErrorDeFirma",
    "FuenteDatos",
    "Indicador",
    "Informe",
    "NivelConfianza",
    "ResultadoVerificacion",
    "SeccionInforme",
    "Ubicacion",
    "VeredictoProhibidoError",
    "Verificacion",
    "ahora_utc_iso",
    "armar_documento_firmado",
    "calcular_hash",
    "cargar_clave_privada",
    "cargar_clave_publica",
    "cargar_documento_firmado",
    "clave_publica_hex",
    "firmar_informe",
    "generar_par_de_claves",
    "guardar_documento_firmado",
    "huella_de_clave",
    "json_canonico",
    "nombre_archivo_informe",
    "nuevo_id_informe",
    "resolutor_georef_ar",
    "resolver_ubicacion",
    "validar_texto_descriptivo",
    "verificar_documento",
]
