# Architecture

Credit chat with identity verification, a deterministic policy, proactive offers and handoff to a human advisor.
Synthetic data and policy (prototype).

## Main decision: the LLM talks, code decides

| Function | Who does it | Why |
|---|---|---|
| Understand the message, detect language, sentiment and sensitive topics | LLM, validated JSON output | It is language, not a decision |
| Word greetings, closings and clarifying questions | LLM, **low-risk messages only** | Decisions and offers come from reviewed templates |
| Eligibility, maximum amount and rate | Deterministic engine: policy 0.4 ([`CREDIT_RULES.md`](CREDIT_RULES.md)), the same as gold, through `data/policy/` (`backend/app/policy/engine.py`) | The model cannot approve credit or invent rules |
| Customer data and permissions | Tools (`backend/app/agent/tools.py`), with the `customer_id` of the authenticated session | No tool takes a `customer_id` from the model |
| Hand off to a human | Code: only if the customer asked explicitly or confirmed a handoff offer | A handoff is an action |
| When to offer credit unasked | Code (`backend/app/agent/proactive.py`) on gold `offer_mode` | It is a commercial and consent decision |
| Predictive credit risk | **Not trained** | No signal in the data (correlation −0.004; see `docs/DATA.md`) |

## Flow

```
Customer ─► UI (nginx) ─► /v1 ─► FastAPI API ─► Orchestrator
                                     │               ├─► NLU: Claude or rules (canonical schema in Spanish)
                                     │               ├─► Validation in code (amounts, terms, explicit handoff)
                                     │               ├─► Credit policy + tools (gold data)
                                     │               └─► es/pt templates (+ bounded LLM rewording)
                                     └─► Session (JWT bound to the session) · lockout per document · JSON logs with trace_id
```

The UI's nginx serves the page and forwards `/v1` to the backend, adding the integration key on the server; the backend
port is not published and `/v1/handoffs` (the agent console queue) is blocked at the proxy.

## Scope and data the assistant uses

The chat handles **credit offers only** (personal loan, credit card, mortgage): showing the pre-approved offers,
simulating amounts and terms, recalculating with what the customer declares and handing the lead to an advisor.
Anything else (balances, products, the status of a complaint, the app, other banking topics) is **handed off to an
advisor after the customer confirms**, without showing bank data; incidents (fraud, an unrecognized charge) get one
line of empathy first. What the customer says is kept as declared, unverified notes for the advisor.

The assistant's only data source is **gold** (`customer_credit_profile` and the policy tables, through the export):
offers, eligibility and reason codes, the proactive decision (`offer_mode`), complaint counts (which already drive
`offer_mode` and flag F04), credit product counts for the advisor summary, and the exchange rate (`fx_to_usd`,
`fx_date`). It does not read raw cases, products, transactions or FX files, and they are not generated or shipped. The
identity check (security questions, masked e-mail, documents on file) uses the customer master: that is
authentication, not assistant knowledge. Offers and the final summary carry a `disclaimer` field (`simulation` /
`final`) that the UI draws as its own panel.

## Credit conversation: currency, application and closing

The orchestrator is a state machine (`awaiting`: `offer_interest`, `amount`, `income`, `household`, `proceed`,
`docs_all`, `doc_item`). Currency is detected and converted in code (`money.py`, gold rates); pending documents come
from the policy and `docs_on_file` (`documents.py`); closing (`/end`) builds the summary and leaves the PDF in a
simulated outbox (`core/outbox.py`, `core/pdf.py`). The LLM only classifies and words low-risk messages; identity
questions ("are you a robot?") are answered from a template and any text claiming to be human is discarded.

## Authentication

Three single-choice security questions generated from the customer's data: registered occupation, city where a product
was opened (only if opened at a branch), year a product was opened and year the person became a customer. No amounts or
exact dates; distractors always come from the same universe (same country, valid years) and the question carries no
real data such as card endings. All answers must be right; 3 failures lock the document for 15 minutes; an unknown
document gets a decoy challenge of the same shape. Known limit: a random guess passes 1 in 64 times per attempt.
Details, measured coverage and limits in `backend/README.md`.

## Proactive offer

When the conversation closes, an indicative offer is made **only if** gold marks the customer as `offer_mode =
proactive` (eligible with bank data, marketing consent and no open critical complaint), there was no negative sentiment
or sensitive topic, no request was declined, no offer was made already and no non-credit topic was handled in the
session. A customer who asks for credit is evaluated **without** looking at marketing consent: consent only governs the
proactive path.

## Language

The LLM does not translate as a separate step: it understands the message and returns canonical values in Spanish
(intent, product, amount, income, language). The reply comes out in the session's language from reviewed Spanish and
Portuguese templates. Amounts and terms are parsed in **code** with local formats (1.500,00 and 1,500.00). In rules
mode the language is detected from words specific to each language, so a word written the same in both ("crédito",
"pesos") does not switch the conversation. The organizer data has no Portuguese: the Portuguese tests were written by
the team.

## Data

The data layer runs in **Databricks** (Unity Catalog) as a medallion pipeline defined as code
([`data/databricks/`](../data/databricks/README.md), Databricks Asset Bundle):

```
S3 (organizer CSVs) → landing → bronze (all STRING, _rescued_data, lineage)
  → silver (typed, deduplicated by key, latest version wins)
  → gold: customer_credit_profile, customer_credit_offer_options, credit_offers, customer summaries
```

- **Credit policy as data:** the silver `ref_*` tables (catalog, rate and term grid, bands, segments, parameters) come from
  [`data/reference/`](../data/reference/) and are version 0.4 of the rules ([`CREDIT_RULES.md`](CREDIT_RULES.md)). Gold
  uses them to compute eligibility and offers for the 150,000 customers.
- **Jobs:** `latam_bank_medallion` (bronze → gold, ~14 min, daily at 06:00 but paused because the data is static),
  `credit_policy_refresh` (recomputes offers when the policy changes, ~1 min), `credit_gold_deploy` and
  `data_update_fixture_test`. Bounded retry per task, timeouts and one run at a time.
- **Quality and contract:** every run writes its metrics to `pipeline_quality_metrics` (history, failed runs included) and
  then stops if one fails: key and content duplicates, nulls per critical column with its own threshold, rescued rows from
  schema changes, the 20% capacity and band terms in the offers, and the **contract** of columns and types the API reads.
- **Freshness and updates:** the data ends in June 2026 and no new deliveries arrive; offers are computed as of 2026-06-30
  (`as_of_date`, a job parameter). With live data the cutoff would be the run date, and each run absorbs late arrivals
  (full reload and dedup by `process_date`). Update correctness is shown with a labeled fixture: late update, exact
  duplicate, late arrival, schema change and null key, all five handled correctly.
- **Consumption:** the backend reads a Parquet export of gold behind the `CustomerRepository` interface
  (`data/scripts/export_gold.py`), with no Databricks credentials in the container; in production it would read gold with
  row filters. Without the export it uses the team's sample set. `data/sql/` keeps the DuckDB version for local work.
- **Access (today and in production):** during the hackathon the team has broad permissions on the schemas. In
  production: a pipeline service principal writes bronze/silver/gold; an API service principal only reads the profile and
  options and inserts into `credit_offers`; people get read-only access.
- **Retention:** bronze and silver can be rebuilt from the source CSVs. `credit_offers` holds `customer_id` and would be
  kept for what each country's credit regulation requires, with `VACUUM` of the Delta history; quality metrics are kept as
  the pipeline audit trail.
- **Capacity:** the full job processes ~23 M rows in ~14 min on a 2X-Small serverless warehouse; the offer refresh takes
  ~1 min and grows with customers × 12 options. The API does not query Databricks online.

## Operations

- **Traceability:** every request carries `X-Trace-Id`; logs are JSON with latency, intent, outcome and, for the model,
  tokens and time. Documents, security answers and request bodies are not logged.
- **Bounded retries and safe fallback:** the model client retries once; if it fails or returns something invalid, the
  turn falls back to the rules extractor. The model's text is discarded if it carries figures foreign to the computed
  facts. The customer's message is escaped before it reaches the model.
- **Containers:** unprivileged images with a read-only file system; `docker compose up` starts everything; accepted
  offers are written to a mounted, git-ignored folder so a restart does not lose them.
- **Cost:** one model call to understand each message, plus another only for low-risk messages. With Haiku 4.5,
  ≈ 490 input and ≈ 105 output tokens per turn were measured (small sample).

## What is missing for production

- Externalize the in-memory state (sessions, lockouts and handoff queue) for several replicas.
- A real second authentication factor; the current questions are a simulation with data from the same dataset.
- Persist the last offer per customer (today the one-offer cap is per session) and rate limiting per IP.
- Business validation of the credit policy, and a learned risk model evaluated against the `credit_score` baseline
  (today the band comes from the score).
- End-to-end evaluation with a held-out set of conversations (see `docs/EVALUATION.md`).
- Data: least-privilege service principals, alerts on `pipeline_quality_metrics`, automatic bundle deployment from CI,
  incremental loads by `process_date` if new deliveries arrived, and the backend reading gold online.
