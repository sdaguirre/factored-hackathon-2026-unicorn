-- =============================================================================================
-- Bias check for the age-at-maturity term cap (policy 0.4, docs/CREDIT_RULES.md section 3).
-- Eligible customers by age bracket at the cutoff: how many keep a personal loan and a mortgage,
-- and how many lose a loan product only because of age. Age comes from silver customers and is
-- used here to MEASURE the rule, as allowed for protected attributes.
-- Parameters (catalog.schema): :silver_schema, :gold_schema. Read-only.
-- =============================================================================================

WITH eligible AS (
    SELECT p.customer_id,
           CAST(floor(months_between(p.as_of_date, c.date_of_birth) / 12) AS INT) AS age
    FROM IDENTIFIER(:gold_schema || '.customer_credit_profile') p
    JOIN IDENTIFIER(:silver_schema || '.customers') c USING (customer_id)
    WHERE p.is_eligible
),
per_customer AS (
    SELECT o.customer_id,
           bool_or(o.product_code = 'PL' AND o.is_available)                                  AS has_pl,
           bool_or(o.product_code = 'MG' AND o.is_available)                                  AS has_mg,
           bool_or(o.product_code = 'PL' AND o.unavailable_reason = 'term_above_age_at_maturity') AS pl_term_cut_by_age,
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
    count_if(NOT has_pl AND pl_term_cut_by_age)                                     AS personal_loan_lost_to_age,
    count_if(NOT has_mg AND mg_term_cut_by_age)                                     AS mortgage_lost_to_age,
    count_if(has_mg AND mg_term_cut_by_age)                                         AS mortgage_shortened_by_age
FROM per_customer pc
JOIN eligible e USING (customer_id)
GROUP BY 1
ORDER BY 1;
