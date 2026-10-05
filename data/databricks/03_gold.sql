-- Databricks notebook source
-- =====================================================================
-- 03_gold — Vistas gold a partir del silver nuevo (ya tipado), sin los
-- parches que necesitábamos con las tablas manuales / bronze directo.
--
-- Cambios respecto a gold_tables_databricks_v2.sql:
--   - gold_customer_complaints_summary ahora lee de silver.complaints
--     (ya tipado y deduplicado ahí) en vez de castear/deduplicar desde
--     bronze acá mismo.
--   - gold_customer_cashflow_summary ahora apunta a silver.transactions
--     (la tabla real nueva) en vez de silver_transactions (la manual).
--   - Se sacaron los CAST redundantes en columnas que silver ya
--     tipa correctamente (current_balance, credit_score, etc.) — ya no
--     hacen falta, silver hace ese trabajo ahora.
--
-- Nombres de schema hardcodeados en _test por ahora. Antes de correr
-- contra el real, buscar-y-reemplazar en VS Code:
--   silver_latam_bank_test -> silver_latam_bank
--   gold_latam_bank_test   -> gold_latam_bank
-- =====================================================================

-- COMMAND ----------

CREATE SCHEMA IF NOT EXISTS workspace.gold_latam_bank_test;

-- COMMAND ----------
-- 1) gold_customer_products_summary
-- COMMAND ----------

CREATE OR REPLACE VIEW workspace.gold_latam_bank_test.gold_customer_products_summary AS
WITH fx AS (
    SELECT
        source_currency,
        target_currency,
        date AS rate_date,
        exchange_rate
    FROM workspace.silver_latam_bank_test.daily_exchange_rates
),
products_typed AS (
    SELECT
        p.product_id,
        p.customer_id,
        p.product_type,                       -- raw Spanish label, kept for display
        rpc.product_type AS product_type_en,   -- English label via official mapping
        p.product_status,
        p.currency,
        p.current_balance,
        p.credit_limit,
        p.interest_rate,
        p.days_past_due,
        p.last_updated
    FROM workspace.silver_latam_bank_test.products p
    LEFT JOIN workspace.silver_latam_bank.ref_product_catalog rpc
        ON rpc.source_product_type = p.product_type
),
products_usd AS (
    SELECT
        pt.*,
        CASE WHEN pt.currency = 'USD' THEN pt.current_balance
             ELSE pt.current_balance * fx.exchange_rate END AS current_balance_usd,
        CASE WHEN pt.currency = 'USD' OR pt.credit_limit IS NULL THEN pt.credit_limit
             ELSE pt.credit_limit * fx.exchange_rate END AS credit_limit_usd
    FROM products_typed pt
    LEFT JOIN fx
        ON fx.source_currency = pt.currency
       AND fx.target_currency = 'USD'
       AND fx.rate_date = LEAST(pt.last_updated, DATE '2026-06-17')
)
SELECT
    customer_id,
    COUNT(*) AS total_products,
    COUNT(*) FILTER (WHERE product_status = 'Active') AS active_products,
    ARRAY_JOIN(COLLECT_SET(product_type), ', ') AS product_types,
    SUM(credit_limit_usd) FILTER (WHERE product_status = 'Active') AS total_credit_limit_usd,
    SUM(current_balance_usd) FILTER (WHERE product_status = 'Active') AS total_current_balance_usd,
    ROUND(AVG(interest_rate) FILTER (
        WHERE product_type_en IN ('Credit Card', 'Personal Loan', 'Mortgage')
    ), 2) AS avg_credit_interest_rate,
    MAX(days_past_due) AS max_days_past_due,
    ROUND(AVG(days_past_due), 1) AS avg_days_past_due,
    MAX(CASE WHEN days_past_due > 30 THEN 1 ELSE 0 END) AS has_delinquent_product,
    MAX(CASE WHEN product_type_en IN ('Credit Card', 'Personal Loan', 'Mortgage')
             THEN 1 ELSE 0 END) AS has_credit_product
FROM products_usd
GROUP BY customer_id;

-- COMMAND ----------
-- 2) gold_customer_complaints_summary
--    Ahora lee directo de silver.complaints (ya tipado + deduplicado
--    en 02_silver), no hace falta castear ni deduplicar acá.
-- COMMAND ----------

CREATE OR REPLACE VIEW workspace.gold_latam_bank_test.gold_customer_complaints_summary AS
WITH fx AS (
    SELECT
        source_currency, target_currency,
        date AS rate_date,
        exchange_rate
    FROM workspace.silver_latam_bank_test.daily_exchange_rates
),
complaints_usd AS (
    SELECT
        c.*,
        CASE WHEN c.currency = 'USD' OR c.claimed_amount IS NULL THEN c.claimed_amount
             ELSE c.claimed_amount * fx_claim.exchange_rate END AS claimed_amount_usd,
        CASE WHEN c.currency = 'USD' OR c.compensation_granted IS NULL THEN c.compensation_granted
             ELSE c.compensation_granted * fx_comp.exchange_rate END AS compensation_granted_usd
    FROM workspace.silver_latam_bank_test.complaints c
    LEFT JOIN fx fx_claim
        ON fx_claim.source_currency = c.currency AND fx_claim.target_currency = 'USD'
       AND fx_claim.rate_date = LEAST(c.creation_date, DATE '2026-06-17')
    LEFT JOIN fx fx_comp
        ON fx_comp.source_currency = c.currency AND fx_comp.target_currency = 'USD'
       AND fx_comp.rate_date = LEAST(COALESCE(c.resolution_date, c.creation_date), DATE '2026-06-17')
)
SELECT
    customer_id,
    COUNT(*) AS total_complaints,
    COUNT(*) FILTER (WHERE case_type = 'Claim') AS total_claims,
    COUNT(*) FILTER (WHERE case_type = 'Complaint') AS total_complaints_only,
    COUNT(*) FILTER (WHERE case_type = 'Request') AS total_requests,
    COUNT(*) FILTER (WHERE case_type = 'Suggestion') AS total_suggestions,
    COUNT(*) FILTER (WHERE status IN ('Open', 'In Process', 'Escalated')) AS open_cases,
    COUNT(*) FILTER (WHERE status IN ('Resolved', 'Closed')) AS resolved_cases,
    COUNT(*) FILTER (WHERE status = 'Rejected') AS rejected_cases,
    COUNT(*) FILTER (WHERE priority = 'Critical') AS critical_cases,
    COUNT(*) FILTER (
        WHERE priority IN ('High', 'Critical') AND status IN ('Open', 'In Process', 'Escalated')
    ) AS open_priority_cases,
    COUNT(*) FILTER (WHERE sla_breached) AS sla_breaches,
    COUNT(*) FILTER (WHERE is_repeat_complainer) AS repeat_complaint_flags,
    SUM(claimed_amount_usd) AS total_claimed_amount_usd,
    SUM(compensation_granted_usd) AS total_compensation_granted_usd,
    AVG(resolution_days) AS avg_resolution_days,
    MAX(resolution_days) AS max_resolution_days,
    AVG(resolution_satisfaction) AS avg_resolution_satisfaction,
    (COUNT(*) FILTER (WHERE sla_breached) > 0) AS has_sla_breach,
    (COUNT(*) FILTER (WHERE priority = 'Critical') > 0) AS has_critical_complaint,
    (COUNT(*) FILTER (
        WHERE priority IN ('High', 'Critical') AND status IN ('Open', 'In Process', 'Escalated')
    ) > 0) AS has_open_priority_complaint
FROM complaints_usd
GROUP BY customer_id;

-- COMMAND ----------
-- 3) gold_customer_cashflow_summary
--    Ahora apunta a silver.transactions (la tabla real nueva de
--    02_silver), ya NO a silver_transactions (la manual/interina).
-- COMMAND ----------

CREATE OR REPLACE VIEW workspace.gold_latam_bank_test.gold_customer_cashflow_summary AS
WITH fx AS (
    SELECT
        source_currency, target_currency,
        date AS rate_date,
        exchange_rate
    FROM workspace.silver_latam_bank_test.daily_exchange_rates
),
snapshot AS (
    SELECT MAX(process_date) AS as_of_date
    FROM workspace.silver_latam_bank_test.transactions
),
tx_usd AS (
    SELECT
        t.transaction_id, t.customer_id, t.transaction_type,
        t.transaction_status, t.process_date, t.is_fraud,
        CASE WHEN t.currency = 'USD' THEN t.amount
             ELSE COALESCE(t.amount_usd, t.amount * fx.exchange_rate) END AS amount_usd_final
    FROM workspace.silver_latam_bank_test.transactions t
    LEFT JOIN fx
        ON fx.source_currency = t.currency
       AND fx.target_currency = 'USD'
       AND fx.rate_date = LEAST(CAST(t.transaction_date AS DATE), DATE '2026-06-17')
),
tx_recent AS (
    SELECT tu.*
    FROM tx_usd tu
    CROSS JOIN snapshot s
    WHERE tu.transaction_status = 'Approved'
      AND tu.process_date > DATE_SUB(s.as_of_date, 90)
)
SELECT
    customer_id,
    COUNT(*) AS total_transactions_90d,
    SUM(CASE WHEN transaction_type = 'Deposit' THEN amount_usd_final ELSE 0 END) AS total_income_usd_90d,
    SUM(CASE WHEN transaction_type IN ('Withdrawal', 'Payment', 'Purchase') THEN amount_usd_final ELSE 0 END) AS total_expense_usd_90d,
    SUM(CASE WHEN transaction_type = 'Transfer' THEN amount_usd_final ELSE 0 END) AS total_transfer_usd_90d,
    SUM(CASE WHEN transaction_type = 'Adjustment' THEN amount_usd_final ELSE 0 END) AS total_adjustment_usd_90d,
    ROUND(AVG(amount_usd_final), 2) AS avg_transaction_amount_usd,
    COUNT(*) FILTER (WHERE is_fraud) AS fraud_flags_90d,
    (COUNT(*) FILTER (WHERE is_fraud) > 0) AS has_recent_fraud
FROM tx_recent
GROUP BY customer_id;

-- COMMAND ----------
-- 4) gold_customer_credit_features
--    Features-only para el rules engine (no banda, no DTI cap, no monto
--    de oferta calculado acá). Mismas notas abiertas que en v2:
--    - current_installments_usd en préstamos sigue usando plazo SINTÉTICO
--      (24/180 meses) porque expiration_date sigue 100% NULL en préstamos
--      (confirmado con datos reales 2026-10-04, no es solo sospecha).
--    - max_days_past_due_proxy usa el valor actual, no un máximo de 12
--      meses real (sin tabla de historial).
--    - NOTA DEL EQUIPO (comparación V vs P, 2026-10-04): esta vista
--      probablemente se reemplace por una vista delgada sobre
--      customer_credit_profile/customer_credit_offer_options (Versión P,
--      policy 0.2). Mantener esta v2 solo como features base hasta que
--      se resuelva el "pending owner" de esa decisión.
-- COMMAND ----------

CREATE OR REPLACE VIEW workspace.gold_latam_bank_test.gold_customer_credit_features AS
WITH fx AS (
    SELECT
        source_currency, target_currency,
        date AS rate_date,
        exchange_rate
    FROM workspace.silver_latam_bank_test.daily_exchange_rates
),
customers_typed AS (
    SELECT
        customer_id, segment, customer_status, country,
        credit_score,
        estimated_monthly_income,
        registration_date,
        accepts_marketing,
        -- Confirmado contra SELECT DISTINCT country (2026-10-04): 'México'
        -- (con tilde), 'Colombia', 'Argentina'. No se observaron otros valores.
        CASE
            WHEN country = 'México'    THEN 'MXN'
            WHEN country = 'Colombia'  THEN 'COP'
            WHEN country = 'Argentina' THEN 'ARS'
            ELSE NULL
        END AS declared_income_currency
    FROM workspace.silver_latam_bank_test.customers
),
declared_income_usd AS (
    SELECT
        ct.customer_id,
        CASE WHEN ct.declared_income_currency = 'USD' OR ct.declared_income_currency IS NULL
                  THEN ct.estimated_monthly_income
             ELSE ct.estimated_monthly_income * fx.exchange_rate END AS declared_income_usd
    FROM customers_typed ct
    LEFT JOIN fx
        ON fx.source_currency = ct.declared_income_currency
       AND fx.target_currency = 'USD'
       AND fx.rate_date = DATE '2026-06-17'
),
products_typed AS (
    SELECT
        p.product_id, p.customer_id, p.product_status, p.currency,
        rpc.product_type AS product_type_en,
        p.current_balance,
        p.interest_rate,
        p.last_updated
    FROM workspace.silver_latam_bank_test.products p
    LEFT JOIN workspace.silver_latam_bank.ref_product_catalog rpc
        ON rpc.source_product_type = p.product_type
    WHERE p.product_status = 'Active'
),
products_usd AS (
    SELECT
        pt.*,
        CASE WHEN pt.currency = 'USD' THEN pt.current_balance
             ELSE pt.current_balance * fx.exchange_rate END AS current_balance_usd
    FROM products_typed pt
    LEFT JOIN fx
        ON fx.source_currency = pt.currency
       AND fx.target_currency = 'USD'
       AND fx.rate_date = LEAST(pt.last_updated, DATE '2026-06-17')
),
installments AS (
    SELECT
        customer_id,
        SUM(
            CASE
                WHEN product_type_en = 'Credit Card' THEN
                    current_balance_usd * 0.04   -- placeholder min-payment %, confirmar con el equipo
                WHEN product_type_en = 'Personal Loan' THEN
                    current_balance_usd * (interest_rate / 100.0 / 12.0)
                    / (1 - POWER(1 + (interest_rate / 100.0 / 12.0), -24))   -- sintético 24mo
                WHEN product_type_en = 'Mortgage' THEN
                    current_balance_usd * (interest_rate / 100.0 / 12.0)
                    / (1 - POWER(1 + (interest_rate / 100.0 / 12.0), -180))  -- sintético 180mo
                ELSE 0
            END
        ) AS current_installments_usd
    FROM products_usd
    GROUP BY customer_id
)
SELECT
    ct.customer_id, ct.segment, ct.customer_status, ct.country, ct.credit_score,
    ct.accepts_marketing,
    FLOOR(MONTHS_BETWEEN(DATE '2026-06-30', ct.registration_date)) AS tenure_months,

    di.declared_income_usd,
    cf.total_income_usd_90d / 3.0 AS observed_income_usd,
    CASE
        WHEN di.declared_income_usd IS NOT NULL AND cf.total_income_usd_90d IS NOT NULL
            THEN LEAST(di.declared_income_usd, cf.total_income_usd_90d / 3.0)
        ELSE COALESCE(cf.total_income_usd_90d / 3.0, di.declared_income_usd)
    END AS income_usd_for_rules,

    COALESCE(ins.current_installments_usd, 0) AS current_installments_usd,

    COALESCE(ps.total_products, 0) AS total_products,
    COALESCE(ps.active_products, 0) AS active_products,
    ps.product_types,
    (COALESCE(ps.has_credit_product, 0) = 1) AS has_credit_product,
    (COALESCE(ps.has_delinquent_product, 0) = 1) AS has_delinquent_product,
    ps.max_days_past_due AS max_days_past_due_proxy,

    COALESCE(cs.total_complaints, 0) AS total_complaints,
    COALESCE(cs.open_cases, 0) AS open_complaints,
    COALESCE(cs.critical_cases, 0) AS critical_complaints,
    COALESCE(cs.has_open_priority_complaint, false) AS has_open_priority_complaint,
    COALESCE(cs.repeat_complaint_flags, 0) AS repeat_complaint_flags,

    COALESCE(cf.has_recent_fraud, false) AS has_recent_fraud,

    (ct.customer_status = 'Active') AS passes_r01_active_customer,
    (FLOOR(MONTHS_BETWEEN(DATE '2026-06-30', ct.registration_date)) >= 6) AS passes_r02_min_tenure,
    (COALESCE(ps.max_days_past_due, 0) <= 30) AS passes_r03_no_recent_delinquency

FROM customers_typed ct
LEFT JOIN declared_income_usd di ON di.customer_id = ct.customer_id
LEFT JOIN workspace.gold_latam_bank_test.gold_customer_products_summary ps ON ps.customer_id = ct.customer_id
LEFT JOIN workspace.gold_latam_bank_test.gold_customer_complaints_summary cs ON cs.customer_id = ct.customer_id
LEFT JOIN workspace.gold_latam_bank_test.gold_customer_cashflow_summary cf ON cf.customer_id = ct.customer_id
LEFT JOIN installments ins ON ins.customer_id = ct.customer_id
WHERE ct.accepts_marketing = true;

-- COMMAND ----------
-- Validación rápida de las 4 vistas (conteos, no debería haber ninguna
-- explosión de filas si el join de fx sigue siendo exacto por fecha)
-- COMMAND ----------

SELECT 'gold_customer_products_summary' AS vista, COUNT(*) AS filas
FROM workspace.gold_latam_bank_test.gold_customer_products_summary
UNION ALL
SELECT 'gold_customer_complaints_summary', COUNT(*)
FROM workspace.gold_latam_bank_test.gold_customer_complaints_summary
UNION ALL
SELECT 'gold_customer_cashflow_summary', COUNT(*)
FROM workspace.gold_latam_bank_test.gold_customer_cashflow_summary
UNION ALL
SELECT 'gold_customer_credit_features', COUNT(*)
FROM workspace.gold_latam_bank_test.gold_customer_credit_features;
