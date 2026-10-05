-- =====================================================================
-- 03_gold — Tablas gold de resumen por cliente a partir del silver nuevo
-- (ya tipado y deduplicado), sin los parches que necesitábamos con las
-- tablas manuales / bronze directo.
--
-- Cambios respecto a gold_tables_databricks_v2.sql:
--   - gold_customer_complaints_summary ahora lee de silver.complaints
--     (ya tipado y deduplicado ahí) en vez de castear/deduplicar desde
--     bronze acá mismo.
--   - gold_customer_cashflow_summary ahora apunta a silver.transactions
--     (la tabla real nueva) en vez de silver_transactions (la manual).
--   - Se sacaron los CAST redundantes en columnas que silver ya
--     tipa correctamente (current_balance, credit_score, etc.).
--   - Son TABLAS (CREATE TABLE AS SELECT), no vistas: el cuerpo de una
--     vista no admite parámetros, y el job las refresca en cada corrida.
--   - gold_customer_credit_features (Versión V, reglas preliminares) ya no
--     se crea acá: el agente lee customer_credit_profile (policy 0.3,
--     data/databricks/gold/10_customer_credit_profile.sql).
--
-- Archivo SQL (no notebook) para correr como tarea SQL de un Job sobre un
-- SQL warehouse. Parámetros con nombre (catálogo.schema):
--   :silver_schema  ej. workspace.silver_latam_bank_test | workspace.silver_latam_bank
--   :gold_schema    ej. workspace.gold_latam_bank_test   | workspace.gold_latam_bank
-- ref_product_catalog se lee siempre de workspace.silver_latam_bank.
--
-- Migración única: si en el schema ya existen como VISTAS (corridas
-- anteriores), CREATE OR REPLACE TABLE falla. Borrarlas una sola vez:
--   DROP VIEW IF EXISTS <gold_schema>.gold_customer_products_summary;
--   DROP VIEW IF EXISTS <gold_schema>.gold_customer_complaints_summary;
--   DROP VIEW IF EXISTS <gold_schema>.gold_customer_cashflow_summary;
--   DROP VIEW IF EXISTS <gold_schema>.gold_customer_credit_features;
-- =====================================================================

-- COMMAND ----------

CREATE SCHEMA IF NOT EXISTS IDENTIFIER(:gold_schema);

-- COMMAND ----------
-- 1) gold_customer_products_summary
-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:gold_schema || '.gold_customer_products_summary') AS
WITH fx AS (
    SELECT
        source_currency,
        target_currency,
        date AS rate_date,
        exchange_rate
    FROM IDENTIFIER(:silver_schema || '.daily_exchange_rates')
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
    FROM IDENTIFIER(:silver_schema || '.products') p
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
--    creation_date es TIMESTAMP en silver: se pasa a DATE para el tipo de cambio.
-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:gold_schema || '.gold_customer_complaints_summary') AS
WITH fx AS (
    SELECT
        source_currency, target_currency,
        date AS rate_date,
        exchange_rate
    FROM IDENTIFIER(:silver_schema || '.daily_exchange_rates')
),
complaints_usd AS (
    SELECT
        c.*,
        CASE WHEN c.currency = 'USD' OR c.claimed_amount IS NULL THEN c.claimed_amount
             ELSE c.claimed_amount * fx_claim.exchange_rate END AS claimed_amount_usd,
        CASE WHEN c.currency = 'USD' OR c.compensation_granted IS NULL THEN c.compensation_granted
             ELSE c.compensation_granted * fx_comp.exchange_rate END AS compensation_granted_usd
    FROM IDENTIFIER(:silver_schema || '.complaints') c
    LEFT JOIN fx fx_claim
        ON fx_claim.source_currency = c.currency AND fx_claim.target_currency = 'USD'
       AND fx_claim.rate_date = LEAST(CAST(c.creation_date AS DATE), DATE '2026-06-17')
    LEFT JOIN fx fx_comp
        ON fx_comp.source_currency = c.currency AND fx_comp.target_currency = 'USD'
       AND fx_comp.rate_date = LEAST(COALESCE(c.resolution_date, CAST(c.creation_date AS DATE)), DATE '2026-06-17')
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

CREATE OR REPLACE TABLE IDENTIFIER(:gold_schema || '.gold_customer_cashflow_summary') AS
WITH fx AS (
    SELECT
        source_currency, target_currency,
        date AS rate_date,
        exchange_rate
    FROM IDENTIFIER(:silver_schema || '.daily_exchange_rates')
),
snapshot AS (
    SELECT MAX(process_date) AS as_of_date
    FROM IDENTIFIER(:silver_schema || '.transactions')
),
tx_usd AS (
    SELECT
        t.transaction_id, t.customer_id, t.transaction_type,
        t.transaction_status, t.process_date, t.is_fraud,
        CASE WHEN t.currency = 'USD' THEN t.amount
             ELSE COALESCE(t.amount_usd, t.amount * fx.exchange_rate) END AS amount_usd_final
    FROM IDENTIFIER(:silver_schema || '.transactions') t
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
-- Validación rápida de las 3 tablas (conteos, no debería haber ninguna
-- explosión de filas si el join de fx sigue siendo exacto por fecha)
-- COMMAND ----------

SELECT 'gold_customer_products_summary' AS tabla, COUNT(*) AS filas
FROM IDENTIFIER(:gold_schema || '.gold_customer_products_summary')
UNION ALL
SELECT 'gold_customer_complaints_summary', COUNT(*)
FROM IDENTIFIER(:gold_schema || '.gold_customer_complaints_summary')
UNION ALL
SELECT 'gold_customer_cashflow_summary', COUNT(*)
FROM IDENTIFIER(:gold_schema || '.gold_customer_cashflow_summary');
