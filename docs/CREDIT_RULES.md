# Credit rules (synthetic, policy version 0.2)

These rules produce **indicative pre-approved offers** for marketing and lead generation.
No offer is final: every customer who wants to proceed is handed off to an advisor, who
verifies documents and decides. Products in scope: credit card, personal loan and mortgage.

All parameters are synthetic and live in silver reference tables, loaded from `data/reference/`:
`ref_policy_params`, `ref_policy_bands`, `ref_segment_adjustments`, `ref_term_grid` and
`ref_product_catalog`. The gold SQL (`data/databricks/gold_credit_tables.sql`) and the rules
service read the **same tables** and must apply the **same formulas** (section 4).

## 1. Hard filters
If any fails, there is no automatic offer and the reason code is returned.

| Code | Rule | Value (`ref_policy_params`) |
|---|---|---|
| R01 | Active customer | `customer_status = 'Active'` |
| R02 | Minimum tenure | `min_tenure_months` = 6 |
| R03 | No delinquency | max `days_past_due` on active credit products ≤ `max_days_past_due` = 30 (current snapshot; the data has no 12-month history) |
| R04 | No blocked or suspended product | any product with status `Blocked` or `Suspended` |
| R05 | Minimum income | income known and ≥ `min_income_usd` = 300 USD/month |
| R06 | Minimum score | `credit_score` present and band ≠ E |
| R07 | Recent confirmed fraud | no fraud transaction in the last `fraud_lookback_days` = 90; otherwise advisor review |
| R08 | Capacity exhausted | available installment ≤ 0 |

`R05_INCOME_MISSING` is recoverable: the agent can ask the customer for their income and the
rules service recalculates (offer becomes conditional, flag `F03`).
Complaints do not block the offer. An open High/Critical complaint sets
`requires_advisor_review`; an open Critical complaint also removes the proactive offer
(section 7). How much complaints should weigh on eligibility beyond this is still open.

## 2. Debt capacity: the 20% hard limit
- **Income used** = declared monthly income from `customers`, converted to USD at the cutoff
  date. Observed deposits are **ignored** for the limit, whether 0 or not: customers can be paid
  into other banks, so deposits here show only part of their income. Among eligible customers
  with deposits, observed income is a median 20% of declared and lower in 94% of cases; using
  the lower of the two would cut the median available installment from 458 to 87 USD. Deposits
  stay as an indicator for the advisor (`avg_monthly_deposits_usd_6m`).
- **Current installments** = sum of the monthly installments of the customer's active credit
  products:
  - loans: annuity of the original amount (`credit_limit`) at the product `interest_rate`, with
    the term taken from `ref_term_grid` (shortest term whose amount range covers the amount),
    because the data has no loan term;
  - credit cards: annuity of the current balance over 60 months at the card rate.
- **Maximum total installment** = `max_debt_to_income` (20%) × income used.
- **Available installment** = maximum total installment − current installments.

The new installment can never exceed the available installment: after the offer, all
installments together stay at or below 20% of income. There are no other amount or term caps
by band; the amount is bounded only by capacity and by the `ref_term_grid` row range.

## 3. Risk band and rate
The band will come from the risk model's probability of default. Until the model exists,
it comes from `credit_score` (`ref_policy_bands`):

| Band | Score | Rate adjustment | Offer |
|---|---|---|---|
| A | 740 or more | −2.0 pp | yes |
| B | 680–739 | −1.0 pp | yes |
| C | 620–679 | 0 | yes |
| D | 560–619 | +2.0 pp | yes |
| E | below 560 | — | no |

Segment adjustment (`ref_segment_adjustments`): Premium −1.0 pp, Plus −0.5 pp, Basic 0,
Student +1.0 pp.

**Offer rate** = `ref_term_grid.reference_rate_pct` + band adjustment + segment adjustment,
clamped to the product range `[min_rate_pct, max_rate_pct]` of `ref_product_catalog`.

## 4. Amount and installment (shared formulas)
With `r = annual_rate_pct / 1200` and `n = term_months`:
- `monthly_installment(P) = P · r / (1 − (1 + r)^−n)` (`fn_monthly_installment`)
- `max_principal(I) = I · (1 − (1 + r)^−n) / r` (`fn_max_principal`)

For each `ref_term_grid` option (loan term or card tier):
- maximum amount by capacity = `max_principal(available installment)` at the offer rate;
- offer maximum = min(capacity amount, option `max_amount_usd`), rounded down to 100 USD;
- the option is available only if the offer maximum ≥ option `min_amount_usd`.

Cards use the same formulas with n = 60 (the limit is sized so that a full balance would be
repaid within the card term).

## 5. Currencies
- Catalog, grid and rules in USD. Income and balances are converted with
  `daily_exchange_rates` at the latest rate on or before the cutoff date (`as_of_date`).
- The customer sees amounts and installments in local currency; `credit_offers` stores the
  exchange rate used.
- The rate is a nominal annual rate and is not converted.

## 6. Live recalculation
The rules service starts from `gold.customer_credit_profile` and recomputes with sections 2–4.

| What the customer says | Treatment |
|---|---|
| Their income is different | Replaces income used; offer conditional, flag `F03` |
| Additional household income | Added to income used; conditional, flag `F03`, document required |
| Wants a lower installment or longer term | Pick another amount or option within the available installment |
| Has a debt outside the bank | Its installment is added to current installments; the offer may drop |
| Paid off a loan at another bank | Only changes if that installment had been declared before |

Advisor flags (they never block the handoff):
- `F02` the new total debt-to-income is within 5% of the 20% limit (≥ 19%)
- `F03` the offer relies on data declared in the chat

## 7. Proactive offers and marketing consent
Offers are computed for every customer. `customer_credit_profile.offer_mode` says how the
agent may use them:
- `proactive`: eligible, `accepts_marketing = true` and no open Critical complaint; the agent
  may present the offer.
- `on_customer_interest`: eligible but without marketing consent or with an open Critical
  complaint (`not_proactive_reason`); the agent does not mention offers unless the customer
  shows interest in credit, then the offer is ready.
- `none`: not eligible (see `reason_codes`).

`credit_offers.offer_origin` records which case led to each accepted offer.

## 8. Accepted offers and advisor handoff
When the customer accepts, the API writes one row to `gold.credit_offers`: offer parameters
in USD and local currency, exchange rate, income used and its source, current installments,
resulting debt-to-income, band, segment, rate adjustment, declared data, open complaints at
offer time (all, High/Critical and Critical), flags, required documents, `offer_id`,
`policy_version`, validity (`offer_validity_days` = 30) and status.
Every accepted offer is handed off to an advisor.

## Example
Band C, Basic segment, income 2,500 USD, current installments 100 USD, personal loan 24 months
(reference rate 15.2%, adjustment 0):
- maximum total installment = 20% × 2,500 = 500; available = 400;
- maximum amount = `max_principal(400)` = 8,233 → offer up to **8,200 USD**, installment 398.37;
  total debt-to-income after = (100 + 398.37) / 2,500 = 19.9% (flag `F02`).

The customer adds 1,000 USD of household income: income 3,500, available 600, offer up to
12,300 USD (flags `F02`, `F03`). The customer declares an external loan of 150 USD/month:
available 250, offer up to 5,100 USD. In every case, if the customer proceeds, the offer is
recorded and handed off to an advisor.
