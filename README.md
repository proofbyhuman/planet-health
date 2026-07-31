# Backend de Planet Health

API que describe las condiciones ambientales de una coordenada usando datos
públicos abiertos. **Toda respuesta declara de dónde salió cada número.**

## Instalación

Requiere Python 3.12. En esta máquina el lanzador `py` apunta a una instalación
de 3.14 que no existe, así que hay que usar el intérprete directo:

```bash
"C:/Users/Ramiro/AppData/Local/Programs/Python/Python312/python.exe" -m venv .venv
```

Después, dentro del venv:

```bash
.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

Y la dependencia que no está en el `pyproject.toml` porque es una ruta local con
licencia propietaria — ver la nota al principio de ese archivo:

```bash
.venv/Scripts/python.exe -m pip install -e "../../proyecto_consultora/02_Andamiaje_Tecnico/consultora_ambiental[informe]"
```

De ahí salen tres piezas que no se reescriben acá: el contrato de datos con su
serialización canónica, el motor de firma Ed25519, y la resolución de ubicación
administrativa con su regla de "no determinado".

## Correr

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

Documentación interactiva en http://localhost:8000/docs

```bash
curl "http://localhost:8000/api/v1/parcela?lat=-41.1335&lon=-71.3103"
```

## Tests

```bash
.venv/Scripts/python.exe -m pytest
```

Ninguno toca la red: las fuentes externas se sirven con `httpx.MockTransport` y
la caché va a un directorio temporal. La suite corre sin internet.

```bash
.venv/Scripts/python.exe -m ruff check app tests
```

## Inferencia con modelos de visión

DeepForest corre como **proceso aparte**, con el intérprete de su propio entorno,
y se comunica por stdout con JSON. El backend nunca importa torch: hay un test
que falla si alguien lo hace.

No es una preferencia de estilo. El `pyproject.toml` de `consultora_ambiental` ya
había decidido que deepforest y torch viven en entornos separados y se invocan
como programas externos, y dejaba anotado que *"ese supuesto todavía NO fue
probado"*. Acá quedó probado, y se mide solo: con el servidor corriendo y una
inferencia en curso, el proceso de uvicorn ocupa 13 MB y el de DeepForest 419 MB.

```bash
# Por defecto busca ~/Desktop/DeepForest/.venv312/Scripts/python.exe
export PLANET_HEALTH_PYTHON_DEEPFOREST=/ruta/al/venv/de/deepforest/bin/python
```

Si ese intérprete no está, `GET /api/v1/inferencia` lo informa, el frontend
esconde el panel de carga de imágenes, y los indicadores de canopia siguen
saliendo `no_disponible` con el motivo escrito. Nada se rompe: falta una
capacidad y se dice cuál.

```bash
# Probar el script solo, sin backend de por medio:
/ruta/al/python/de/deepforest inferencia/canopia_deepforest.py imagen.png
```

La primera corrida descarga los pesos del modelo desde Hugging Face y tarda unos
100 segundos. Después, unos 35 en CPU. Por eso el endpoint devuelve 202 con un id
y hay que consultar `GET /api/v1/inferencia/{id}`.

**MegaDetector y bioacústica no están.** `PyTorch-Wildlife` no está instalado ni
evaluado. `GET /api/v1/inferencia` lo declara en lugar de omitirlo.

## Lo que hay que saber antes de tocar el código

**El invariante.** `app/procedencia.py` impide construir un `Indicador` sin
declarar de dónde salió el valor. Un indicador `medido` sin fuente, `estimado`
sin método o `no_disponible` con un valor adentro levanta excepción en tiempo de
validación y nunca llega a la respuesta HTTP. No es una convención: es la única
defensa contra que el proyecto vuelva a mostrar `Math.random()` bajo el logo de
una institución científica, que es literalmente lo que hacía antes.

**Nada de valores por defecto.** Si una fuente no responde, el indicador sale con
`valor: null` y el motivo escrito. Nunca un promedio, nunca el último valor que
funcionó, nunca una constante verosímil.

**Sin veredicto.** No hay puntaje global ni etiquetas de aptitud. `app/contrato.py`
explica por qué se eliminó el `salud_global: 82`.

**Cortesía con las fuentes.** Open-Meteo, GBIF, SoilGrids y el IGN sostienen esto
sin cobrar. La caché en disco existe sobre todo por eso. El TTL de cada fuente
está elegido por cuánto cambia el dato, no por cuánto nos conviene: ver
`TTL_POR_FUENTE` en `app/fuentes/cache.py`.

## Fuentes

| Módulo | Servicio | Licencia |
|---|---|---|
| Hidrología | Open-Meteo, reanálisis ERA5 (Copernicus / ECMWF) | CC-BY-4.0 |
| Suelo | SoilGrids v2.0, ISRIC — World Soil Information | CC-BY-4.0 |
| Biodiversidad | GBIF, registros de ocurrencia | CC-BY-4.0 |
| Ubicación | georef-ar, IGN / datos.gob.ar (solo Argentina) | pública |
