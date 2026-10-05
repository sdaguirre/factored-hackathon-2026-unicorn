# Asistente de crédito con IA para banca

Prototipo para el **Factored AI & Data Hackathon 2026**: un chat de atención al cliente de un banco latinoamericano
(México, Colombia, Argentina) en **español y portugués**. Verifica la identidad con preguntas de seguridad, informa y
simula la elegibilidad de crédito con una política determinista, ofrece crédito de forma proactiva solo cuando
corresponde, y deriva a un asesor humano con un resumen estructurado.

> **Datos y política sintéticos.** Los datos son sintéticos (organizador y equipo) y la política de crédito la inventó el
> equipo. Ninguna respuesta del asistente es una aprobación de crédito.

## Idea central

Un LLM se ocupa **solo del lenguaje** (entender, aclarar, redactar mensajes de bajo riesgo). Todo lo que compromete al
banco lo decide **código**: el motor de política calcula elegibilidad, monto máximo y tasa; las herramientas aplican
permisos por cliente; las acciones piden confirmación. El modelo no puede aprobar crédito, ni derivar a un humano si el
cliente no lo pidió, ni introducir cifras que no estén en los hechos calculados.

```
Cliente ─► Interfaz web ─► API (sesión de prueba, trace_id)
                              │
                              ▼
                      Orquestador (máquina de estados)
          ┌───────────────┼──────────────────────────┐
          ▼               ▼                          ▼
   LLM: intención +   Herramientas (permisos      Motor de política
   idioma + tono      por cliente de la sesión)   (credit_policy.yaml)
          │               │                          │
          └───────► hechos verificados ◄─────────────┘
                              │
              respuesta es/pt  ·  oferta proactiva  ·  derivación a humano
```

## Probarlo

Requiere Docker. Con un clon limpio no hacen falta datos del organizador ni claves: usa un conjunto de ejemplo inventado
por el equipo y un extractor por reglas.

```bash
docker compose up --build        # interfaz en http://localhost:8080
```

- Documentos, clientes de prueba y cómo responder las preguntas de seguridad: [`backend/DATOS_DE_PRUEBA.md`](backend/DATOS_DE_PRUEBA.md).
- Para usar Claude como modelo: copie `backend/.env.example` a `backend/.env` y complete `CHAT_LLM_PROVIDER=anthropic` y `ANTHROPIC_API_KEY`.
- Cómo levantar el servicio para la evaluación, verificarlo y apagarlo: [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

## Qué se midió hasta ahora

| Qué | Resultado | Límites |
|---|---|---|
| Pruebas automáticas del backend | 96 pruebas, sin red | No cubren la calidad conversacional del modelo real |
| Intención del NLU, conjunto **reservado** (29 frases es/pt) | Reglas 79%. Claude 93–97% (dos corridas) | Una sola persona etiquetó; muestra pequeña |
| Prueba en vivo con Claude Haiku 4.5 (13 turnos) | 0 caídas a reglas; ~1,1 s por llamada (p95 1,7 s); ~490 tokens de entrada y ~105 de salida por turno | Muestra pequeña, no es un benchmark |
| Política de crédito preliminar del backend sobre 150.000 clientes (solicitud tipo) | 44,2% elegible, 27,2% datos faltantes, 18,5% revisión humana, 10,1% rechazado | Versión preliminar; se reemplaza por la 0.3 (fila siguiente) |
| Credit policy 0.3 in gold (Databricks), 150,000 customers | 50,707 eligible (33.8%); 24,953 with a proactive offer; no offer mainly because of missing income (30,033, recoverable in the chat) or a blocked product (25,519). The Python implementation matches gold on all 1.8 M options | Synthetic policy defined by the team; no external ground truth |
| Data pipeline in Databricks (bronze → silver → gold) | Full job in ~14 min; 90 quality metrics per run; 0 key duplicates; update fixture: 5 of 5 cases correct | Static data: updates are shown with a labeled fixture, not real deliveries |
| Baseline de fraude con `fraud_score` del organizador | PR-AUC 0,577; recall 58% revisando el 1% con mayor riesgo; precisión 5% | Sin `fraud_score`, ningún modelo supera al azar en estos datos |

Detalle, errores hallados y lo que falta medir: [`docs/EVALUATION.md`](docs/EVALUATION.md).

## Cómo responde a lo que pide el reto

| El reto pide | Dónde está |
|---|---|
| Problema respaldado por datos | [`docs/DATA.md`](docs/DATA.md), [`analysis/`](analysis/) |
| Sistema de IA funcionando | [`backend/`](backend/), [`frontend/`](frontend/) |
| Automatización controlada, permisos fuera del texto del modelo, resumen para el humano | `backend/app/agent/`, `backend/app/policy/`, credit rules in [`docs/CREDIT_RULES.md`](docs/CREDIT_RULES.md) |
| Práctica de datos y ML sana | [`data/databricks/`](data/databricks/) (medallion, quality, contract, fixture), [`data/reference/`](data/reference/), [`data/policy/`](data/policy/), [`analysis/`](analysis/), [`backend/eval/`](backend/eval/) |
| Calidad medida y manejo de fallos | [`docs/EVALUATION.md`](docs/EVALUATION.md), `backend/tests/` |
| Ruta a operación | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/RUNBOOK.md`](docs/RUNBOOK.md), [`data/databricks/README.md`](data/databricks/README.md) (jobs, metrics, retries) |

## Estructura

```
backend/    API FastAPI, agente, política de crédito, pruebas, evaluación del NLU, Dockerfile
frontend/   interfaz de chat (HTML/CSS/JS sin dependencias) + nginx
data/databricks/  medallion pipeline in Databricks: bronze, silver, gold, quality, jobs (Asset Bundle), fixture
data/reference/   synthetic reference tables: product catalog, rate and term grid, credit policy parameters
data/policy/      reference implementation of the credit policy (Python) and its tests
data/scripts/     reference loading, SQL runner, gold export, engine parity check
data/sql/         gold layer SQL in DuckDB (local exploration)
analysis/   exploración de datos, baselines (notebooks) y scripts de validación
docs/       arquitectura, datos, evaluación, runbook
```

## Datos

El repositorio **no incluye datos del organizador**. Incluye un conjunto de ejemplo inventado por el equipo
(`backend/data/fixture/`, generado con `backend/scripts/make_fixture.py`) con la misma forma que el dataset. Para
reconstruir el snapshot derivado del dataset real se necesita acceso al bucket del organizador; ver
[`docs/DATA.md`](docs/DATA.md). Nunca suba credenciales ni el `.env`.

## Límites conocidos y trabajo restante

- **Falta una evaluación de punta a punta** con un conjunto reservado de conversaciones completas y las métricas del
  enunciado (resolución automática segura, contención, calidad del escalamiento, resultados inseguros con
  denominadores, latencia y costo por resolución, por idioma). Hoy hay pruebas unitarias y la evaluación del NLU.
- The backend still evaluates with the preliminary policy (`backend/policy/credit_policy.yaml`). The reference version is
  0.3 ([`docs/CREDIT_RULES.md`](docs/CREDIT_RULES.md), [`data/reference/`](data/reference/)), already computed in
  Databricks; aligning the engine is in progress ([`docs/ENGINE_ALIGNMENT.md`](docs/ENGINE_ALIGNMENT.md)).
- There is no learned risk model: the band comes from `credit_score`. In this data delinquency is unrelated to the score,
  so a model has little signal; it still has to be evaluated against that baseline.
- La verificación por preguntas de seguridad tiene 1/64 de probabilidad de acierto al azar por intento; mitigada con
  bloqueo, pero no sustituye un segundo factor real.
- Sesiones, bloqueos y cola de derivaciones viven en memoria (una réplica). Para producción hay que externalizarlos.
- The data layer runs in Databricks as bundle jobs (`data/databricks/`), but the demo reads a Parquet export (static data,
  no credentials in the container). Least-privilege service principals, alerts on the quality metrics and automatic
  deployment to production are described, not implemented (see `docs/ARCHITECTURE.md`).
- La interfaz solo se probó a mano; no hay pruebas automáticas ni auditoría de accesibilidad con lector de pantalla.

## Cómo trabajamos

Ramas por tema (`feat/…`, `fix/…`, `docs/…`), PRs pequeños con descripción y pruebas, commits con prefijo
(`feat:`, `fix:`, `docs:`, `test:`, `chore:`) y versiones con tags (`v0.x.0`). La CI (`.github/workflows/ci.yml`) ejecuta
las pruebas, construye las imágenes y revisa que no haya secretos.
