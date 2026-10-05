# Evaluation: what was measured, what failed and what is missing

All measurements are **offline, on synthetic data and with a policy invented by the team**. They are not improvements
measured in production. Sample sizes are small and always stated.

## 1. NLU intent (rules vs Claude)

### First held-out set

`backend/scripts/eval_nlu.py` on `backend/eval/nlu_cases.py`. **Single-annotator** labels: the team must review them
before quoting the figures. The prompt examples do not appear in any test phrase.

| Set | Phrases | Rules | Claude Haiku 4.5 |
|---|---|---|---|
| Development, **before** tuning the prompt | 60 | 51/60 (85%) | 49/60 (82%) |
| Development, after tuning (**optimistic**: tuned while looking at these errors) | 60 | 57/60 (95%) | 60/60 (100%) |
| **Held-out** (written before tuning, measured once) | 29 | 23/29 (79%) | 28/29 and 27/29 (two runs: 93–97%) |

Model errors before tuning: rates and limits in Portuguese classified as "other topic"; "no gracias" taken as a
goodbye because the model did not know a question was pending; non-banking topics classified as "other topic".
Fixes: definitions and examples in the prompt, a notice when a yes/no question is pending, and more complete rules.

In the held-out set, the only consistent failure was "necesito que me atienda una persona", and it was not the
model's: the explicit-handoff guard did not recognize the phrase. Its lexicon was extended after looking at that case,
**so that figure is no longer clean**. Sensitive-topic detection: 2/2 in the held-out set and 5/5 in development, with
no false positives. The two model runs differ in one phrase: it is not deterministic.

### Held-out set v2

`backend/eval/nlu_heldout_v2.py` (PR #26): 114 new team-made phrases (59 es, 55 pt; clear, colloquial, mixed and adversarial
strata), written in one pass and committed **before** the first measurement, with a blind sheet for a second annotator
(Cohen's kappa and adjudication: `backend/scripts/nlu_agreement.py`).

| Measurement | Result | Status |
|---|---|---|
| **Rules baseline, clean** (rules as of the commit that froze the set) | **82/114 = 72%** (95% Wilson CI 63–79%); es 73%, pt 71%; sensitive topic 9/13, 1 false positive | **This is the reference rules figure** |
| Claude Haiku 4.5 | — | Pending (needs the API key; `--rescore` applies the final labels without calling the model again) |
| Second annotator, kappa | — | Pending |

**Contamination note.** After the clean measurement, the rules NLU was changed to fix behavior found in scripted demo
conversations: language-specific word lists, charges the customer did not make as a sensitive topic, requests inside
greetings, named products and words shared by both languages (commits `f62dbc0`, `ebde991`, `8775959`). Several of those
phrasings also appear in v2, so **any new rules measurement on v2 is optimistic** and must not replace the 72%. A
measurement with Claude on v2 is still clean (the prompt was not tuned on v2). Later rules changes need the affected
phrases replaced or a new v3 set nobody has looked at.

## 2. Full conversations with the real model

`backend/scripts/e2e_llm.py`: 5 conversations (13 turns) in Spanish and Portuguese.

| | Before restricting rewording | After |
|---|---|---|
| Model calls for 13 turns | 26 | 18 |
| Fallbacks to rules | 0 | 0 |
| Latency per call | p50 ≈ 1.2 s, p95 ≈ 1.6 s | p50 ≈ 1.1 s, p95 ≈ 1.7 s |
| Tokens per turn (input / output) | ≈ 536 / 176 | ≈ 487 / 105 |

Failures found in that test and fixed:

1. The model classified an incident ("I don't recognize a charge… I'm furious") as a request for a human and the handoff
   was created without confirmation. Now a handoff is accepted only if the customer explicitly asked for it in their text.
2. The model's rewording added unverified sentences (the guard only checks numbers) and addressed the customer as "o
   senhor". Now the model only rewords low-risk messages; decisions, offers, handoffs and notices come from reviewed
   templates.
3. Rates in Portuguese routed wrongly (see section 1).

Second live run after the currency, application, summary and identity flows (`e2e_llm.py`, 6 conversations): ~1.0 s
median and ~1.4 s p95 per call, ≈ 979 input and ≈ 93 output tokens per turn (more than before: history and new prompts).
Identity phrases were tested; the model's rewording was restricted because it made greetings and declines worse than
the templates. The NLU development set grew by 5 `ask_identity` cases (65/65 with Claude, ~98% with rules, optimistic
because they were developed on those cases); the held-out set was not touched.

### Scripted conversations with the rules NLU

Nine full conversations with nine fixture customers (Spanish and Portuguese), from the identity check to the handoff:
offers and comparison between products, recalculation with declared and household income, a lower card limit, the
age-at-maturity cap, a delinquency decline that states the rule, an incident followed by a credit question, an
identity question, an off-topic question and a prompt injection, and no payment capacity. They found and fixed
Portuguese replies switching to Spanish, the requested product lost after the income question, a new term or amount not
recalculated, "no thanks" and "goodbye" not closing politely, and household income with installments in one message.
They are a smoke test of behavior with the rules NLU, not a held-out evaluation.

## 3. Failure and safety tests (253 automated tests, no network)

| Challenge scenario | Coverage |
|---|---|
| Missing data | No income: asks for it once more if the answer has no figure, then the offer is conditional. No score: offers a handoff |
| Expired session | 401 `SESSION_EXPIRED`; token bound to its session |
| Unauthorized access | No conversation without verifying identity; lockout after 3 failures, also for unknown documents; another customer's token or PDF is rejected |
| Prompt injection | The customer's text is data and is escaped before reaching the model; forcing an approval does not produce one (with a fake LLM and with rules; one real phrase was tried live with the model) |
| LLM failure | Timeouts, connection errors, 429 and 529 responses, missing or malformed JSON and out-of-range values fall back to rules without breaking the turn (fake Anthropic client); a fake LLM that errs on purpose cannot hand off, reword decisions or introduce figures |
| Scope | Anything that is not a credit offer is handed off after confirmation without showing bank data; the chat reads only the gold profile (a test fails on any raw-data read) |
| Multilingual ambiguity | Spanish, Portuguese, language switch mid-conversation, local number formats (1.500,00 and 1,500.00) |
| Currency | Detection, conversion with the gold rate, equivalents with the same rate, unsupported currencies |
| Application and documents | Only missing documents are requested; handoff when everything is in order; notice if something is missing; no summary for incidents |
| Identity | Answers "are you a robot?" truthfully; model text claiming to be a person is discarded |
| Consent and timing of the proactive offer | No consent, sensitive topic, negative sentiment, after a decline, or already offered: no offer |

**Not covered:** tool failures other than the LLM failing (there are no real external tools: the data is a local
export) and injection with many variants against the real model.

## 4. Data baselines

- **Credit policy** on 150,000 customers, standard request (personal loan, 36 months, 3 times income), with the
  backend's earlier preliminary policy: 44.2% eligible, 27.2% missing data, 18.5% human review, 10.1% declined. A fixed
  pre-approval by segment (Premium and Plus) would approve customers the policy declines in 3.9% of cases or sends to
  ask for data in 27.4%, and would leave out 40.6% who are eligible. No external ground truth.
- **Credit policy 0.4 in gold** (Databricks, 150,000 customers, cutoff 2026-06-30): 50,707 eligible (33.8%), 24,953 with a
  proactive offer and 25,754 only if the customer asks. The Python reference implementation
  (`data/policy/credit_policy.py`) matches gold on all 1,800,000 options (`data/scripts/check_engine_parity.py`), and so
  does the backend engine.
- **Age-at-maturity cap (policy 0.4), bias check by age bracket** (`data/databricks/analysis/age_cap_impact.sql`,
  eligible customers, 2026-06-30 cutoff). Age only caps loan terms; eligibility, amounts and rates do not depend on it,
  and every bracket keeps its card offer. "No term left by age": the age cap is below the shortest grid term (24 months
  for personal loans, 180 for mortgages), so no term of that product can be offered; the same definition gives the
  19,256 and 8,677 in `docs/CREDIT_RULES.md`. "Shortened": a mortgage is still offered but some longer terms are cut.

  | Age | Eligible | With personal loan | With mortgage | Personal loan: no term left by age | Mortgage: no term left by age | Mortgage shortened by age |
  |---|---|---|---|---|---|---|
  | 18-29 | 7,088 | 6,375 | 7,040 | 0 | 0 | 0 |
  | 30-39 | 8,060 | 7,219 | 7,986 | 0 | 0 | 0 |
  | 40-49 | 8,143 | 7,256 | 8,064 | 0 | 0 | 1,444 |
  | 50-59 | 8,096 | 7,231 | 8,020 | 0 | 0 | 6,564 |
  | 60-64 | 4,026 | 3,615 | 64 | 0 | 3,962 | 64 |
  | 65-69 | 4,038 | 3,607 | 0 | 0 | 4,038 | 0 |
  | 70-74 | 4,095 | 2,212 | 0 | 1,516 | 4,095 | 0 |
  | 75+ | 7,161 | 0 | 0 | 7,161 | 7,161 | 0 |
  | **Total** | **50,707** | | | **8,677** | **19,256** | **8,072** |

  There is no "unknown" row: no customer is missing a birth date at this cutoff (0 of 150,000 in silver), so the cap
  applies to everyone. A missing birth date would mean no cap; silver measures `null_share_date_of_birth` on every run
  and fails above 1%, so a jump in missing birth dates cannot silently remove the cap. The effect is by design
  and concentrated from age 60 (mortgages) and 70 (personal loans): a disclosed policy trade-off (life-insurance
  practice), not a model bias; the advisor can review exceptions.
- **Data pipeline quality:** 94 metrics per run (80 bronze/silver, 14 gold) in `pipeline_quality_metrics`; 0 key
  duplicates and 0 content duplicates except 6 repeated `product_number` values; gold built on the typed silver is
  identical to the previous one, customer by customer. Update fixture (static data): 5 of 5 cases handled correctly, and
  the assertions fail without the delivery.
- **Fraud triage** (test set of 686,502 transactions and 603 frauds, temporal split): the organizer's `fraud_score`
  gives PR-AUC 0.577 and 58% recall reviewing the riskiest 1%, with 5% precision. A model without `fraud_score` stays at
  chance level (PR-AUC 0.0009).

## 5. What is still to be measured (against the challenge)

The challenge asks to evaluate on held-out cases and report, with sample sizes and denominators:

- safe automated resolution over all in-scope cases, and the share where automation was attempted;
- containment (and why it is not enough);
- escalation quality: missed and unnecessary handoffs, and the usefulness of the context given to the advisor;
- unsafe outcomes (unauthorized disclosure or action, materially wrong outcome) with counts;
- p50/p95 latency and cost per attempted case and per successful resolution;
- comparison by language and by segment, with sample limitations.

**None of this is measured yet on a held-out set of full conversations.** Sections 1 to 3 are partial pieces. The plan:
a held-out set of scripted conversations in Spanish and Portuguese with the expected result (the policy engine is
deterministic, so the right outcome of each case is known), run against the system and against a baseline (everything
to a human), with these metrics.

## 6. Reproduce

```bash
cd backend
pytest                                          # 253 tests, no network
python scripts/eval_nlu.py --no-llm             # rules; without --no-llm it uses Claude (needs ANTHROPIC_API_KEY and spends credit)
python scripts/eval_nlu.py --heldout --runs 2   # first held-out set (use with care: already measured)
python scripts/e2e_llm.py                       # full conversations with Claude
```
