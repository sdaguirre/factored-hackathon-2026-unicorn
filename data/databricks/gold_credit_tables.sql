-- =============================================================================================
-- Gold layer for the pre-approved credit assistant (policy_version 0.3).
--
--   fn_monthly_installment / fn_max_principal  annuity math shared by every table below
--   customer_credit_profile                     one row per customer: indicators + eligibility
--   customer_credit_offer_options               customer x product/term: baseline offer
--   credit_offers                               offers accepted in the chat (written by the API)
--
-- The agent reads the profile and the options through a tool scoped to the session customer_id.
-- When the customer gives new data in the chat (income, external debt, desired amount or term),
-- the rules service recomputes with the SAME formulas and the SAME silver ref_* parameters.
-- Rebuild the two derived tables whenever a parameter in silver ref_* changes.
--
-- Policy (docs/CREDIT_RULES.md): all monthly installments, current + new, cannot exceed
-- max_debt_to_income (20%) of monthly income. Risk band (credit_score) and segment move the
-- reference rate of ref_term_grid, clamped to the product rate range. Loans can take any amount in
-- the product range at terms up to the band maximum (risk limits the term, not the amount).
-- Every parameter is synthetic. Results are offline, not production decisions.
-- =============================================================================================

-- ---------------------------------------------------------------------------------------------
-- Shared annuity math. The rules service must implement exactly these two formulas.
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION workspace.gold_latam_bank.fn_monthly_installment(
    principal DOUBLE, annual_rate_pct DOUBLE, term_months INT)
RETURNS DOUBLE
COMMENT 'Fixed monthly installment of an annuity loan: P * r / (1 - (1 + r)^-n), r = annual_rate_pct / 1200'
RETURN CASE
    WHEN principal IS NULL OR principal <= 0 OR term_months IS NULL OR term_months <= 0 THEN 0
    WHEN coalesce(annual_rate_pct, 0) = 0 THEN principal / term_months
    ELSE principal * (annual_rate_pct / 1200) / (1 - pow(1 + annual_rate_pct / 1200, -term_months))
END;

CREATE OR REPLACE FUNCTION workspace.gold_latam_bank.fn_max_principal(
    monthly_installment DOUBLE, annual_rate_pct DOUBLE, term_months INT)
RETURNS DOUBLE
COMMENT 'Largest principal a monthly installment can repay: I * (1 - (1 + r)^-n) / r, r = annual_rate_pct / 1200'
RETURN CASE
    WHEN monthly_installment IS NULL OR monthly_installment <= 0 OR term_months IS NULL OR term_months <= 0 THEN 0
    WHEN coalesce(annual_rate_pct, 0) = 0 THEN monthly_installment * term_months
    ELSE monthly_installment * (1 - pow(1 + annual_rate_pct / 1200, -term_months)) / (annual_rate_pct / 1200)
END;

-- ---------------------------------------------------------------------------------------------
-- customer_credit_profile
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE TABLE workspace.gold_latam_bank.customer_credit_profile
COMMENT 'One row per customer with the indicators the credit assistant reads: income, current installments, 20% debt capacity, risk band, rate adjustment, hard-filter reason codes and eligibility. As of the policy cutoff date. Amounts in USD; local currency via fx_to_usd. Synthetic policy (silver ref_*), offline results.'
AS
WITH params AS (
    SELECT
        max(CASE WHEN param_name = 'max_debt_to_income'  THEN CAST(param_value AS DOUBLE) END) AS max_dti,
        max(CASE WHEN param_name = 'min_tenure_months'   THEN CAST(param_value AS INT)    END) AS min_tenure_months,
        max(CASE WHEN param_name = 'max_days_past_due'   THEN CAST(param_value AS INT)    END) AS max_days_past_due,
        max(CASE WHEN param_name = 'min_income_usd'      THEN CAST(param_value AS DOUBLE) END) AS min_income_usd,
        max(CASE WHEN param_name = 'fraud_lookback_days' THEN CAST(param_value AS INT)    END) AS fraud_lookback_days,
        max(CASE WHEN param_name = 'as_of_date'          THEN CAST(param_value AS DATE)   END) AS as_of_date,
        max(CASE WHEN param_name = 'policy_version'      THEN param_value                 END) AS policy_version
    FROM workspace.silver_latam_bank.ref_policy_params
),
-- latest rate on or before the cutoff (the rate table ends 2026-06-17)
fx AS (
    SELECT r.source_currency AS currency,
           max_by(CAST(r.exchange_rate AS DOUBLE), r.date) AS to_usd,
           CAST(max(r.date) AS DATE)                       AS fx_date
    FROM workspace.silver_latam_bank.daily_exchange_rates r
    CROSS JOIN params p
    WHERE r.__END_AT IS NULL
      AND r.target_currency = 'USD'
      AND CAST(r.date AS DATE) <= p.as_of_date
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
        CAST(try_cast(credit_score AS DOUBLE) AS INT)                 AS credit_score,
        try_cast(estimated_monthly_income AS DOUBLE)                  AS declared_income_local,
        CAST(try_cast(registration_date AS TIMESTAMP) AS DATE)        AS registration_date,
        lower(accepts_marketing) = 'true'                             AS accepts_marketing
    FROM workspace.silver_latam_bank.customers
    WHERE __END_AT IS NULL
),
catalog AS (
    SELECT product_code, source_product_type, max_amount_usd, min_rate_pct, max_rate_pct
    FROM workspace.silver_latam_bank.ref_product_catalog
),
products AS (
    SELECT
        p.product_id,
        p.customer_id,
        p.product_status,
        c.product_code,                                         -- CC / PL / MG; null = not a credit product
        CAST(try_cast(p.opening_date AS TIMESTAMP) AS DATE)     AS opening_date,
        try_cast(p.interest_rate AS DOUBLE)                     AS interest_rate,
        try_cast(p.days_past_due AS DOUBLE)                     AS days_past_due,
        try_cast(p.credit_limit AS DOUBLE) * fx.to_usd          AS credit_limit_usd,
        try_cast(p.current_balance AS DOUBLE) * fx.to_usd       AS current_balance_usd
    FROM workspace.silver_latam_bank.products p
    LEFT JOIN catalog c ON c.source_product_type = p.product_type
    LEFT JOIN fx ON fx.currency = p.currency
    WHERE p.__END_AT IS NULL
),
-- Existing loans have no term in the data: take it from ref_term_grid, using the shortest term
-- whose amount range covers the original amount (credit_limit), clamped to the catalog maximum.
existing_loans AS (
    SELECT
        l.customer_id,
        l.product_code,
        workspace.gold_latam_bank.fn_monthly_installment(
            l.principal_usd, coalesce(l.interest_rate, g.reference_rate_pct), g.term_months) AS installment_usd
    FROM (
        SELECT pr.*, coalesce(pr.credit_limit_usd, pr.current_balance_usd) AS principal_usd, c.max_amount_usd AS catalog_max_usd
        FROM products pr
        JOIN catalog c USING (product_code)
        WHERE pr.product_status = 'Active' AND pr.product_code IN ('PL', 'MG')
    ) l
    JOIN workspace.silver_latam_bank.ref_term_grid g
      ON g.product_code = l.product_code
     AND least(l.principal_usd, l.catalog_max_usd) <= g.max_amount_usd
    QUALIFY row_number() OVER (PARTITION BY l.product_id ORDER BY g.term_months) = 1
),
-- Existing cards: current balance repaid over the card term in ref_term_grid (60 months).
existing_cards AS (
    SELECT
        pr.customer_id,
        pr.product_code,
        workspace.gold_latam_bank.fn_monthly_installment(
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
        coalesce(t.amount_usd, CASE WHEN t.currency = 'USD' THEN t.amount END, t.amount * fx.to_usd) AS amount_usd
    FROM workspace.silver_latam_bank.silver_transactions t
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
    FROM workspace.bronze_latam_bank.complaints
    GROUP BY customer_id
),
banded AS (
    SELECT c.customer_id, b.band, b.offer_allowed, b.rate_adjustment_pp AS band_rate_adjustment_pp,
           b.max_term_personal_loan_months, b.max_term_mortgage_months
    FROM customers c
    JOIN workspace.silver_latam_bank.ref_policy_bands b ON c.credit_score >= b.min_credit_score
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
    LEFT JOIN workspace.silver_latam_bank.ref_segment_adjustments sa ON sa.segment = c.segment
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

-- ---------------------------------------------------------------------------------------------
-- customer_credit_offer_options
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE TABLE workspace.gold_latam_bank.customer_credit_offer_options
COMMENT 'Baseline pre-approved offer per customer and product option (ref_term_grid row: loan term or card tier). Options are alternatives: each uses the whole 20% capacity. Rate = reference rate + band and segment adjustments, clamped to the product range. Loans: any amount in the product range, at terms up to the band maximum. Cards: the tier credit limit range. Maximum amount = what the available installment can repay, capped by the range. Indicative only; the rules service recomputes when the customer gives new data. Amounts in USD and local currency.'
AS
WITH catalog AS (
    SELECT product_code, min_amount_usd, max_amount_usd, min_rate_pct, max_rate_pct
    FROM workspace.silver_latam_bank.ref_product_catalog
),
priced AS (
    SELECT
        p.customer_id,
        p.local_currency,
        p.fx_to_usd,
        p.is_eligible,
        p.offer_mode,
        p.reason_codes,
        p.available_installment_usd,
        p.risk_band,
        p.segment,
        p.total_rate_adjustment_pp,
        p.as_of_date,
        p.policy_version,
        g.product_code                                    AS option_code,
        c.product_code,
        g.product_type,
        g.tier,
        g.term_months,
        -- loans: whole product range at any allowed term; cards: the tier limit range
        CASE WHEN c.product_code = 'CC' THEN g.min_amount_usd ELSE c.min_amount_usd END AS option_min_amount_usd,
        CASE WHEN c.product_code = 'CC' THEN g.max_amount_usd ELSE c.max_amount_usd END AS option_max_amount_usd,
        CASE c.product_code
            WHEN 'PL' THEN g.term_months <= p.max_term_personal_loan_months
            WHEN 'MG' THEN g.term_months <= p.max_term_mortgage_months
            ELSE true END                                 AS term_allowed,
        g.reference_rate_pct,
        least(greatest(g.reference_rate_pct + p.total_rate_adjustment_pp, c.min_rate_pct), c.max_rate_pct) AS offer_rate_pct
    FROM workspace.gold_latam_bank.customer_credit_profile p
    CROSS JOIN workspace.silver_latam_bank.ref_term_grid g
    JOIN catalog c ON c.product_code = split(g.product_code, '-')[0]
),
sized AS (
    SELECT
        *,
        workspace.gold_latam_bank.fn_max_principal(greatest(coalesce(available_installment_usd, 0), 0), offer_rate_pct, term_months) AS max_amount_by_capacity_usd
    FROM priced
),
capped AS (
    SELECT *, floor(least(max_amount_by_capacity_usd, option_max_amount_usd) / 100) * 100 AS capped_amount_usd
    FROM sized
),
available AS (
    SELECT *,
        coalesce(is_eligible AND term_allowed AND capped_amount_usd >= option_min_amount_usd, false) AS is_available
    FROM capped
)
SELECT
    customer_id,
    option_code,
    product_code,
    product_type,
    tier,
    term_months,
    reference_rate_pct,
    total_rate_adjustment_pp,
    round(offer_rate_pct, 2)                                                   AS offer_rate_pct,
    option_min_amount_usd,
    option_max_amount_usd,
    term_allowed,
    round(max_amount_by_capacity_usd, 2)                                       AS max_amount_by_capacity_usd,
    is_available,
    offer_mode,
    CASE WHEN NOT is_eligible                              THEN 'customer_not_eligible'
         WHEN NOT term_allowed                             THEN 'term_above_band_maximum'
         WHEN capped_amount_usd < option_min_amount_usd    THEN 'capacity_below_option_minimum' END AS unavailable_reason,
    CASE WHEN is_available THEN capped_amount_usd END                          AS offer_max_amount_usd,
    CASE WHEN is_available
         THEN round(workspace.gold_latam_bank.fn_monthly_installment(capped_amount_usd, offer_rate_pct, term_months), 2) END AS offer_monthly_installment_usd,
    local_currency,
    fx_to_usd,
    CASE WHEN is_available THEN round(capped_amount_usd / fx_to_usd, 0) END   AS offer_max_amount_local,
    CASE WHEN is_available
         THEN round(workspace.gold_latam_bank.fn_monthly_installment(capped_amount_usd, offer_rate_pct, term_months) / fx_to_usd, 0) END AS offer_monthly_installment_local,
    risk_band,
    segment,
    as_of_date,
    policy_version,
    current_timestamp()                                                        AS computed_at
FROM available;

-- ---------------------------------------------------------------------------------------------
-- credit_offers: offers the customer accepted in the chat, before the advisor handoff.
-- Written only by the API / rules service (never by the LLM). One row per accepted offer;
-- later changes update status. Not rebuilt: CREATE IF NOT EXISTS keeps the history.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workspace.gold_latam_bank.credit_offers (
    offer_id                   STRING        NOT NULL COMMENT 'Unique offer id generated by the rules service (UUID)',
    customer_id                STRING        NOT NULL COMMENT 'Customer, taken from the session token, never from LLM text',
    session_id                 STRING                 COMMENT 'Chat session id',
    channel                    STRING                 COMMENT 'Channel where the offer was accepted, e.g. chat',
    language                   STRING                 COMMENT 'Conversation language: es or pt',
    offer_origin               STRING        NOT NULL COMMENT 'proactive (customer accepts marketing) or customer_interest (customer asked in the chat)',
    -- what was offered
    option_code                STRING        NOT NULL COMMENT 'ref_term_grid option: PL, MG or CC-<tier>',
    product_code               STRING        NOT NULL COMMENT 'CC, PL or MG',
    product_type               STRING        NOT NULL COMMENT 'Credit Card, Personal Loan or Mortgage',
    tier                       STRING                 COMMENT 'Card tier; null for loans',
    term_months                INT           NOT NULL COMMENT 'Term in months',
    amount_usd                 DOUBLE        NOT NULL COMMENT 'Accepted amount or credit limit in USD',
    annual_rate_pct            DOUBLE        NOT NULL COMMENT 'Offered nominal annual rate, percent',
    monthly_installment_usd    DOUBLE        NOT NULL COMMENT 'Monthly installment in USD',
    local_currency             STRING        NOT NULL COMMENT 'MXN, COP or ARS',
    fx_to_usd                  DOUBLE        NOT NULL COMMENT 'Exchange rate used: 1 local unit = fx_to_usd USD',
    fx_date                    DATE                   COMMENT 'Date of the exchange rate used',
    amount_local               DOUBLE        NOT NULL COMMENT 'Accepted amount in local currency, as shown to the customer',
    monthly_installment_local  DOUBLE        NOT NULL COMMENT 'Monthly installment in local currency, as shown to the customer',
    -- how it was computed
    income_used_usd            DOUBLE        NOT NULL COMMENT 'Monthly income used for the 20% limit',
    income_source              STRING        NOT NULL COMMENT 'declared_profile or declared_in_chat',
    current_installments_usd   DOUBLE        NOT NULL COMMENT 'Existing monthly installments, including external debt declared in chat',
    max_total_installment_usd  DOUBLE        NOT NULL COMMENT 'max_debt_to_income x income_used_usd',
    debt_to_income_after       DOUBLE        NOT NULL COMMENT '(current installments + new installment) / income; must be <= max_debt_to_income',
    risk_band                  STRING        NOT NULL COMMENT 'Band at offer time',
    segment                    STRING                 COMMENT 'Customer segment at offer time',
    rate_adjustment_pp         DOUBLE        NOT NULL COMMENT 'Total band + segment adjustment applied to the reference rate',
    customer_declared_data     STRING                 COMMENT 'JSON with data the customer gave in the chat (income, household income, external debts)',
    -- complaint context at offer time, recorded for the advisor
    open_complaints            INT           NOT NULL COMMENT 'Open complaints (Open, In Process, Escalated) at offer time',
    open_priority_complaints   INT           NOT NULL COMMENT 'Open High or Critical complaints at offer time',
    open_critical_complaints   INT           NOT NULL COMMENT 'Open Critical complaints at offer time; > 0 means the offer was not proactive',
    -- controls and handoff
    is_conditional             BOOLEAN       NOT NULL COMMENT 'True when the offer relies on data declared in the chat',
    flags                      ARRAY<STRING>          COMMENT 'Advisor flags: F02_NEAR_LIMIT_DECLARED_INCOME, F03_DECLARED_DATA, F04_OPEN_COMPLAINTS',
    required_documents         ARRAY<STRING>          COMMENT 'Documents the advisor must request',
    status                     STRING        NOT NULL COMMENT 'accepted, handed_off, expired or cancelled',
    handoff_ticket_id          STRING                 COMMENT 'Advisor queue ticket once handed off',
    advisor_summary            STRING                 COMMENT 'Summary sent to the advisor',
    valid_until                DATE          NOT NULL COMMENT 'Offer expiry (offer_validity_days after acceptance)',
    profile_as_of_date         DATE          NOT NULL COMMENT 'as_of_date of the profile used',
    policy_version             STRING        NOT NULL COMMENT 'Policy parameters version used',
    created_at                 TIMESTAMP     NOT NULL COMMENT 'When the customer accepted',
    updated_at                 TIMESTAMP              COMMENT 'Last status change',
    CONSTRAINT credit_offers_pk PRIMARY KEY (offer_id)
)
COMMENT 'Offers accepted by customers in the chat, pending or after advisor handoff. Every accepted offer goes to an advisor, who verifies documents and decides. Written by the API/rules service only.'
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

ALTER TABLE workspace.gold_latam_bank.credit_offers DROP CONSTRAINT IF EXISTS valid_status;
ALTER TABLE workspace.gold_latam_bank.credit_offers ADD CONSTRAINT valid_status
    CHECK (status IN ('accepted', 'handed_off', 'expired', 'cancelled'));

ALTER TABLE workspace.gold_latam_bank.credit_offers DROP CONSTRAINT IF EXISTS valid_offer_origin;
ALTER TABLE workspace.gold_latam_bank.credit_offers ADD CONSTRAINT valid_offer_origin
    CHECK (offer_origin IN ('proactive', 'customer_interest'));

ALTER TABLE workspace.gold_latam_bank.credit_offers DROP CONSTRAINT IF EXISTS positive_amounts;
ALTER TABLE workspace.gold_latam_bank.credit_offers ADD CONSTRAINT positive_amounts
    CHECK (amount_usd > 0 AND monthly_installment_usd > 0 AND income_used_usd > 0);

-- Guardrail duplicating ref_policy_params.max_debt_to_income: update it if that parameter changes.
ALTER TABLE workspace.gold_latam_bank.credit_offers DROP CONSTRAINT IF EXISTS within_debt_limit;
ALTER TABLE workspace.gold_latam_bank.credit_offers ADD CONSTRAINT within_debt_limit
    CHECK (debt_to_income_after <= 0.20 + 1e-9);

-- Keep column comments current on an existing table (CREATE TABLE IF NOT EXISTS does not update them).
ALTER TABLE workspace.gold_latam_bank.credit_offers ALTER COLUMN flags
    COMMENT 'Advisor flags: F02_NEAR_LIMIT_DECLARED_INCOME, F03_DECLARED_DATA, F04_OPEN_COMPLAINTS';
