# Databricks: credit layers

SQL, scripts and a Databricks Asset Bundle that build the credit flow in Unity Catalog
(`workspace` catalog). Policy and formulas are in `docs/CREDIT_RULES.md`; reference tables in
`data/reference/`. All policy values are synthetic and results are offline.

| File | Builds | Schema | When |
|---|---|---|---|
| `load_bronze_service.sql` | `complaints`, `call_center_interactions` from the daily CSVs in the landing volume | `bronze_latam_bank` | When the landing files change |
| `load_silver_reference.sql` | `ref_product_catalog`, `ref_term_grid`, `ref_policy_params`, `ref_policy_bands`, `ref_segment_adjustments` | `silver_latam_bank` | When a CSV in `data/reference/` changes |
| `gold/00_deploy_objects.sql` | `fn_monthly_installment`, `fn_max_principal`, `credit_offers` | `gold_latam_bank` | On deploy (job `credit_gold_deploy`) |
| `gold/10_customer_credit_profile.sql` | `customer_credit_profile` | `gold_latam_bank` | Daily (job `credit_gold_daily`) |
| `gold/20_customer_credit_offer_options.sql` | `customer_credit_offer_options` | `gold_latam_bank` | Daily, after the profile |
| `gold/90_quality_checks.sql` | Quality checks that fail the run | — | Daily, last task |

## Gold objects

| Object | Grain | Purpose |
|---|---|---|
| `customer_credit_profile` | one row per customer | Income used, current installments, 20% capacity, risk band, rate adjustment, maximum terms, reason codes R01–R08, `is_eligible`, `offer_mode`, advisor-review flag, FX used |
| `customer_credit_offer_options` | customer × `ref_term_grid` option | Alternatives, each using the whole 20% capacity: offer rate, maximum amount, installment and availability, in USD and local currency. Loans at any amount in the product range up to the band maximum term |
| `credit_offers` | one row per accepted offer | Written by the API only. Change Data Feed on; CHECK constraints on status, origin, positive amounts and the 20% limit. Never dropped by a deploy |
| `fn_monthly_installment`, `fn_max_principal` | — | Annuity formulas the rules service must reproduce exactly |

`offer_mode`: `proactive` (eligible, marketing consent, no open Critical complaint),
`on_customer_interest` (eligible, offer only if the customer asks about credit) or `none`.

## Daily job (Databricks Asset Bundle)

The bundle in this folder (`databricks.yml`, `resources/`) defines two jobs that run the SQL
files above on a SQL warehouse:

| Job | Tasks | Trigger |
|---|---|---|
| `credit_gold_daily` | `gold_customer_credit_profile` → `gold_customer_credit_offer_options` → `gold_quality_checks` | Daily at 06:00 America/Bogota (deployed **paused**) |
| `credit_gold_deploy` | `gold_deploy_objects` | Manual, once per deploy |

- **Parameter `as_of_date`** (`YYYY-MM-DD`): empty uses the policy cutoff in
  `ref_policy_params` (2026-06-30 for the static hackathon data). With live data, set its
  default to `{{job.start_time.iso_date}}`.
- **Quality checks** (`gold/90_quality_checks.sql`) fail the job when: the profile does not have
  one row per current customer; cutoff, policy version or exchange rate is missing; the profile
  was built with a different policy version than silver; the eligible share leaves 20–50%; the
  options are not one per customer and grid row; or any available option exceeds the 20%
  capacity, the band maximum term or its amount range.
- **Retries and limits:** one retry per build task, task and job timeouts, one concurrent run.
- **Upstream:** the job can run after ingestion instead of on a clock with a `table_update`
  trigger on silver `customers` and `products` (commented in `resources/credit_gold_daily.job.yml`).

```bash
cd data/databricks
databricks bundle validate -t dev --profile <profile> --var="warehouse_id=<id>"
databricks bundle deploy   -t dev --profile <profile> --var="warehouse_id=<id>"
databricks bundle run credit_gold_deploy -t dev --profile <profile> --var="warehouse_id=<id>"
databricks bundle run credit_gold_daily  -t dev --profile <profile> --var="warehouse_id=<id>"
# one-off rebuild as of another date
databricks bundle run credit_gold_daily  -t dev --profile <profile> --var="warehouse_id=<id>" --params as_of_date=2026-05-31
```

`dev` prefixes the jobs with `[dev <user>]` and keeps schedules paused. Before using the `prod`
target: set `run_as` to a service principal, add failure notifications
(`email_notifications.on_failure` or a webhook) and unpause the schedule.

## Manual run order (without the bundle)

```bash
pip install -r data/scripts/requirements.txt
databricks auth login --host <workspace-url> --profile <profile>

# 1. bronze (needs the daily CSVs in /Volumes/workspace/staging_latam_bank/landing/<table>/)
python data/scripts/run_databricks_sql.py data/databricks/load_bronze_service.sql --profile <profile> --warehouse-id <id>
# 2. silver reference tables (rerun when a CSV in data/reference changes)
python data/scripts/load_reference.py --profile <profile> --warehouse-id <id>
# 3. gold deploy objects, then the daily build and checks
python data/scripts/run_databricks_sql.py data/databricks/gold/00_deploy_objects.sql --profile <profile> --warehouse-id <id>
python data/scripts/run_databricks_sql.py data/databricks/gold/10_customer_credit_profile.sql \
    data/databricks/gold/20_customer_credit_offer_options.sql data/databricks/gold/90_quality_checks.sql \
    --param as_of_date= --profile <profile> --warehouse-id <id>
```

## Results as of the 2026-06-30 cutoff (policy 0.3)
- 150,000 customers; 50,707 eligible; 24,953 `proactive`, 25,754 `on_customer_interest`.
- Main reasons for no offer: missing income 30,033 (recoverable by asking the customer),
  blocked or suspended product 25,519, missing score 22,492, inactive customer 22,300.
- Band term limits: eligible customers without any personal loan option dropped from 10,772
  (policy 0.2 grid) to 5,319; no option exceeds the band maximum term or the 20% capacity.
- SQL and Python annuity results match (40,000 USD at 6.2% over 180 months: 341.88 per month).
- `credit_gold_daily` runs in about 50 seconds on the serverless starter warehouse; with
  `as_of_date=2026-05-31` it yields 50,110 eligible customers.
