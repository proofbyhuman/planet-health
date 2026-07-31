# Claves de firma de Planet Health

Los registros se firman con Ed25519. Este documento explica qué prueba esa firma,
qué no, y cómo se manejan las claves.

## Qué prueba la firma, en tres niveles

Los tres son independientes y hay que leerlos por separado. Están así en
`ResultadoVerificacion` de `contrato_informe/motor_firma.py`, en la respuesta de
`GET /api/v1/registros/{id}/verificacion` y en la pantalla.

| Nivel | Qué comprueba | Contra qué |
|---|---|---|
| **Integridad** | El contenido coincide con el hash firmado. Si alguien editó una letra, falla. | Datos que vienen **dentro** del archivo |
| **Firma** | La firma es válida para ese contenido y esa clave pública. | Datos que vienen **dentro** del archivo |
| **Ancla** | La clave que firmó es la que Planet Health publicó. | Una huella traída de **afuera** |

Los dos primeros se comprueban contra datos que viajan en el propio documento.
Cualquiera puede generar un par de claves, escribir lo que quiera, firmarlo, y
pasar los niveles 1 y 2 sin problema. **El único nivel que dice algo sobre quién
emitió el documento es el tercero**, y por eso la huella tiene que venir por un
canal distinto del documento.

## Qué no prueba

Nada sobre si el contenido es **correcto**. Una firma opera sobre bytes y no sabe
qué dicen. Que un registro verifique en verde significa que nadie lo tocó después
de emitido, no que la precipitación que informa sea la que cayó.

Tampoco es firma digital en los términos de la Ley 25.506 argentina, que exige un
certificador licenciado. Es prueba de integridad.

## La clave de desarrollo

La primera vez que se firma algo, si no existe `backend/claves/` y no está
definida `PLANET_HEALTH_PASSPHRASE`, el servidor **genera un par sin contraseña**
y lo registra en el log con una advertencia.

Suena a atajo peligroso, así que conviene decir por qué no lo es: una clave que
nadie publicó no prueba nada sobre el emisor, y el verificador lo dice con todas
las letras. Un registro firmado con la clave de desarrollo pasa integridad y
firma, y **falla el ancla**, que es exactamente el resultado correcto.

Lo que sí sería un error grave es publicar la huella de una clave sin contraseña
como si fuera la clave oficial del proyecto.

## Para producción

```bash
# 1. Generar el par a mano, con contraseña.
python -c "
from contrato_informe.motor_firma import generar_par_de_claves
generar_par_de_claves('claves/planet_health_privada.pem',
                      'claves/planet_health_publica.pem',
                      passphrase=b'la-contrasena-que-corresponda')
"

# 2. Arrancar el servidor con la contraseña en el entorno.
export PLANET_HEALTH_PASSPHRASE='la-contrasena-que-corresponda'
```

Recién ahí se publica la huella (`GET /api/v1/clave-publica`) en un archivo
`CLAVES_PUBLICAS.md` del repositorio público, siguiendo el formato que ya usa
`proyecto_consultora/03_Identidad_Clientes/repo_publico/CLAVES_PUBLICAS.md`.

**La clave privada nunca entra al repositorio.** El `.gitignore` excluye `*.pem`,
`*.key` y `claves/` entera. Una clave privada que llegó al historial de git no se
puede borrar: solo se puede rotar y avisar.

## Verificar un registro sin confiar en nosotros

```bash
curl -s http://localhost:8000/api/v1/registros/PH-xxxx -o registro.json
python verificar_informe.py registro.json --huella-esperada <huella publicada>
```

`verificar_informe.py` es autónomo: solo necesita `cryptography` y no importa
nada de este proyecto. Está probado contra registros reales de Planet Health y
funciona sin modificaciones.

La verificación que ofrece `GET /api/v1/registros/{id}/verificacion` sirve para
mostrar el estado en pantalla, **no como prueba**: la hace el mismo servidor que
firmó, así que si estuviera comprometido mentiría en las dos cosas. La respuesta
lo dice.
