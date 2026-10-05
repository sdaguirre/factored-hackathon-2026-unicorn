# AI credit assistant for banking

Prototype for the **Factored AI & Data Hackathon 2026**: a customer-service chat for a Latin American bank (Mexico,
Colombia, Argentina) in **Spanish and Portuguese**. It verifies identity with security questions, shows the customer's
pre-approved credit offers (personal loan, credit card, mortgage), simulates amounts and terms with a deterministic
policy, recalculates live with what the customer declares, and hands every interested customer to a human advisor with
a structured summary. Anything that is not about credit offers is handed off to an advisor.

> **Synthetic data and policy.** The data is synthetic (organizer and team) and the credit policy was defined by the
> team. No answer from the assistant is a credit approval: every offer is an indicative simulation.

## Core idea

An LLM handles **language only** (understanding, clarifying, wording low-risk messages). Everything that commits the
bank is decided by **code**: the policy engine computes eligibility, maximum amount and rate; the tools enforce
per-customer permissions; actions ask for confirmation. The model cannot approve credit, hand off to a human unless the
customer asked, or introduce figures that are not in the computed facts. The assistant's only data source is the
**gold layer** built in Databricks (credit profile and policy tables).

```
Customer ─► Web UI ─► API (test session, trace_id)
                          │
                          ▼
                 Orchestrator (state machine)
       ┌───────────────┼──────────────────────────┐
       ▼               ▼                          ▼
 LLM: intent +     Tools (permissions of the   Policy engine
 language + tone   session's customer)         (policy 0.4 = gold)
       │               │                          │
       └───────► verified facts ◄─────────────────┘
                          │
        es/pt reply  ·  simulation notice  ·  proactive offer  ·  advisor handoff
```

## Try it

Requires Docker. A clean clone needs no organizer data and no keys: it uses an example set invented by the team and a
rules-based extractor.

```bash
docker compose up --build        # UI at http://localhost:8080
```

- Test documents and customers, and how to answer the security questions: [`backend/DATOS_DE_PRUEBA.md`](backend/DATOS_DE_PRUEBA.md).
- To use Claude as the model: copy `backend/.env.example` to `backend/.env` and set `CHAT_LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`.
- How to start the service for evaluation, check it and shut it down: [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

## What the assistant does

- **Credit offers only.** Shows the highest pre-approved option of each product, simulates other amounts and terms,
  recalculates with declared income and household income (with that person's installments), explains which policy
  rule blocks an offer, and collects the documents before handing the lead to an advisor.
- **Everything else goes to an advisor**, after the customer confirms and without showing bank data (balances,
  products, complaint status, other banking topics). Incidents such as fraud get one line of empathy first.
- **Simulation notice.** Every reply with offer figures, and the final offer summary, carry a notice that the UI shows
  as its own panel: it is a simulation, not an approval; an advisor verifies and the bank decides.
- **Proactive offers** only when the gold profile allows it (`offer_mode = proactive`: eligible, marketing consent, no
  open critical complaint) and at the end of the conversation.

## What has been measured

| What | Result | Limits |
|---|---|---|
| Backend automated tests | 253 tests, no network | Do not cover the conversational quality of the real model |
| Scripted end-to-end conversations | 9 full conversations with 9 fixture customers (es/pt): offers, recalculation, household income, age cap, decline reasons, incidents, injection, handoff | Rules NLU; smoke test, not a held-out evaluation |
| NLU intent, first held-out set (29 es/pt phrases) | Rules 79%. Claude 93–97% (two runs) | Single annotator; small sample |
| NLU intent, held-out set v2 (114 es/pt phrases) | Two annotators, Cohen's kappa 0.93; rules baseline 68% (95% CI 59–76%) on the adjudicated labels | Claude run pending (needs the API key) |
| Live test with Claude Haiku 4.5 (13 turns) | 0 fallbacks to rules; ~1.1 s per call (p95 1.7 s); ~490 input and ~105 output tokens per turn | Small sample, not a benchmark |
| Credit policy 0.4 in gold (Databricks), 150,000 customers | 50,707 eligible (33.8%); 24,953 with a proactive offer; no offer mainly because of missing income (30,033, recoverable in the chat) or a blocked product (25,519). The Python implementation matches gold on all 1.8 M options | Synthetic policy defined by the team; no external ground truth |
| Data pipeline in Databricks (bronze → silver → gold) | Full job in ~14 min; 94 quality metrics per run; 0 key duplicates; update fixture: 5 of 5 cases correct | Static data: updates are shown with a labeled fixture, not real deliveries |
| Fraud baseline with the organizer's `fraud_score` | PR-AUC 0.577; recall 58% reviewing the riskiest 1%; precision 5% | Without `fraud_score`, no model beats chance on this data |

Details, errors found and what is still to be measured: [`docs/EVALUATION.md`](docs/EVALUATION.md).

## How it answers the challenge

| The challenge asks for | Where it is |
|---|---|
| A problem backed by data | [`docs/DATA.md`](docs/DATA.md), [`analysis/`](analysis/) |
| A working AI system | [`backend/`](backend/), [`frontend/`](frontend/) |
| Controlled automation, permissions outside the model's text, a summary for the human | `backend/app/agent/`, `backend/app/policy/`, credit rules in [`docs/CREDIT_RULES.md`](docs/CREDIT_RULES.md) |
| Sound data and ML practice | [`data/databricks/`](data/databricks/) (medallion, quality, contract, fixture), [`data/reference/`](data/reference/), [`data/policy/`](data/policy/), [`analysis/`](analysis/), [`backend/eval/`](backend/eval/) |
| Measured quality and failure handling | [`docs/EVALUATION.md`](docs/EVALUATION.md), `backend/tests/` |
| A route to operation | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/RUNBOOK.md`](docs/RUNBOOK.md), [`data/databricks/README.md`](data/databricks/README.md) (jobs, metrics, retries) |

## Structure

```
backend/          FastAPI API, agent, credit policy engine, tests, NLU evaluation, Dockerfile
frontend/         chat UI (HTML/CSS/JS without dependencies) + nginx
data/databricks/  medallion pipeline in Databricks: bronze, silver, gold, quality, jobs (Asset Bundle), fixture
data/reference/   synthetic reference tables: product catalog, rate and term grid, credit policy parameters
data/policy/      reference implementation of the credit policy (Python) and its tests
data/scripts/     reference loading, SQL runner, gold export, engine parity check
data/sql/         gold layer SQL in DuckDB (local exploration)
analysis/         data exploration, baselines (notebooks) and validation scripts
docs/             architecture, data, credit rules, evaluation, runbook
```

## Data

The repository **does not include organizer data**. It includes an example set invented by the team
(`backend/data/fixture/`, generated with `backend/scripts/make_fixture.py`) with the same shape as the dataset. The
assistant reads the gold credit profile; the customer master is used only for the identity check. Rebuilding the
snapshot derived from the real dataset needs access to the organizer's bucket; see [`docs/DATA.md`](docs/DATA.md).
Never commit credentials or the `.env` file.

## Known limits and remaining work

- **The end-to-end evaluation is not finished**: a held-out set of full conversations with the challenge's metrics
  (safe automated resolution, containment, escalation quality, unsafe outcomes with denominators, latency and cost per
  resolution, by language). Today there are unit tests, scripted conversations and the NLU evaluation.
- The backend evaluates with policy 0.4 ([`docs/CREDIT_RULES.md`](docs/CREDIT_RULES.md), [`data/reference/`](data/reference/)),
  the same version computed in Databricks gold: 0 mismatches on all 1.8 M offer options
  ([`docs/ENGINE_ALIGNMENT.md`](docs/ENGINE_ALIGNMENT.md)). Accepted offers are written locally and synced to
  `credit_offers` with a script; the demo does not query Databricks live.
- There is no learned risk model: the band comes from `credit_score`. In this data delinquency is unrelated to the score,
  so a model has little signal; it still has to be evaluated against that baseline.
- The security-question check has a 1/64 chance of a random guess passing per attempt; mitigated with lockout, but it
  does not replace a real second factor.
- Sessions, lockouts and the handoff queue live in memory (one replica). Production needs them externalized.
- The data layer runs in Databricks as bundle jobs (`data/databricks/`), but the demo reads a Parquet export (static data,
  no credentials in the container). Least-privilege service principals, alerts on the quality metrics and automatic
  deployment to production are described, not implemented (see `docs/ARCHITECTURE.md`).
- The UI was only tested by hand; there are no automated UI tests and no screen-reader accessibility audit.

## How we work

Topic branches (`feat/…`, `fix/…`, `docs/…`), small PRs with a description and tests, prefixed commits
(`feat:`, `fix:`, `docs:`, `test:`, `chore:`) and tagged versions (`v0.x.0`). CI (`.github/workflows/ci.yml`) runs the
tests, builds the images and checks for secrets.
