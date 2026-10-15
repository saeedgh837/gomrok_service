<?php
/**
 * Schema.php — the machine-readable contract for the IRICA cloud-sync host.
 *
 * One declared structure, from which everything else is derived: table list,
 * column list, column type, nullability, SQL default, enum, reference target
 * and the spec §3 settings allowlist. There is no second copy of any of it, so
 * `tables()`, `columnsFor()` and `validate()` cannot drift from each other —
 * and lab/tests/test_host_schema.py proves this file matches
 * information_schema exactly, which is what stops the PHP API and
 * host/migrations/001_initial.sql from diverging.
 *
 * Validation reports STRUCTURED failures, never prose: each failure is
 * `["code", "table", "column"]` with a stable `code` the HTTP layer maps to a
 * stable `error.code` (spec §6). The offending VALUE is deliberately absent —
 * echoing it back would leak PII into a response or a log (spec §10.9).
 *
 * Timestamps: every datetime column holds UTC ISO-8601 WITH an explicit
 * offset (spec §4). A naive `2026-10-05 10:00:00` is rejected — guessing
 * whether it meant Tehran or UTC would silently move a time.
 */

declare(strict_types=1);

final class Schema
{
    /** Spec §4: the envelope carried by every synced table, in order. */
    public const ENVELOPE = [
        'id', 'tenant_id', 'legacy_id', 'row_version',
        'created_at', 'updated_at', 'deleted_at', 'deleted_by',
    ];

    /** The envelope without `legacy_id` (users / sync_devices have no legacy). */
    public const ENVELOPE_PLAIN = [
        'id', 'tenant_id', 'row_version',
        'created_at', 'updated_at', 'deleted_at', 'deleted_by',
    ];

    /**
     * Phase B: the closed set of security lifecycle event names.
     *
     * Written by the operation that owns the state change, in the SAME SQL
     * transaction as that change — never through Log::audit(), whose write is
     * swallowed on failure. A failed login is NOT one of these: it stays in the
     * bounded login_attempts rate limiter, because a raw credential audit
     * payload would be a secret store.
     */
    public const SECURITY_EVENT_TYPES = [
        'account_created', 'password_reset', 'account_disabled',
        'account_enabled', 'legacy_linked', 'membership_assigned',
        'membership_removed', 'workspace_frozen', 'login_success',
        'logout', 'password_changed',
    ];

    /** Objects a LATER migration introduces; an earlier file's postcondition must not expect them. */
    private const LATER_INTRODUCTIONS = [
        'columns' => ['tenants.lifecycle', 'tenants.api_contract',
                      'tenants.context_version', 'tenants.replacement_tenant_id'],
        // Everything a migration AFTER 001 adds to a table 001 owns. 001's
        // postcondition is the whole declared legacy schema, so it must assert
        // these ABSENT: without the entry here 001 could never verify on a
        // target that has not run the later file yet, and adoption could never
        // excuse its now-failing claim about the same object.
        'indexes' => [
            'users.uq_users_tenant_id',
            'customers.uq_customers_tenant_id',
            'owners.uq_owners_tenant_id',
            'periods.uq_periods_tenant_id',
            'crm_devices.uq_crm_devices_tenant_id',
        ],
        'foreign_keys' => [
            'tenants.fk_tenants_replacement',
            'settings.fk_settings_period_scoped',
            'crm_devices.fk_crm_devices_customer_scoped',
            'crm_devices.fk_crm_devices_owner_scoped',
            'crm_devices.fk_crm_devices_period_scoped',
            'customer_transactions.fk_customer_tx_customer_scoped',
            'customer_transactions.fk_customer_tx_device_scoped',
        ],
    ];

    /** Every table in the central schema uses this character set. */
    private const TABLE_CHARSET = ['charset' => 'utf8mb4', 'collation' => 'utf8mb4_unicode_ci'];

    /** Tables owned by each migration file, so an untracked object can be told apart from a known one. */
    private const TABLES_FOR_VERSION = [
        '001_initial' => [
            'schema_migrations', 'tenants', 'users', 'memberships', 'sync_devices',
            'auth_sessions', 'login_attempts', 'audit_events', 'idempotency_keys',
            'change_log', 'tenant_state', 'periods', 'settings', 'customers',
            'owners', 'identities', 'crm_devices', 'customer_transactions',
        ],
        '002_identity_foundation' => [
            'accounts', 'account_workspace_memberships', 'account_sessions',
            'legacy_account_map', 'identity_state', 'account_security_events',
        ],
        // 003 creates no table: it only adds unique parent keys and composite
        // same-tenant foreign keys to tables 001 already owns.
        '003_scoped_business_keys' => [],
    ];

    /**
     * The six same-tenant business relationships, and the objects that enforce
     * them.
     *
     * 001 declared each of these as a single-column foreign key on the
     * surrogate id, which proves the referenced row EXISTS but not that it
     * belongs to the SAME tenant as the row pointing at it. 003 adds the
     * composite constraint; the ID-only key stays until a separately reviewed
     * retirement.
     *
     * `columns` and `ref_columns` are ORDERED lists, never sets: the composite
     * key (tenant_id, customer_id) and the key (customer_id, tenant_id) are
     * different constraints, and only the first is the one installed. The
     * ordering is what `MigrationPreflight::scopedIntegrity()` and the
     * integrity suite both compare against information_schema.
     *
     * @var list<array{step_id: string, constraint: string, table: string,
     *   columns: list<string>, ref_table: string, ref_columns: list<string>,
     *   parent_key: string}>
     */
    private const SCOPED_RELATIONSHIPS = [
        ['step_id' => 's05_settings_period_scoped_fk',
         'constraint' => 'fk_settings_period_scoped', 'table' => 'settings',
         'columns' => ['tenant_id', 'current_period_id'],
         'ref_table' => 'periods', 'ref_columns' => ['tenant_id', 'id'],
         'parent_key' => 'uq_periods_tenant_id'],
        ['step_id' => 's06_crm_devices_customer_scoped_fk',
         'constraint' => 'fk_crm_devices_customer_scoped', 'table' => 'crm_devices',
         'columns' => ['tenant_id', 'customer_id'],
         'ref_table' => 'customers', 'ref_columns' => ['tenant_id', 'id'],
         'parent_key' => 'uq_customers_tenant_id'],
        ['step_id' => 's07_crm_devices_owner_scoped_fk',
         'constraint' => 'fk_crm_devices_owner_scoped', 'table' => 'crm_devices',
         'columns' => ['tenant_id', 'owner_id'],
         'ref_table' => 'owners', 'ref_columns' => ['tenant_id', 'id'],
         'parent_key' => 'uq_owners_tenant_id'],
        ['step_id' => 's08_crm_devices_period_scoped_fk',
         'constraint' => 'fk_crm_devices_period_scoped', 'table' => 'crm_devices',
         'columns' => ['tenant_id', 'period_id'],
         'ref_table' => 'periods', 'ref_columns' => ['tenant_id', 'id'],
         'parent_key' => 'uq_periods_tenant_id'],
        ['step_id' => 's09_customer_tx_customer_scoped_fk',
         'constraint' => 'fk_customer_tx_customer_scoped', 'table' => 'customer_transactions',
         'columns' => ['tenant_id', 'customer_id'],
         'ref_table' => 'customers', 'ref_columns' => ['tenant_id', 'id'],
         'parent_key' => 'uq_customers_tenant_id'],
        ['step_id' => 's10_customer_tx_device_scoped_fk',
         'constraint' => 'fk_customer_tx_device_scoped', 'table' => 'customer_transactions',
         'columns' => ['tenant_id', 'device_id'],
         'ref_table' => 'crm_devices', 'ref_columns' => ['tenant_id', 'id'],
         'parent_key' => 'uq_crm_devices_tenant_id'],
    ];

    /**
     * The unique parent keys 003 adds, IN THE ORDER the file installs them.
     * InnoDB refuses a composite foreign key unless the referenced columns
     * carry a unique index, so every one of these must precede the constraints
     * that reference it.
     *
     * @var list<array{step_id: string, table: string, index: string}>
     */
    private const SCOPED_PARENT_KEYS = [
        ['step_id' => 's01_customers_tenant_id_unique',
         'table' => 'customers', 'index' => 'uq_customers_tenant_id'],
        ['step_id' => 's02_owners_tenant_id_unique',
         'table' => 'owners', 'index' => 'uq_owners_tenant_id'],
        ['step_id' => 's03_periods_tenant_id_unique',
         'table' => 'periods', 'index' => 'uq_periods_tenant_id'],
        ['step_id' => 's04_crm_devices_tenant_id_unique',
         'table' => 'crm_devices', 'index' => 'uq_crm_devices_tenant_id'],
    ];

    /**
     * Grouped index declarations, in key order.
     *
     * `PRIMARY` is spelled out like every other index so the declaration can be
     * compared with information_schema.STATISTICS without a special case. The
     * migration verifier asserts that each declared index is PRESENT with this
     * shape; it does not assert that no other index exists, because InnoDB
     * creates helper indexes for foreign keys on its own.
     *
     * @var array<string, list<array{name: string, unique: bool, columns: list<string>}>>
     */
    private const INDEXES = [
        'schema_migrations' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['version']],
        ],
        'migration_steps' => [
            ['name' => 'PRIMARY', 'unique' => true,
             'columns' => ['migration_version', 'step_id']],
        ],
        'tenants' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
            ['name' => 'uq_tenants_slug', 'unique' => true, 'columns' => ['slug']],
        ],
        'users' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
            ['name' => 'uq_users_tenant_username', 'unique' => true,
             'columns' => ['tenant_id', 'username']],
            // Phase B: legacy_account_map references users(tenant_id, id), and
            // InnoDB needs a UNIQUE index on the referenced columns. Added by
            // 002 BEFORE that table exists.
            ['name' => 'uq_users_tenant_id', 'unique' => true,
             'columns' => ['tenant_id', 'id']],
            ['name' => 'ix_users_tenant', 'unique' => false, 'columns' => ['tenant_id']],
        ],
        'memberships' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
            ['name' => 'uq_memberships_tenant_user', 'unique' => true,
             'columns' => ['tenant_id', 'user_id']],
        ],
        'sync_devices' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
            ['name' => 'ix_sync_devices_tenant_status', 'unique' => false,
             'columns' => ['tenant_id', 'status']],
            ['name' => 'ix_sync_devices_last_auth', 'unique' => false,
             'columns' => ['last_auth_at']],
        ],
        'auth_sessions' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
            ['name' => 'uq_auth_sessions_token_hash', 'unique' => true,
             'columns' => ['token_hash']],
            ['name' => 'ix_auth_sessions_family', 'unique' => false,
             'columns' => ['family_id']],
            ['name' => 'ix_auth_sessions_tenant_kind', 'unique' => false,
             'columns' => ['tenant_id', 'kind']],
            ['name' => 'ix_auth_sessions_absolute_expiry', 'unique' => false,
             'columns' => ['absolute_expires_at']],
            ['name' => 'ix_auth_sessions_user', 'unique' => false, 'columns' => ['user_id']],
            ['name' => 'ix_auth_sessions_device', 'unique' => false,
             'columns' => ['sync_device_id']],
        ],
        'login_attempts' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
            ['name' => 'ix_login_attempts_account_time', 'unique' => false,
             'columns' => ['account', 'attempted_at']],
            ['name' => 'ix_login_attempts_ip_time', 'unique' => false,
             'columns' => ['ip', 'attempted_at']],
            ['name' => 'ix_login_attempts_tenant', 'unique' => false,
             'columns' => ['tenant_id']],
        ],
        'audit_events' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
            ['name' => 'ix_audit_events_tenant_time', 'unique' => false,
             'columns' => ['tenant_id', 'occurred_at']],
            ['name' => 'ix_audit_events_action', 'unique' => false, 'columns' => ['action']],
        ],
        'idempotency_keys' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['operation_id']],
            ['name' => 'ix_idempotency_keys_expiry', 'unique' => false,
             'columns' => ['expires_at']],
            ['name' => 'ix_idempotency_keys_tenant', 'unique' => false,
             'columns' => ['tenant_id']],
        ],
        'change_log' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['seq']],
            ['name' => 'ix_change_log_tenant_seq', 'unique' => false,
             'columns' => ['tenant_id', 'seq']],
            ['name' => 'ix_change_log_entity_id', 'unique' => false,
             'columns' => ['tenant_id', 'entity', 'id']],
            ['name' => 'ix_change_log_operation', 'unique' => false,
             'columns' => ['operation_id']],
        ],
        'tenant_state' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['tenant_id']],
            ['name' => 'ix_tenant_state_source', 'unique' => false,
             'columns' => ['source_installation_id']],
        ],
        'periods' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
            ['name' => 'uq_periods_tenant_legacy', 'unique' => true,
             'columns' => ['tenant_id', 'legacy_id']],
            // Phase B: the parent key the composite same-tenant foreign keys
            // (003) reference. InnoDB refuses a composite foreign key without
            // a UNIQUE index on exactly the referenced columns.
            ['name' => 'uq_periods_tenant_id', 'unique' => true,
             'columns' => ['tenant_id', 'id']],
            ['name' => 'ix_periods_tenant_closed', 'unique' => false,
             'columns' => ['tenant_id', 'closed_at']],
        ],
        'settings' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
            ['name' => 'uq_settings_tenant', 'unique' => true, 'columns' => ['tenant_id']],
            ['name' => 'uq_settings_tenant_legacy', 'unique' => true,
             'columns' => ['tenant_id', 'legacy_id']],
        ],
        'customers' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
            ['name' => 'uq_customers_tenant_legacy', 'unique' => true,
             'columns' => ['tenant_id', 'legacy_id']],
            // Phase B: parent key of the scoped same-tenant foreign keys (003).
            ['name' => 'uq_customers_tenant_id', 'unique' => true,
             'columns' => ['tenant_id', 'id']],
            ['name' => 'ix_customers_tenant_mobile', 'unique' => false,
             'columns' => ['tenant_id', 'mobile']],
        ],
        'owners' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
            ['name' => 'uq_owners_tenant_legacy', 'unique' => true,
             'columns' => ['tenant_id', 'legacy_id']],
            // Phase B: parent key of the scoped same-tenant foreign keys (003).
            ['name' => 'uq_owners_tenant_id', 'unique' => true,
             'columns' => ['tenant_id', 'id']],
            ['name' => 'ix_owners_tenant_national', 'unique' => false,
             'columns' => ['tenant_id', 'national']],
        ],
        'identities' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
            ['name' => 'uq_identities_tenant_legacy', 'unique' => true,
             'columns' => ['tenant_id', 'legacy_id']],
            ['name' => 'ix_identities_tenant_national', 'unique' => false,
             'columns' => ['tenant_id', 'national']],
        ],
        'crm_devices' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
            ['name' => 'uq_crm_devices_tenant_legacy', 'unique' => true,
             'columns' => ['tenant_id', 'legacy_id']],
            // Phase B: parent key of the scoped same-tenant foreign keys (003).
            ['name' => 'uq_crm_devices_tenant_id', 'unique' => true,
             'columns' => ['tenant_id', 'id']],
            ['name' => 'ix_crm_devices_tenant_stage', 'unique' => false,
             'columns' => ['tenant_id', 'stage']],
            ['name' => 'ix_crm_devices_tenant_period', 'unique' => false,
             'columns' => ['tenant_id', 'period_id']],
            ['name' => 'ix_crm_devices_serial1', 'unique' => false,
             'columns' => ['tenant_id', 'serial1']],
            ['name' => 'ix_crm_devices_customer', 'unique' => false,
             'columns' => ['customer_id']],
            ['name' => 'ix_crm_devices_owner', 'unique' => false, 'columns' => ['owner_id']],
        ],
        'customer_transactions' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
            ['name' => 'uq_customer_tx_tenant_customer_seq', 'unique' => true,
             'columns' => ['tenant_id', 'customer_id', 'seq']],
            ['name' => 'uq_customer_tx_tenant_legacy', 'unique' => true,
             'columns' => ['tenant_id', 'legacy_id']],
            ['name' => 'ix_customer_tx_tenant_at', 'unique' => false,
             'columns' => ['tenant_id', 'at']],
            ['name' => 'ix_customer_tx_device', 'unique' => false, 'columns' => ['device_id']],
        ],
        'accounts' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
            ['name' => 'uq_accounts_login', 'unique' => true, 'columns' => ['login']],
        ],
        'account_workspace_memberships' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
            ['name' => 'uq_awm_account_tenant', 'unique' => true,
             'columns' => ['account_id', 'tenant_id']],
            ['name' => 'ix_awm_tenant_status_account', 'unique' => false,
             'columns' => ['tenant_id', 'status', 'account_id']],
        ],
        'account_sessions' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
            ['name' => 'uq_account_sessions_token_hash', 'unique' => true,
             'columns' => ['token_hash']],
            ['name' => 'ix_account_sessions_account_revocation', 'unique' => false,
             'columns' => ['account_id', 'revoked_at', 'absolute_expires_at']],
        ],
        'legacy_account_map' => [
            ['name' => 'PRIMARY', 'unique' => true,
             'columns' => ['tenant_id', 'legacy_user_id']],
        ],
        'identity_state' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
        ],
        'account_security_events' => [
            ['name' => 'PRIMARY', 'unique' => true, 'columns' => ['id']],
            ['name' => 'ix_ase_subject_time', 'unique' => false,
             'columns' => ['subject_account_id', 'created_at']],
            ['name' => 'ix_ase_tenant_time', 'unique' => false,
             'columns' => ['tenant_id', 'created_at']],
        ],
    ];

    /**
     * Grouped foreign-key declarations.
     *
     * @var array<string, list<array{name: string, columns: list<string>, ref_table: string, ref_columns: list<string>}>>
     */
    private const FOREIGN_KEYS = [
        'schema_migrations' => [],
        'migration_steps' => [],
        'tenants' => [],
        'users' => [
            ['name' => 'fk_users_tenant', 'columns' => ['tenant_id'],
             'ref_table' => 'tenants', 'ref_columns' => ['id']],
            ['name' => 'fk_users_deleted_by', 'columns' => ['deleted_by'],
             'ref_table' => 'users', 'ref_columns' => ['id']],
        ],
        'memberships' => [
            ['name' => 'fk_memberships_tenant', 'columns' => ['tenant_id'],
             'ref_table' => 'tenants', 'ref_columns' => ['id']],
            ['name' => 'fk_memberships_user', 'columns' => ['user_id'],
             'ref_table' => 'users', 'ref_columns' => ['id']],
        ],
        'sync_devices' => [
            ['name' => 'fk_sync_devices_tenant', 'columns' => ['tenant_id'],
             'ref_table' => 'tenants', 'ref_columns' => ['id']],
            ['name' => 'fk_sync_devices_enrolled_by', 'columns' => ['enrolled_by'],
             'ref_table' => 'users', 'ref_columns' => ['id']],
            ['name' => 'fk_sync_devices_deleted_by', 'columns' => ['deleted_by'],
             'ref_table' => 'users', 'ref_columns' => ['id']],
        ],
        'auth_sessions' => [
            ['name' => 'fk_auth_sessions_tenant', 'columns' => ['tenant_id'],
             'ref_table' => 'tenants', 'ref_columns' => ['id']],
            ['name' => 'fk_auth_sessions_user', 'columns' => ['user_id'],
             'ref_table' => 'users', 'ref_columns' => ['id']],
            ['name' => 'fk_auth_sessions_device', 'columns' => ['sync_device_id'],
             'ref_table' => 'sync_devices', 'ref_columns' => ['id']],
            ['name' => 'fk_auth_sessions_parent', 'columns' => ['parent_id'],
             'ref_table' => 'auth_sessions', 'ref_columns' => ['id']],
            ['name' => 'fk_auth_sessions_rotated', 'columns' => ['rotated_to'],
             'ref_table' => 'auth_sessions', 'ref_columns' => ['id']],
        ],
        'login_attempts' => [
            ['name' => 'fk_login_attempts_tenant', 'columns' => ['tenant_id'],
             'ref_table' => 'tenants', 'ref_columns' => ['id']],
        ],
        'audit_events' => [
            ['name' => 'fk_audit_events_tenant', 'columns' => ['tenant_id'],
             'ref_table' => 'tenants', 'ref_columns' => ['id']],
            ['name' => 'fk_audit_events_user', 'columns' => ['actor_user_id'],
             'ref_table' => 'users', 'ref_columns' => ['id']],
            ['name' => 'fk_audit_events_device', 'columns' => ['actor_device_id'],
             'ref_table' => 'sync_devices', 'ref_columns' => ['id']],
        ],
        'idempotency_keys' => [
            ['name' => 'fk_idempotency_keys_tenant', 'columns' => ['tenant_id'],
             'ref_table' => 'tenants', 'ref_columns' => ['id']],
        ],
        'change_log' => [
            ['name' => 'fk_change_log_tenant', 'columns' => ['tenant_id'],
             'ref_table' => 'tenants', 'ref_columns' => ['id']],
            ['name' => 'fk_change_log_user', 'columns' => ['actor_user_id'],
             'ref_table' => 'users', 'ref_columns' => ['id']],
            ['name' => 'fk_change_log_device', 'columns' => ['actor_device_id'],
             'ref_table' => 'sync_devices', 'ref_columns' => ['id']],
        ],
        'tenant_state' => [
            ['name' => 'fk_tenant_state_tenant', 'columns' => ['tenant_id'],
             'ref_table' => 'tenants', 'ref_columns' => ['id']],
        ],
        'periods' => [
            ['name' => 'fk_periods_tenant', 'columns' => ['tenant_id'],
             'ref_table' => 'tenants', 'ref_columns' => ['id']],
            ['name' => 'fk_periods_deleted_by', 'columns' => ['deleted_by'],
             'ref_table' => 'users', 'ref_columns' => ['id']],
        ],
        'settings' => [
            ['name' => 'fk_settings_tenant', 'columns' => ['tenant_id'],
             'ref_table' => 'tenants', 'ref_columns' => ['id']],
            ['name' => 'fk_settings_period', 'columns' => ['current_period_id'],
             'ref_table' => 'periods', 'ref_columns' => ['id']],
            // Phase B: the same reference, scoped to the tenant. The ID-only
            // key above stays; this one additionally proves the period belongs
            // to THIS tenant. Added by 003.
            ['name' => 'fk_settings_period_scoped',
             'columns' => ['tenant_id', 'current_period_id'],
             'ref_table' => 'periods', 'ref_columns' => ['tenant_id', 'id']],
            ['name' => 'fk_settings_deleted_by', 'columns' => ['deleted_by'],
             'ref_table' => 'users', 'ref_columns' => ['id']],
        ],
        'customers' => [
            ['name' => 'fk_customers_tenant', 'columns' => ['tenant_id'],
             'ref_table' => 'tenants', 'ref_columns' => ['id']],
            ['name' => 'fk_customers_deleted_by', 'columns' => ['deleted_by'],
             'ref_table' => 'users', 'ref_columns' => ['id']],
        ],
        'owners' => [
            ['name' => 'fk_owners_tenant', 'columns' => ['tenant_id'],
             'ref_table' => 'tenants', 'ref_columns' => ['id']],
            ['name' => 'fk_owners_deleted_by', 'columns' => ['deleted_by'],
             'ref_table' => 'users', 'ref_columns' => ['id']],
        ],
        'identities' => [
            ['name' => 'fk_identities_tenant', 'columns' => ['tenant_id'],
             'ref_table' => 'tenants', 'ref_columns' => ['id']],
            ['name' => 'fk_identities_deleted_by', 'columns' => ['deleted_by'],
             'ref_table' => 'users', 'ref_columns' => ['id']],
        ],
        'crm_devices' => [
            ['name' => 'fk_crm_devices_tenant', 'columns' => ['tenant_id'],
             'ref_table' => 'tenants', 'ref_columns' => ['id']],
            ['name' => 'fk_crm_devices_customer', 'columns' => ['customer_id'],
             'ref_table' => 'customers', 'ref_columns' => ['id']],
            ['name' => 'fk_crm_devices_owner', 'columns' => ['owner_id'],
             'ref_table' => 'owners', 'ref_columns' => ['id']],
            ['name' => 'fk_crm_devices_period', 'columns' => ['period_id'],
             'ref_table' => 'periods', 'ref_columns' => ['id']],
            // Phase B: the same three references, scoped to the tenant. The
            // ID-only keys above stay; these additionally prove the customer,
            // the owner and the period belong to THIS tenant. Added by 003.
            ['name' => 'fk_crm_devices_customer_scoped',
             'columns' => ['tenant_id', 'customer_id'],
             'ref_table' => 'customers', 'ref_columns' => ['tenant_id', 'id']],
            ['name' => 'fk_crm_devices_owner_scoped',
             'columns' => ['tenant_id', 'owner_id'],
             'ref_table' => 'owners', 'ref_columns' => ['tenant_id', 'id']],
            ['name' => 'fk_crm_devices_period_scoped',
             'columns' => ['tenant_id', 'period_id'],
             'ref_table' => 'periods', 'ref_columns' => ['tenant_id', 'id']],
            ['name' => 'fk_crm_devices_deleted_by', 'columns' => ['deleted_by'],
             'ref_table' => 'users', 'ref_columns' => ['id']],
        ],
        'customer_transactions' => [
            ['name' => 'fk_customer_tx_tenant', 'columns' => ['tenant_id'],
             'ref_table' => 'tenants', 'ref_columns' => ['id']],
            ['name' => 'fk_customer_tx_customer', 'columns' => ['customer_id'],
             'ref_table' => 'customers', 'ref_columns' => ['id']],
            ['name' => 'fk_customer_tx_device', 'columns' => ['device_id'],
             'ref_table' => 'crm_devices', 'ref_columns' => ['id']],
            // Phase B: the same two references, scoped to the tenant. Added by 003.
            ['name' => 'fk_customer_tx_customer_scoped',
             'columns' => ['tenant_id', 'customer_id'],
             'ref_table' => 'customers', 'ref_columns' => ['tenant_id', 'id']],
            ['name' => 'fk_customer_tx_device_scoped',
             'columns' => ['tenant_id', 'device_id'],
             'ref_table' => 'crm_devices', 'ref_columns' => ['tenant_id', 'id']],
            ['name' => 'fk_customer_tx_deleted_by', 'columns' => ['deleted_by'],
             'ref_table' => 'users', 'ref_columns' => ['id']],
        ],
        'accounts' => [],
        'account_workspace_memberships' => [
            ['name' => 'fk_awm_account', 'columns' => ['account_id'],
             'ref_table' => 'accounts', 'ref_columns' => ['id']],
            ['name' => 'fk_awm_tenant', 'columns' => ['tenant_id'],
             'ref_table' => 'tenants', 'ref_columns' => ['id']],
        ],
        'account_sessions' => [
            ['name' => 'fk_account_sessions_account', 'columns' => ['account_id'],
             'ref_table' => 'accounts', 'ref_columns' => ['id']],
        ],
        'legacy_account_map' => [
            ['name' => 'fk_lam_tenant_user',
             'columns' => ['tenant_id', 'legacy_user_id'],
             'ref_table' => 'users', 'ref_columns' => ['tenant_id', 'id']],
            ['name' => 'fk_lam_account', 'columns' => ['account_id'],
             'ref_table' => 'accounts', 'ref_columns' => ['id']],
        ],
        'identity_state' => [
            ['name' => 'fk_identity_state_operator', 'columns' => ['first_operator_id'],
             'ref_table' => 'accounts', 'ref_columns' => ['id']],
        ],
        'account_security_events' => [
            ['name' => 'fk_ase_actor', 'columns' => ['actor_account_id'],
             'ref_table' => 'accounts', 'ref_columns' => ['id']],
            ['name' => 'fk_ase_subject', 'columns' => ['subject_account_id'],
             'ref_table' => 'accounts', 'ref_columns' => ['id']],
            ['name' => 'fk_ase_tenant', 'columns' => ['tenant_id'],
             'ref_table' => 'tenants', 'ref_columns' => ['id']],
        ],
    ];

    // Stable failure codes (spec §6 — the client maps codes, not messages).
    public const E_UNKNOWN_TABLE   = 'UNKNOWN_TABLE';
    public const E_UNKNOWN_ENTITY  = 'UNKNOWN_ENTITY';
    public const E_UNKNOWN_COLUMN  = 'UNKNOWN_COLUMN';
    public const E_MISSING_COLUMN  = 'MISSING_COLUMN';
    public const E_NULL_NOT_ALLOWED = 'NULL_NOT_ALLOWED';
    public const E_TYPE_MISMATCH   = 'TYPE_MISMATCH';
    public const E_ENUM_MISMATCH   = 'ENUM_MISMATCH';
    public const E_TOO_LONG        = 'TOO_LONG';
    public const E_INVALID_TIMESTAMP = 'INVALID_TIMESTAMP';
    public const E_INVALID_UUID    = 'INVALID_UUID';
    public const E_INVALID_LEGACY_ID = 'INVALID_LEGACY_ID';
    public const E_INVALID_JSON    = 'INVALID_JSON';
    public const E_EMPTY_REFERENCE = 'EMPTY_REFERENCE';
    /**
     * The value is an integer but outside the declared domain.
     *
     * Deliberately not TYPE_MISMATCH: the type is right, the domain is not, and
     * a client must be able to tell "you sent a string" from "you sent 0 where
     * a positive version is required". The domain exists because MySQL 5.7
     * parses and ignores CHECK, so a CHECK alone is not an integrity oracle on
     * the documented support matrix.
     */
    public const E_OUT_OF_RANGE   = 'OUT_OF_RANGE';

    /** @var array<string, array<string, mixed>>|null */
    private static ?array $schema = null;

    // ------------------------------------------------------------------
    // The one declared structure
    // ------------------------------------------------------------------

    /**
     * Column spec keys:
     *   sql      exact information_schema type, e.g. "varchar(80)"
     *   type     validation family: char|varchar|int|bigint|tinyint|datetime|
     *            text|mediumtext
     *   nullable whether SQL NULL is allowed
     *   default  SQL default as declared; null means "no default"
     *   enum     allowed values, or null
     *   format   uuid|timestamp|json|legacy_id, or null
     *   ref      "table.column" foreign key target, or null
     *   auto     true when the server generates the value (AUTO_INCREMENT)
     *   charset  explicit character set override, or null (derived from type)
     *   collation explicit collation override, or null
     *   min/max inclusive integer domain for type int|bigint, or null.
     *            Declared only where the value is a version counter that must
     *            never reach 0 or go negative; enforced by validate(), not by
     *            SQL, because MySQL 5.7 parses CHECK and ignores it.
     *
     * `required` is DERIVED (NOT NULL, no default, not auto-generated), never
     * declared, so it can never disagree with the SQL.
     *
     * @return array<string, array<string, mixed>>
     */
    private static function schema(): array
    {
        if (self::$schema !== null) {
            return self::$schema;
        }

        $envelope = [
            'id'          => ['sql' => 'char(36)', 'type' => 'char',
                              'nullable' => false, 'format' => 'uuid'],
            'tenant_id'   => ['sql' => 'char(36)', 'type' => 'char',
                              'nullable' => false, 'format' => 'uuid',
                              'ref' => 'tenants.id'],
            'legacy_id'   => ['sql' => 'varchar(64)', 'type' => 'varchar',
                              'nullable' => true, 'format' => 'legacy_id'],
            'row_version' => ['sql' => 'int', 'type' => 'int',
                              'nullable' => false, 'default' => 1],
            'created_at'  => ['sql' => 'datetime', 'type' => 'datetime',
                              'nullable' => false, 'format' => 'timestamp'],
            'updated_at'  => ['sql' => 'datetime', 'type' => 'datetime',
                              'nullable' => false, 'format' => 'timestamp'],
            'deleted_at'  => ['sql' => 'datetime', 'type' => 'datetime',
                              'nullable' => true, 'format' => 'timestamp'],
            'deleted_by'  => ['sql' => 'char(36)', 'type' => 'char',
                              'nullable' => true, 'format' => 'uuid',
                              'ref' => 'users.id'],
        ];

        $tables = [
            // ---- infrastructure -----------------------------------------
            'schema_migrations' => [
                'entity' => null, 'envelope' => 'none',
                'columns' => [
                    'version'     => ['sql' => 'varchar(64)', 'type' => 'varchar',
                                      'nullable' => false],
                    'description' => ['sql' => 'varchar(255)', 'type' => 'varchar',
                                      'nullable' => false, 'default' => ''],
                    'checksum'    => ['sql' => 'char(64)', 'type' => 'char',
                                      'nullable' => false],
                    'applied_at'  => ['sql' => 'datetime', 'type' => 'datetime',
                                      'nullable' => false, 'format' => 'timestamp'],
                    'applied_by'  => ['sql' => 'varchar(64)', 'type' => 'varchar',
                                      'nullable' => false, 'default' => ''],
                ],
            ],
            // Phase B: the named step journal. `schema_migrations` records the
            // FILE (filename stem + file SHA-256); this table records each step
            // inside a file so an interrupted run can be reconciled from engine
            // metadata instead of being repaired by hand.
            'migration_steps' => [
                'entity' => null, 'envelope' => 'none',
                'columns' => [
                    'migration_version' => ['sql' => 'varchar(64)', 'type' => 'varchar',
                                            'nullable' => false],
                    'step_id'           => ['sql' => 'varchar(64)', 'type' => 'varchar',
                                            'nullable' => false],
                    'checksum'          => ['sql' => 'char(64)', 'type' => 'char',
                                            'nullable' => false],
                    'status'            => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                            'nullable' => false,
                                            'enum' => ['started', 'applied',
                                                       'verified', 'failed']],
                    'cursor_value'      => ['sql' => 'varchar(64)', 'type' => 'varchar',
                                            'nullable' => true],
                    'started_at'        => ['sql' => 'datetime', 'type' => 'datetime',
                                            'nullable' => false, 'format' => 'timestamp'],
                    'completed_at'      => ['sql' => 'datetime', 'type' => 'datetime',
                                            'nullable' => true, 'format' => 'timestamp'],
                ],
            ],
            'tenants' => [
                'entity' => null, 'envelope' => 'none',
                'columns' => [
                    'id'         => ['sql' => 'char(36)', 'type' => 'char',
                                     'nullable' => false, 'format' => 'uuid'],
                    'slug'       => ['sql' => 'varchar(64)', 'type' => 'varchar',
                                     'nullable' => false],
                    'name'       => ['sql' => 'varchar(120)', 'type' => 'varchar',
                                     'nullable' => false, 'default' => ''],
                    'status'     => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                     'nullable' => false, 'default' => 'active',
                                     'enum' => ['active', 'suspended']],
                    'created_at' => ['sql' => 'datetime', 'type' => 'datetime',
                                     'nullable' => false, 'format' => 'timestamp'],
                    'updated_at' => ['sql' => 'datetime', 'type' => 'datetime',
                                     'nullable' => false, 'format' => 'timestamp'],
                    // Phase B (002_identity_foundation): lifecycle and contract
                    // are SEPARATE from `status`. `status = 'suspended'` stays an
                    // independent denial gate; `lifecycle` distinguishes a
                    // private staging workspace from an active one, and
                    // `api_contract` records whether /v1 or /v2 owns writes.
                    'lifecycle'          => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                             'nullable' => false, 'default' => 'staging',
                                             'enum' => ['staging', 'active']],
                    'api_contract'       => ['sql' => 'varchar(24)', 'type' => 'varchar',
                                             'nullable' => false,
                                             'default' => 'authoritative_v2',
                                             'enum' => ['legacy_v1', 'authoritative_v2']],
                    'context_version'    => ['sql' => 'int', 'type' => 'int',
                                             'nullable' => false, 'default' => 1,
                                             'min' => 1],
                    'replacement_tenant_id' => ['sql' => 'char(36)', 'type' => 'char',
                                                'nullable' => true, 'format' => 'uuid',
                                                'ref' => 'tenants.id'],
                ],
            ],
            'users' => [
                'entity' => null, 'envelope' => 'plain',
                'columns' => [
                    'username'             => ['sql' => 'varchar(64)', 'type' => 'varchar',
                                                'nullable' => false],
                    'password_hash'        => ['sql' => 'varchar(255)', 'type' => 'varchar',
                                                'nullable' => false],
                    'display_name'         => ['sql' => 'varchar(120)', 'type' => 'varchar',
                                                'nullable' => false, 'default' => ''],
                    'must_change_password' => ['sql' => 'tinyint(1)', 'type' => 'tinyint',
                                                'nullable' => false, 'default' => 1,
                                                'enum' => [0, 1]],
                    'status'               => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                                'nullable' => false, 'default' => 'active',
                                                'enum' => ['active', 'disabled']],
                    'token_version'        => ['sql' => 'int', 'type' => 'int',
                                                'nullable' => false, 'default' => 1],
                    'last_login_at'        => ['sql' => 'datetime', 'type' => 'datetime',
                                                'nullable' => true, 'format' => 'timestamp'],
                ],
            ],
            'memberships' => [
                'entity' => null, 'envelope' => 'none',
                'columns' => [
                    'id'         => ['sql' => 'char(36)', 'type' => 'char',
                                     'nullable' => false, 'format' => 'uuid'],
                    'tenant_id'  => ['sql' => 'char(36)', 'type' => 'char',
                                     'nullable' => false, 'format' => 'uuid',
                                     'ref' => 'tenants.id'],
                    'user_id'    => ['sql' => 'char(36)', 'type' => 'char',
                                     'nullable' => false, 'format' => 'uuid',
                                     'ref' => 'users.id'],
                    'role'       => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                     'nullable' => false, 'enum' => ['admin', 'user']],
                    'created_at' => ['sql' => 'datetime', 'type' => 'datetime',
                                     'nullable' => false, 'format' => 'timestamp'],
                    'updated_at' => ['sql' => 'datetime', 'type' => 'datetime',
                                     'nullable' => false, 'format' => 'timestamp'],
                ],
            ],
            'sync_devices' => [
                'entity' => null, 'envelope' => 'plain',
                'columns' => [
                    'name'          => ['sql' => 'varchar(120)', 'type' => 'varchar',
                                        'nullable' => false, 'default' => ''],
                    'status'        => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                        'nullable' => false, 'default' => 'active',
                                        'enum' => ['active', 'revoked']],
                    'token_version' => ['sql' => 'int', 'type' => 'int',
                                        'nullable' => false, 'default' => 1],
                    'last_auth_at'  => ['sql' => 'datetime', 'type' => 'datetime',
                                        'nullable' => true, 'format' => 'timestamp'],
                    'last_seen_at'  => ['sql' => 'datetime', 'type' => 'datetime',
                                        'nullable' => true, 'format' => 'timestamp'],
                    'enrolled_by'   => ['sql' => 'char(36)', 'type' => 'char',
                                        'nullable' => true, 'format' => 'uuid',
                                        'ref' => 'users.id'],
                ],
            ],
            'auth_sessions' => [
                'entity' => null, 'envelope' => 'none',
                'columns' => [
                    'id'                  => ['sql' => 'char(36)', 'type' => 'char',
                                              'nullable' => false, 'format' => 'uuid'],
                    'tenant_id'           => ['sql' => 'char(36)', 'type' => 'char',
                                              'nullable' => false, 'format' => 'uuid',
                                              'ref' => 'tenants.id'],
                    'family_id'           => ['sql' => 'char(36)', 'type' => 'char',
                                              'nullable' => false, 'format' => 'uuid'],
                    'kind'                => ['sql' => 'varchar(8)', 'type' => 'varchar',
                                              'nullable' => false,
                                              'enum' => ['user', 'device']],
                    'token_hash'          => ['sql' => 'char(64)', 'type' => 'char',
                                              'nullable' => false],
                    'user_id'             => ['sql' => 'char(36)', 'type' => 'char',
                                              'nullable' => true, 'format' => 'uuid',
                                              'ref' => 'users.id'],
                    'sync_device_id'      => ['sql' => 'char(36)', 'type' => 'char',
                                              'nullable' => true, 'format' => 'uuid',
                                              'ref' => 'sync_devices.id'],
                    'parent_id'           => ['sql' => 'char(36)', 'type' => 'char',
                                              'nullable' => true, 'format' => 'uuid',
                                              'ref' => 'auth_sessions.id'],
                    'issued_at'           => ['sql' => 'datetime', 'type' => 'datetime',
                                              'nullable' => false, 'format' => 'timestamp'],
                    'idle_expires_at'     => ['sql' => 'datetime', 'type' => 'datetime',
                                              'nullable' => false, 'format' => 'timestamp'],
                    'absolute_expires_at' => ['sql' => 'datetime', 'type' => 'datetime',
                                              'nullable' => false, 'format' => 'timestamp'],
                    'used_at'             => ['sql' => 'datetime', 'type' => 'datetime',
                                              'nullable' => true, 'format' => 'timestamp'],
                    'revoked_at'          => ['sql' => 'datetime', 'type' => 'datetime',
                                              'nullable' => true, 'format' => 'timestamp'],
                    'revoked_reason'      => ['sql' => 'varchar(64)', 'type' => 'varchar',
                                              'nullable' => false, 'default' => ''],
                    'rotated_to'          => ['sql' => 'char(36)', 'type' => 'char',
                                              'nullable' => true, 'format' => 'uuid',
                                              'ref' => 'auth_sessions.id'],
                    'created_at'          => ['sql' => 'datetime', 'type' => 'datetime',
                                              'nullable' => false, 'format' => 'timestamp'],
                ],
            ],
            'login_attempts' => [
                'entity' => null, 'envelope' => 'none',
                'columns' => [
                    'id'           => ['sql' => 'bigint', 'type' => 'bigint',
                                       'nullable' => false, 'auto' => true],
                    'tenant_id'    => ['sql' => 'char(36)', 'type' => 'char',
                                       'nullable' => true, 'format' => 'uuid',
                                       'ref' => 'tenants.id'],
                    'account'      => ['sql' => 'varchar(120)', 'type' => 'varchar',
                                       'nullable' => false],
                    'ip'           => ['sql' => 'varchar(45)', 'type' => 'varchar',
                                       'nullable' => false],
                    'attempted_at' => ['sql' => 'datetime', 'type' => 'datetime',
                                       'nullable' => false, 'format' => 'timestamp'],
                    'outcome'      => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                       'nullable' => false,
                                       'enum' => ['success', 'failure', 'locked']],
                ],
            ],
            'audit_events' => [
                'entity' => null, 'envelope' => 'none',
                'columns' => [
                    'id'              => ['sql' => 'char(36)', 'type' => 'char',
                                          'nullable' => false, 'format' => 'uuid'],
                    'tenant_id'       => ['sql' => 'char(36)', 'type' => 'char',
                                          'nullable' => false, 'format' => 'uuid',
                                          'ref' => 'tenants.id'],
                    'occurred_at'     => ['sql' => 'datetime', 'type' => 'datetime',
                                          'nullable' => false, 'format' => 'timestamp'],
                    'action'          => ['sql' => 'varchar(64)', 'type' => 'varchar',
                                          'nullable' => false],
                    'actor_type'      => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                          'nullable' => false, 'default' => 'system',
                                          'enum' => ['system', 'user', 'device']],
                    'actor_user_id'   => ['sql' => 'char(36)', 'type' => 'char',
                                          'nullable' => true, 'format' => 'uuid',
                                          'ref' => 'users.id'],
                    'actor_device_id' => ['sql' => 'char(36)', 'type' => 'char',
                                          'nullable' => true, 'format' => 'uuid',
                                          'ref' => 'sync_devices.id'],
                    'entity'          => ['sql' => 'varchar(64)', 'type' => 'varchar',
                                          'nullable' => false, 'default' => ''],
                    'entity_id'       => ['sql' => 'char(36)', 'type' => 'char',
                                          'nullable' => true, 'format' => 'uuid'],
                    'request_id'      => ['sql' => 'char(36)', 'type' => 'char',
                                          'nullable' => true, 'format' => 'uuid'],
                    'ip'              => ['sql' => 'varchar(45)', 'type' => 'varchar',
                                          'nullable' => true],
                    'outcome'         => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                          'nullable' => false, 'default' => 'ok'],
                    'metadata'        => ['sql' => 'mediumtext', 'type' => 'mediumtext',
                                          'nullable' => true, 'format' => 'json'],
                ],
            ],
            'idempotency_keys' => [
                'entity' => null, 'envelope' => 'none',
                'columns' => [
                    'operation_id' => ['sql' => 'char(36)', 'type' => 'char',
                                       'nullable' => false, 'format' => 'uuid'],
                    'tenant_id'    => ['sql' => 'char(36)', 'type' => 'char',
                                       'nullable' => false, 'format' => 'uuid',
                                       'ref' => 'tenants.id'],
                    'request_hash' => ['sql' => 'char(64)', 'type' => 'char',
                                       'nullable' => true],
                    'status'       => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                       'nullable' => false, 'default' => 'applied',
                                       'enum' => ['applied', 'conflict', 'rejected']],
                    'response'     => ['sql' => 'mediumtext', 'type' => 'mediumtext',
                                       'nullable' => false],
                    'created_at'   => ['sql' => 'datetime', 'type' => 'datetime',
                                       'nullable' => false, 'format' => 'timestamp'],
                    'expires_at'   => ['sql' => 'datetime', 'type' => 'datetime',
                                       'nullable' => false, 'format' => 'timestamp'],
                ],
            ],
            'change_log' => [
                'entity' => null, 'envelope' => 'none',
                'columns' => [
                    'seq'             => ['sql' => 'bigint', 'type' => 'bigint',
                                          'nullable' => false, 'auto' => true],
                    'tenant_id'       => ['sql' => 'char(36)', 'type' => 'char',
                                          'nullable' => false, 'format' => 'uuid',
                                          'ref' => 'tenants.id'],
                    'entity'          => ['sql' => 'varchar(64)', 'type' => 'varchar',
                                          'nullable' => false],
                    'id'              => ['sql' => 'char(36)', 'type' => 'char',
                                          'nullable' => false, 'format' => 'uuid'],
                    'row_version'     => ['sql' => 'int', 'type' => 'int',
                                          'nullable' => false],
                    'action'          => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                          'nullable' => false, 'default' => 'upsert',
                                          'enum' => ['upsert', 'delete']],
                    'actor_type'      => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                          'nullable' => false, 'default' => 'system',
                                          'enum' => ['system', 'user', 'device']],
                    'actor_user_id'   => ['sql' => 'char(36)', 'type' => 'char',
                                          'nullable' => true, 'format' => 'uuid',
                                          'ref' => 'users.id'],
                    'actor_device_id' => ['sql' => 'char(36)', 'type' => 'char',
                                          'nullable' => true, 'format' => 'uuid',
                                          'ref' => 'sync_devices.id'],
                    'operation_id'    => ['sql' => 'char(36)', 'type' => 'char',
                                          'nullable' => true, 'format' => 'uuid'],
                    'changed_at'      => ['sql' => 'datetime', 'type' => 'datetime',
                                          'nullable' => false, 'format' => 'timestamp'],
                ],
            ],
            'tenant_state' => [
                'entity' => null, 'envelope' => 'none',
                'columns' => [
                    'tenant_id'        => ['sql' => 'char(36)', 'type' => 'char',
                                           'nullable' => false, 'format' => 'uuid',
                                           'ref' => 'tenants.id'],
                    'bootstrap_status' => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                           'nullable' => false, 'default' => 'pending',
                                           'enum' => ['pending', 'running', 'done']],
                    'dataset_epoch'    => ['sql' => 'int', 'type' => 'int',
                                           'nullable' => false, 'default' => 0],
                    'last_import_id'   => ['sql' => 'char(36)', 'type' => 'char',
                                           'nullable' => true, 'format' => 'uuid'],
                    'last_import_hash' => ['sql' => 'char(64)', 'type' => 'char',
                                           'nullable' => true],
                    'source_installation_id' => ['sql' => 'char(36)', 'type' => 'char',
                                           'nullable' => true, 'format' => 'uuid'],
                    'created_at'       => ['sql' => 'datetime', 'type' => 'datetime',
                                           'nullable' => false, 'format' => 'timestamp'],
                    'updated_at'       => ['sql' => 'datetime', 'type' => 'datetime',
                                           'nullable' => false, 'format' => 'timestamp'],
                ],
            ],

            // ---- business (spec §3) -------------------------------------
            'periods' => [
                'entity' => 'periods', 'envelope' => 'legacy',
                'columns' => [
                    'number'        => ['sql' => 'int', 'type' => 'int',
                                        'nullable' => false],
                    'label'         => ['sql' => 'varchar(120)', 'type' => 'varchar',
                                        'nullable' => false, 'default' => ''],
                    'opened_at'     => ['sql' => 'datetime', 'type' => 'datetime',
                                        'nullable' => false, 'format' => 'timestamp'],
                    'opened_by'     => ['sql' => 'varchar(64)', 'type' => 'varchar',
                                        'nullable' => false, 'default' => 'system'],
                    'closed_at'     => ['sql' => 'datetime', 'type' => 'datetime',
                                        'nullable' => true, 'format' => 'timestamp'],
                    'closed_by'     => ['sql' => 'varchar(64)', 'type' => 'varchar',
                                        'nullable' => false, 'default' => ''],
                    'closed_reason' => ['sql' => 'varchar(1000)', 'type' => 'varchar',
                                        'nullable' => false, 'default' => ''],
                ],
            ],
            'settings' => [
                'entity' => 'settings', 'envelope' => 'legacy',
                'columns' => [
                    'business_name'             => ['sql' => 'varchar(80)', 'type' => 'varchar',
                                                    'nullable' => false, 'default' => ''],
                    'business_phone'            => ['sql' => 'varchar(80)', 'type' => 'varchar',
                                                    'nullable' => false, 'default' => ''],
                    'watch_interval_hours'      => ['sql' => 'int', 'type' => 'int',
                                                    'nullable' => false, 'default' => 2],
                    'scheduler_enabled'         => ['sql' => 'tinyint(1)', 'type' => 'tinyint',
                                                    'nullable' => false, 'default' => 1,
                                                    'enum' => [0, 1]],
                    'auto_advance_default'      => ['sql' => 'tinyint(1)', 'type' => 'tinyint',
                                                    'nullable' => false, 'default' => 0,
                                                    'enum' => [0, 1]],
                    'telegram_report_full_data' => ['sql' => 'tinyint(1)', 'type' => 'tinyint',
                                                    'nullable' => false, 'default' => 1,
                                                    'enum' => [0, 1]],
                    'registry_fee_amount'       => ['sql' => 'bigint', 'type' => 'bigint',
                                                    'nullable' => false,
                                                    'default' => 24000000],
                    'registration_chat_id'      => ['sql' => 'varchar(32)', 'type' => 'varchar',
                                                    'nullable' => false, 'default' => ''],
                    'report_chat_id'            => ['sql' => 'varchar(32)', 'type' => 'varchar',
                                                    'nullable' => false, 'default' => ''],
                    'sound_enabled'             => ['sql' => 'tinyint(1)', 'type' => 'tinyint',
                                                    'nullable' => false, 'default' => 1,
                                                    'enum' => [0, 1]],
                    'current_period_id'         => ['sql' => 'char(36)', 'type' => 'char',
                                                    'nullable' => true, 'format' => 'uuid',
                                                    'ref' => 'periods.id'],
                    'links'                     => ['sql' => 'mediumtext', 'type' => 'mediumtext',
                                                    'nullable' => true, 'format' => 'json'],
                ],
            ],
            'customers' => [
                'entity' => 'customers', 'envelope' => 'legacy',
                'columns' => [
                    'full_name'    => ['sql' => 'varchar(80)', 'type' => 'varchar',
                                       'nullable' => false],
                    'address'      => ['sql' => 'varchar(300)', 'type' => 'varchar',
                                       'nullable' => false, 'default' => ''],
                    'mobile'       => ['sql' => 'varchar(11)', 'type' => 'varchar',
                                       'nullable' => false, 'default' => ''],
                    'telegram_id'  => ['sql' => 'varchar(64)', 'type' => 'varchar',
                                       'nullable' => false, 'default' => ''],
                    'instagram_id' => ['sql' => 'varchar(64)', 'type' => 'varchar',
                                       'nullable' => false, 'default' => ''],
                    'notes'        => ['sql' => 'varchar(500)', 'type' => 'varchar',
                                       'nullable' => false, 'default' => ''],
                    'created'      => ['sql' => 'datetime', 'type' => 'datetime',
                                       'nullable' => true, 'format' => 'timestamp'],
                    'updated'      => ['sql' => 'datetime', 'type' => 'datetime',
                                       'nullable' => true, 'format' => 'timestamp'],
                ],
            ],
            'owners' => [
                'entity' => 'owners', 'envelope' => 'legacy',
                'columns' => [
                    'first_name' => ['sql' => 'varchar(120)', 'type' => 'varchar',
                                     'nullable' => false],
                    'last_name'  => ['sql' => 'varchar(120)', 'type' => 'varchar',
                                     'nullable' => false],
                    'national'   => ['sql' => 'varchar(10)', 'type' => 'varchar',
                                     'nullable' => false],
                    'birth'      => ['sql' => 'varchar(10)', 'type' => 'varchar',
                                     'nullable' => false],
                    'mobile'     => ['sql' => 'varchar(11)', 'type' => 'varchar',
                                     'nullable' => false],
                    'card_no'    => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                     'nullable' => false, 'default' => ''],
                    'passport'   => ['sql' => 'varchar(20)', 'type' => 'varchar',
                                     'nullable' => false, 'default' => ''],
                    'created'    => ['sql' => 'datetime', 'type' => 'datetime',
                                     'nullable' => true, 'format' => 'timestamp'],
                    'updated'    => ['sql' => 'datetime', 'type' => 'datetime',
                                     'nullable' => true, 'format' => 'timestamp'],
                ],
            ],
            'identities' => [
                'entity' => 'identities', 'envelope' => 'legacy',
                'columns' => [
                    'label'    => ['sql' => 'varchar(80)', 'type' => 'varchar',
                                   'nullable' => false, 'default' => ''],
                    'national' => ['sql' => 'varchar(10)', 'type' => 'varchar',
                                   'nullable' => false],
                    'birth'    => ['sql' => 'varchar(10)', 'type' => 'varchar',
                                   'nullable' => false],
                    'mobile'   => ['sql' => 'varchar(11)', 'type' => 'varchar',
                                   'nullable' => false],
                    'created'  => ['sql' => 'datetime', 'type' => 'datetime',
                                   'nullable' => true, 'format' => 'timestamp'],
                    'updated'  => ['sql' => 'datetime', 'type' => 'datetime',
                                   'nullable' => true, 'format' => 'timestamp'],
                ],
            ],
            'crm_devices' => [
                'entity' => 'crm_devices', 'envelope' => 'legacy',
                'columns' => [
                    'name'                 => ['sql' => 'varchar(40)', 'type' => 'varchar',
                                               'nullable' => false],
                    'serial1'              => ['sql' => 'varchar(15)', 'type' => 'varchar',
                                               'nullable' => false],
                    'serial2'              => ['sql' => 'varchar(15)', 'type' => 'varchar',
                                               'nullable' => false, 'default' => ''],
                    'customer_id'          => ['sql' => 'char(36)', 'type' => 'char',
                                               'nullable' => true, 'format' => 'uuid',
                                               'ref' => 'customers.id'],
                    'owner_id'             => ['sql' => 'char(36)', 'type' => 'char',
                                               'nullable' => true, 'format' => 'uuid',
                                               'ref' => 'owners.id'],
                    'stage'                => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                               'nullable' => false, 'default' => 'new',
                                               'enum' => ['new', 'inquiry', 'problem',
                                                          'ready_owner', 'handed',
                                                          'to_payment', 'payment',
                                                          'payment_done', 'done',
                                                          'closed']],
                    'auto_advance'         => ['sql' => 'tinyint(1)', 'type' => 'tinyint',
                                               'nullable' => false, 'default' => 0,
                                               'enum' => [0, 1]],
                    'profile_id'           => ['sql' => 'varchar(64)', 'type' => 'varchar',
                                               'nullable' => false, 'default' => ''],
                    'handed_at'            => ['sql' => 'datetime', 'type' => 'datetime',
                                               'nullable' => true, 'format' => 'timestamp'],
                    'registered_at'        => ['sql' => 'datetime', 'type' => 'datetime',
                                               'nullable' => true, 'format' => 'timestamp'],
                    'payment_confirmed_at' => ['sql' => 'datetime', 'type' => 'datetime',
                                               'nullable' => true, 'format' => 'timestamp'],
                    'code_delivered_at'    => ['sql' => 'datetime', 'type' => 'datetime',
                                               'nullable' => true, 'format' => 'timestamp'],
                    'done_at'              => ['sql' => 'datetime', 'type' => 'datetime',
                                               'nullable' => true, 'format' => 'timestamp'],
                    'last_inquiry_at'      => ['sql' => 'datetime', 'type' => 'datetime',
                                               'nullable' => true, 'format' => 'timestamp'],
                    'notes'                => ['sql' => 'varchar(500)', 'type' => 'varchar',
                                               'nullable' => false, 'default' => ''],
                    'advance_attempts'     => ['sql' => 'int', 'type' => 'int',
                                               'nullable' => false, 'default' => 0],
                    'period_id'            => ['sql' => 'char(36)', 'type' => 'char',
                                               'nullable' => true, 'format' => 'uuid',
                                               'ref' => 'periods.id'],
                    'created'              => ['sql' => 'datetime', 'type' => 'datetime',
                                               'nullable' => true, 'format' => 'timestamp'],
                    'updated'              => ['sql' => 'datetime', 'type' => 'datetime',
                                               'nullable' => true, 'format' => 'timestamp'],
                    'skipped_stages'       => ['sql' => 'mediumtext', 'type' => 'mediumtext',
                                               'nullable' => true, 'format' => 'json'],
                    'closed_at'            => ['sql' => 'datetime', 'type' => 'datetime',
                                               'nullable' => true, 'format' => 'timestamp'],
                    'closed_reason'        => ['sql' => 'varchar(1000)', 'type' => 'varchar',
                                               'nullable' => false, 'default' => ''],
                    'periodic_opt_out'     => ['sql' => 'tinyint(1)', 'type' => 'tinyint',
                                               'nullable' => false, 'default' => 0,
                                               'enum' => [0, 1]],
                    'payment_started_at'   => ['sql' => 'datetime', 'type' => 'datetime',
                                               'nullable' => true, 'format' => 'timestamp'],
                ],
            ],
            'customer_transactions' => [
                'entity' => 'customer_transactions', 'envelope' => 'legacy',
                'columns' => [
                    'seq'         => ['sql' => 'int', 'type' => 'int',
                                      'nullable' => false],
                    'customer_id' => ['sql' => 'char(36)', 'type' => 'char',
                                      'nullable' => false, 'format' => 'uuid',
                                      'ref' => 'customers.id'],
                    'at'          => ['sql' => 'datetime', 'type' => 'datetime',
                                      'nullable' => true, 'format' => 'timestamp'],
                    'amount'      => ['sql' => 'bigint', 'type' => 'bigint',
                                      'nullable' => false],
                    'note'        => ['sql' => 'varchar(1000)', 'type' => 'varchar',
                                      'nullable' => false, 'default' => ''],
                    'kind'        => ['sql' => 'varchar(32)', 'type' => 'varchar',
                                      'nullable' => false, 'default' => ''],
                    'image'       => ['sql' => 'varchar(64)', 'type' => 'varchar',
                                      'nullable' => false, 'default' => ''],
                    'device_id'   => ['sql' => 'char(36)', 'type' => 'char',
                                      'nullable' => true, 'format' => 'uuid',
                                      'ref' => 'crm_devices.id'],
                    'voided_at'   => ['sql' => 'datetime', 'type' => 'datetime',
                                      'nullable' => true, 'format' => 'timestamp'],
                    'void_reason' => ['sql' => 'varchar(1000)', 'type' => 'varchar',
                                      'nullable' => false, 'default' => ''],
                ],
            ],

            // ---- Phase B identity foundation (002_identity_foundation) -----
            // Every table below has `entity => null`: accounts, sessions and
            // security events are NOT synchronization entities and must never
            // appear in Schema::entities() (workstream sync/import).
            'accounts' => [
                'entity' => null, 'envelope' => 'none',
                'columns' => [
                    'id'                   => ['sql' => 'char(36)', 'type' => 'char',
                                                'nullable' => false, 'format' => 'uuid'],
                    'login'                => ['sql' => 'varchar(64)', 'type' => 'varchar',
                                                'nullable' => false,
                                                'charset' => 'ascii',
                                                'collation' => 'ascii_bin'],
                    'password_hash'        => ['sql' => 'varchar(255)', 'type' => 'varchar',
                                                'nullable' => false],
                    'display_name'         => ['sql' => 'varchar(120)', 'type' => 'varchar',
                                                'nullable' => false, 'default' => ''],
                    'status'               => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                                'nullable' => false, 'default' => 'active',
                                                'enum' => ['active', 'disabled']],
                    'must_change_password' => ['sql' => 'tinyint(1)', 'type' => 'tinyint',
                                                'nullable' => false, 'default' => 1,
                                                'enum' => [0, 1]],
                    'security_version'     => ['sql' => 'int', 'type' => 'int',
                                                'nullable' => false, 'default' => 1,
                                                'min' => 1],
                    'platform_role'        => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                                'nullable' => false,
                                                'default' => 'member',
                                                'enum' => ['operator', 'member']],
                    'created_at'           => ['sql' => 'datetime', 'type' => 'datetime',
                                                'nullable' => false, 'format' => 'timestamp'],
                    'updated_at'           => ['sql' => 'datetime', 'type' => 'datetime',
                                                'nullable' => false, 'format' => 'timestamp'],
                ],
            ],
            'account_workspace_memberships' => [
                'entity' => null, 'envelope' => 'none',
                'columns' => [
                    'id'         => ['sql' => 'char(36)', 'type' => 'char',
                                     'nullable' => false, 'format' => 'uuid'],
                    'account_id' => ['sql' => 'char(36)', 'type' => 'char',
                                     'nullable' => false, 'format' => 'uuid',
                                     'ref' => 'accounts.id'],
                    'tenant_id'  => ['sql' => 'char(36)', 'type' => 'char',
                                     'nullable' => false, 'format' => 'uuid',
                                     'ref' => 'tenants.id'],
                    'role'       => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                     'nullable' => false, 'enum' => ['admin', 'user']],
                    'status'     => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                     'nullable' => false, 'default' => 'active',
                                     'enum' => ['active', 'removed']],
                    'created_at' => ['sql' => 'datetime', 'type' => 'datetime',
                                     'nullable' => false, 'format' => 'timestamp'],
                    'updated_at' => ['sql' => 'datetime', 'type' => 'datetime',
                                     'nullable' => false, 'format' => 'timestamp'],
                ],
            ],
            'account_sessions' => [
                'entity' => null, 'envelope' => 'none',
                'columns' => [
                    'id'                  => ['sql' => 'char(36)', 'type' => 'char',
                                              'nullable' => false, 'format' => 'uuid'],
                    'account_id'          => ['sql' => 'char(36)', 'type' => 'char',
                                              'nullable' => false, 'format' => 'uuid',
                                              'ref' => 'accounts.id'],
                    'token_hash'          => ['sql' => 'char(64)', 'type' => 'char',
                                              'nullable' => false,
                                              'charset' => 'ascii',
                                              'collation' => 'ascii_bin'],
                    'purpose'             => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                              'nullable' => false,
                                              'enum' => ['desktop', 'admin_web']],
                    'security_version'    => ['sql' => 'int', 'type' => 'int',
                                              'nullable' => false, 'min' => 1],
                    'issued_at'           => ['sql' => 'datetime', 'type' => 'datetime',
                                              'nullable' => false, 'format' => 'timestamp'],
                    'idle_expires_at'     => ['sql' => 'datetime', 'type' => 'datetime',
                                              'nullable' => false, 'format' => 'timestamp'],
                    'absolute_expires_at' => ['sql' => 'datetime', 'type' => 'datetime',
                                              'nullable' => false, 'format' => 'timestamp'],
                    'last_activity_at'    => ['sql' => 'datetime', 'type' => 'datetime',
                                              'nullable' => false, 'format' => 'timestamp'],
                    'revoked_at'          => ['sql' => 'datetime', 'type' => 'datetime',
                                              'nullable' => true, 'format' => 'timestamp'],
                ],
            ],
            'legacy_account_map' => [
                'entity' => null, 'envelope' => 'none',
                'columns' => [
                    'tenant_id'      => ['sql' => 'char(36)', 'type' => 'char',
                                         'nullable' => false, 'format' => 'uuid',
                                         'ref' => 'tenants.id'],
                    'legacy_user_id' => ['sql' => 'char(36)', 'type' => 'char',
                                         'nullable' => false, 'format' => 'uuid',
                                         'ref' => 'users.id'],
                    'account_id'     => ['sql' => 'char(36)', 'type' => 'char',
                                         'nullable' => false, 'format' => 'uuid',
                                         'ref' => 'accounts.id'],
                    'review_id'      => ['sql' => 'char(36)', 'type' => 'char',
                                         'nullable' => true, 'format' => 'uuid'],
                    'confirmed_at'   => ['sql' => 'datetime', 'type' => 'datetime',
                                         'nullable' => true, 'format' => 'timestamp'],
                ],
            ],
            'identity_state' => [
                'entity' => null, 'envelope' => 'none',
                'columns' => [
                    'id'                => ['sql' => 'tinyint(1)', 'type' => 'tinyint',
                                            'nullable' => false, 'enum' => [1]],
                    'first_operator_id' => ['sql' => 'char(36)', 'type' => 'char',
                                            'nullable' => true, 'format' => 'uuid',
                                            'ref' => 'accounts.id'],
                    'row_version'       => ['sql' => 'int', 'type' => 'int',
                                            'nullable' => false, 'default' => 1,
                                            'min' => 1],
                ],
            ],
            'account_security_events' => [
                'entity' => null, 'envelope' => 'none',
                'columns' => [
                    'id'                 => ['sql' => 'char(36)', 'type' => 'char',
                                             'nullable' => false, 'format' => 'uuid'],
                    'actor_account_id'   => ['sql' => 'char(36)', 'type' => 'char',
                                             'nullable' => true, 'format' => 'uuid',
                                             'ref' => 'accounts.id'],
                    'subject_account_id' => ['sql' => 'char(36)', 'type' => 'char',
                                             'nullable' => true, 'format' => 'uuid',
                                             'ref' => 'accounts.id'],
                    'tenant_id'          => ['sql' => 'char(36)', 'type' => 'char',
                                             'nullable' => true, 'format' => 'uuid',
                                             'ref' => 'tenants.id'],
                    'actor_kind'         => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                             'nullable' => false,
                                             'enum' => ['private_cli', 'account', 'system']],
                    'event_type'         => ['sql' => 'varchar(32)', 'type' => 'varchar',
                                             'nullable' => false,
                                             'enum' => self::SECURITY_EVENT_TYPES],
                    'outcome'            => ['sql' => 'varchar(16)', 'type' => 'varchar',
                                             'nullable' => false, 'default' => 'success',
                                             'enum' => ['success']],
                    'created_at'         => ['sql' => 'datetime', 'type' => 'datetime',
                                             'nullable' => false, 'format' => 'timestamp'],
                ],
            ],
        ];

        // Merge the envelope in front of each table's business columns, from
        // the single declaration above, so no table can be given a different
        // envelope by hand.
        $built = [];
        foreach ($tables as $name => $spec) {
            $columns = match ($spec['envelope']) {
                'legacy' => $envelope + $spec['columns'],
                'plain'  => array_intersect_key(
                                $envelope, array_flip(self::ENVELOPE_PLAIN))
                            + $spec['columns'],
                default  => $spec['columns'],
            };
            unset($spec['envelope']);
            $spec['columns'] = $columns;
            $built[$name] = $spec;
        }

        self::$schema = $built;
        return self::$schema;
    }

    // ------------------------------------------------------------------
    // Introspection
    // ------------------------------------------------------------------

    /** @return list<string> every table name, sorted */
    public static function tables(): array
    {
        $names = array_keys(self::schema());
        sort($names);
        return $names;
    }

    public static function isTable(string $table): bool
    {
        return isset(self::schema()[$table]);
    }

    /**
     * Column names of one table, in declaration order.
     *
     * @return list<string>
     */
    public static function columnsFor(string $table): array
    {
        $spec = self::schema()[$table] ?? null;
        if ($spec === null) {
            return [];
        }
        return array_keys($spec['columns']);
    }

    public static function hasColumn(string $table, string $column): bool
    {
        return isset(self::schema()[$table]['columns'][$column]);
    }

    /**
     * Full spec of one column: sql, type, nullable, required, default, enum,
     * format, ref. null when the table or column is unknown.
     *
     * @return array<string, mixed>|null
     */
    public static function column(string $table, string $column): ?array
    {
        $raw = self::schema()[$table]['columns'][$column] ?? null;
        if ($raw === null) {
            return null;
        }
        return self::withRequired($raw, $table);
    }

    /**
     * Full spec of one table: entity (or null) and every column spec.
     *
     * @return array<string, mixed>|null
     */
    public static function table(string $table): ?array
    {
        $spec = self::schema()[$table] ?? null;
        if ($spec === null) {
            return null;
        }
        $columns = [];
        foreach ($spec['columns'] as $name => $raw) {
            $columns[$name] = self::withRequired($raw);
        }
        return ['entity' => $spec['entity'], 'columns' => $columns];
    }

    /**
     * Spec of every table: table => ['entity' => ..., 'columns' => [...]].
     *
     * @return array<string, array<string, mixed>>
     */
    public static function describe(): array
    {
        $out = [];
        foreach (array_keys(self::schema()) as $name) {
            $out[$name] = self::table($name);
        }
        return $out;
    }

    /** Spec §3 entity name -> table name, for the synced collections only. */
    public static function entities(): array
    {
        $out = [];
        foreach (self::schema() as $name => $spec) {
            if ($spec['entity'] !== null) {
                $out[$spec['entity']] = $name;
            }
        }
        return $out;
    }

    public static function tableForEntity(string $entity): ?string
    {
        return self::entities()[$entity] ?? null;
    }

    public static function entityForTable(string $table): ?string
    {
        return self::schema()[$table]['entity'] ?? null;
    }

    /**
     * Business columns of a table — everything that is not the §4 envelope.
     *
     * @return list<string>
     */
    public static function businessColumns(string $table): array
    {
        $envelope = self::envelopeFor($table);
        return array_values(array_diff(self::columnsFor($table), $envelope));
    }

    /** The envelope column names actually present on this table. */
    public static function envelopeFor(string $table): array
    {
        $columns = self::columnsFor($table);
        if ($columns === []) {
            return [];
        }
        // `legacy_id` exists only on the tables that have a legacy source.
        $names = isset(self::schema()[$table]['columns']['legacy_id'])
            ? self::ENVELOPE : self::ENVELOPE_PLAIN;
        return array_values(array_intersect($names, $columns));
    }

    /**
     * Spec §3 settings allowlist — the ONLY settings keys that may cross the
     * wire. The client keeps its own copy in cloud_sync/mapper.py; workstream
     * G owns the cross-language consistency test.
     *
     * @return list<string>
     */
    public static function settingsAllowlist(): array
    {
        return self::businessColumns('settings');
    }

    // ------------------------------------------------------------------
    // Phase B: grouped schema accessors and migration step metadata
    // ------------------------------------------------------------------

    /** The closed set of security lifecycle event names (see the const). */
    public static function securityEventTypes(): array
    {
        return self::SECURITY_EVENT_TYPES;
    }

    /**
     * Declared indexes of one table, in key order.
     *
     * @return list<array{name: string, unique: bool, columns: list<string>}>
     */
    public static function indexesFor(string $table): array
    {
        return self::INDEXES[$table] ?? [];
    }

    /**
     * Declared foreign keys of one table, in declaration order.
     *
     * @return list<array{name: string, columns: list<string>, ref_table: string, ref_columns: list<string>}>
     */
    public static function foreignKeysFor(string $table): array
    {
        return self::FOREIGN_KEYS[$table] ?? [];
    }

    /** Tables a given migration file owns; [] when the version is unknown. */
    public static function tablesForVersion(string $version): array
    {
        return self::TABLES_FOR_VERSION[$version] ?? [];
    }

    /**
     * The six same-tenant composite relationships, in declaration order.
     *
     * @return list<array{constraint: string, table: string, columns: list<string>,
     *   ref_table: string, ref_columns: list<string>, parent_key: string}>
     */
    public static function scopedRelationships(): array
    {
        return self::SCOPED_RELATIONSHIPS;
    }

    /** Normalised, engine-independent description of one declared column. */
    public static function columnFact(string $table, string $column): ?array
    {
        $spec = self::column($table, $column);
        if ($spec === null) {
            return null;
        }
        return [
            'sql'       => (string) $spec['sql'],
            'charset'   => $spec['charset'],
            'collation' => $spec['collation'],
            'nullable'  => (bool) $spec['nullable'],
            'default'   => $spec['default'],
            'auto'      => (bool) $spec['auto'],
        ];
    }

    /**
     * Expected pre/post conditions of every step of a migration file.
     *
     * The migration runner never compares `SHOW CREATE TABLE` strings: those
     * differ between MySQL and MariaDB for an identical logical schema. It
     * compares these declared facts against engine-normalised
     * information_schema metadata instead (host/src/MigrationPreflight.php).
     *
     * Facts are partial on purpose: a step states only what it changes.
     *   tables        table => true (must exist) | null (must be absent)
     *   columns       "table.column" => fact | null
     *   indexes       "table.index"  => {unique, columns, type} | null
     *   foreign_keys  "table.fk"     => {columns, ref_table, ref_columns} | null
     *   rows          table => {mismatch, min_count, max_count} (counts only)
     *
     * @return list<array{step_id: string, mode: string, pre: array, post: array}>
     */
    public static function migrationSteps(string $version): array
    {
        if ($version === '001_initial') {
            $owned = self::tablesForVersion($version);
            $post = self::emptyFacts();
            foreach ($owned as $table) {
                $post = self::mergeFacts($post, self::presentFacts($table));
            }
            // Schema is the CENTRAL declaration and therefore describes the
            // schema AFTER 002 as well. A file's postcondition must only cover
            // what that file creates, so everything a later migration adds is
            // removed here and asserted ABSENT instead. Without this, 001 could
            // never verify on a target that has not run 002 yet.
            foreach (self::LATER_INTRODUCTIONS as $kind => $keys) {
                foreach ($keys as $key) {
                    $post[$kind][$key] = null;
                }
            }
            // 001 is byte-frozen and carries no step markers: it is one
            // forward-only unit step whose postcondition is the whole declared
            // legacy schema. A crash in the middle of it is mixed state and is
            // reported as MIGRATION_STATE_AMBIGUOUS rather than repaired.
            return [[
                'step_id' => 'legacy_file',
                'mode'    => 'file',
                'pre'     => self::absentFacts($owned),
                'post'    => $post,
            ]];
        }
        if ($version === '003_scoped_business_keys') {
            return self::scopedSteps();
        }
        if ($version !== '002_identity_foundation') {
            return [];
        }

        $legacyDefault = static fn (array $fact, mixed $default): array => [
            'sql' => $fact['sql'], 'charset' => $fact['charset'],
            'collation' => $fact['collation'], 'nullable' => $fact['nullable'],
            'default' => $default, 'auto' => $fact['auto'],
        ];
        // The tenants contract columns are added with the LEGACY value as the
        // server default, so existing rows are backfilled on ALTER; only after
        // the bounded backfill step do the defaults become the v2 values.
        $lifecycle  = self::columnFact('tenants', 'lifecycle');
        $contract   = self::columnFact('tenants', 'api_contract');
        $context    = self::columnFact('tenants', 'context_version');
        $replacement = self::columnFact('tenants', 'replacement_tenant_id');

        $steps = [
            ['step_id' => 's01_users_tenant_id_unique', 'mode' => 'statement',
             'pre'  => self::facts(['indexes' => ['users.uq_users_tenant_id' => null]]),
             'post' => self::facts(['indexes' => ['users.uq_users_tenant_id' => [
                 'unique' => true, 'columns' => ['tenant_id', 'id'], 'type' => 'btree']]]),
            ],
            ['step_id' => 's02_tenants_add_lifecycle', 'mode' => 'statement',
             'pre'  => self::facts(['columns' => ['tenants.lifecycle' => null]]),
             'post' => self::facts(['columns' => ['tenants.lifecycle' => $legacyDefault($lifecycle, 'active')]]),
            ],
            ['step_id' => 's03_tenants_add_api_contract', 'mode' => 'statement',
             'pre'  => self::facts(['columns' => ['tenants.api_contract' => null]]),
             'post' => self::facts(['columns' => ['tenants.api_contract' => $legacyDefault($contract, 'legacy_v1')]]),
            ],
            ['step_id' => 's04_tenants_add_context_version', 'mode' => 'statement',
             'pre'  => self::facts(['columns' => ['tenants.context_version' => null]]),
             'post' => self::facts(['columns' => ['tenants.context_version' => $context]]),
            ],
            ['step_id' => 's05_tenants_add_replacement_tenant_id', 'mode' => 'statement',
             'pre'  => self::facts(['columns' => ['tenants.replacement_tenant_id' => null]]),
             'post' => self::facts(['columns' => ['tenants.replacement_tenant_id' => $replacement]]),
            ],
            // Bounded keyset backfill with a persisted cursor: no claim is made
            // that an arbitrarily large tenant table fits one transaction.
            ['step_id' => 's06_tenants_backfill_legacy_contract', 'mode' => 'keyset',
             'pre'  => self::emptyFacts(),
             'post' => self::facts(['rows' => ['tenants' => [
                 'mismatch' => ['lifecycle' => 'active', 'api_contract' => 'legacy_v1',
                                'context_version' => 1],
                 'max' => 0]]]),
            ],
            ['step_id' => 's07_tenants_lifecycle_default', 'mode' => 'statement',
             'pre'  => self::facts(['columns' => ['tenants.lifecycle' => $legacyDefault($lifecycle, 'active')]]),
             'post' => self::facts(['columns' => ['tenants.lifecycle' => $lifecycle]]),
            ],
            ['step_id' => 's08_tenants_api_contract_default', 'mode' => 'statement',
             'pre'  => self::facts(['columns' => ['tenants.api_contract' => $legacyDefault($contract, 'legacy_v1')]]),
             'post' => self::facts(['columns' => ['tenants.api_contract' => $contract]]),
            ],
            ['step_id' => 's09_tenants_replacement_fk', 'mode' => 'statement',
             'pre'  => self::facts(['foreign_keys' => ['tenants.fk_tenants_replacement' => null]]),
             'post' => self::facts(['foreign_keys' => ['tenants.fk_tenants_replacement' => [
                 'columns' => ['replacement_tenant_id'], 'ref_table' => 'tenants',
                 'ref_columns' => ['id']]]]),
            ],
        ];
        foreach (['s10_accounts_create' => 'accounts',
                  's11_account_workspace_memberships_create' => 'account_workspace_memberships',
                  's12_account_sessions_create' => 'account_sessions',
                  's13_identity_state_create' => 'identity_state',
                  's14_legacy_account_map_create' => 'legacy_account_map',
                  's15_account_security_events_create' => 'account_security_events'] as $id => $table) {
            $steps[] = ['step_id' => $id, 'mode' => 'statement',
                        'pre'  => self::absentFacts([$table]),
                        'post' => self::presentFacts($table)];
        }
        $steps[] = ['step_id' => 's16_identity_state_seed', 'mode' => 'statement',
                    'pre'  => self::emptyFacts(),
                    'post' => self::facts(['rows' => ['identity_state' => ['min_count' => 1]]])];
        return $steps;
    }

    /**
     * Step ledger for 003_scoped_business_keys.
     *
     * The four unique parent keys come first and the six composite foreign
     * keys follow, each as its own step, in exactly the order the file marks
     * them. That order is load-bearing twice over: InnoDB refuses a composite
     * foreign key whose referenced columns carry no unique index, and a crash
     * between two steps must leave a state the journal can describe (some
     * parent keys installed, some constraints not) rather than an opaque
     * half-applied block.
     *
     * Facts are written against the CENTRAL declaration, so the same
     * information_schema comparison the other files use applies unchanged.
     *
     * @return list<array{step_id: string, mode: string, pre: array, post: array}>
     */
    private static function scopedSteps(): array
    {
        $steps = [];
        foreach (self::SCOPED_PARENT_KEYS as $key) {
            $fact = $key['table'] . '.' . $key['index'];
            $steps[] = ['step_id' => $key['step_id'], 'mode' => 'statement',
                'pre'  => self::facts(['indexes' => [$fact => null]]),
                'post' => self::facts(['indexes' => [$fact => [
                    'unique' => true, 'columns' => ['tenant_id', 'id'], 'type' => 'btree']]])];
        }
        foreach (self::SCOPED_RELATIONSHIPS as $relation) {
            $fact = $relation['table'] . '.' . $relation['constraint'];
            $steps[] = ['step_id' => $relation['step_id'], 'mode' => 'statement',
                'pre'  => self::facts(['foreign_keys' => [$fact => null]]),
                'post' => self::facts(['foreign_keys' => [$fact => [
                    'columns' => $relation['columns'], 'ref_table' => $relation['ref_table'],
                    'ref_columns' => $relation['ref_columns']]]])];
        }
        return $steps;
    }

    // ------------------------------------------------------------------
    // Phase B fact builders (used by migrationSteps)
    // ------------------------------------------------------------------

    private static function emptyFacts(): array
    {
        return ['tables' => [], 'columns' => [], 'indexes' => [],
                'foreign_keys' => [], 'rows' => []];
    }

    /**
     * @param array<string, array<string, mixed>> $partial
     * @return array<string, array<string, mixed>>
     */
    private static function facts(array $partial): array
    {
        return self::mergeFacts(self::emptyFacts(), $partial);
    }

    /**
     * @param array<string, array<string, mixed>> $base
     * @param array<string, array<string, mixed>> $extra
     * @return array<string, array<string, mixed>>
     */
    private static function mergeFacts(array $base, array $extra): array
    {
        foreach (['tables', 'columns', 'indexes', 'foreign_keys', 'rows'] as $kind) {
            foreach ($extra[$kind] ?? [] as $key => $value) {
                $base[$kind][$key] = $value;
            }
        }
        return $base;
    }

    /** Every declared object of one table, expected to be present. */
    private static function presentFacts(string $table): array
    {
        $facts = self::facts(['tables' => [$table => true]]);
        foreach (self::columnsFor($table) as $column) {
            $facts['columns'][$table . '.' . $column] = self::columnFact($table, $column);
        }
        foreach (self::indexesFor($table) as $index) {
            $facts['indexes'][$table . '.' . $index['name']] = [
                'unique' => $index['unique'], 'columns' => $index['columns'],
                'type' => 'btree'];
        }
        foreach (self::foreignKeysFor($table) as $fk) {
            $facts['foreign_keys'][$table . '.' . $fk['name']] = [
                'columns' => $fk['columns'], 'ref_table' => $fk['ref_table'],
                'ref_columns' => $fk['ref_columns']];
        }
        return $facts;
    }

    /**
     * @param list<string> $tables
     * @return array<string, array<string, mixed>>
     */
    private static function absentFacts(array $tables): array
    {
        return self::facts(['tables' => array_fill_keys($tables, null)]);
    }

    // ------------------------------------------------------------------
    // Timestamp conversion (SQL DATETIME <-> spec §4 wire form)
    // ------------------------------------------------------------------

    /**
     * Convert a wire timestamp to the text a DATETIME column accepts.
     *
     * A SQL DATETIME carries no offset, so the ISO-8601 value of spec §4 has
     * to be normalised to UTC before it can be bound. A value that already has
     * no offset is passed through untouched: the connection time zone is
     * '+00:00', so it is already UTC (Database::fromConfig sets it).
     *
     * Returns null for null — a NULL timestamp stays NULL.
     */
    public static function toSqlTimestamp(?string $value): ?string
    {
        if ($value === null) {
            return null;
        }
        if (preg_match('/(Z|z|[+-]\d{2}:?\d{2})$/', $value) !== 1) {
            return $value;
        }
        try {
            $moment = new \DateTimeImmutable($value);
        } catch (\Exception $e) {
            // Let the database raise its own error rather than guessing.
            return $value;
        }
        return $moment->setTimezone(new \DateTimeZone('UTC'))
            ->format('Y-m-d H:i:s');
    }

    /**
     * Convert a DATETIME read back from the database into the wire form of
     * spec §4: UTC ISO-8601 with an explicit offset. The client rejects a
     * naive timestamp, so a raw `2026-10-05 01:23:45` must not be sent as-is.
     */
    public static function toWireTimestamp(?string $sqlDateTime): ?string
    {
        if ($sqlDateTime === null || $sqlDateTime === '') {
            return null;
        }
        try {
            $moment = new \DateTimeImmutable($sqlDateTime,
                new \DateTimeZone('UTC'));
        } catch (\Exception $e) {
            return null;
        }
        return $moment->format('Y-m-d\TH:i:s+00:00');
    }

    // ------------------------------------------------------------------
    // Validation
    // ------------------------------------------------------------------

    /**
     * Validate one incoming business record against a table's schema.
     *
     * "Record" is what travels on the wire (spec §6.1): the business columns
     * plus `id`. The client mints every id — including `settings` and
     * `customer_transactions`, as a uuid5 of (installation, entity, legacy id)
     * — so a push without one is rejected here rather than silently receiving
     * a server-minted identity. The rest of the envelope (tenant_id, the
     * server timestamps, the tombstone) is minted by the server and is NOT
     * demanded here; envelope keys that ARE supplied are still type-checked.
     *
     * Returns a list of failures, each `['code' => ..., 'table' => ...,
     * 'column' => ...]`, empty when the record is acceptable. Never includes
     * the rejected value: a national code or a phone number must not leave the
     * validator (spec §10.9).
     *
     * Rules encoded here, derived from the code that writes the store:
     *   * unknown column -> UNKNOWN_COLUMN (this is what drops a settings key
     *     that is not on the allowlist, and what stops a client inventing one)
     *   * NOT NULL without a default and absent -> MISSING_COLUMN
     *   * a reference column given as "" -> EMPTY_REFERENCE; SQL NULL is the
     *     only way to say "no customer / no owner / no period"
     *   * datetime must carry an explicit UTC offset -> INVALID_TIMESTAMP
     *
     * @param array<string, mixed> $record
     * @return list<array{code: string, table: string, column: string}>
     */
    public static function validate(string $table, array $record): array
    {
        return self::validateInternal($table, $record, false);
    }

    /**
     * Validate a complete row as it will be written, envelope included.
     *
     * Use this immediately before INSERT/UPDATE: it additionally requires
     * id, tenant_id and the server timestamps, so a row can never reach the
     * database missing a column that has no default.
     *
     * @param array<string, mixed> $row
     * @return list<array{code: string, table: string, column: string}>
     */
    public static function validateRow(string $table, array $row): array
    {
        return self::validateInternal($table, $row, true);
    }

    /**
     * Validate a record addressed by spec §3 entity name rather than table.
     *
     * @param array<string, mixed> $record
     * @return list<array{code: string, table: string, column: string}>
     */
    public static function validateEntity(string $entity, array $record): array
    {
        $table = self::tableForEntity($entity);
        if ($table === null) {
            return [self::failure(self::E_UNKNOWN_ENTITY, $entity, '')];
        }
        return self::validate($table, $record);
    }

    /**
     * @param array<string, mixed> $record
     * @return list<array{code: string, table: string, column: string}>
     */
    private static function validateInternal(string $table, array $record,
                                             bool $row): array
    {
        $spec = self::schema()[$table] ?? null;
        if ($spec === null) {
            return [self::failure(self::E_UNKNOWN_TABLE, $table, '')];
        }

        $envelope = self::envelopeFor($table);
        $failures = [];
        foreach ($record as $column => $value) {
            if (!isset($spec['columns'][$column])) {
                $failures[] = self::failure(self::E_UNKNOWN_COLUMN, $table,
                                            (string) $column);
            }
        }

        foreach ($spec['columns'] as $column => $raw) {
            $check = self::withRequired($raw);
            $isEnvelope = in_array($column, $envelope, true);
            if ($isEnvelope && !$row && $column !== 'id') {
                // The server mints the rest of the envelope; only check what
                // arrived. `id` is the exception: the client mints it, so a
                // record without one must never pass.
                if (!array_key_exists($column, $record)) {
                    continue;
                }
            }
            $present = array_key_exists($column, $record);

            if (!$present || $record[$column] === null) {
                if (!empty($check['required'])) {
                    $failures[] = self::failure(self::E_MISSING_COLUMN, $table,
                                                $column);
                }
                continue;
            }

            $failures = array_merge(
                $failures,
                self::validateValue($table, $column, $check, $record[$column])
            );
        }

        return $failures;
    }

    /**
     * @param array<string, mixed> $check
     * @return list<array{code: string, table: string, column: string}>
     */
    private static function validateValue(string $table, string $column,
                                          array $check, mixed $value): array
    {
        $type = (string) $check['type'];

        if ($check['ref'] !== null && is_string($value) && $value === '') {
            // An empty string cannot satisfy a foreign key. Say so precisely
            // instead of letting SQL raise 1452 with no column context.
            return [self::failure(self::E_EMPTY_REFERENCE, $table, $column)];
        }

        switch ($type) {
            case 'tinyint':
                if (is_bool($value)) {
                    $value = $value ? 1 : 0;
                }
                if (!is_int($value)) {
                    return [self::failure(self::E_TYPE_MISMATCH, $table, $column)];
                }
                if ($check['enum'] !== null && !in_array($value, $check['enum'], true)) {
                    return [self::failure(self::E_ENUM_MISMATCH, $table, $column)];
                }
                return [];

            case 'int':
            case 'bigint':
                if (is_bool($value) || !is_int($value)) {
                    return [self::failure(self::E_TYPE_MISMATCH, $table, $column)];
                }
                if ($check['enum'] !== null && !in_array($value, $check['enum'], true)) {
                    return [self::failure(self::E_ENUM_MISMATCH, $table, $column)];
                }
                // Declared domain, independent of any SQL CHECK. MySQL 5.7
                // parses CHECK and ignores it, so a version counter of 0 would
                // otherwise validate clean on the documented support floor.
                if (($check['min'] !== null && $value < $check['min'])
                    || ($check['max'] !== null && $value > $check['max'])) {
                    return [self::failure(self::E_OUT_OF_RANGE, $table, $column)];
                }
                return [];

            case 'datetime':
                if (!is_string($value) || !self::isUtcTimestamp($value)) {
                    return [self::failure(self::E_INVALID_TIMESTAMP, $table, $column)];
                }
                return [];

            case 'char':
            case 'varchar':
            case 'text':
            case 'mediumtext':
                if (!is_string($value)) {
                    return [self::failure(self::E_TYPE_MISMATCH, $table, $column)];
                }
                if ($check['enum'] !== null
                    && !in_array($value, $check['enum'], true)) {
                    return [self::failure(self::E_ENUM_MISMATCH, $table, $column)];
                }
                if ($check['format'] === 'uuid' && !self::isUuid($value)) {
                    return [self::failure(self::E_INVALID_UUID, $table, $column)];
                }
                if ($check['format'] === 'legacy_id'
                    && (strlen($value) === 0 || strlen($value) > 64)) {
                    return [self::failure(self::E_INVALID_LEGACY_ID, $table, $column)];
                }
                if ($check['format'] === 'json' && !self::isJson($value)) {
                    return [self::failure(self::E_INVALID_JSON, $table, $column)];
                }
                $limit = self::lengthLimit((string) $check['sql']);
                if ($limit !== null && mb_strlen($value, 'UTF-8') > $limit) {
                    return [self::failure(self::E_TOO_LONG, $table, $column)];
                }
                return [];

            default:
                return [self::failure(self::E_TYPE_MISMATCH, $table, $column)];
        }
    }

    /** ISO-8601 with an explicit offset (or Z), e.g. 2026-10-05T01:23:45+00:00. */
    private static function isUtcTimestamp(string $value): bool
    {
        if (preg_match(
            '/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?'
            . '(Z|[z]|[+-]\d{2}:?\d{2})$/',
            $value
        ) !== 1) {
            return false;
        }
        $normalized = ($value[-1] === 'Z' || $value[-1] === 'z')
            ? substr($value, 0, -1) . '+00:00' : $value;
        try {
            new \DateTimeImmutable($normalized);
        } catch (\Exception $e) {
            return false;
        }
        return true;
    }

    private static function isUuid(string $value): bool
    {
        return preg_match(
            '/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
            . '[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/',
            $value
        ) === 1;
    }

    private static function isJson(string $value): bool
    {
        json_decode($value, true, 32, JSON_BIGINT_AS_STRING);
        return json_last_error() === JSON_ERROR_NONE;
    }

    /** Character capacity of a char(n)/varchar(n) declaration, else null. */
    private static function lengthLimit(string $sql): ?int
    {
        if (preg_match('/^(?:char|varchar)\((\d+)\)$/', $sql, $m) === 1) {
            return (int) $m[1];
        }
        return null;
    }

    /**
     * @param array<string, mixed> $raw
     * @param string $table owning table, so the character set can be resolved
     * @return array<string, mixed>
     */
    private static function withRequired(array $raw, string $table = ''): array
    {
        $raw += [
            'type' => 'varchar', 'nullable' => false, 'default' => null,
            'enum' => null, 'format' => null, 'ref' => null, 'auto' => false,
            'charset' => null, 'collation' => null, 'min' => null, 'max' => null,
        ];
        if ($table !== '') {
            // A character-type column inherits the table's character set unless
            // the declaration overrides it (accounts.login is ascii_bin, and
            // must stay ascii so a byte comparison is possible). Numeric and
            // temporal columns carry neither, so they resolve to null rather
            // than to a collation they cannot have.
            $textual = self::carriesCharset((string) $raw['sql'], (string) $raw['type']);
            if ($textual) {
                $raw['charset'] ??= self::TABLE_CHARSET['charset'];
                $raw['collation'] ??= self::TABLE_CHARSET['collation'];
            } else {
                $raw['charset'] = null;
                $raw['collation'] = null;
            }
        }
        // Required <=> NOT NULL with no default and not server-generated:
        // derived so it cannot drift from the SQL it describes. An
        // AUTO_INCREMENT key (change_log.seq, login_attempts.id) is filled in
        // by the server, so demanding it from an incoming record would be a
        // false rejection.
        $raw['required'] = !$raw['nullable']
            && $raw['default'] === null
            && !$raw['auto'];
        return $raw;
    }

    /** True when the column type participates in a character set/collation. */
    private static function carriesCharset(string $sql, string $type): bool
    {
        if (preg_match('/^(char|varchar|tinytext|text|mediumtext|longtext|enum|set)\b/',
                strtolower($sql)) === 1) {
            return true;
        }
        return in_array($type, ['char', 'varchar', 'text', 'mediumtext'], true);
    }

    /**
     * @return array{code: string, table: string, column: string}
     */
    private static function failure(string $code, string $table,
                                    string $column): array
    {
        return ['code' => $code, 'table' => $table, 'column' => $column];
    }
}
