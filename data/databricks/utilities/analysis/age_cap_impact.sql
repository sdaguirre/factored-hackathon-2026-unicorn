-- =============================================================================================
-- Bias check for the age-at-maturity term cap (policy 0.4, docs/CREDIT_RULES.md section 3).
-- Eligible customers by age bracket at the cutoff: how many keep a personal loan and a mortgage,
-- how many have no term left for a loan product because of age (the age cap is below the shortest
-- grid term: 24 months for personal loans, 180 for mortgages; every band allows at least those),
-- and how many keep a mortgage with fewer terms. A missing birth date means no age cap. Age comes from silver customers and is
-- used here to MEASURE the rule, as allowed for protected attributes.
-- Parameters (catalog.schema): :silver_schema, :gold_schema. Read-only.
-- =============================================================================================

WITH eligible AS (
    SELECT p.customer_id, p.max_term_personal_loan_months, p.max_term_mortgage_months,
           CAST(floor(months_between(p.as_of_date, c.date_of_birth) / 12) AS INT) AS age
    FROM IDENTIFIER(:gold_schema || '.customer_credit_profile') p
    JOIN IDENTIFIER(:silver_schema || '.customers') c USING (customer_id)
    WHERE p.is_eligible
),
per_customer AS (
    SELECT o.customer_id,
           bool_or(o.product_code = 'PL' AND o.is_available)                                  AS has_pl,
           bool_or(o.product_code = 'MG' AND o.is_available)                                  AS has_mg,
           bool_or(o.product_code = 'MG' AND o.unavailable_reason = 'term_above_age_at_maturity') AS mg_term_cut_by_age
    FROM IDENTIFIER(:gold_schema || '.customer_credit_offer_options') o
    JOIN eligible e USING (customer_id)
    GROUP BY o.customer_id
)
SELECT
    CASE WHEN e.age IS NULL THEN 'unknown'
         WHEN e.age < 30 THEN '18-29' WHEN e.age < 40 THEN '30-39' WHEN e.age < 50 THEN '40-49'
         WHEN e.age < 60 THEN '50-59' WHEN e.age < 65 THEN '60-64' WHEN e.age < 70 THEN '65-69'
         WHEN e.age < 75 THEN '70-74' ELSE '75+' END                                AS age_bracket,
    count(*)                                                                        AS eligible,
    count_if(has_pl)                                                                AS with_personal_loan,
    count_if(has_mg)                                                                AS with_mortgage,
    count_if(e.max_term_personal_loan_months < 24)                                  AS personal_loan_no_term_by_age,
    count_if(e.max_term_mortgage_months < 180)                                      AS mortgage_no_term_by_age,
    count_if(has_mg AND mg_term_cut_by_age)                                         AS mortgage_shortened_by_age
FROM per_customer pc
JOIN eligible e USING (customer_id)
GROUP BY 1
ORDER BY 1;
