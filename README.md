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
| Política de crédito 0.3 en gold (Databricks), 150.000 clientes | 50.707 elegibles (33,8%); 24.953 con oferta proactiva; sin oferta sobre todo por ingreso faltante (30.033, recuperable en el chat) o producto bloqueado (25.519). La implementación en Python coincide con gold en las 1,8 M opciones | Política sintética definida por el equipo; sin verdad de terreno externa |
| Pipeline de datos en Databricks (bronze → silver → gold) | Job completo en ~14 min; 90 métricas de calidad por corrida; 0 duplicados por clave; fixture de actualización: 5 de 5 casos correctos | Datos estáticos: la actualización se demuestra con un fixture etiquetado, no con entregas reales |
| Baseline de fraude con `fraud_score` del organizador | PR-AUC 0,577; recall 58% revisando el 1% con mayor riesgo; precisión 5% | Sin `fraud_score`, ningún modelo supera al azar en estos datos |

Detalle, errores hallados y lo que falta medir: [`docs/EVALUATION.md`](docs/EVALUATION.md).

## Cómo responde a lo que pide el reto

| El reto pide | Dónde está |
|---|---|
| Problema respaldado por datos | [`docs/DATA.md`](docs/DATA.md), [`analysis/`](analysis/) |
| Sistema de IA funcionando | [`backend/`](backend/), [`frontend/`](frontend/) |
| Automatización controlada, permisos fuera del texto del modelo, resumen para el humano | `backend/app/agent/`, `backend/app/policy/`, reglas de crédito en [`docs/CREDIT_RULES.md`](docs/CREDIT_RULES.md) |
| Práctica de datos y ML sana | [`data/databricks/`](data/databricks/) (medallion, calidad, contrato, fixture), [`data/reference/`](data/reference/), [`data/policy/`](data/policy/), [`analysis/`](analysis/), [`backend/eval/`](backend/eval/) |
| Calidad medida y manejo de fallos | [`docs/EVALUATION.md`](docs/EVALUATION.md), `backend/tests/` |
| Ruta a operación | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/RUNBOOK.md`](docs/RUNBOOK.md), [`data/databricks/README.md`](data/databricks/README.md) (jobs, métricas, reintentos) |

## Estructura

```
backend/    API FastAPI, agente, política de crédito, pruebas, evaluación del NLU, Dockerfile
frontend/   interfaz de chat (HTML/CSS/JS sin dependencias) + nginx
data/databricks/  pipeline medallion en Databricks: bronze, silver, gold, calidad, jobs (Asset Bundle), fixture
data/reference/   tablas de referencia sintéticas: catálogo, grilla de tasas y plazos, parámetros de la política
data/policy/      implementación de referencia de la política de crédito (Python) y sus pruebas
data/scripts/     carga de referencias, ejecución de SQL, exportación de gold, prueba de paridad
data/sql/   SQL de la capa gold en DuckDB (exploración local)
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
- El backend todavía evalúa con la política preliminar (`backend/policy/credit_policy.yaml`). La versión de referencia es la
  0.3 ([`docs/CREDIT_RULES.md`](docs/CREDIT_RULES.md), [`data/reference/`](data/reference/)), ya calculada en Databricks;
  la alineación del motor está en curso ([`docs/ENGINE_ALIGNMENT.md`](docs/ENGINE_ALIGNMENT.md)).
- No hay modelo de riesgo aprendido: la banda sale del `credit_score`. En estos datos la mora no se relaciona con el score,
  así que un modelo tiene poca señal; queda por evaluar contra ese baseline.
- La verificación por preguntas de seguridad tiene 1/64 de probabilidad de acierto al azar por intento; mitigada con
  bloqueo, pero no sustituye un segundo factor real.
- Sesiones, bloqueos y cola de derivaciones viven en memoria (una réplica). Para producción hay que externalizarlos.
- La capa de datos corre en Databricks como jobs del bundle (`data/databricks/`), pero la demo lee una exportación a Parquet
  (datos estáticos, sin credenciales en el contenedor). Permisos mínimos por *service principal*, alertas sobre las
  métricas y despliegue automático a producción están descritos, no implementados (ver `docs/ARCHITECTURE.md`).
- La interfaz solo se probó a mano; no hay pruebas automáticas ni auditoría de accesibilidad con lector de pantalla.

## Cómo trabajamos

Ramas por tema (`feat/…`, `fix/…`, `docs/…`), PRs pequeños con descripción y pruebas, commits con prefijo
(`feat:`, `fix:`, `docs:`, `test:`, `chore:`) y versiones con tags (`v0.x.0`). La CI (`.github/workflows/ci.yml`) ejecuta
las pruebas, construye las imágenes y revisa que no haya secretos.
