-- =============================================================================================
-- customer_credit_profile: one row per customer with the indicators the agent reads.
-- Task of the latam_bank_medallion and credit_policy_refresh jobs. Parameter :as_of_date
-- (YYYY-MM-DD); empty means the policy cutoff in silver ref_policy_params (2026-06-30 for the
-- static hackathon dataset).
-- Parameters :silver_schema and :gold_schema (catalog.schema) select every table and function
-- (silver data and ref_*, gold fn_*), so the same file runs against the _test or the real schemas.
-- Reads the typed, deduplicated silver built by 02_silver.sql (one current row per key).
-- Policy and formulas: docs/CREDIT_RULES.md. Synthetic policy, offline results.
-- =============================================================================================

CREATE SCHEMA IF NOT EXISTS IDENTIFIER(:gold_schema);

CREATE OR REPLACE TABLE IDENTIFIER(:gold_schema || '.customer_credit_profile')
COMMENT 'One row per customer with the indicators the credit assistant reads: income, current installments, 20% debt capacity, risk band, rate adjustment, hard-filter reason codes and eligibility. As of the policy cutoff date. Amounts in USD; local currency via fx_to_usd. Synthetic policy (silver ref_*), offline results.'
AS
WITH params AS (
    SELECT
        max(CASE WHEN param_name = 'max_debt_to_income'  THEN CAST(param_value AS DOUBLE) END) AS max_dti,
        max(CASE WHEN param_name = 'min_tenure_months'   THEN CAST(param_value AS INT)    END) AS min_tenure_months,
        max(CASE WHEN param_name = 'max_days_past_due'   THEN CAST(param_value AS INT)    END) AS max_days_past_due,
        max(CASE WHEN param_name = 'min_income_usd'      THEN CAST(param_value AS DOUBLE) END) AS min_income_usd,
        max(CASE WHEN param_name = 'fraud_lookback_days' THEN CAST(param_value AS INT)    END) AS fraud_lookback_days,
        -- job parameter overrides the policy cutoff; empty = policy cutoff
        coalesce(try_cast(nullif(:as_of_date, '') AS DATE),
                 max(CASE WHEN param_name = 'as_of_date' THEN CAST(param_value AS DATE) END)) AS as_of_date,
        max(CASE WHEN param_name = 'policy_version'      THEN param_value                 END) AS policy_version
    FROM IDENTIFIER(:silver_schema || '.ref_policy_params')
),
-- latest rate on or before the cutoff (the rate table ends 2026-06-17)
fx AS (
    SELECT r.source_currency AS currency,
           max_by(r.exchange_rate, r.date) AS to_usd,
           max(r.date)                     AS fx_date
    FROM IDENTIFIER(:silver_schema || '.daily_exchange_rates') r
    CROSS JOIN params p
    WHERE r.target_currency = 'USD'
      AND r.date <= p.as_of_date
    GROUP BY r.source_currency
    UNION ALL
    SELECT 'USD', 1.0, CAST(NULL AS DATE)
),
customers AS (
    SELECT
        customer_id,
        country,
        CASE country WHEN 'México' THEN 'MXN' WHEN 'Colombia' THEN 'COP' WHEN 'Argentina' THEN 'ARS' END AS local_currency,
        segment,
        customer_status,
        credit_score,
        estimated_monthly_income                                      AS declared_income_local,
        registration_date,
        coalesce(accepts_marketing, false)                            AS accepts_marketing
    FROM IDENTIFIER(:silver_schema || '.customers')
),
catalog AS (
    SELECT product_code, source_product_type, max_amount_usd, min_rate_pct, max_rate_pct
    FROM IDENTIFIER(:silver_schema || '.ref_product_catalog')
),
products AS (
    SELECT
        p.product_id,
        p.customer_id,
        p.product_status,
        c.product_code,                                         -- CC / PL / MG; null = not a credit product
        p.opening_date,
        p.interest_rate,
        p.days_past_due,
        p.credit_limit * fx.to_usd                              AS credit_limit_usd,
        p.current_balance * fx.to_usd                           AS current_balance_usd
    FROM IDENTIFIER(:silver_schema || '.products') p
    LEFT JOIN catalog c ON c.source_product_type = p.product_type
    LEFT JOIN fx ON fx.currency = p.currency
),
-- Existing loans have no term in the data: take it from ref_term_grid, using the shortest term
-- whose amount range covers the original amount (credit_limit), clamped to the catalog maximum.
existing_loans AS (
    SELECT
        l.customer_id,
        l.product_code,
        IDENTIFIER(:gold_schema || '.fn_monthly_installment')(
            l.principal_usd, coalesce(l.interest_rate, g.reference_rate_pct), g.term_months) AS installment_usd
    FROM (
        SELECT pr.*, coalesce(pr.credit_limit_usd, pr.current_balance_usd) AS principal_usd, c.max_amount_usd AS catalog_max_usd
        FROM products pr
        JOIN catalog c USING (product_code)
        WHERE pr.product_status = 'Active' AND pr.product_code IN ('PL', 'MG')
    ) l
    JOIN IDENTIFIER(:silver_schema || '.ref_term_grid') g
      ON g.product_code = l.product_code
     AND least(l.principal_usd, l.catalog_max_usd) <= g.max_amount_usd
    QUALIFY row_number() OVER (PARTITION BY l.product_id ORDER BY g.term_months) = 1
),
-- Existing cards: current balance repaid over the card term in ref_term_grid (60 months).
existing_cards AS (
    SELECT
        pr.customer_id,
        pr.product_code,
        IDENTIFIER(:gold_schema || '.fn_monthly_installment')(
            greatest(coalesce(pr.current_balance_usd, 0), 0),
            coalesce(pr.interest_rate, (c.min_rate_pct + c.max_rate_pct) / 2),
            60) AS installment_usd
    FROM products pr
    JOIN catalog c USING (product_code)
    WHERE pr.product_status = 'Active' AND pr.product_code = 'CC'
),
debt AS (
    SELECT
        customer_id,
        sum(installment_usd)                                   AS current_installments_usd,
        count(*)                                               AS active_credit_products,
        count_if(product_code = 'CC')                          AS active_credit_cards,
        count_if(product_code = 'PL')                          AS active_personal_loans,
        count_if(product_code = 'MG')                          AS active_mortgages
    FROM (SELECT * FROM existing_loans UNION ALL SELECT * FROM existing_cards)
    GROUP BY customer_id
),
product_risk AS (
    SELECT
        customer_id,
        max(CASE WHEN product_code IS NOT NULL AND product_status = 'Active' THEN days_past_due END) AS max_days_past_due,
        bool_or(product_status IN ('Blocked', 'Suspended'))                                         AS has_blocked_or_suspended_product,
        sum(CASE WHEN product_code = 'CC' AND product_status = 'Active' THEN credit_limit_usd END)     AS card_limit_usd,
        sum(CASE WHEN product_code = 'CC' AND product_status = 'Active' THEN current_balance_usd END)  AS card_balance_usd,
        count_if(product_status = 'Active')                                                         AS active_products
    FROM products
    GROUP BY customer_id
),
transactions AS (
    SELECT
        t.customer_id, t.transaction_type, t.transaction_status, t.process_date, t.is_fraud,
        coalesce(t.amount_usd, t.amount * fx.to_usd) AS amount_usd   -- silver already fills USD rows
    FROM IDENTIFIER(:silver_schema || '.transactions') t
    LEFT JOIN fx ON fx.currency = t.currency
),
-- Observed deposits are sparse in the data (median under 2 per year): informational only.
deposits AS (
    SELECT
        t.customer_id,
        sum(t.amount_usd) / 6                             AS avg_monthly_deposits_usd_6m,
        count(DISTINCT date_trunc('MONTH', t.process_date)) AS deposit_months_6m
    FROM transactions t
    CROSS JOIN params p
    WHERE t.transaction_type = 'Deposit' AND t.transaction_status = 'Approved'
      AND t.process_date > add_months(p.as_of_date, -6)
    GROUP BY t.customer_id
),
fraud AS (
    SELECT t.customer_id, count(*) AS confirmed_fraud_tx_recent
    FROM transactions t
    CROSS JOIN params p
    WHERE t.is_fraud AND t.process_date > date_sub(p.as_of_date, p.fraud_lookback_days)
    GROUP BY t.customer_id
),
complaints AS (
    SELECT
        customer_id,
        count_if(status IN ('Open', 'In Process', 'Escalated'))                                  AS open_complaints,
        count_if(status IN ('Open', 'In Process', 'Escalated') AND priority IN ('High', 'Critical')) AS open_priority_complaints,
        count_if(status IN ('Open', 'In Process', 'Escalated') AND priority = 'Critical')            AS open_critical_complaints
    FROM IDENTIFIER(:silver_schema || '.complaints')   -- deduplicated in silver
    GROUP BY customer_id
),
banded AS (
    SELECT c.customer_id, b.band, b.offer_allowed, b.rate_adjustment_pp AS band_rate_adjustment_pp,
           b.max_term_personal_loan_months, b.max_term_mortgage_months
    FROM customers c
    JOIN IDENTIFIER(:silver_schema || '.ref_policy_bands') b ON c.credit_score >= b.min_credit_score
    QUALIFY row_number() OVER (PARTITION BY c.customer_id ORDER BY b.min_credit_score DESC) = 1
),
base AS (
    SELECT
        c.*,
        p.as_of_date,
        p.policy_version,
        p.max_dti,
        p.min_tenure_months,
        p.max_days_past_due AS policy_max_days_past_due,
        p.min_income_usd,
        fxl.to_usd                                                   AS fx_to_usd,
        fxl.fx_date,
        CAST(floor(months_between(p.as_of_date, c.registration_date)) AS INT) AS tenure_months,
        c.declared_income_local * fxl.to_usd                         AS declared_income_usd,
        d.avg_monthly_deposits_usd_6m,
        coalesce(d.deposit_months_6m, 0)                             AS deposit_months_6m,
        coalesce(db.current_installments_usd, 0)                     AS current_installments_usd,
        coalesce(db.active_credit_products, 0)                       AS active_credit_products,
        coalesce(db.active_credit_cards, 0)                          AS active_credit_cards,
        coalesce(db.active_personal_loans, 0)                        AS active_personal_loans,
        coalesce(db.active_mortgages, 0)                             AS active_mortgages,
        coalesce(pr.active_products, 0)                              AS active_products,
        pr.card_limit_usd,
        pr.card_balance_usd,
        coalesce(pr.max_days_past_due, 0)                            AS max_days_past_due,
        coalesce(pr.has_blocked_or_suspended_product, false)         AS has_blocked_or_suspended_product,
        coalesce(f.confirmed_fraud_tx_recent, 0)                     AS confirmed_fraud_tx_recent,
        coalesce(cm.open_complaints, 0)                              AS open_complaints,
        coalesce(cm.open_priority_complaints, 0)                     AS open_priority_complaints,
        coalesce(cm.open_critical_complaints, 0)                     AS open_critical_complaints,
        bd.band                                                      AS risk_band,
        coalesce(bd.offer_allowed, false)                            AS band_offer_allowed,
        coalesce(bd.band_rate_adjustment_pp, 0)                      AS band_rate_adjustment_pp,
        coalesce(bd.max_term_personal_loan_months, 0)                AS max_term_personal_loan_months,
        coalesce(bd.max_term_mortgage_months, 0)                     AS max_term_mortgage_months,
        coalesce(sa.rate_adjustment_pp, 0)                           AS segment_rate_adjustment_pp
    FROM customers c
    CROSS JOIN params p
    LEFT JOIN fx fxl ON fxl.currency = c.local_currency
    LEFT JOIN deposits d USING (customer_id)
    LEFT JOIN debt db USING (customer_id)
    LEFT JOIN product_risk pr USING (customer_id)
    LEFT JOIN fraud f USING (customer_id)
    LEFT JOIN complaints cm USING (customer_id)
    LEFT JOIN banded bd USING (customer_id)
    LEFT JOIN IDENTIFIER(:silver_schema || '.ref_segment_adjustments') sa ON sa.segment = c.segment
),
capacity AS (
    SELECT
        *,
        declared_income_usd                                    AS income_used_usd,
        max_dti * declared_income_usd                          AS max_total_installment_usd,
        max_dti * declared_income_usd - current_installments_usd AS available_installment_usd
    FROM base
)
SELECT
    customer_id,
    country,
    local_currency,
    segment,
    customer_status,
    registration_date,
    tenure_months,
    accepts_marketing,
    credit_score,
    risk_band,
    -- income
    declared_income_local,
    round(declared_income_usd, 2)                                          AS declared_income_usd,
    round(avg_monthly_deposits_usd_6m, 2)                                  AS avg_monthly_deposits_usd_6m,
    deposit_months_6m,
    round(income_used_usd, 2)                                              AS income_used_usd,
    CASE WHEN income_used_usd IS NULL THEN 'missing' ELSE 'declared_profile' END AS income_source,
    -- debt and capacity
    active_products,
    active_credit_products,
    active_credit_cards,
    active_personal_loans,
    active_mortgages,
    round(card_limit_usd, 2)                                               AS card_limit_usd,
    round(card_balance_usd, 2)                                             AS card_balance_usd,
    round(card_balance_usd / nullif(card_limit_usd, 0), 4)                 AS card_utilization,
    round(current_installments_usd, 2)                                     AS current_installments_usd,
    round(current_installments_usd / nullif(income_used_usd, 0), 4)        AS current_debt_to_income,
    max_dti                                                                AS max_debt_to_income,
    round(max_total_installment_usd, 2)                                    AS max_total_installment_usd,
    round(available_installment_usd, 2)                                    AS available_installment_usd,
    -- risk signals
    max_days_past_due,
    has_blocked_or_suspended_product,
    confirmed_fraud_tx_recent,
    open_complaints,
    open_priority_complaints,
    open_critical_complaints,
    -- pricing
    band_rate_adjustment_pp,
    segment_rate_adjustment_pp,
    max_term_personal_loan_months,
    max_term_mortgage_months,
    band_rate_adjustment_pp + segment_rate_adjustment_pp                   AS total_rate_adjustment_pp,
    -- hard filters (docs/CREDIT_RULES.md section 1)
    filter(array(
        CASE WHEN coalesce(customer_status, '') <> 'Active'                         THEN 'R01_INACTIVE_CUSTOMER' END,
        CASE WHEN coalesce(tenure_months, 0) < min_tenure_months                   THEN 'R02_SHORT_TENURE' END,
        CASE WHEN max_days_past_due > policy_max_days_past_due                     THEN 'R03_DELINQUENCY' END,
        CASE WHEN has_blocked_or_suspended_product                                 THEN 'R04_BLOCKED_PRODUCT' END,
        CASE WHEN income_used_usd IS NULL                                          THEN 'R05_INCOME_MISSING'
             WHEN income_used_usd < min_income_usd                                 THEN 'R05_INCOME_BELOW_MIN' END,
        CASE WHEN risk_band IS NULL                                                THEN 'R06_SCORE_MISSING'
             WHEN NOT band_offer_allowed                                           THEN 'R06_SCORE_BELOW_MIN' END,
        CASE WHEN confirmed_fraud_tx_recent > 0                                    THEN 'R07_RECENT_FRAUD' END,
        CASE WHEN income_used_usd IS NOT NULL AND available_installment_usd <= 0   THEN 'R08_NO_CAPACITY' END
    ), x -> x IS NOT NULL)                                                 AS reason_codes,
    -- eligibility and handoff
    coalesce(customer_status = 'Active'
        AND coalesce(tenure_months, 0) >= min_tenure_months
        AND max_days_past_due <= policy_max_days_past_due
        AND NOT has_blocked_or_suspended_product
        AND income_used_usd >= min_income_usd
        AND band_offer_allowed
        AND confirmed_fraud_tx_recent = 0
        AND available_installment_usd > 0, false)                          AS is_eligible,
    -- Offers are computed for every customer. Only customers who accept marketing and have no
    -- open critical complaint get them proactively; for the rest the offer stays ready in case
    -- they show interest in the chat.
    CASE WHEN NOT is_eligible                                                   THEN 'none'
         WHEN coalesce(accepts_marketing, false) AND open_critical_complaints = 0 THEN 'proactive'
         ELSE 'on_customer_interest' END                                   AS offer_mode,
    CASE WHEN is_eligible AND NOT coalesce(accepts_marketing, false)  THEN 'no_marketing_consent'
         WHEN is_eligible AND open_critical_complaints > 0           THEN 'open_critical_complaint' END AS not_proactive_reason,
    (income_used_usd IS NULL)                                              AS can_become_eligible_with_declared_income,
    (confirmed_fraud_tx_recent > 0 OR open_priority_complaints > 0)        AS requires_advisor_review,
    -- lineage
    fx_to_usd,
    fx_date,
    as_of_date,
    policy_version,
    current_timestamp()                                                    AS computed_at
FROM capacity;
