-- ===========================================================================
-- 003_scoped_business_keys.sql — enforce the same-tenant relationships that
-- 001 declared only as ID-only foreign keys.
--
-- Why this file exists
--   001 models every business relationship as a single-column foreign key on
--   the surrogate id. That proves the referenced row EXISTS; it does not prove
--   the referenced row belongs to the SAME tenant as the row that points at
--   it. A write that pairs tenant A's crm_devices row with tenant B's customer
--   id is accepted today. This file closes that hole with composite,
--   same-tenant foreign keys, without retiring a single historical key: the
--   ID-only constraints stay until a separately reviewed retirement.
--
-- Rules for this file (same as 001/002)
--   * Forward-only. Rollback is a restore from backup (host/INSTALL.md).
--   * Every executable statement carries a stable `-- irica-step: <id>` marker
--     and is its own journal entry, so an interrupted install is reconciled
--     from engine metadata.
--   * Strictly ADDITIVE: 5 unique keys and 6 foreign keys, nothing else. No
--     DROP, no TRUNCATE, no column rewrite, no row rewrite, no row deletion.
--   * DDL is NOT transactional on MySQL or MariaDB. No claim of atomic
--     rollback is made anywhere in the runner.
--   * Postconditions are declared in host/src/Schema.php
--     (Schema::migrationSteps) and compared against normalised
--     information_schema metadata — never against SHOW CREATE TABLE text.
--
-- Order that must not be changed
--   s01..s04 add the UNIQUE (tenant_id, id) parent keys FIRST. InnoDB refuses
--   a composite foreign key unless the referenced columns carry a unique
--   index, so no constraint in s05..s10 may run before its parent key.
--   Each step is separate on purpose: a crash between two steps leaves a
--   schema the journal can describe exactly (some parent keys installed, some
--   constraints not), which is recoverable, unlike one opaque multi-statement
--   block.
--
-- Precondition, checked by the runner, not assumed
--   host/src/MigrationPreflight.php::scopedIntegrity() refuses to install any
--   of this file's constraints when the target already carries an orphan or a
--   cross-tenant reference for one of the six relationships, and reports only
--   table / constraint / count / code. A blocked installation changes no
--   business record: it never repairs, never deletes, never truncates. The
--   operator gets a counts-only report and decides how the bad rows are
--   resolved.
--
-- Engine matrix (Phase B)
--   MariaDB 10.3.39  — provisioned and exercised by lab/fixtures/host_v2.
--   MySQL 5.7        — BLOCKED, UNRUN: the official CDN returns an embargo
--                      redirect that was not followed. No MySQL support,
--                      compatibility or PASS is claimed by this file.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- Parent keys. A composite foreign key needs a UNIQUE index on exactly the
-- referenced columns, in the referenced order. `users (tenant_id, id)` already
-- has one since 002 s01; the four parents this file needs do not.
-- ---------------------------------------------------------------------------
-- irica-step: s01_customers_tenant_id_unique
ALTER TABLE customers ADD UNIQUE KEY uq_customers_tenant_id (tenant_id, id);

-- irica-step: s02_owners_tenant_id_unique
ALTER TABLE owners ADD UNIQUE KEY uq_owners_tenant_id (tenant_id, id);

-- irica-step: s03_periods_tenant_id_unique
ALTER TABLE periods ADD UNIQUE KEY uq_periods_tenant_id (tenant_id, id);

-- irica-step: s04_crm_devices_tenant_id_unique
ALTER TABLE crm_devices ADD UNIQUE KEY uq_crm_devices_tenant_id (tenant_id, id);

-- ---------------------------------------------------------------------------
-- Composite same-tenant foreign keys. The ID-only keys they sit next to stay
-- in place: this file adds a stronger guarantee, it does not replace one.
--
-- Nullable columns keep their nullable semantics. A NULL foreign key is still
-- "no reference" and is not an integrity failure, and a tombstoned parent row
-- (deleted_at set) is still a row that exists — a soft delete is not a SQL
-- delete, so the constraint continues to accept it.
-- ---------------------------------------------------------------------------
-- irica-step: s05_settings_period_scoped_fk
ALTER TABLE settings ADD CONSTRAINT fk_settings_period_scoped
  FOREIGN KEY (tenant_id, current_period_id) REFERENCES periods (tenant_id, id);

-- irica-step: s06_crm_devices_customer_scoped_fk
ALTER TABLE crm_devices ADD CONSTRAINT fk_crm_devices_customer_scoped
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, id);

-- irica-step: s07_crm_devices_owner_scoped_fk
ALTER TABLE crm_devices ADD CONSTRAINT fk_crm_devices_owner_scoped
  FOREIGN KEY (tenant_id, owner_id) REFERENCES owners (tenant_id, id);

-- irica-step: s08_crm_devices_period_scoped_fk
ALTER TABLE crm_devices ADD CONSTRAINT fk_crm_devices_period_scoped
  FOREIGN KEY (tenant_id, period_id) REFERENCES periods (tenant_id, id);

-- irica-step: s09_customer_tx_customer_scoped_fk
ALTER TABLE customer_transactions ADD CONSTRAINT fk_customer_tx_customer_scoped
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, id);

-- irica-step: s10_customer_tx_device_scoped_fk
ALTER TABLE customer_transactions ADD CONSTRAINT fk_customer_tx_device_scoped
  FOREIGN KEY (tenant_id, device_id) REFERENCES crm_devices (tenant_id, id);
