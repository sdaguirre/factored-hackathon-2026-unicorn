# backend

FastAPI backend of the credit chat: it verifies identity with security questions, talks in Spanish and Portuguese,
applies a deterministic credit policy and hands off to a human advisor with a structured summary. It is **decoupled
from any UI**: any site or channel that speaks HTTP/JSON can integrate it.

> **Synthetic** data and policy. This is not a real banking system. See "Known limits".

## Quick start

With Docker (Docker Desktop must be running):

```bash
docker compose up --build
```

Without Docker:

```bash
pip install -r requirements-dev.txt
CHAT_JWT_SECRET=a-secret-of-at-least-32-characters uvicorn app.main:app --port 8000
```

The API is at `http://localhost:8000`; the interactive documentation (OpenAPI) at `/docs`.
`CHAT_LLM_PROVIDER=mock` is the default: it works without network or keys.

Full smoke test (simulates an account holder who knows their data and talks in es or pt):

```bash
python scripts/demo_client.py --base http://localhost:8000 --lang es
```

## Integration flow

```
POST /v1/sessions                      document + language  -> session_id, token, 3 security questions
POST /v1/sessions/{id}/verify          answers              -> authenticated | failed (new challenge) | 403 locked
POST /v1/sessions/{id}/messages        message              -> reply, intent, outcome, disclaimer, handoff ticket
GET  /v1/sessions/{id}/handoff         summary for the human advisor (of that session)
GET  /v1/handoffs                      handoff queue (agent console, separate key)
POST /v1/sessions/{id}/end             closing: proposal summary + e-mail notice (simulated)
GET  /v1/sessions/{id}/summary.pdf     summary PDF (only if there was a proposal)
DELETE /v1/sessions/{id}
GET  /health  ·  GET /v1/meta
```

- Every `/v1` call sends `X-API-Key` if keys are configured (`CHAT_API_KEYS`), and session calls also send
  `Authorization: Bearer <token>`.
- The client never sees the right answers: it receives random option ids per challenge.
- Errors always have the shape `{"error": {"code", "message", "trace_id"}}`. Codes: `INVALID_API_KEY`,
  `INVALID_TOKEN`, `SESSION_EXPIRED`, `AUTH_REQUIRED`, `AUTH_LOCKED`, `AUTH_UNAVAILABLE`, `MESSAGE_TOO_LONG`,
  `VALIDATION_ERROR`, `NO_HANDOFF`, `SESSION_ENDED`, `NO_SUMMARY`, `INTERNAL_ERROR`.
- On authentication, `verify` also returns `customer` (id, name, country, segment, status: no document or financial
  data), a credit-only `greeting` and the first quick replies.
- `messages` returns `disclaimer` (`simulation` on replies that present offer figures, `final` on the final offer
  summary, otherwise `null`); the UI draws it as its own panel.
- `messages` and `end` return `evidence` **only if the agent used tools in that turn** (`null` for a greeting or a
  thank-you): `steps[]` (tools actually executed and their status), `verification[]` (`verified`, `inconclusive`,
  `unverified` for data declared by the customer, `reference` for reference rates), `customer` (profile read) and
  `evaluation` (this turn's policy evaluation). It is built in `app/agent/evidence.py` from the real calls to
  `tools.py`; if a tool fails, `verification` is empty. The UI only draws this: it does not infer verifications.
- Every response carries `X-Trace-Id` (an incoming one is accepted) to correlate with the JSON logs.
- An external website must call this API **from its server**: an `X-API-Key` in the browser is not secret. For direct
  calls from the browser, configure `CHAT_CORS_ORIGINS` and treat the key as an identifier.

Example (documents of the team's example set, see `DATOS_DE_PRUEBA.md`; e.g. `53464097`, Mexico):

```bash
curl -s localhost:8000/v1/sessions -H 'Content-Type: application/json' \
     -d '{"document_number":"53464097","language":"es"}'
```

## Authentication with security questions

Three questions of different types, four options each, generated from the customer's data. The series (occupation,
city and years) was chosen by measuring each candidate on the 150,000 customers: **no amounts, no exact dates and no
need to remember a transaction**.

| Type | Example | Coverage | Random guess |
|---|---|---|---|
| `occupation` | What is your occupation registered at the bank? | 90% | 25% (20 uniform values) |
| `product_city` | In which city did you open your savings account? (only products opened at a branch) | 63% | 25%, with distractors from the **same country** |
| `product_year` | In which year did you open your oldest credit card? | 90% | 25% |
| `customer_since` | In which year did you become a customer of the bank? | 100% | 25% |

With these four types, **87%** of customers have data for 3 questions of different types (79% with the previous
series). Customers without it receive `AUTH_UNAVAILABLE` and are referred to an advisor.

Design rules:
- **A product is named by its type** ("your savings account") or, if there are several of the same type, "the oldest"
  (no ties). Never by its last digits: the question is shown **before** authentication and must not carry real data.
- **Distractors come from the same universe as the answer**: cities of the same country and valid years. With
  distractors from the three countries (there are only 16 cities), someone who knew the customer's country got 63% right.
- **The city is only asked if the product was opened at a branch**: someone who opened online has no city to remember.
- **One kind of year is preferred** per challenge. With two, the registration year and the opening year may not match
  in this data.
- **All** answers must be right; 3 failures per document lock it for 15 minutes (even in new sessions); each failure
  brings new questions; verification is constant-time; the LLM neither sees nor generates the questions.
- An unknown document gets a **decoy challenge**: the question *types* come from a random real customer (so the mix
  matches real customers) and the content is invented, never from that customer.

Why the previous questions were dropped (measured): the **amount** of a transaction is hard to remember and an attacker
choosing the central value gets 45% right; the **city of a transaction** matches the city of residence in 95% of
transactions; the opening **month** needs more memory than the year. Also excluded: date of birth (it is on the ID
document), phone, card expiry (printed on it), marital status and education (sensitive), and the most frequent merchant
(only 3% of customers have a clearly dominant category).

**Known limits.** (1) The question text still reveals the *type* of one of the customer's products before
authentication (without last digits): less than before, but not zero. (2) `customer_since` uses the registration date,
which in this dataset does not match the first product (`docs/DATA.md`): it is kept to reach 87% coverage; without it,
coverage would drop to 57%. (3) Everything was measured on synthetic data: with real data the distributions will
differ. (4) Security questions are weak by nature; what protects is the attempt limit and the lockout.

## Agent

- **LLM** (optional): extracts intent and data into a canonical Spanish schema and can reword low-risk replies to sound
  more natural. It does not decide, calculate or execute actions.
- **Code**: checks amounts and terms against the original text, parses es/pt number formats, applies credit policy 0.4
  (`app/policy/engine.py`, see "Credit policy") and runs the tools.
- **Tools**: none takes a `customer_id`; the context is set by the authenticated session. The credit tools are
  `get_offers()`, `recalculate_offer(...)` and `accept_offer(...)` (`app/agent/tools.py`).
- **Actions with confirmation**: a handoff to a human only happens after an explicit "yes" (or if the customer asks).
- **LLM reply**: discarded if it contains numbers that are not in the computed facts. The customer's message is escaped
  inside `<user_message>` so it cannot close the tag and write outside it.
- **Handoff summary**: request, evaluation with policy version, verified facts, actions, open questions and the last
  messages. No model chain of thought. Customer context: see "Scope, customer context and the advisor summary".

To use Claude: `CHAT_LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` in `.env` (it does not go into the image). If the
call fails or returns something invalid, the turn falls back to the rules extractor.

**What the model can and cannot do** (verified with tests without network and with the real model):

- It extracts intent and data. Code checks amounts and terms against the original text, and a **handoff to a human is
  only accepted if the customer explicitly asked for it**; an incident (unrecognized charge, fraud) asks for
  confirmation.
- It only rewords low-risk messages (greeting, closing, amount or income questions, accepting or declining an offer,
  non-credit topic, incident, noted detail). Credit decisions, offers, handoffs, simulation notices and **every message
  with bank facts** always come **from the reviewed template**: the number guard does not catch new sentences that change
  the commitment. Configured with `CHAT_LLM_REWRITE_KINDS` (comma-separated list; `none` = the model never rewords).
- Its text is discarded if it carries numbers that are not in the computed facts, if it **promises** something
  (outcome, deadline, refund: "it will be solved", "I guarantee"…) or if, in a message that expects an answer (e.g.
  "shall I connect you?"), it stops asking.

Measurement with `claude-haiku-4-5-20251001` (13 turns, 5 conversations in es/pt, `scripts/e2e_llm.py`):
18 calls, 0 fallbacks to rules, latency per call p50 ≈ 1.1 s and p95 ≈ 1.7 s, about 490 input and 105 output tokens
per turn. A small sample, not a benchmark. `scripts/smoke_llm.py` tests the key and the extraction.
`tests/test_llm_anthropic_client.py` covers the integration code with a fake Anthropic client (prompt, JSON parsing,
rejection of malformed or out-of-range output, fallback on timeouts and 429/529 errors).

### NLU evaluation (intent, sentiment, sensitive topic)

`python scripts/eval_nlu.py` measures the rules extractor and the model with es/pt phrases (`eval/nlu_cases.py`).
There is a development set (78 phrases, used to tune the prompt and the rules) and a held-out set (29 phrases, written
before tuning and measured once).

| Set | Rules | Claude (claude-haiku-4-5) |
|---|---|---|
| Development (78), **optimistic**: tuned while looking at these errors | 77/78 (99%) | **not re-measured** (see below) |
| **Held-out (29)**, already seen: **not clean** (see below) | 27/29 (93%) | 28/29 and 27/29 with the previous prompt |

**Taxonomy change.** The intents `account_inquiry` (balance, movements, which products) and `case_status` (status of a
complaint or case already open) were added; an incident is still `other_topic` with `sensitive_topic`. Both are now
handed off to an advisor (credit-only scope). Because of this, **4 phrases were relabeled** and 13 added to development.
When measuring, "alguien usó mi tarjeta sin permiso" moved from other topic to `account_inquiry`; it was fixed by
extending the unauthorized-use markers in the rules. **That fix was made looking at a held-out phrase, so the held-out
set is no longer clean for the rules.** Claude's prompt also changed and **was not re-measured with the real model**
(no key in the development environment): run `python scripts/eval_nlu.py` with `ANTHROPIC_API_KEY` before quoting a
model figure. A new held-out set (v2, 114 phrases) is in `docs/EVALUATION.md`.

Before tuning, the model got 49/60 (82%): it confused rates and limits with another topic in Portuguese and took "no
gracias" as a goodbye because it did not know a question was pending (now it is told). In the held-out set, the only
consistent failure was "necesito que me atienda una persona": the explicit-handoff guard downgraded it because its
lexicon did not cover it; the lexicon was extended, but **that fix was made looking at the held-out set, so that figure
is no longer clean**. Sensitive-topic detection (rules): 2/2 in held-out and 9/10 in development, 0 false positives.
Limits: small sample, single-annotator labels (the team must review them), and the two model runs differ in one phrase.

## Credit policy

The backend applies **policy 0.4** ([`docs/CREDIT_RULES.md`](../docs/CREDIT_RULES.md)), the same one gold computes in
Databricks. It does not reimplement the rules: `app/policy/engine.py` loads the reference implementation
(`data/policy/credit_policy.py`) and its parameters (`data/reference/*.csv`), and only maps a chat request to a grid
option. With the gold export, the backend engine matches all 1,800,000 options:

```bash
python data/scripts/export_gold.py --profile <profile>                       # gold export -> .local/gold/
python data/scripts/check_engine_parity.py --engine app.policy.engine:offer_options --pythonpath backend
```

- **Data.** The credit profile is the customer's gold `customer_credit_profile` row (USD), in `credit_profile.parquet`
  (`scripts/build_snapshot.py --gold-only` takes it from the export; `scripts/make_fixture.py` generates it for the team
  set). The options are recomputed in memory, identical to gold.
- **Rules.** Bands A–E with a segment adjustment; grid rate per term or card tier; maximum term per band and, since 0.4,
  by age at maturity (the loan ends before age 75; the backend never sees the birth date: gold delivers `max_term_*`
  already capped); 20% limit with no margin; credit card by tier (Classic, Gold, Platinum, Black). A term rejected by
  age is explained differently from one rejected by the band: the reason comes from the option's `unavailable_reason`
  (`term_above_age_at_maturity`), the same as gold. A declined credit states which rule failed (inactive, tenure,
  delinquency, blocked product, score); recent fraud is not disclosed.
- **Version.** At startup the profile `policy_version` (gold export) is compared with `data/reference`: if they differ,
  with `CHAT_ENV=dev` it only warns and outside dev it does not start (a 0.3 profile would offer terms without the age
  cap).
- **Highest first.** Without an amount, the assistant proposes the featured option (gold `is_featured`) and asks how
  much the customer needs; "yes" takes that maximum. "What offers do I have?" shows the featured option of each product.
  A new term or amount alone ("¿y a 36 meses?") recalculates the product on the table.
- **Declared data.** If the customer states their income, it replaces the registered one and the offer becomes
  **conditional** (`F03`). If the income was missing, the assistant asks for it (once more if the answer has no figure)
  and then continues with the product the customer had asked for. After the first result, **every customer** is asked
  the same way whether someone else in the household contributes income; if so, that person's income and installments
  are asked for (household income comes with its debt; both can come in one message). If the customer does not know
  those installments, the income is not added and the advisor completes it.
- **Currencies.** The policy works in USD (`fx_to_usd` of the profile); the customer sees and writes amounts in their
  local currency. Maximums are shown rounded down so that, converted back, they do not exceed the cap. The debt-to-income
  share is shown truncated to one decimal, so an offer at the limit never reads as "20.0% under a 20.0% maximum".
- **Accepted offer.** "Yes" to moving forward calls `accept_offer`: it recalculates (never takes figures from the text),
  validates amount and 20%, adds `F02`/`F03`/`F04` and writes the gold `credit_offers` row to `CHAT_OFFERS_PATH`
  (JSONL). On handoff, the row is rewritten with `status = handed_off` and the ticket. `scripts/sync_credit_offers.py`
  uploads the rows to Databricks with a `MERGE` by `offer_id` (without `--apply` it only shows what it would upload), so
  the container needs no credentials. With Docker Compose the file lives in `.local/offers/` on the host (volume,
  git-ignored) and survives restarts:
  `python backend/scripts/sync_credit_offers.py --file .local/offers/credit_offers.jsonl --gold-schema
  workspace.gold_latam_bank_test --apply --profile <profile> --warehouse-id <id>` (try it on `_test` first).
- **Agent rules** (`policy/agent_rules.yaml`): documents per product (and those of the household member if their income
  was added) and protected attributes. `date_of_birth` is only used, in gold, for the age-at-maturity term cap.

## Proactive credit offer

The chat offers credit on the bank's initiative only when the **conversation closes** ("thanks", "that's all"), and
only if no non-credit topic was handled in the session. It is a decision in code (`app/agent/proactive.py`); the LLM
only words the message. The `/messages` response carries `proactive_offer: true` and `awaiting: "offer_interest"`.

| Path | What it looks at | What it does NOT look at |
|---|---|---|
| **Reactive**: the customer asks for credit, rates or their capacity | Credit policy with bank data (and what they declare, as a conditional offer) | `accepts_marketing` |
| **Proactive**: the bank offers | All of the following at once (below) | Income declared in the chat |

It is offered only if **all** hold: gold marks the customer as `offer_mode = proactive` (eligible with bank data,
marketing consent and no open critical complaint) and there is an available option, presented starting with the highest
(personal loan; otherwise card; otherwise mortgage); there was no negative sentiment or sensitive topic (fraud, dispute,
complaint, stolen card, unrecognized charge) in the session; no request was declined in the session; it was not
offered already and the customer did not say no; and **nothing is pending**: no non-credit topic in this conversation
and no handoff. Complaints only count through gold (`offer_mode`). If accepted, it enters the normal eligibility flow;
if declined, it is not repeated. The offer goes to `actions_taken` and `verified_facts` of the handoff summary so the
advisor sees it.

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

## Currency, application, summary and tone

- **Currency.** Code (`app/agent/money.py`) detects the currency the customer mentions (USD, MXN, COP, ARS; "pesos"
  without a country is read as the customer's currency) and converts it to the customer's local currency with the gold
  rates (`fx_to_usd` and `fx_date` of the credit profiles, the same ones gold used for the offers: **not a live quote**).
  The message shows both amounts with the same rate. BRL and EUR are reported as unsupported instead of inventing a
  rate.
- **Application.** After a favorable evaluation the assistant asks whether to move forward. If yes, it computes the
  documents the agent rules require (`required_documents` in `policy/agent_rules.yaml`), subtracts those the bank
  already has (`docs_on_file`, synthetic) and asks one by one only for the missing ones. The chat **does not receive
  files**: the customer confirms they have them and the advisor verifies them. With everything in order the case is
  handed off with the summary; if something is missing, the customer is told what.
- **Closing.** On goodbye or `POST /end`, the assistant summarizes the offer and says the detail will arrive by e-mail as
  a PDF. **Sending is simulated:** the PDF and a `simulated_not_sent` JSON stay in `CHAT_OUTBOX_DIR` and only the masked
  e-mail is shown. The PDF is downloaded with `GET /summary.pdf`. An incident (fraud, complaint) never produces a
  commercial summary.
- **Tone and identity.** Replies use a warm tone and vary their wording. The assistant introduces itself once as a
  "virtual assistant", never claims to be a person and, if asked whether it is a robot or an AI, answers truthfully
  (`ask_identity` intent). Model text claiming to be human is discarded.

## Configuration (environment variables, see `.env.example`)

| Variable | Default | Use |
|---|---|---|
| `CHAT_ENV` | `dev` | With `dev`, `GET /v1/handoffs` needs no key; otherwise `CHAT_ADMIN_API_KEYS` is required |
| `CHAT_API_KEYS` | empty | Integration keys (comma-separated); empty = no key |
| `CHAT_JWT_SECRET` | random | Token secret (≥ 32 characters); if missing, sessions do not survive a restart |
| `CHAT_CORS_ORIGINS` | empty | Allowed origins |
| `CHAT_LLM_PROVIDER` | `mock` | `mock` or `anthropic` |
| `CHAT_DATA_DIR` | `data/snapshot` if it exists; otherwise `data/fixture` | Folder with the parquet files (mount as a volume in other environments) |
| `CHAT_OUTBOX_DIR` | system temp folder | Outbox of **simulated** e-mails (PDF + JSON) |
| `CHAT_OFFERS_PATH` | system temp folder (Compose: `.local/offers/`) | Accepted offers (`credit_offers` rows, JSONL) |
| `CHAT_SESSION_TTL_MINUTES` · `CHAT_AUTH_MAX_ATTEMPTS` · `CHAT_AUTH_LOCKOUT_MINUTES` | 30 · 3 · 15 | Session and lockout |

## Data

There are two sources with the same shape. The assistant reads only the gold credit profile (`credit_profile.parquet`,
with `fx_to_usd`/`fx_date` for currency conversion); customers, products and branches are used only by the identity
check. No transactions, FX or case files are generated or shipped.

- `data/fixture/`: **example set invented by the team** (21 customers, fixed seed; `scripts/make_fixture.py`). It is in
  the repository and is what the tests, CI and a clean clone use.
- `data/snapshot/`: sample derived from the organizer's dataset (225 customers). **Not versioned** (`.gitignore`); if it
  exists, the backend prefers it. `GET /v1/meta` shows the source in use (`data_source`). Rebuilt with:

```bash
python backend/scripts/build_snapshot.py --customers 400    # from the repo root; needs the organizer tables (see docs/DATA.md)
```

## Tests

```bash
pip install -r requirements-dev.txt && pytest
```

253 tests: credit scope and gold-only data, currency and conversion, application and documents, summary/PDF/simulated
e-mail, tone and identity, security-question generation and verification, lockout, tokens, expiry, API key, number
formats, es/pt intents and language detection, credit policy 0.4, handoff, prompt injection, agent console, proactive
offer (consent, pre-approval, right moment, sensitive topic, acceptance and decline), the Anthropic client with a fake
SDK, and safeguards against an LLM that errs (unrequested handoff, rewording decisions, foreign numbers, model failure).

## Known limits

- **Simulated e-mail:** there is no real sending; the PDF is generated and downloaded, and the notice says so clearly.
- **No file upload:** the customer confirms they have the documents; verification is the advisor's. `docs_on_file` is
  synthetic.
- **Exchange rates:** the gold rates at the data cutoff (not live); only USD, MXN, COP and ARS.
- **Tokens:** history grows with the conversation, so the cost per turn increases in long sessions.
- **Security of the questions:** with 3 questions of 4 options, a random guess passes 1 in 64 times per attempt; the
  mitigation is the lockout, but it does not replace a real second factor (OTP, biometrics) in production. The question
  data comes from the same dataset the customer would state, so it is a simulation, not proof of identity.
- **In-memory state:** sessions, lockouts and the handoff queue live in one process. One replica; for more,
  `SessionStore`, `AuthLockout` and `HandoffQueue` must be externalized (Redis or a database).
- **A customer without enough data for 3 questions** gets `AUTH_UNAVAILABLE` (it reveals that the document exists); in
  the snapshot derived from the dataset this happens to 4 of 225 customers (1.8%); in the example set, to 1 of 21 (on
  purpose).
- **Synthetic policy:** the 20% debt-to-income cap, score bands and rates were defined by the team; there is no external
  ground truth.
- **Claude tested only on a small sample** (see above). Intent classification and sentiment and sensitive-topic
  detection still need a hand-labeled set measured with the model, in es and pt.
- **Docker image verified** (652 MB): it starts as a non-root user (uid 10001), with a read-only file system, passes the
  `HEALTHCHECK` and completed the es/pt flow from `scripts/demo_client.py`.
- No rate limiting per IP and no protection against concurrent requests on the same session.
- Logs do not include documents, security answers or request bodies; the customer's messages are kept in memory during
  the session and included (last 8, with long numbers and e-mails removed) in the handoff summary.
