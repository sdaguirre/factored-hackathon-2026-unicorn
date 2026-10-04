# Databricks: credit layers

SQL and scripts that build the credit flow in Unity Catalog (`workspace` catalog). Policy and
formulas are in `docs/CREDIT_RULES.md`; reference tables in `data/reference/`. All policy
values are synthetic and results are offline.

| File | Builds | Schema |
|---|---|---|
| `load_bronze_service.sql` | `complaints`, `call_center_interactions` from the daily CSVs in the landing volume | `bronze_latam_bank` |
| `load_silver_reference.sql` | `ref_product_catalog`, `ref_term_grid`, `ref_policy_params`, `ref_policy_bands`, `ref_segment_adjustments` | `silver_latam_bank` |
| `gold_credit_tables.sql` | `fn_monthly_installment`, `fn_max_principal`, `customer_credit_profile`, `customer_credit_offer_options`, `credit_offers` | `gold_latam_bank` |

## Gold objects

| Object | Grain | Purpose |
|---|---|---|
| `customer_credit_profile` | one row per customer | Income used, current installments, 20% capacity, risk band, rate adjustment, reason codes R01–R08, `is_eligible`, `offer_mode`, advisor-review flag, FX used |
| `customer_credit_offer_options` | customer × `ref_term_grid` option | Alternatives, each using the whole 20% capacity: offer rate, maximum amount, installment and availability, in USD and local currency. Loans at any amount in the product range up to the band maximum term |
| `credit_offers` | one row per accepted offer | Written by the API only. Change Data Feed on; CHECK constraints on status, origin, positive amounts and the 20% limit |
| `fn_monthly_installment`, `fn_max_principal` | — | Annuity formulas the rules service must reproduce exactly |

`offer_mode`: `proactive` (eligible, marketing consent, no open Critical complaint),
`on_customer_interest` (eligible, offer only if the customer asks about credit) or `none`.

## Run order

```bash
pip install -r data/scripts/requirements.txt
databricks auth login --host <workspace-url> --profile <profile>

# 1. bronze (needs the daily CSVs in /Volumes/workspace/staging_latam_bank/landing/<table>/)
python data/scripts/run_databricks_sql.py data/databricks/load_bronze_service.sql --profile <profile> --warehouse-id <id>
# 2. silver reference tables (rerun when a CSV in data/reference changes)
python data/scripts/load_reference.py --profile <profile> --warehouse-id <id>
# 3. gold (rerun after step 2 or when silver data changes)
python data/scripts/run_databricks_sql.py data/databricks/gold_credit_tables.sql --profile <profile> --warehouse-id <id>
```

`gold_credit_tables.sql` recreates the profile and options tables but keeps `credit_offers`
(`CREATE TABLE IF NOT EXISTS`), so accepted offers are never lost on a rebuild.

## Results as of the 2026-06-30 cutoff (policy 0.3)
- 150,000 customers; 50,707 eligible; 24,953 `proactive`, 25,754 `on_customer_interest`.
- Main reasons for no offer: missing income 30,033 (recoverable by asking the customer),
  blocked or suspended product 25,519, missing score 22,492, inactive customer 22,300.
- Band term limits: eligible customers without any personal loan option dropped from 10,772
  (policy 0.2 grid) to 5,319; no option exceeds the band maximum term or the 20% capacity.
- SQL and Python annuity results match (40,000 USD at 6.2% over 180 months: 341.88 per month).
