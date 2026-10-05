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
POST /v1/sessions/{id}/end             cierre: resumen de la propuesta + aviso de correo (simulado)
GET  /v1/sessions/{id}/summary.pdf     PDF del resumen (solo si hubo propuesta)
DELETE /v1/sessions/{id}
GET  /health  ·  GET /v1/meta
```

- Todas las llamadas de `/v1` envían `X-API-Key` si hay claves configuradas (`CHAT_API_KEYS`), y las de una sesión
  envían además `Authorization: Bearer <token>`.
- El cliente nunca ve las respuestas correctas: recibe ids de opción aleatorios por reto.
- Los errores tienen siempre la forma `{"error": {"code", "message", "trace_id"}}`. Códigos: `INVALID_API_KEY`,
  `INVALID_TOKEN`, `SESSION_EXPIRED`, `AUTH_REQUIRED`, `AUTH_LOCKED`, `AUTH_UNAVAILABLE`, `MESSAGE_TOO_LONG`,
  `VALIDATION_ERROR`, `NO_HANDOFF`, `SESSION_ENDED`, `NO_SUMMARY`, `INTERNAL_ERROR`.
- `verify` devuelve además `customer` (id, nombre, país, segmento, estado: sin documento ni datos financieros) al autenticar.
- `messages` y `end` devuelven `evidence` **solo si el agente usó herramientas en ese turno** (`null` en un saludo o un
  agradecimiento): `steps[]` (herramientas ejecutadas de verdad y su estado), `verification[]` (`verified`, `inconclusive`,
  `unverified` para datos declarados por el cliente, `reference` para tasas de referencia), `customer` (perfil leído) y
  `evaluation` (evaluación de política de este turno). Se arma en `app/agent/evidence.py` desde las llamadas reales a
  `tools.py`; si una herramienta falla, `verification` va vacío. La interfaz solo pinta esto: no infiere verificaciones.
- Cada respuesta lleva `X-Trace-Id` (se acepta uno entrante) para correlacionar con los logs JSON.
- Un sitio web externo debe llamar a esta API **desde su servidor**: una `X-API-Key` en el navegador no es secreta.
  Para llamadas directas desde el navegador, configure `CHAT_CORS_ORIGINS` y trate la clave como identificador.

Ejemplo (documentos del conjunto de ejemplo del equipo, ver `DATOS_DE_PRUEBA.md`; p. ej. `53464097`, México):

```bash
curl -s localhost:8000/v1/sessions -H 'Content-Type: application/json' \
     -d '{"document_number":"53464097","language":"es"}'
```

## Autenticación por preguntas de seguridad

Tres preguntas de tipos distintos, cuatro opciones cada una, generadas desde los datos del cliente. La serie (ocupación,
ciudad y años) se eligió midiendo cada candidata con los 150.000 clientes: **sin montos, sin fechas exactas y sin pedir
recordar una operación**.

| Tipo | Ejemplo | Cobertura | Acierto al azar |
|---|---|---|---|
| `occupation` | ¿Cuál es su ocupación registrada en el banco? | 90% | 25% (20 valores uniformes) |
| `product_city` | ¿En qué ciudad abrió su cuenta de ahorro? (solo productos abiertos en sucursal) | 63% | 25%, con distractores del **mismo país** |
| `product_year` | ¿En qué año abrió su tarjeta de crédito más antigua? | 90% | 25% |
| `customer_since` | ¿En qué año se hizo cliente del banco? | 100% | 25% |

Con estos cuatro tipos, el **87%** de los clientes tiene datos para 3 preguntas de tipos distintos (con la serie anterior,
el 79%). Quien no los tiene recibe `AUTH_UNAVAILABLE` y se le deriva a un asesor.

Reglas de diseño:
- **Un producto se nombra por su tipo** ("su cuenta de ahorro") o, si hay varios del mismo tipo, "el más antiguo" (sin
  empate). Nunca por su terminación: el enunciado se muestra **antes** de autenticar y no debe llevar datos reales.
- **Los distractores salen del mismo universo que la respuesta**: ciudades del mismo país y años válidos. Con
  distractores de los tres países (solo hay 16 ciudades), quien conoce el país del cliente acertaba el 63%.
- **La ciudad solo se pregunta si el producto se abrió en sucursal**: quien abrió online no tiene una ciudad que recordar.
- **Se prefiere un solo tipo de año** por reto. Con dos, el año de alta y el de apertura pueden no cuadrar en estos datos.
- Se exigen **todas** correctas; 3 fallos por documento bloquean 15 minutos (incluso en sesiones nuevas); cada fallo
  entrega preguntas nuevas; la verificación es en tiempo constante; el LLM no ve ni genera las preguntas.
- Un documento inexistente recibe un **reto señuelo**: los *tipos* de pregunta salen de un cliente real al azar (la
  mezcla coincide con la de los clientes reales) y el contenido es inventado, nunca de ese cliente.

Por qué se descartaron las anteriores (medido): el **monto** de una operación es difícil de recordar y un atacante que
elige el valor central acierta el 45%; la **ciudad de una operación** coincide con la ciudad de residencia en el 95% de las
operaciones; el **mes** de apertura exige más memoria que el año. Tampoco entran la fecha de nacimiento (está en el
documento), el teléfono, el vencimiento de la tarjeta (impreso en ella), el estado civil ni la educación (sensibles), ni
el comercio más frecuente (solo el 3% de los clientes tiene una categoría claramente dominante).

**Límites conocidos.** (1) El texto de la pregunta todavía revela el *tipo* de un producto del cliente antes de
autenticar (sin terminación): menos que antes, pero no es cero. (2) `customer_since` usa la fecha de registro, que en este
dataset no cuadra con la del primer producto (`docs/DATA.md`): se mantiene para llegar al 87% de cobertura; sin él, bajaría
al 57% (ocupación + ciudad + año de apertura obligatorios). (3) Todo se midió sobre datos sintéticos: con datos reales
las distribuciones serán distintas. (4) La verificación por preguntas es débil por naturaleza; lo que protege es el límite
de intentos y el bloqueo.

## Agente

- **LLM** (opcional): extrae intención y datos a un esquema canónico en español y puede reescribir la respuesta
  para sonar más natural. No decide, no calcula, no ejecuta acciones.
- **Código**: contrasta montos y plazos con el texto original, interpreta formatos numéricos es/pt, aplica la
  política de crédito 0.4 (`app/policy/engine.py`, ver "Política de crédito") y ejecuta las herramientas.
- **Herramientas**: ninguna recibe `customer_id`; el contexto lo fija la sesión autenticada. Las de crédito son
  `get_offers()`, `recalculate_offer(...)` y `accept_offer(...)` (`app/agent/tools.py`).
- **Acciones con confirmación**: derivar a un humano solo ocurre tras un "sí" explícito (o si el cliente lo pide).
- **Respuesta del LLM**: se descarta si contiene números que no están en los hechos calculados.
- **Resumen de derivación**: solicitud, evaluación con versión de política, hechos verificados, acciones, preguntas
  abiertas y los últimos mensajes. Sin cadena de pensamiento del modelo. Con contexto del cliente: ver "Soporte, contexto
  y resumen para el asesor".

Para usar Claude: `CHAT_LLM_PROVIDER=anthropic` y `ANTHROPIC_API_KEY` en `.env` (no entra en la imagen). Si la llamada
falla o devuelve algo inválido, el turno cae al extractor por reglas.

**Qué puede hacer el modelo y qué no** (verificado con pruebas sin red y con el modelo real):

- Extrae intención y datos. El código contrasta montos y plazos con el texto original, y una **derivación a humano solo
  se acepta si el cliente la pidió de forma explícita**; un incidente (cargo no reconocido, fraude) pide confirmación.
- Solo reescribe mensajes de bajo riesgo (saludo, cierre, preguntas de monto o ingreso, aceptación o rechazo de una
  oferta, y en soporte: tema ajeno, incidente, detalle anotado). Las decisiones de crédito, las ofertas, las derivaciones,
  los avisos de simulación y **todo mensaje con hechos del banco** salen **siempre de la
  plantilla revisada**: el guardia de números no detecta frases nuevas que cambien el compromiso. Se ajusta con
  `CHAT_LLM_REWRITE_KINDS` (lista por comas; `none` = el modelo nunca reescribe).
- Su texto se descarta si trae números que no están en los hechos calculados, si **promete** algo (resultado, plazo,
  reembolso: "se resolverá", "le garantizo"…) o si, en un mensaje que espera respuesta (p. ej. "¿lo conecto?"), deja de
  preguntarlo.

Medición con `claude-haiku-4-5-20251001` (13 turnos, 5 conversaciones en es/pt, `scripts/e2e_llm.py`):
18 llamadas, 0 caídas a reglas, latencia por llamada p50 ≈ 1,1 s y p95 ≈ 1,7 s, unos 490 tokens de entrada y 105 de
salida por turno. Es una muestra pequeña, no un benchmark. `scripts/smoke_llm.py` prueba la clave y la extracción.

### Evaluación del NLU (intención, sentimiento, tema delicado)

`python scripts/eval_nlu.py` mide el extractor por reglas y el modelo con frases en es/pt (`eval/nlu_cases.py`).
Hay un conjunto de desarrollo (78 frases, usado para afinar el prompt y las reglas) y uno reservado (29 frases,
escrito antes de afinar y medido una vez).

| Conjunto | Reglas | Claude (claude-haiku-4-5) |
|---|---|---|
| Desarrollo (78), **optimista**: se afinó mirando estos fallos | 77/78 (99%) | **sin remedir** (ver abajo) |
| **Reservado (29)**, ya visto: **no es limpio** (ver abajo) | 27/29 (93%) | 28/29 y 27/29 con el prompt anterior |

**Cambio de taxonomía (soporte).** Se agregaron las intenciones `account_inquiry` (saldo, movimientos, qué productos
tiene) y `case_status` (estado de un reclamo o caso ya abierto); un incidente sigue siendo `other_topic` con
`sensitive_topic`. Por eso se **reetiquetaron 4 frases** (saldo/extracto → `account_inquiry`, "llevo semanas con un
reclamo sin respuesta" → `case_status`) y se agregaron 13 al desarrollo. Al medir, "alguien usó mi tarjeta sin permiso"
pasó de tema ajeno a `account_inquiry` (un reporte de fraude recibiría "sí veo su tarjeta"); se corrigió ampliando los
marcadores de uso no autorizado en las reglas. **Esa corrección se hizo viendo una frase del reservado, así que el
reservado ya no es limpio para las reglas.** El prompt de Claude también cambió (intenciones nuevas, tono, promesas) y
**no se volvió a medir con el modelo real** (no hay clave en el entorno de desarrollo): hay que correr
`python scripts/eval_nlu.py` con `ANTHROPIC_API_KEY` antes de citar una cifra del modelo.

Antes de afinar, el modelo acertaba 49/60 (82%): confundía tasas y límites con otro tema en portugués y tomaba "no
gracias" como despedida por no saber que había una pregunta pendiente (ahora se le informa). En el reservado, el único
fallo constante era "necesito que me atienda una persona": lo degradaba la guardia de derivación explícita porque su
léxico no la cubría; se amplió el léxico, pero **esa corrección se hizo mirando el reservado, así que esa cifra ya no
es limpia**. Detección de tema sensible (reglas): 2/2 en el reservado y 9/10 en desarrollo, 0 falsos positivos. Límites: muestra
pequeña, etiquetas de un solo anotador (el equipo debe revisarlas), y las dos corridas del modelo difieren en una frase.

## Política de crédito

El backend aplica la **política 0.4** ([`docs/CREDIT_RULES.md`](../docs/CREDIT_RULES.md)), la misma que calcula gold en
Databricks. No reimplementa las reglas: `app/policy/engine.py` carga la implementación de referencia
(`data/policy/credit_policy.py`) y sus parámetros (`data/reference/*.csv`), y solo traduce una solicitud del chat a una opción
de la grilla. Con el export de gold, el motor del backend coincide en las 1.800.000 opciones:

```bash
python data/scripts/export_gold.py --profile <perfil>                       # export de gold -> .local/gold/
python data/scripts/check_engine_parity.py --engine app.policy.engine:offer_options --pythonpath backend
```

- **Datos.** El perfil de crédito es la fila de gold `customer_credit_profile` del cliente (USD), en
  `credit_profile.parquet` (`scripts/build_snapshot.py --gold-only` lo toma del export; `scripts/make_fixture.py` lo
  genera para el conjunto del equipo). Las opciones se recalculan en memoria, idénticas a gold.
- **Reglas.** Bandas A–E con ajuste por segmento; tasa de la grilla por plazo o nivel de tarjeta; plazo máximo por banda y,
  desde 0.4, por edad al vencimiento (el crédito termina antes de los 75 años; el backend nunca ve la fecha de nacimiento:
  gold entrega `max_term_*` ya recortados); límite del 20% sin margen; tarjeta de crédito por nivel (Clásica, Gold,
  Platinum, Black). Un plazo rechazado por edad se explica distinto que uno rechazado por la banda: el motivo sale de
  `unavailable_reason` de la opción (`term_above_age_at_maturity`), el mismo de gold.
- **Versión.** Al arrancar se compara la `policy_version` del perfil (export de gold) con la de `data/reference`: si no
  coinciden, en `CHAT_ENV=dev` solo avisa y fuera de dev no arranca (un perfil 0.3 ofrecería plazos sin el tope por edad).
- **Lo más alto primero.** Sin monto, el asistente propone la opción destacada (`is_featured` de gold) y pregunta cuánto
  necesita; «sí» toma ese máximo. «¿Qué ofertas tengo?» muestra la destacada de cada producto.
- **Datos declarados.** Si el cliente dice su ingreso, reemplaza al registrado y la oferta queda **condicional** (`F03`): ya
  no hay un tope de aumento que mande a revisión. Tras el primer resultado se pregunta **a todos por igual** si alguien más
  del hogar aporta ingresos; si sí, se piden su ingreso y sus cuotas (el ingreso del hogar entra con su deuda). Si el
  cliente no sabe esas cuotas, el ingreso no se suma y el asesor lo completa.
- **Monedas.** La política trabaja en USD (`fx_to_usd` del perfil); el cliente ve y escribe montos en su moneda local.
  Los máximos se muestran redondeados hacia abajo para que, convertidos de vuelta, no pasen del tope.
- **Oferta aceptada.** «Sí» a avanzar llama a `accept_offer`: recalcula (nunca toma cifras del texto), valida monto y 20%,
  agrega `F02`/`F03`/`F04` y escribe la fila de gold `credit_offers` en `CHAT_OFFERS_PATH` (JSONL). Al derivar, la fila se
  reescribe con `status = handed_off` y el ticket. `scripts/sync_credit_offers.py` sube las filas a Databricks con `MERGE`
  por `offer_id` (sin `--apply` solo muestra lo que subiría), así el contenedor no necesita credenciales. Con Docker
  Compose el archivo vive en `.local/offers/` del host (volumen, ignorado por git) y sobrevive a los reinicios:
  `python backend/scripts/sync_credit_offers.py --file .local/offers/credit_offers.jsonl --gold-schema
  workspace.gold_latam_bank_test --apply --profile <perfil> --warehouse-id <id>` (probarlo primero en `_test`).
- **Reglas del agente** (`policy/agent_rules.yaml`): documentos por producto (y los de la persona del hogar si se sumó su
  ingreso) y atributos protegidos. `date_of_birth` solo se usa, en gold, para el tope de plazo por edad.

## Oferta proactiva de crédito

El chat ofrece un crédito por iniciativa del banco solo al **cerrar la conversación** ("gracias", "eso es todo"), y solo
si no se atendió ningún tema de soporte en la sesión: **ya no se ofrece crédito justo después de derivar un problema**.
Es una decisión en código (`app/agent/proactive.py`); el LLM solo redacta el mensaje. La respuesta de `/messages` trae
`proactive_offer: true` y `awaiting: "offer_interest"`.

| Camino | Qué mira | Qué NO mira |
|---|---|---|
| **Reactivo**: el cliente pide un crédito, tasas o su capacidad | Política de crédito con datos del banco (y lo que declare, como oferta condicional) | `accepts_marketing` |
| **Proactivo**: el banco ofrece | Todo lo siguiente a la vez (abajo) | El ingreso declarado en el chat |

Se ofrece solo si se cumplen **todas**: gold lo marca como `offer_mode = proactive` (elegible con datos del banco, acepta
marketing y sin reclamo crítico abierto) y tiene una opción disponible, que se presenta empezando por lo más alto
(préstamo personal; si no, tarjeta; si no, hipoteca); no hubo sentimiento negativo ni tema delicado (fraude, disputa, reclamo, tarjeta robada, cargo no reconocido) en
la sesión; no se le rechazó una solicitud en la sesión; y no se ofreció ya ni dijo que no. Además **no le queda nada
pendiente**: ni un tema ajeno al crédito en esta conversación ni una derivación. Las quejas solo cuentan a través de gold (`offer_mode`). Si acepta, entra al flujo normal de elegibilidad; si rechaza, no se repite. La oferta
queda en `actions_taken` y `verified_facts` del resumen de derivación para que el agente la vea.

## Scope, customer context and the advisor summary

The chat handles **credit offers only**. Anything else is handed off to an advisor **after a "yes"**, without showing
any bank data: `account_inquiry` (balances, movements, which products), `case_status` (a complaint already open) and
other banking topics answer "that topic is handled by an advisor… shall I connect you?" (`non_credit` template).
Incidents (fraud, unrecognized charge, stolen card) keep one line of empathy and the same confirmation.

- **Customer context** (`app/agent/context.py`, built at `sessions.verify`): **only the gold profile row**. Complaint
  counts (open, High/Critical, Critical), credit product counts, `offer_mode`, `requires_advisor_review` and reason
  codes. No raw cases, products, transactions or FX files are read by the assistant (`tests/test_credit_scope.py`
  fails if the chat touches them).
- **Welcome**: always about credit ("I can show your pre-approved credit offers… for anything else I connect you with
  an advisor"), with the quick replies "Ver mis ofertas de crédito" and "Hablar con un asesor".
- **Customer notes**: what the customer tells while deciding is kept as *declared, unverified*. Long numbers (cards,
  accounts, documents) and e-mails are removed from the notes and from the last messages of the summary.
- **Empathy**: a negative message opens with an acknowledgement (at most once every 3 turns).
- **Summary** (`GET /v1/sessions/{id}/handoff`, `GET /v1/handoffs`): `topic`, `case_notes`, `customer_context` (gold
  counts, `source: gold.customer_credit_profile`), `sentiment`, `priority` (`normal|high|urgent`), `suggested_route`,
  `suggested_next_actions`, the accepted offer with its flags, and `narrative` (written by code; an LLM version replaces
  it only if it adds no data foreign to the summary).
- **Simulation notice**: replies that present an offer carry `disclaimer: "simulation"` and the final offer summary
  `disclaimer: "final"`; the UI draws it as a panel above the offer and below the summary. The reply text does not
  repeat it.

Limits: the one-offer-per-session cap is not persisted across sessions; the offer shows the policy's maximum capacity
(20% debt-to-income); and in `mock` mode sentiment and sensitive topics come from a simple lexicon, not a model.


## Moneda, solicitud, resumen y tono

- **Moneda.** El código (`app/agent/money.py`) detecta la moneda que menciona el cliente (USD, MXN, COP, ARS; «pesos» sin
  país se interpreta en la moneda del cliente). Convierte con la tabla de tipos de cambio del dataset (última fecha
  disponible, triangulando por USD: **no es una cotización en vivo**) a la moneda local del cliente; la política la pasa a
  USD con `fx_to_usd` del perfil de gold. El mensaje muestra ambos montos con la misma tasa. BRL y EUR se informan como no soportadas en vez de inventar una tasa.
- **Solicitud.** Tras una evaluación favorable el asistente pregunta si quiere avanzar. Si acepta, calcula los documentos
  que exigen las reglas del agente (`required_documents` en `policy/agent_rules.yaml`), resta los que el banco ya tiene (`docs_on_file`,
  sintético) y pide uno por uno solo los que faltan. El chat **no recibe archivos**: el cliente confirma que los tiene y
  el asesor los verifica. Con todo en orden se deriva a un asesor con el resumen; si falta algo, se le dice qué.
- **Cierre.** Al despedirse o llamar a `POST /end`, el asistente resume la oferta y avisa que el detalle llegará por correo
  en PDF. **El envío es simulado:** el PDF y un JSON `simulated_not_sent` quedan en `CHAT_OUTBOX_DIR` y solo se muestra el
  correo enmascarado. El PDF se descarga con `GET /summary.pdf`. Un incidente (fraude, reclamo) nunca genera resumen comercial.
- **Tono e identidad.** Las respuestas usan un tono cercano y varían su formulación. El asistente se presenta una vez como
  «asistente virtual», nunca afirma ser una persona y, si el cliente pregunta si es un robot o una IA, responde con la
  verdad (intención `ask_identity`). Un texto del modelo que diga ser humano se descarta.

## Configuración (variables de entorno, ver `.env.example`)

| Variable | Defecto | Uso |
|---|---|---|
| `CHAT_ENV` | `dev` | Con `dev`, `GET /v1/handoffs` no exige clave; en otro valor se exige `CHAT_ADMIN_API_KEYS` |
| `CHAT_API_KEYS` | vacío | Claves de integración (lista por comas); vacío = sin clave |
| `CHAT_JWT_SECRET` | aleatorio | Secreto de tokens (≥ 32 caracteres); si falta, las sesiones no sobreviven al reinicio |
| `CHAT_CORS_ORIGINS` | vacío | Orígenes permitidos |
| `CHAT_LLM_PROVIDER` | `mock` | `mock` o `anthropic` |
| `CHAT_DATA_DIR` | `data/snapshot` si existe; si no, `data/fixture` | Carpeta con los parquet (montar como volumen en otros entornos) |
| `CHAT_OUTBOX_DIR` | carpeta temporal del sistema | Bandeja de correos **simulados** (PDF + JSON) |
| `CHAT_SESSION_TTL_MINUTES` · `CHAT_AUTH_MAX_ATTEMPTS` · `CHAT_AUTH_LOCKOUT_MINUTES` | 30 · 3 · 15 | Sesión y bloqueo |

## Datos

Hay dos orígenes, con la misma forma. The assistant reads only the gold credit profile (`credit_profile.parquet`, with
`fx_to_usd`/`fx_date` for currency conversion); customers, products and branches are used only by the identity check.
The `transactions`, `fx_rates` and `case_context` files are no longer read.

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

155 pruebas: moneda y conversión, solicitud y documentos, resumen/PDF/correo simulado, tono e identidad, generación y verificación de preguntas, bloqueo, tokens, expiración, API key, formatos numéricos,
intenciones es/pt, política, derivación, inyección de instrucciones, consola de agentes y oferta proactiva
(consentimiento, preaprobación, momento adecuado, tema delicado, aceptación y rechazo) y salvaguardas contra un LLM
que se equivoca (derivación no pedida, reescritura de decisiones, números ajenos, caída del modelo).

## Límites conocidos

- **Correo simulado:** no hay envío real; el PDF se genera y se descarga, y el aviso lo dice con claridad.
- **Sin carga de archivos:** el cliente confirma que tiene los documentos; la verificación es del asesor. `docs_on_file` es sintético.
- **Tipos de cambio:** son los del dataset en su última fecha (no en vivo); solo USD, MXN, COP y ARS.
- **Tokens:** el historial crece con la conversación, así que el costo por turno aumenta en sesiones largas.

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
  guardan en memoria durante la sesión y se incluyen (últimos 8, con números largos y correos omitidos) en el resumen de
  derivación.
