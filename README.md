# Planet Health

Consultá las condiciones ambientales de cualquier coordenada del planeta —agua,
suelo y biodiversidad— con datos públicos y abiertos.

**Cada número dice de dónde salió.** Lo que no se sabe se muestra vacío, con el
motivo escrito. Nunca con un valor de relleno.

---

## Por qué esto existe

La idea original está en [`docs/idea_original.md`](docs/idea_original.md): que
cualquier persona con un teléfono pueda saber cómo está el lugar donde vive,
donde piensa mudarse o donde está de paso. Un campesino que quiere saber cómo
está su parcela. Una familia que evalúa un destino. Un biólogo de campo.

## La regla del proyecto

Una herramienta ambiental que inventa datos es peor que ninguna, porque se la
cree. Así que acá **no se puede emitir un número sin declarar de dónde salió**, y
eso no es una convención sino código: el modelo `Indicador` no se construye si no
lo declara, y revienta antes de llegar a la respuesta HTTP.

Cada dato viaja con una de estas cuatro etiquetas:

| | Significa |
|---|---|
| 🟢 **medido** | Lo informó una fuente externa. El valor es lo que respondió, convertido de unidades si hizo falta |
| 🔵 **estimado** | Es un cálculo derivado. El método está escrito y se puede reproducir |
| 🔴 **simulado** | Dato generado. Solo aparece en informes marcados como ficticios, con banda de advertencia |
| ⚪ **no disponible** | Todavía no hay fuente. El valor es nulo y el motivo está escrito |

No hay puntaje global ni etiquetas de aptitud. El informe describe condiciones y
dice qué requiere confirmación; no emite un veredicto sobre el lugar.

## Qué informa hoy

| Módulo | Fuente | Licencia |
|---|---|---|
| Precipitación, temperatura, anomalía | Open-Meteo — reanálisis ERA5 (Copernicus / ECMWF) | CC-BY-4.0 |
| pH, carbono orgánico, arcilla, arena | SoilGrids v2.0 — ISRIC World Soil Information | CC-BY-4.0 |
| Especies, Shannon, Pielou, familias | GBIF — registros de ocurrencia | CC-BY-4.0 |
| Provincia y departamento | georef-ar — IGN / datos.gob.ar (solo Argentina) | pública |
| Copas de árboles | DeepForest — Weecology, University of Florida | MIT |

**Lo que todavía no hace**, declarado en la propia respuesta de la API en lugar
de omitirse: NDVI (las APIs satelitales procesadas piden credenciales), fauna por
cámara trampa y bioacústica (PyTorch-Wildlife no está instalado ni evaluado).

## Informes firmados

Un informe se puede sellar con Ed25519 y cualquiera puede verificarlo **sin
instalar ni confiar en este software**. La verificación devuelve tres resultados
independientes:

- **Integridad** — el contenido coincide con el hash firmado.
- **Firma** — la firma es válida para ese contenido y esa clave.
- **Ancla** — la clave es la que el proyecto publicó.

Los dos primeros se comprueban contra datos que vienen dentro del propio archivo,
así que **no prueban quién lo emitió**: cualquiera puede generar un par de claves
y firmar lo que quiera. Solo el tercero dice algo sobre el emisor, y necesita una
huella traída de otro lado. Por eso se muestran por separado y nunca resumidos en
un cartel de "verificado".

Nada de esto dice si el contenido es **correcto**. Un sello opera sobre bytes.

## Correrlo

Requiere Python 3.12.

```bash
git clone https://github.com/USUARIO_GITHUB/planet-health
cd planet-health/backend
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```

Y abrís `http://localhost:8000`. La documentación interactiva de la API está en
`/docs`.

Los tests corren sin conexión: las fuentes externas se simulan y la caché va a un
directorio temporal.

```bash
.venv/bin/pytest
```

## Cómo está armado

```
frontend/     Página, sin paso de compilación. index.html es una cáscara con
              <template>: no tiene ni un valor ni un estilo en el marcado.
backend/      API FastAPI. app/procedencia.py es la pieza que impide emitir un
              número sin fuente.
              inferencia/ corre en OTRO proceso: el servidor nunca importa torch.
docs/         La idea original del proyecto.
```

Se mantiene JavaScript sin empaquetador a propósito. Es una herramienta
comunitaria: un `npm install` de 300 MB es una barrera para quien quiera
colaborar desde una máquina modesta.

## Colaborar

Hace falta de todo: fuentes de datos nuevas, traducciones, resolutores de
ubicación para otros países —hoy solo hay para Argentina—, y sobre todo criterio
sobre qué se puede afirmar y qué no.

Una sola condición, y es la de arriba: **nada de valores por defecto**. Si una
fuente no responde, el indicador sale vacío con el motivo. Si un número es una
cuenta nuestra, el método va escrito. Un pull request que agregue un dato sin
procedencia no va a pasar los tests, y eso es a propósito.

## Licencia

AGPL-3.0-or-later. El paquete [`contrato-informe`](https://github.com/USUARIO_GITHUB/contrato-informe),
que trae el contrato de datos y el motor de firma, es MIT.

## Atribuciones

Este proyecto no genera datos: los consulta. Las leyendas de atribución que
exigen las licencias CC-BY viajan en cada respuesta de la API y se muestran al
pie de cada informe.
