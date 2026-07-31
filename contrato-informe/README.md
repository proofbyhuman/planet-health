# contrato-informe

Contrato de datos, sello de integridad Ed25519 y regla de no inventar valores,
para informes ambientales descriptivos.

Tres módulos, 989 líneas, una sola dependencia (`cryptography`).

```bash
pip install -e .            # núcleo: esquema y firma
pip install -e ".[ubicacion]"   # + resolución de ubicación contra el IGN
```

## Qué resuelve

Un problema que aparece en cualquier herramienta que informe sobre el ambiente, y
que es más de diseño que de programación: **qué hacer cuando no se sabe algo**.

| Módulo | Qué aporta |
|---|---|
| `esquema_informe` | Forma del informe, serialización canónica reproducible, `NO_DETERMINADO` obligatorio, bandera `es_ficticio`, y una guardia que rechaza palabras de veredicto ("apto", "garantizamos") en los textos |
| `motor_firma` | Ed25519 + SHA-256, y una verificación que separa integridad, firma y clave anclada en lugar de mezclarlas en un solo "válido" |
| `ubicacion` | Encuadre administrativo de una coordenada, con la regla dura de que una consulta fallida devuelve `"no determinado"` y nunca un nombre de relleno |

## Las tres reglas

**1. Sin veredicto.** No existe un campo apto/no apto, PASS/FAIL ni equivalente.
El informe describe condiciones y dice qué requiere confirmación.
`validar_texto_descriptivo()` rechaza activamente las palabras de juicio para que
no se cuelen por descuido.

**2. Nada de valores por defecto.** Si un dato no se pudo determinar de forma
confiable, el valor es literalmente `"no determinado"`. Nunca un relleno
verosímil, nunca el último que funcionó, nunca una constante del código.

**3. La firma prueba integridad, no autoría ni verdad.**
`verificar_documento()` devuelve tres resultados independientes:

| Nivel | Qué comprueba | Contra qué |
|---|---|---|
| Integridad | El contenido coincide con el hash firmado | Datos de **adentro** del archivo |
| Firma | La firma es válida para ese contenido y esa clave | Datos de **adentro** del archivo |
| Ancla | La clave es la que el emisor publicó | Una huella traída de **afuera** |

Los dos primeros no dicen nada sobre quién emitió el documento: cualquiera puede
generar un par de claves y firmar lo que quiera. Por eso `clave_anclada` vale
`None` —y no `True`— cuando no se pasó una huella externa.

Nada de esto dice si el contenido es **correcto**. Un sello opera sobre bytes y no
sabe qué dicen. Y no es firma digital en los términos de la Ley 25.506 argentina,
que exige un certificador licenciado.

## Uso

```python
from contrato_informe import (
    Informe, Ubicacion, SeccionInforme, Componente,
    generar_par_de_claves, cargar_clave_privada,
    firmar_informe, armar_documento_firmado, verificar_documento,
)

informe = Informe(
    tipo_informe="parcela",
    ubicacion=Ubicacion(lat=-41.1335, lon=-71.3103),
    secciones=[SeccionInforme(
        componente=Componente.AGUA,
        titulo="Hidrología",
        resumen_descriptivo="Precipitación acumulada de 1034 mm en los últimos 365 días.",
    )],
)

clave = cargar_clave_privada("privada.pem", b"contraseña")
documento = armar_documento_firmado(informe, firmar_informe(informe, clave))

resultado = verificar_documento(documento, huella_de_confianza="A1B2-C3D4-E5F6-0718")
print(resultado.resumen())
```

También se puede firmar un diccionario cualquiera y conservar la forma exterior
`{"informe": ..., "firma": {...}}`, que es lo que hace Planet Health para no
aplanar su propio contrato. Ver `planet-health/backend/app/firma.py`.

## De dónde salió

Se escribió dentro de `consultora_ambiental` y se extrajo cuando Planet Health
—software libre— empezó a depender de él. La razón no fue solo la licencia: los
bytes que produce `json_canonico()` **tienen que ser idénticos** en todo lo que
firme o verifique estos documentos. Dos copias del mismo código terminan
divergiendo, y el día que divergen, todas las firmas emitidas antes dejan de
verificar sin que nada avise.

Por eso `tests/test_nucleo.py` tiene un test con los bytes canónicos escritos a
mano para un caso conocido. Si ese test falla, no se arregla cambiando el valor
esperado: se revisa qué tocó la serialización y se revierte.

El origen se nota en las decisiones: cada regla viene de un error concreto del
código anterior, documentado en el módulo que lo corrige. El más claro está en
`ubicacion.py` — había un valor por defecto donde no debía haber ninguno, y el
sistema terminaba firmando criptográficamente un dato falso.

## Quién lo usa

- **Planet Health** (AGPL-3.0) — firma los informes de parcela.
- **consultora_ambiental** (propietario, uso interno) — todavía tiene su propia
  copia de estos tres módulos. Hay que apuntarlo a este paquete y borrar la copia;
  ver la nota de abajo.

La licencia MIT es lo que permite que los dos lo usen: una copyleft obligaría al
segundo a abrirse.

## Pendiente

`consultora_ambiental` sigue con su copia de `esquema_informe.py`,
`motor_firma.py` y `ubicacion.py` dentro de `informe_parcela/`. No se tocó porque
ese repositorio tiene trabajo sin commitear —unas 2100 líneas, incluida la
implementación de `resolutor_georef_ar()` en el propio `ubicacion.py`— y no
corresponde refactorizar encima de eso.

Cuando ese trabajo esté commiteado, el cambio es corto:

1. Borrar los tres archivos de `informe_parcela/`.
2. Cambiar `from .esquema_informe import ...` por `from contrato_informe...` en
   `armador.py`, `generador_pdf.py` y `fuentes_datos/`.
3. Que `informe_parcela/__init__.py` reexporte desde `contrato_informe` para que
   nada más se entere.
4. Agregar `contrato-informe` a las dependencias de su `pyproject.toml`.

Mientras tanto hay dos copias. Están idénticas hoy; el riesgo es que alguien toque
una sola. Para comprobarlo en cualquier momento:

```bash
sha256sum contrato_informe/{esquema_informe,motor_firma,ubicacion}.py \
          ../proyecto_consultora/02_Andamiaje_Tecnico/consultora_ambiental/informe_parcela/{esquema_informe,motor_firma,ubicacion}.py
```

Al 30/07/2026 los tres pares coinciden:

| Archivo | SHA-256 (primeros 16) |
|---|---|
| `esquema_informe.py` | `0884efcc407be137` |
| `motor_firma.py` | `842c268f59ee265e` |
| `ubicacion.py` | `d971c15034e509b8` |

## Tests

```bash
pytest
```

27 tests, ninguno sale a la red.
