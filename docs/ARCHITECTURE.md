# Arquitectura

Chat de crédito con verificación de identidad, política determinista, oferta proactiva y derivación a un humano.
Datos y política sintéticos (prototipo).

## Decisión principal: el LLM habla, el código decide

| Función | Quién la hace | Por qué |
|---|---|---|
| Entender el mensaje, detectar idioma, sentimiento y tema delicado | LLM, salida JSON validada | Es lenguaje, no decisión |
| Redactar saludos, cierres y preguntas de aclaración | LLM, **solo mensajes de bajo riesgo** | Las decisiones y ofertas salen de plantillas revisadas |
| Elegibilidad, monto máximo y tasa | Motor determinista (`backend/app/policy/credit_engine.py` + `backend/policy/credit_policy.yaml`) | El modelo no puede aprobar ni inventar reglas |
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

## Conversación de crédito: moneda, solicitud y cierre

El orquestador sigue siendo una máquina de estados (`awaiting`: `offer_interest`, `amount`, `income`, `proceed`, `docs_all`,
`doc_item`). La moneda se detecta y convierte en código (`money.py`); los documentos pendientes salen de la política y de
`docs_on_file` (`documents.py`); el cierre (`/end`) arma el resumen y deja el PDF en una bandeja simulada (`core/outbox.py`,
`core/pdf.py`). El LLM solo clasifica y redacta mensajes de bajo riesgo; la identidad (¿eres un robot?) se responde con
plantilla y cualquier texto que afirme ser humano se descarta.

## Autenticación

Tres preguntas de seguridad de opción única generadas desde los datos del cliente (ciudad o mes y año de apertura de un
producto; ciudad o monto de un movimiento reciente). Se exigen todas correctas; 3 fallos bloquean el documento 15 minutos;
un documento inexistente recibe un reto señuelo con la misma forma. Límite conocido: adivinar acierta 1 de 64 veces por
intento. Detalle en `backend/README.md`.

## Oferta proactiva

Al cerrar la conversación se ofrece un préstamo personal indicativo **solo si** el cliente acepta marketing, está
preaprobado con datos del banco (no con ingreso declarado en el chat), no hubo sentimiento negativo ni tema delicado, no
se le rechazó una solicitud, no se ofreció ya y **no le queda nada pendiente** (ni un tema de soporte en la sesión, ni un
caso crítico abierto, ni uno abierto en los últimos 180 días). Quien pide un crédito se evalúa **sin** mirar el
consentimiento de marketing: ese consentimiento solo gobierna lo proactivo.

## Soporte y contexto del cliente

El agente atiende primero lo que el cliente trae. Al autenticar lee, una vez, sus casos abiertos y la existencia de sus
productos (lista blanca de campos: nunca saldos, movimientos ni montos). Responde con datos verificados (que un producto
existe, categoría, fecha y estado de un caso), anota lo que el cliente cuenta como declarado y ofrece conectar con un
asesor; no deriva solo. El resumen para el asesor lleva ese contexto, el ánimo, una prioridad y una ruta sugeridas.
Detalle y límites en `backend/README.md`.

## Idioma

El LLM no traduce como paso aparte: entiende el mensaje y devuelve valores canónicos en español (intención, producto,
monto, ingreso, idioma). La respuesta sale en el idioma de la sesión desde plantillas revisadas en español y portugués.
Los montos y plazos se interpretan en **código** con formatos locales (1.500,00 y 1,500.00). Los datos del organizador no
traen portugués: las pruebas en ese idioma las escribió el equipo. Pendiente de confirmar con los organizadores si el
portugués es requisito real (el enunciado lo pide de forma explícita).

## Datos

El backend lee un snapshot parquet (capa gold exportada) detrás de la interfaz `CustomerRepository`; en producción sería un
almacén con filtros por fila. Si no existe el snapshot derivado del dataset del organizador, usa el conjunto de ejemplo del
equipo. La capa de datos está en `data/sql/` (DuckDB). **No implementado:** el proyecto dbt y la migración a Databricks.

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
- Validación de la política de crédito por negocio y un modelo de riesgo con datos reales.
- Evaluación de punta a punta con un conjunto reservado de conversaciones (ver `docs/EVALUATION.md`).
- Contrato de datos formal, política de frescura y la capa gold en Databricks.
