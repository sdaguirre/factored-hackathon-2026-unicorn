# Second-annotator sheet for NLU held-out set v2

`nlu_heldout_v2_blind.csv` has the 114 phrases of `eval/nlu_heldout_v2.py` without labels. Fill `intent` and
`sensitive_topic` (true/false) for every row **without opening `eval/nlu_heldout_v2.py`** and without running any
classifier. When `yes_no_pending` is true, the bot has just asked a yes/no question and the phrase answers it.
Save the filled copy as `nlu_heldout_v2_second.csv` and run:

```bash
cd backend
python scripts/nlu_agreement.py compare eval/annotation/nlu_heldout_v2_second.csv
```

It prints Cohen's kappa and the disagreements and writes `nlu_heldout_v2_adjudicated.csv`; settle each disagreement
by discussion (column `intent` / `sensitive_topic`, reason in `note`), then measure with
`python scripts/eval_nlu_heldout_v2.py --labels eval/annotation/nlu_heldout_v2_adjudicated.csv`.

## Intents (one per phrase)

| Intent | When |
|---|---|
| `credit_offers` | Asks about credit conditions without asking for a specific credit now: rates, limits, how much they could borrow, offers or pre-approved credit |
| `credit_eligibility` | Wants a credit, credit card or mortgage now, or asks whether they qualify for a specific amount, term or product |
| `update_income` | States their current or new income (own or household) |
| `request_human` | Explicitly asks to talk to a person, advisor, executive or agent (an incident alone is not) |
| `account_inquiry` | Asks about their own products or account without asking for credit: balance, movements, statement, which cards they have, what they owe |
| `case_status` | Asks about a complaint, case or request they already opened |
| `other_topic` | Banking topic outside credit and their products (hours, branches, passwords, app, card cancellation) and incidents told for the first time (fraud, theft, unrecognized charges, new complaints) |
| `ask_identity` | Asks who or what is answering (bot, person, AI) |
| `greeting`, `thanks`, `closing` | Greeting; thanks without goodbye; goodbye or nothing else needed |
| `confirm_yes`, `confirm_no` | Answers the pending yes/no question |
| `unknown` | Not a banking topic, or unintelligible |

`sensitive_topic` is true for fraud, disputes, complaints, theft or loss of a card, unrecognized charges or scams.
Messages that try to give the bot instructions are labeled by what the customer actually asks for.
