# backend

Backend FastAPI del chat de crédito: verifica la identidad con preguntas de seguridad, conversa en español y
portugués, aplica una política de crédito determinista y deriva a un humano con un resumen estructurado.
Está **desacoplado de cualquier interfaz**: cualquier sitio o canal que hable HTTP/JSON puede integrarlo.

> Datos y política **sintéticos**. No es un sistema bancario real. Ver "Límites conocidos".

## Arranque rápido

Con Docker (requiere Docker Desktop en ejecución):

```bash
docker compose up --build
```

Sin Docker:

```bash
pip install -r requirements-dev.txt
CHAT_JWT_SECRET=un-secreto-de-al-menos-32-caracteres uvicorn app.main:app --port 8000
```

La API queda en `http://localhost:8000`; la documentación interactiva (OpenAPI) en `/docs`.
`CHAT_LLM_PROVIDER=mock` es el valor por defecto: funciona sin red ni clave.

Prueba de humo completa (simula a un titular que conoce sus datos y conversa en es o pt):

```bash
python scripts/demo_client.py --base http://localhost:8000 --lang es
```

## Flujo de integración

```
POST /v1/sessions                      documento + idioma   -> session_id, token, 3 preguntas de seguridad
POST /v1/sessions/{id}/verify          respuestas           -> authenticated | failed (nuevo reto) | 403 bloqueado
POST /v1/sessions/{id}/messages        mensaje              -> respuesta, intención, resultado, ticket de derivación
GET  /v1/sessions/{id}/handoff         resumen para el agente humano (de esa sesión)
GET  /v1/handoffs                      cola de derivaciones (consola de agentes, clave aparte)
DELETE /v1/sessions/{id}
GET  /health  ·  GET /v1/meta
```

- Todas las llamadas de `/v1` envían `X-API-Key` si hay claves configuradas (`CHAT_API_KEYS`), y las de una sesión
  envían además `Authorization: Bearer <token>`.
- El cliente nunca ve las respuestas correctas: recibe ids de opción aleatorios por reto.
- Los errores tienen siempre la forma `{"error": {"code", "message", "trace_id"}}`. Códigos: `INVALID_API_KEY`,
  `INVALID_TOKEN`, `SESSION_EXPIRED`, `AUTH_REQUIRED`, `AUTH_LOCKED`, `AUTH_UNAVAILABLE`, `MESSAGE_TOO_LONG`,
  `VALIDATION_ERROR`, `NO_HANDOFF`, `INTERNAL_ERROR`.
- Cada respuesta lleva `X-Trace-Id` (se acepta uno entrante) para correlacionar con los logs JSON.
- Un sitio web externo debe llamar a esta API **desde su servidor**: una `X-API-Key` en el navegador no es secreta.
  Para llamadas directas desde el navegador, configure `CHAT_CORS_ORIGINS` y trate la clave como identificador.

Ejemplo (documentos del conjunto de ejemplo del equipo, ver `DATOS_DE_PRUEBA.md`; p. ej. `53464097`, México):

```bash
curl -s localhost:8000/v1/sessions -H 'Content-Type: application/json' \
     -d '{"document_number":"53464097","language":"es"}'
```

## Autenticación por preguntas de seguridad

Tres preguntas de tipos distintos, cuatro opciones cada una, generadas desde los datos del cliente:

| Tipo | Ejemplo |
|---|---|
| Ciudad de apertura de un producto | ¿En qué ciudad abrió su cuenta de ahorro con terminación 6611? |
| Mes y año de apertura | ¿En qué mes y año abrió su préstamo hipotecario con terminación 0917? |
| Ciudad de un movimiento reciente | ¿En qué ciudad hizo su compra del 13/05/2026? |
| Monto de un movimiento reciente | ¿Cuál fue el monto de su compra del 08/01/2026? |

Reglas: se exigen **todas** correctas; 3 fallos por documento bloquean 15 minutos (incluso en sesiones nuevas);
cada fallo entrega preguntas nuevas; un documento inexistente recibe un reto señuelo con la misma forma, para no
revelar qué documentos existen; la verificación es en tiempo constante; el LLM no ve ni genera las preguntas.

Hallazgos de los datos que condicionan el diseño: el lugar de registro del cliente (`registration_branch_id`) no
cruza con la tabla de sucursales (solo 1 de cada 30.000 filas), y la fecha de registro nunca coincide con la del primer
producto (en el 70% de los casos el producto es anterior al registro); por eso se usan **productos** (fecha y sucursal de apertura) y movimientos.

## Agente

- **LLM** (opcional): extrae intención y datos a un esquema canónico en español y puede reescribir la respuesta
  para sonar más natural. No decide, no calcula, no ejecuta acciones.
- **Código**: contrasta montos y plazos con el texto original, interpreta formatos numéricos es/pt, aplica la
  política (`policy/credit_policy.yaml` + `app/policy/credit_engine.py`) y ejecuta las herramientas.
- **Herramientas**: ninguna recibe `customer_id`; el contexto lo fija la sesión autenticada.
- **Acciones con confirmación**: derivar a un humano solo ocurre tras un "sí" explícito (o si el cliente lo pide).
- **Respuesta del LLM**: se descarta si contiene números que no están en los hechos calculados.
- **Resumen de derivación**: solicitud, evaluación con versión de política, hechos verificados, acciones, preguntas
  abiertas y los últimos mensajes. Sin cadena de pensamiento del modelo.

Para usar Claude: `CHAT_LLM_PROVIDER=anthropic` y `ANTHROPIC_API_KEY` en `.env` (no entra en la imagen). Si la llamada
falla o devuelve algo inválido, el turno cae al extractor por reglas.

**Qué puede hacer el modelo y qué no** (verificado con pruebas sin red y con el modelo real):

- Extrae intención y datos. El código contrasta montos y plazos con el texto original, y una **derivación a humano solo
  se acepta si el cliente la pidió de forma explícita**; un incidente (cargo no reconocido, fraude) pide confirmación.
- Solo reescribe mensajes de bajo riesgo (saludo, cierre, preguntas de monto o ingreso, aceptación o rechazo de una
  oferta). Las decisiones de crédito, las ofertas, las derivaciones y los avisos de simulación salen **siempre de la
  plantilla revisada**: el guardia de números no detecta frases nuevas que cambien el compromiso. Se ajusta con
  `CHAT_LLM_REWRITE_KINDS` (lista por comas; `none` = el modelo nunca reescribe).
- Su texto se descarta si trae números que no están en los hechos calculados.

Medición con `claude-haiku-4-5-20251001` (13 turnos, 5 conversaciones en es/pt, `scripts/e2e_llm.py`):
18 llamadas, 0 caídas a reglas, latencia por llamada p50 ≈ 1,1 s y p95 ≈ 1,7 s, unos 490 tokens de entrada y 105 de
salida por turno. Es una muestra pequeña, no un benchmark. `scripts/smoke_llm.py` prueba la clave y la extracción.

### Evaluación del NLU (intención, sentimiento, tema delicado)

`python scripts/eval_nlu.py` mide el extractor por reglas y el modelo con frases en es/pt (`eval/nlu_cases.py`).
Hay un conjunto de desarrollo (60 frases, usado para afinar el prompt y las reglas) y uno reservado (29 frases,
escrito antes de afinar y medido una vez).

| Conjunto | Reglas | Claude (claude-haiku-4-5) |
|---|---|---|
| Desarrollo (60), **optimista**: se afinó mirando estos fallos | 57/60 (95%) | 60/60 (100%) |
| **Reservado (29)**, medido una vez, dos corridas del modelo | 23/29 (79%) | 28/29 y 27/29 (93–97%) |

Antes de afinar, el modelo acertaba 49/60 (82%): confundía tasas y límites con otro tema en portugués y tomaba "no
gracias" como despedida por no saber que había una pregunta pendiente (ahora se le informa). En el reservado, el único
fallo constante era "necesito que me atienda una persona": lo degradaba la guardia de derivación explícita porque su
léxico no la cubría; se amplió el léxico, pero **esa corrección se hizo mirando el reservado, así que esa cifra ya no
es limpia**. Detección de tema sensible: 2/2 en el reservado y 5/5 en desarrollo, 0 falsos positivos. Límites: muestra
pequeña, etiquetas de un solo anotador (el equipo debe revisarlas), y las dos corridas del modelo difieren en una frase.

## Oferta proactiva de crédito

El chat ofrece un crédito por iniciativa del banco al **cerrar la conversación** ("gracias", "eso es todo") o **tras
atender otro tema** (se deriva el tema y luego se ofrece). Es una decisión en código (`app/agent/proactive.py`); el LLM
solo redacta el mensaje. La respuesta de `/messages` trae `proactive_offer: true` y `awaiting: "offer_interest"`.

| Camino | Qué mira | Qué NO mira |
|---|---|---|
| **Reactivo**: el cliente pide un crédito, tasas o su capacidad | Política de crédito con datos del banco (y el ingreso que declare, como provisional) | `accepts_marketing` |
| **Proactivo**: el banco ofrece | Todo lo siguiente a la vez (abajo) | El ingreso declarado en el chat |

Se ofrece solo si se cumplen **todas**: el cliente acepta marketing; está preaprobado por la política con datos del
banco; no hubo sentimiento negativo ni tema delicado (fraude, disputa, reclamo, tarjeta robada, cargo no reconocido) en
la sesión; no se le rechazó una solicitud en la sesión; y no se ofreció ya ni dijo que no. Si acepta, entra al flujo
normal de elegibilidad; si rechaza, no se repite. La oferta queda en `actions_taken` y `verified_facts` del resumen de
derivación para que el agente la vea.

Límites: el tope de una oferta por sesión no se persiste entre sesiones (hace falta guardar la última oferta por
cliente); la oferta indica la capacidad máxima de la política (20% de endeudamiento), lo cual es agresivo para una
propuesta comercial: conviene que el equipo decida un tope menor; y el sentimiento y el tema delicado los detecta
un léxico simple en modo `mock`, no un modelo.

## Configuración (variables de entorno, ver `.env.example`)

| Variable | Defecto | Uso |
|---|---|---|
| `CHAT_ENV` | `dev` | Con `dev`, `GET /v1/handoffs` no exige clave; en otro valor se exige `CHAT_ADMIN_API_KEYS` |
| `CHAT_API_KEYS` | vacío | Claves de integración (lista por comas); vacío = sin clave |
| `CHAT_JWT_SECRET` | aleatorio | Secreto de tokens (≥ 32 caracteres); si falta, las sesiones no sobreviven al reinicio |
| `CHAT_CORS_ORIGINS` | vacío | Orígenes permitidos |
| `CHAT_LLM_PROVIDER` | `mock` | `mock` o `anthropic` |
| `CHAT_DATA_DIR` | `data/snapshot` si existe; si no, `data/fixture` | Carpeta con los parquet (montar como volumen en otros entornos) |
| `CHAT_SESSION_TTL_MINUTES` · `CHAT_AUTH_MAX_ATTEMPTS` · `CHAT_AUTH_LOCKOUT_MINUTES` | 30 · 3 · 15 | Sesión y bloqueo |

## Datos

Hay dos orígenes, con la misma forma (clientes, productos, sucursales, movimientos recientes, perfil crediticio y `accepts_marketing`):

- `data/fixture/`: **conjunto de ejemplo inventado por el equipo** (21 clientes, semilla fija; `scripts/make_fixture.py`).
  Está en el repositorio y es lo que usan las pruebas, el CI y un clon limpio.
- `data/snapshot/`: muestra derivada del dataset del organizador (225 clientes). **No se versiona** (`.gitignore`);
  si existe, el backend lo prefiere. `GET /v1/meta` indica el origen en uso (`data_source`). Se regenera con:

```bash
python backend/scripts/build_snapshot.py --customers 400    # desde la raíz; requiere las tablas del organizador (ver docs/DATA.md)
```

## Pruebas

```bash
pip install -r requirements-dev.txt && pytest
```

96 pruebas: generación y verificación de preguntas, bloqueo, tokens, expiración, API key, formatos numéricos,
intenciones es/pt, política, derivación, inyección de instrucciones, consola de agentes y oferta proactiva
(consentimiento, preaprobación, momento adecuado, tema delicado, aceptación y rechazo) y salvaguardas contra un LLM
que se equivoca (derivación no pedida, reescritura de decisiones, números ajenos, caída del modelo).

## Límites conocidos

- **Seguridad de las preguntas:** con 3 preguntas de 4 opciones, adivinar acierta 1 de cada 64 veces por intento; la
  mitigación es el bloqueo, pero no sustituye un segundo factor real (OTP, biometría) en producción. Los datos de las
  preguntas salen del mismo dataset que el cliente declararía, así que es una simulación, no una prueba de identidad.
- **Estado en memoria:** sesiones, bloqueos y cola de derivaciones viven en un proceso. Una sola réplica; para más hay
  que externalizar `SessionStore`, `AuthLockout` y `HandoffQueue` (Redis o base de datos).
- **Un cliente sin datos suficientes para 3 preguntas** recibe `AUTH_UNAVAILABLE` (revela que el documento existe);
  en el snapshot derivado del dataset les pasa a 4 de 225 clientes (1,8%); en el conjunto de ejemplo, a 1 de 21 (a propósito).
- **Política sintética:** tope de 20% de endeudamiento, bandas de score y tasas son valores provisionales; no hay
  verdad de terreno externa. Las cuotas de deudas existentes se estiman con supuestos del YAML del baseline.
- **Claude probado solo con una muestra pequeña** (ver arriba). Falta medir clasificación de intención y detección de
  sentimiento y tema delicado con un conjunto etiquetado a mano, en es y pt.
- **Imagen Docker verificada** (652 MB): arranca como usuario no root (uid 10001), con sistema de archivos de solo
  lectura, pasa el `HEALTHCHECK` y completó el flujo es/pt desde `scripts/demo_client.py`. Un primer `docker build`
  falló por una descarga corrupta de un paquete (hash no coincidente) y funcionó al reintentar.
- No hay límite de peticiones por IP ni protección contra concurrencia sobre una misma sesión.
- Los logs no incluyen documentos, respuestas de seguridad ni cuerpos de petición; los mensajes del cliente sí se
  guardan en memoria durante la sesión y se incluyen (últimos 8) en el resumen de derivación.
