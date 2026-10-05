# Arquitectura

Chat de crédito con verificación de identidad, política determinista, oferta proactiva y derivación a un humano.
Datos y política sintéticos (prototipo).

## Decisión principal: el LLM habla, el código decide

| Función | Quién la hace | Por qué |
|---|---|---|
| Entender el mensaje, detectar idioma, sentimiento y tema delicado | LLM, salida JSON validada | Es lenguaje, no decisión |
| Redactar saludos, cierres y preguntas de aclaración | LLM, **solo mensajes de bajo riesgo** | Las decisiones y ofertas salen de plantillas revisadas |
| Elegibilidad, monto máximo y tasa | Motor determinista (`backend/app/policy/credit_engine.py`); reglas de referencia: política 0.3 ([`CREDIT_RULES.md`](CREDIT_RULES.md)), calculada en gold y con implementación en `data/policy/` | El modelo no puede aprobar ni inventar reglas |
| Datos y permisos del cliente | Herramientas (`backend/app/agent/tools.py`), con el `customer_id` de la sesión autenticada | Ninguna herramienta recibe un `customer_id` del modelo |
| Derivar a un humano | Código: solo si el cliente lo pidió de forma explícita o confirmó una oferta de derivación | Una derivación es una acción |
| Cuándo ofrecer crédito sin que lo pidan | Código (`backend/app/agent/proactive.py`) | Es una decisión comercial y de consentimiento |
| Riesgo predictivo de crédito | **No se entrena** | Sin señal en los datos (correlación −0,004; ver `docs/DATA.md`) |

## Flujo

```
Cliente ─► Interfaz (nginx) ─► /v1 ─► API FastAPI ─► Orquestador
                                        │               ├─► NLU: Claude o reglas (esquema canónico en español)
                                        │               ├─► Validación en código (montos, plazos, derivación explícita)
                                        │               ├─► Política de crédito + herramientas
                                        │               └─► Plantillas es/pt (+ reescritura acotada del LLM)
                                        └─► Sesión (JWT atado a la sesión) · bloqueo por documento · logs JSON con trace_id
```

El nginx de la interfaz sirve la página y reenvía `/v1` al backend agregando la clave de integración en el servidor; el
puerto del backend no se publica y `/v1/handoffs` (cola de la consola del agente) se bloquea en el proxy.

## Autenticación

Tres preguntas de seguridad de opción única generadas desde los datos del cliente (ciudad o mes y año de apertura de un
producto; ciudad o monto de un movimiento reciente). Se exigen todas correctas; 3 fallos bloquean el documento 15 minutos;
un documento inexistente recibe un reto señuelo con la misma forma. Límite conocido: adivinar acierta 1 de 64 veces por
intento. Detalle en `backend/README.md`.

## Oferta proactiva

Al cerrar la conversación o tras atender otro tema se ofrece un préstamo personal indicativo **solo si** el cliente acepta
marketing, está preaprobado con datos del banco (no con ingreso declarado en el chat), no hubo sentimiento negativo ni
tema delicado, no se le rechazó una solicitud y no se ofreció ya. Quien pide un crédito se evalúa **sin** mirar el
consentimiento de marketing: ese consentimiento solo gobierna lo proactivo.

## Idioma

El LLM no traduce como paso aparte: entiende el mensaje y devuelve valores canónicos en español (intención, producto,
monto, ingreso, idioma). La respuesta sale en el idioma de la sesión desde plantillas revisadas en español y portugués.
Los montos y plazos se interpretan en **código** con formatos locales (1.500,00 y 1,500.00). Los datos del organizador no
traen portugués: las pruebas en ese idioma las escribió el equipo. Pendiente de confirmar con los organizadores si el
portugués es requisito real (el enunciado lo pide de forma explícita).

## Datos

La capa de datos corre en **Databricks** (Unity Catalog) como un pipeline medallion definido en código
([`data/databricks/`](../data/databricks/README.md), Databricks Asset Bundle):

```
S3 (CSV del organizador) → landing → bronze (todo STRING, _rescued_data, linaje)
  → silver (tipado, deduplicado por clave, la última versión gana)
  → gold: customer_credit_profile, customer_credit_offer_options, credit_offers, resúmenes por cliente
```

- **Política de crédito como datos:** las tablas `ref_*` de silver (catálogo, grilla de tasas y plazos, bandas, segmentos,
  parámetros) salen de [`data/reference/`](../data/reference/) y son la versión 0.3 de las reglas
  ([`CREDIT_RULES.md`](CREDIT_RULES.md)). Gold calcula con ellas la elegibilidad y las ofertas de los 150.000 clientes.
- **Jobs:** `latam_bank_medallion` (bronze → gold, ~14 min, diario a las 06:00 en pausa porque los datos son estáticos),
  `credit_policy_refresh` (recalcula ofertas cuando cambia la política, ~1 min), `credit_gold_deploy` y
  `data_update_fixture_test`. Reintento acotado por tarea, tiempos límite y una corrida a la vez.
- **Calidad y contrato:** cada corrida escribe sus métricas en `pipeline_quality_metrics` (historial, también las corridas
  fallidas) y después corta si alguna falla: duplicados por clave y de contenido, nulos por columna crítica con umbral
  propio, filas rescatadas por cambio de esquema, capacidad del 20% y plazos por banda en las ofertas, y el **contrato** de
  columnas y tipos que consume la API.
- **Frescura y actualización:** los datos terminan en junio de 2026 y no llegan entregas nuevas; el corte de las ofertas es
  2026-06-30 (`as_of_date`, parámetro del job). Con datos vivos el corte sería la fecha de la corrida y cada corrida
  absorbe llegadas tardías (recarga completa y deduplicación por `process_date`). La corrección de una actualización se
  demuestra con un fixture etiquetado: actualización tardía, duplicado exacto, llegada tardía, cambio de esquema y clave
  nula, los cinco resueltos correctamente.
- **Consumo:** el backend lee una exportación a Parquet de gold detrás de la interfaz `CustomerRepository`
  (`data/scripts/export_gold.py`), sin credenciales de Databricks en el contenedor; en producción leería gold con filtros
  por fila. Si no hay exportación usa el conjunto de ejemplo del equipo. `data/sql/` conserva la versión DuckDB para
  explorar en local.
- **Permisos (hoy y en producción):** en el hackathon el equipo tiene permisos amplios en los esquemas. En producción: un
  *service principal* del pipeline escribe bronze/silver/gold; uno de la API solo lee perfil y opciones e inserta en
  `credit_offers`; las personas, solo lectura.
- **Retención:** bronze y silver se pueden reconstruir desde los CSV de origen. `credit_offers` contiene `customer_id` y se
  conservaría lo que exija la regulación de crédito de cada país, con `VACUUM` del historial de Delta; las métricas de
  calidad se conservan como auditoría del pipeline.
- **Capacidad:** el job completo procesa ~23 M filas en ~14 min en un warehouse serverless 2X-Small; el recálculo de ofertas
  es ~1 min y crece con clientes × 12 opciones. La API no consulta Databricks en línea.

## Operación

- **Trazabilidad:** cada petición lleva `X-Trace-Id`; los logs son JSON con latencia, intención, resultado y, para el modelo,
  tokens y tiempo. No se registran documentos, respuestas de seguridad ni cuerpos de petición.
- **Reintentos acotados y respaldo seguro:** el cliente del modelo reintenta una vez; si falla o devuelve algo inválido, el
  turno cae al extractor por reglas. El texto del modelo se descarta si trae cifras ajenas a los hechos calculados.
- **Contenedores:** imágenes sin privilegios y con sistema de archivos de solo lectura; `docker compose up` levanta todo.
- **Costo:** una llamada al modelo para entender cada mensaje, más otra solo para mensajes de bajo riesgo. Con Haiku 4.5 se
  midieron ≈ 490 tokens de entrada y ≈ 105 de salida por turno (muestra pequeña).

## Qué falta para producción

- Externalizar el estado en memoria (sesiones, bloqueos y cola de derivaciones) para varias réplicas.
- Un segundo factor de autenticación real; las preguntas actuales son una simulación con datos del mismo dataset.
- Persistir la última oferta por cliente (hoy el tope de una oferta es por sesión) y límite de peticiones por IP.
- Validación de la política de crédito por negocio y un modelo de riesgo aprendido evaluado contra el baseline de
  `credit_score` (hoy la banda sale del score).
- Evaluación de punta a punta con un conjunto reservado de conversaciones (ver `docs/EVALUATION.md`).
- Datos: permisos mínimos por *service principal*, alertas sobre `pipeline_quality_metrics`, despliegue automático del
  bundle desde CI, carga incremental por `process_date` si llegaran entregas nuevas, y que el backend lea gold en línea.
