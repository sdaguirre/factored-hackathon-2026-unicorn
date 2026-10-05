-- =============================================================================================
-- customer_credit_offer_options: baseline offer per customer and term-grid option.
-- Task of the latam_bank_medallion and credit_policy_refresh jobs; runs after customer_credit_profile.
-- Parameters :silver_schema (ref_*) and :gold_schema (profile, fn_*), catalog.schema.
-- Policy and formulas: docs/CREDIT_RULES.md. Synthetic policy, offline results.
-- =============================================================================================

CREATE OR REPLACE TABLE IDENTIFIER(:gold_schema || '.customer_credit_offer_options')
COMMENT 'Baseline pre-approved offer per customer and product option (ref_term_grid row: loan term or card tier). Options are alternatives: each uses the whole 20% capacity. Rate = reference rate + band and segment adjustments, clamped to the product range. Loans: any amount in the product range, at terms up to the band maximum. Cards: the tier credit limit range. Maximum amount = what the available installment can repay, capped by the range. Indicative only; the rules service recomputes when the customer gives new data. Amounts in USD and local currency.'
AS
WITH catalog AS (
    SELECT product_code, min_amount_usd, max_amount_usd, min_rate_pct, max_rate_pct
    FROM IDENTIFIER(:silver_schema || '.ref_product_catalog')
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
    FROM IDENTIFIER(:gold_schema || '.customer_credit_profile') p
    CROSS JOIN IDENTIFIER(:silver_schema || '.ref_term_grid') g
    JOIN catalog c ON c.product_code = split(g.product_code, '-')[0]
),
sized AS (
    SELECT
        *,
        IDENTIFIER(:gold_schema || '.fn_max_principal')(greatest(coalesce(available_installment_usd, 0), 0), offer_rate_pct, term_months) AS max_amount_by_capacity_usd
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
         THEN round(IDENTIFIER(:gold_schema || '.fn_monthly_installment')(capped_amount_usd, offer_rate_pct, term_months), 2) END AS offer_monthly_installment_usd,
    local_currency,
    fx_to_usd,
    CASE WHEN is_available THEN round(capped_amount_usd / fx_to_usd, 0) END   AS offer_max_amount_local,
    CASE WHEN is_available
         THEN round(IDENTIFIER(:gold_schema || '.fn_monthly_installment')(capped_amount_usd, offer_rate_pct, term_months) / fx_to_usd, 0) END AS offer_monthly_installment_local,
    risk_band,
    segment,
    as_of_date,
    policy_version,
    current_timestamp()                                                        AS computed_at
FROM available;
