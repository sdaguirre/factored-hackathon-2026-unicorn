-- =============================================================================================
-- Quality checks for the gold credit tables. Last task of the credit_gold_daily job.
-- Each statement fails with assert_true (USER_RAISED_EXCEPTION) when a check does not hold, so a
-- broken rebuild fails the job and its notification instead of reaching the agent silently.
-- =============================================================================================

-- 1. One profile row per current customer in silver.
SELECT assert_true(
    p.n = p.n_ids AND p.n = c.n,
    concat('profile rows ', p.n, ', distinct ids ', p.n_ids, ', silver customers ', c.n))
FROM (SELECT count(*) AS n, count(DISTINCT customer_id) AS n_ids
      FROM workspace.gold_latam_bank.customer_credit_profile) p
CROSS JOIN (SELECT count(*) AS n FROM workspace.silver_latam_bank.customers WHERE __END_AT IS NULL) c;

-- 2. Cutoff date, policy version and exchange rate present on every row.
SELECT assert_true(
    count_if(as_of_date IS NULL OR policy_version IS NULL OR fx_to_usd IS NULL) = 0,
    concat('rows missing as_of_date, policy_version or fx_to_usd: ',
           count_if(as_of_date IS NULL OR policy_version IS NULL OR fx_to_usd IS NULL)))
FROM workspace.gold_latam_bank.customer_credit_profile;

-- 3. The profile was built with the policy version currently loaded in silver.
SELECT assert_true(
    p.versions = 1 AND p.v = r.v,
    concat('profile policy_version ', p.v, ' vs silver ', r.v))
FROM (SELECT count(DISTINCT policy_version) AS versions, max(policy_version) AS v
      FROM workspace.gold_latam_bank.customer_credit_profile) p
CROSS JOIN (SELECT max(param_value) AS v FROM workspace.silver_latam_bank.ref_policy_params
            WHERE param_name = 'policy_version') r;

-- 4. Eligible share inside the expected range (33.8% at the 2026-06-30 cutoff with policy 0.3).
--    A share outside 20%-50% usually means a broken join, cast or parameter, not a real change.
SELECT assert_true(
    avg(CAST(is_eligible AS INT)) BETWEEN 0.20 AND 0.50,
    concat('eligible share out of range: ', round(avg(CAST(is_eligible AS INT)), 4)))
FROM workspace.gold_latam_bank.customer_credit_profile;

-- 5. One option row per customer and term-grid row.
SELECT assert_true(
    o.n = p.n * g.n,
    concat('options ', o.n, ' expected ', p.n * g.n))
FROM (SELECT count(*) AS n FROM workspace.gold_latam_bank.customer_credit_offer_options) o
CROSS JOIN (SELECT count(*) AS n FROM workspace.gold_latam_bank.customer_credit_profile) p
CROSS JOIN (SELECT count(*) AS n FROM workspace.silver_latam_bank.ref_term_grid) g;

-- 6. No available option exceeds the 20% capacity.
SELECT assert_true(
    count(*) = 0,
    concat('available options above the available installment: ', count(*)))
FROM workspace.gold_latam_bank.customer_credit_offer_options o
JOIN workspace.gold_latam_bank.customer_credit_profile p USING (customer_id)
WHERE o.is_available AND o.offer_monthly_installment_usd > p.available_installment_usd + 0.01;

-- 7. No available loan option above the band maximum term.
SELECT assert_true(
    count(*) = 0,
    concat('available loan options above the band maximum term: ', count(*)))
FROM workspace.gold_latam_bank.customer_credit_offer_options o
JOIN workspace.gold_latam_bank.customer_credit_profile p USING (customer_id)
WHERE o.is_available
  AND ((o.product_code = 'PL' AND o.term_months > p.max_term_personal_loan_months)
    OR (o.product_code = 'MG' AND o.term_months > p.max_term_mortgage_months));

-- 8. Available amounts inside the option range; no option for non-eligible customers.
SELECT assert_true(
    count_if(is_available AND (offer_max_amount_usd < option_min_amount_usd
                               OR offer_max_amount_usd > option_max_amount_usd)) = 0
    AND count_if(is_available AND unavailable_reason IS NOT NULL) = 0
    AND count_if(is_available AND offer_mode = 'none') = 0,
    'available options with amounts out of range, a reason, or a non-eligible customer')
FROM workspace.gold_latam_bank.customer_credit_offer_options;
