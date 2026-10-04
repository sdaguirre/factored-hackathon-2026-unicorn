-- Databricks notebook source
-- =====================================================================
-- 02_silver — Tipado explícito + dedup desde las tablas bronze (todo
-- STRING) a tablas silver con tipos reales.
--
-- Correr DESPUÉS de 01_bronze. No toca ref_product_catalog ni
-- ref_term_grid (esas se mantienen aparte con scripts/load_reference.py).
--
-- Los nombres de schema están hardcodeados abajo como
-- workspace.bronze_latam_bank_test / workspace.silver_latam_bank_test
-- (los widgets de Databricks no son confiables en compute Serverless SQL
-- Warehouse). Una vez validado acá, hacé buscar-y-reemplazar en VS Code:
--   bronze_latam_bank_test -> bronze_latam_bank
--   silver_latam_bank_test -> silver_latam_bank
-- antes de correrlo contra el schema real.
-- =====================================================================

-- COMMAND ----------

CREATE SCHEMA IF NOT EXISTS workspace.silver_latam_bank_test;

-- COMMAND ----------
-- 1) branches
-- NOTA: no tiene columna de fecha de carga propia (solo _bronze_ingested_at),
-- así que el dedup usa esa.
-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.silver_latam_bank_test.branches AS
SELECT
    branch_id,
    branch_code,
    branch_name,
    branch_type,
    branch_status,
    address,
    city,
    state,
    country,
    postal_code,
    CAST(latitude AS DOUBLE) AS latitude,
    CAST(longitude AS DOUBLE) AS longitude,
    phone,
    email,
    opening_time,
    closing_time,
    CAST(branch_opening_date AS DATE) AS branch_opening_date,
    CAST(has_atms AS BOOLEAN) AS has_atms,
    CAST(CAST(atm_count AS DOUBLE) AS INT) AS atm_count,
    CAST(has_teller_windows AS BOOLEAN) AS has_teller_windows,
    CAST(CAST(teller_window_count AS DOUBLE) AS INT) AS teller_window_count,
    geographic_zone,
    _bronze_ingested_at,
    current_timestamp() AS _silver_processed_at
FROM workspace.bronze_latam_bank_test.branches
WHERE branch_id IS NOT NULL   -- prevención: nunca dejar que una clave nula colapse filas en el QUALIFY
QUALIFY ROW_NUMBER() OVER (PARTITION BY branch_id ORDER BY _bronze_ingested_at DESC) = 1;

-- COMMAND ----------
-- 2) call_center_interactions
-- NOTA: verificar con el equipo el significado real de agent_used_accent
-- y mentioned_products (acá asumo boolean y string libre respectivamente
-- — mentioned_products puede venir como lista separada por comas, la dejo
-- como STRING sin parsear hasta confirmar el formato real).
-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.silver_latam_bank_test.call_center_interactions
PARTITIONED BY (process_date) AS
SELECT
    interaction_id,
    CAST(interaction_date AS TIMESTAMP) AS interaction_date,
    CAST(process_date AS DATE) AS process_date,
    customer_id,
    agent_id,
    channel,
    interaction_type,
    contact_reason,
    reason_category,
    mentioned_products,
    CAST(CAST(duration_seconds AS DOUBLE) AS INT) AS duration_seconds,
    CAST(CAST(wait_time_seconds AS DOUBLE) AS INT) AS wait_time_seconds,
    CAST(was_resolved AS BOOLEAN) AS was_resolved,
    CAST(requires_followup AS BOOLEAN) AS requires_followup,
    CAST(was_escalated AS BOOLEAN) AS was_escalated,
    CAST(has_recording AS BOOLEAN) AS has_recording,
    CAST(has_transcript AS BOOLEAN) AS has_transcript,
    detected_sentiment,
    CAST(sentiment_score AS DOUBLE) AS sentiment_score,
    customer_detected_accent,
    agent_used_accent  -- STRING: confirmado NO es boolean, viene como texto (ej. 'colombian'),
    _bronze_ingested_at,
    current_timestamp() AS _silver_processed_at
FROM workspace.bronze_latam_bank_test.call_center_interactions
WHERE interaction_id IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY interaction_id ORDER BY process_date DESC) = 1;

-- COMMAND ----------
-- 3) campaign_sends
-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.silver_latam_bank_test.campaign_sends
PARTITIONED BY (process_date) AS
SELECT
    send_id,
    CAST(send_date AS TIMESTAMP) AS send_date,
    CAST(process_date AS DATE) AS process_date,
    campaign_id,
    customer_id,
    TRIM(send_channel) AS send_channel,
    template_used,
    subject,
    TRIM(send_status) AS send_status,
    CAST(was_delivered AS BOOLEAN) AS was_delivered,
    CAST(was_opened AS BOOLEAN) AS was_opened,
    CAST(open_date AS TIMESTAMP) AS open_date,
    CAST(was_clicked AS BOOLEAN) AS was_clicked,
    CAST(click_date AS TIMESTAMP) AS click_date,
    CAST(CAST(click_count AS DOUBLE) AS INT) AS click_count,
    CAST(had_conversion AS BOOLEAN) AS had_conversion,
    CAST(conversion_date AS TIMESTAMP) AS conversion_date,
    CAST(conversion_value AS DOUBLE) AS conversion_value,
    open_device,
    open_country,
    failure_reason,
    CAST(send_cost AS DOUBLE) AS send_cost,
    _bronze_ingested_at,
    current_timestamp() AS _silver_processed_at
FROM workspace.bronze_latam_bank_test.campaign_sends
WHERE send_id IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY send_id ORDER BY process_date DESC, send_date DESC) = 1;

-- COMMAND ----------
-- 4) complaints
-- (mismo patrón que ya veníamos usando directo en gold; ahora vive acá)
-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.silver_latam_bank_test.complaints
PARTITIONED BY (process_date) AS
SELECT
    complaint_id,
    customer_id,
    case_type,
    category,
    subcategory,
    reception_channel,
    affected_product_id,
    related_branch_id,
    origin_interaction_id,
    description,
    CAST(claimed_amount AS DOUBLE) AS claimed_amount,
    currency,
    priority,
    status,
    assigned_agent_id,
    CAST(assignment_date AS TIMESTAMP) AS assignment_date,
    CAST(first_response_date AS TIMESTAMP) AS first_response_date,
    CAST(resolution_date AS DATE) AS resolution_date,
    CAST(closing_date AS DATE) AS closing_date,
    CAST(sla_breached AS BOOLEAN) AS sla_breached,
    CAST(resolution_days AS DOUBLE) AS resolution_days,
    resolution,
    CAST(compensation_granted AS DOUBLE) AS compensation_granted,
    CAST(resolution_satisfaction AS DOUBLE) AS resolution_satisfaction,
    CAST(is_repeat_complainer AS BOOLEAN) AS is_repeat_complainer,
    CAST(creation_date AS DATE) AS creation_date,
    CAST(process_date AS DATE) AS process_date,
    _bronze_ingested_at,
    current_timestamp() AS _silver_processed_at
FROM workspace.bronze_latam_bank_test.complaints
WHERE complaint_id IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY complaint_id ORDER BY creation_date DESC) = 1;

-- COMMAND ----------
-- 5) customers
-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.silver_latam_bank_test.customers AS
SELECT
    customer_id,
    first_name,
    last_name,
    email,
    document_type,
    document_number,
    CAST(date_of_birth AS DATE) AS date_of_birth,
    gender,
    marital_status,
    education_level,
    occupation,
    mobile_phone,
    landline_phone,
    address,
    city,
    state,
    postal_code,
    country,
    segment,
    customer_status,
    CAST(CAST(credit_score AS DOUBLE) AS INT) AS credit_score,
    CAST(estimated_monthly_income AS DOUBLE) AS estimated_monthly_income,
    CAST(accepts_marketing AS BOOLEAN) AS accepts_marketing,
    CAST(registration_date AS DATE) AS registration_date,
    registration_branch_id,
    CAST(last_updated AS DATE) AS last_updated,
    detected_accent,
    _bronze_ingested_at,
    current_timestamp() AS _silver_processed_at
FROM workspace.bronze_latam_bank_test.customers
WHERE customer_id IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY last_updated DESC) = 1;

-- COMMAND ----------
-- 6) daily_exchange_rates
-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.silver_latam_bank_test.daily_exchange_rates AS
SELECT
    source_currency,
    target_currency,
    CAST(date AS DATE) AS date,
    CAST(exchange_rate AS DOUBLE) AS exchange_rate,
    CAST(buy_rate AS DOUBLE) AS buy_rate,
    CAST(sell_rate AS DOUBLE) AS sell_rate,
    source,
    _bronze_ingested_at,
    current_timestamp() AS _silver_processed_at
FROM workspace.bronze_latam_bank_test.daily_exchange_rates
WHERE source_currency IS NOT NULL AND target_currency IS NOT NULL AND date IS NOT NULL
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY source_currency, target_currency, date
    ORDER BY _bronze_ingested_at DESC
) = 1;

-- COMMAND ----------
-- 7) marketing_campaigns
-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.silver_latam_bank_test.marketing_campaigns AS
SELECT
    campaign_id,
    campaign_name,
    campaign_type,
    campaign_objective,
    campaign_status,
    description,
    CAST(start_date AS DATE) AS start_date,
    CAST(end_date AS DATE) AS end_date,
    CAST(budget AS DOUBLE) AS budget,
    CAST(expected_conversion_rate AS DOUBLE) AS expected_conversion_rate,
    target_segment,
    target_country,
    promoted_product,
    _bronze_ingested_at,
    current_timestamp() AS _silver_processed_at
FROM workspace.bronze_latam_bank_test.marketing_campaigns
WHERE campaign_id IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY campaign_id ORDER BY _bronze_ingested_at DESC) = 1;

-- COMMAND ----------
-- 8) products
-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.silver_latam_bank_test.products AS
SELECT
    product_id,
    product_number,
    customer_id,
    product_type,
    product_status,
    currency,
    CAST(current_balance AS DOUBLE) AS current_balance,
    CAST(credit_limit AS DOUBLE) AS credit_limit,
    CAST(interest_rate AS DOUBLE) AS interest_rate,
    CAST(CAST(days_past_due AS DOUBLE) AS INT) AS days_past_due,
    CAST(opening_date AS DATE) AS opening_date,
    CAST(expiration_date AS DATE) AS expiration_date,
    CAST(last_updated AS DATE) AS last_updated,
    CAST(last_transaction_date AS DATE) AS last_transaction_date,
    opening_branch_id,
    opening_channel,
    CAST(has_linked_app AS BOOLEAN) AS has_linked_app,
    _bronze_ingested_at,
    current_timestamp() AS _silver_processed_at
FROM workspace.bronze_latam_bank_test.products
WHERE product_id IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY last_updated DESC) = 1;

-- COMMAND ----------
-- 9) service_agents
-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.silver_latam_bank_test.service_agents AS
SELECT
    agent_id,
    employee_code,
    first_name,
    last_name,
    email,
    phone,
    agent_type,
    agent_status,
    assigned_branch_id,
    CAST(hire_date AS DATE) AS hire_date,
    experience_level,
    specialty,
    languages,
    native_accent,
    country_of_origin,
    work_shift,
    CAST(avg_csat AS DOUBLE) AS avg_csat,
    CAST(CAST(total_monthly_interactions AS DOUBLE) AS INT) AS total_monthly_interactions,
    _bronze_ingested_at,
    current_timestamp() AS _silver_processed_at
FROM workspace.bronze_latam_bank_test.service_agents
WHERE agent_id IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY agent_id ORDER BY _bronze_ingested_at DESC) = 1;

-- COMMAND ----------
-- 10) transactions
-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.silver_latam_bank_test.transactions
PARTITIONED BY (process_date) AS
SELECT
    transaction_id,
    CAST(transaction_date AS TIMESTAMP) AS transaction_date,
    CAST(process_date AS DATE) AS process_date,
    product_id,
    customer_id,
    TRIM(transaction_type) AS transaction_type,
    TRIM(transaction_category) AS transaction_category,
    CAST(amount AS DOUBLE) AS amount,
    currency,
    CAST(amount_usd AS DOUBLE) AS amount_usd,
    TRIM(channel) AS channel,
    branch_id,
    merchant_name,
    merchant_category,
    transaction_country,
    transaction_city,
    TRIM(transaction_status) AS transaction_status,
    response_code,
    CAST(is_fraud AS BOOLEAN) AS is_fraud,
    CAST(fraud_score AS DOUBLE) AS fraud_score,
    CAST(latitude AS DOUBLE) AS latitude,
    CAST(longitude AS DOUBLE) AS longitude,
    _bronze_ingested_at,
    current_timestamp() AS _silver_processed_at
FROM workspace.bronze_latam_bank_test.transactions
WHERE transaction_id IS NOT NULL
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY transaction_id
    ORDER BY process_date DESC, transaction_date DESC
) = 1;

-- COMMAND ----------
-- Validación rápida: conteos silver vs bronze (deberían ser iguales o
-- levemente menores si hubo duplicados reales que el QUALIFY sacó)
-- COMMAND ----------

SELECT 'branches' AS tabla,
       (SELECT COUNT(*) FROM workspace.bronze_latam_bank_test.branches) AS bronze,
       (SELECT COUNT(*) FROM workspace.silver_latam_bank_test.branches) AS silver
UNION ALL
SELECT 'call_center_interactions',
       (SELECT COUNT(*) FROM workspace.bronze_latam_bank_test.call_center_interactions),
       (SELECT COUNT(*) FROM workspace.silver_latam_bank_test.call_center_interactions)
UNION ALL
SELECT 'campaign_sends',
       (SELECT COUNT(*) FROM workspace.bronze_latam_bank_test.campaign_sends),
       (SELECT COUNT(*) FROM workspace.silver_latam_bank_test.campaign_sends)
UNION ALL
SELECT 'complaints',
       (SELECT COUNT(*) FROM workspace.bronze_latam_bank_test.complaints),
       (SELECT COUNT(*) FROM workspace.silver_latam_bank_test.complaints)
UNION ALL
SELECT 'customers',
       (SELECT COUNT(*) FROM workspace.bronze_latam_bank_test.customers),
       (SELECT COUNT(*) FROM workspace.silver_latam_bank_test.customers)
UNION ALL
SELECT 'daily_exchange_rates',
       (SELECT COUNT(*) FROM workspace.bronze_latam_bank_test.daily_exchange_rates),
       (SELECT COUNT(*) FROM workspace.silver_latam_bank_test.daily_exchange_rates)
UNION ALL
SELECT 'marketing_campaigns',
       (SELECT COUNT(*) FROM workspace.bronze_latam_bank_test.marketing_campaigns),
       (SELECT COUNT(*) FROM workspace.silver_latam_bank_test.marketing_campaigns)
UNION ALL
SELECT 'products',
       (SELECT COUNT(*) FROM workspace.bronze_latam_bank_test.products),
       (SELECT COUNT(*) FROM workspace.silver_latam_bank_test.products)
UNION ALL
SELECT 'service_agents',
       (SELECT COUNT(*) FROM workspace.bronze_latam_bank_test.service_agents),
       (SELECT COUNT(*) FROM workspace.silver_latam_bank_test.service_agents)
UNION ALL
SELECT 'transactions',
       (SELECT COUNT(*) FROM workspace.bronze_latam_bank_test.transactions),
       (SELECT COUNT(*) FROM workspace.silver_latam_bank_test.transactions);

-- COMMAND ----------
-- GATE DE CALIDAD — a diferencia de los chequeos de arriba (que solo
-- informan), esta celda CORTA la ejecución del notebook si encuentra
-- duplicados reales por clave de negocio en alguna tabla silver. Así,
-- si en el futuro llega un CSV con datos duplicados, el notebook falla
-- en rojo acá mismo en vez de dejar pasar silver corrupto a gold.
--
-- Guarda el resultado en una vista temporal que lee la celda de Python
-- de abajo (es la única parte en Python del archivo, a propósito
-- mínima: lo demás sigue en SQL).
-- COMMAND ----------

CREATE OR REPLACE TEMP VIEW _dq_duplicate_check AS
SELECT 'branches' AS tabla, COUNT(*) AS claves_duplicadas
FROM (SELECT branch_id FROM workspace.silver_latam_bank_test.branches GROUP BY branch_id HAVING COUNT(*) > 1)
UNION ALL
SELECT 'call_center_interactions', COUNT(*) FROM (
    SELECT interaction_id FROM workspace.silver_latam_bank_test.call_center_interactions
    GROUP BY interaction_id HAVING COUNT(*) > 1)
UNION ALL
SELECT 'campaign_sends', COUNT(*) FROM (
    SELECT send_id FROM workspace.silver_latam_bank_test.campaign_sends
    GROUP BY send_id HAVING COUNT(*) > 1)
UNION ALL
SELECT 'complaints', COUNT(*) FROM (
    SELECT complaint_id FROM workspace.silver_latam_bank_test.complaints
    GROUP BY complaint_id HAVING COUNT(*) > 1)
UNION ALL
SELECT 'customers', COUNT(*) FROM (
    SELECT customer_id FROM workspace.silver_latam_bank_test.customers
    GROUP BY customer_id HAVING COUNT(*) > 1)
UNION ALL
SELECT 'daily_exchange_rates', COUNT(*) FROM (
    SELECT source_currency, target_currency, date FROM workspace.silver_latam_bank_test.daily_exchange_rates
    GROUP BY source_currency, target_currency, date HAVING COUNT(*) > 1)
UNION ALL
SELECT 'marketing_campaigns', COUNT(*) FROM (
    SELECT campaign_id FROM workspace.silver_latam_bank_test.marketing_campaigns
    GROUP BY campaign_id HAVING COUNT(*) > 1)
UNION ALL
SELECT 'products', COUNT(*) FROM (
    SELECT product_id FROM workspace.silver_latam_bank_test.products
    GROUP BY product_id HAVING COUNT(*) > 1)
UNION ALL
SELECT 'service_agents', COUNT(*) FROM (
    SELECT agent_id FROM workspace.silver_latam_bank_test.service_agents
    GROUP BY agent_id HAVING COUNT(*) > 1)
UNION ALL
SELECT 'transactions', COUNT(*) FROM (
    SELECT transaction_id FROM workspace.silver_latam_bank_test.transactions
    GROUP BY transaction_id HAVING COUNT(*) > 1);

SELECT * FROM _dq_duplicate_check;

-- COMMAND ----------

-- MAGIC %python
-- MAGIC # Única celda en Python del notebook, a propósito chica: lee el
-- MAGIC # resultado de la vista temporal de arriba y corta la ejecución
-- MAGIC # si encuentra algún duplicado. Si este notebook se programa como
-- MAGIC # Job más adelante, esto hace que el Job quede marcado como
-- MAGIC # FALLIDO en vez de avanzar con datos malos a gold.
-- MAGIC rows = spark.sql("SELECT * FROM _dq_duplicate_check WHERE claves_duplicadas > 0").collect()
-- MAGIC if rows:
-- MAGIC     detalle = ", ".join(f"{r['tabla']} ({r['claves_duplicadas']} claves)" for r in rows)
-- MAGIC     raise Exception(f"GATE DE CALIDAD FALLÓ: duplicados reales encontrados en: {detalle}")
-- MAGIC print("Gate de calidad OK: sin duplicados reales en ninguna tabla silver.")
