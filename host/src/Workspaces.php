<?php
/**
 * Workspaces.php — Phase B workspace lifecycle, membership and contract locks.
 *
 * Contract: the Phase B identity design. One row of `tenants` is one
 * workspace; an account reaches a workspace only through an ACTIVE row of
 * `account_workspace_memberships`. `accounts.platform_role` is a PLATFORM
 * property and grants nothing inside an ordinary workspace.
 *
 * What this module guarantees
 *
 *   * Every decision that gates a workspace is taken INSIDE the caller's
 *     transaction, on a tenant row locked with `SELECT … FOR UPDATE`. A read
 *     that happens before that lock cannot be trusted: a freeze or a contract
 *     change could commit between the read and the write.
 *   * The expected `context_version` is validated as a positive integer and
 *     compared only AFTER the lock, so a stale caller cannot act on a
 *     workspace whose lifecycle or contract has moved on.
 *   * Every refusal that must not become an oracle — unknown tenant, staging
 *     lifecycle, suspended/frozen status, a non-authoritative contract, a
 *     removed or absent membership, an unknown role — answers with ONE code
 *     (NOT_FOUND) and ONE message.
 *   * Capabilities fail closed in both directions: an unknown capability and
 *     an unknown role are never granted anything, and a known role that is
 *     simply not entitled answers ROLE_FORBIDDEN.
 *   * The ACTING ACCOUNT is part of the decision. `lockAuthorizedWithin()`
 *     locks `accounts.status` first (account → tenant → membership) and
 *     refuses a non-active account with the same generic denial, so a
 *     disabled account never keeps workspace authority through a stale
 *     membership row.
 *   * The LAST active admin of a workspace cannot be removed
 *     (`LAST_WORKSPACE_ADMIN`): a workspace left with no admin can never be
 *     administered again, and `freeze()` itself requires one.
 *   * `lockLegacyWithin()` is the single gate that keeps /v1 traffic out of a
 *     workspace that is not `active` + `lifecycle = active` +
 *     `api_contract = legacy_v1`. Sync and Import call it inside their own
 *     mutation transactions, BEFORE the tenant_state and business locks.
 *   * `listWithin()` pages with a `LIMIT limit+1` query ordered by tenant id.
 *     It never materialises a whole membership set to slice it in PHP.
 *
 * Lock order used by this module, and the order C/D must keep:
 *   account → tenant → membership.
 * `freeze()` starts at the tenant because it never writes a membership.
 *
 * This phase deliberately exposes NO activate, archive or contract-switch
 * endpoint. `freeze()` is the only state transition reachable from here, and
 * it exists so a workspace can be taken out of service safely. Cutover work
 * belongs to the later phases and must go through these same helpers.
 */

declare(strict_types=1);

final class Workspaces
{
    // ------------------------------------------------------------------
    // The closed vocabulary. Nothing outside these sets is ever granted.
    // ------------------------------------------------------------------

    /** Both roles may read and edit. */
    public const MEMBER_CAPABILITIES = ['read' => true, 'edit' => true];

    /** Only an admin may delete, write settings or administer periods. */
    public const ADMIN_CAPABILITIES = [
        'delete' => true, 'settings' => true, 'period_admin' => true,
    ];

    public const CONTRACT_V2 = 'authoritative_v2';
    public const CONTRACT_LEGACY = 'legacy_v1';
    public const LIFECYCLE_ACTIVE = 'active';
    public const STATUS_ACTIVE = 'active';
    /** An account keeps no authority while its own status is not active. */
    public const ACCOUNT_ACTIVE = 'active';
    /** A frozen workspace: out of service, absent from every selectable list. */
    public const STATUS_FROZEN = 'suspended';
    public const MEMBERSHIP_ACTIVE = 'active';
    public const MEMBERSHIP_REMOVED = 'removed';

    /**
     * The one tenant-row query this module is built on. `FOR UPDATE` is the
     * whole point: the state it returns is the state the write that follows
     * is authorised against.
     */
    private const TENANT_ROW =
        'SELECT id, status, lifecycle, api_contract, context_version'
        . ' FROM tenants WHERE id = ?';

    /** One refusal for every condition that must not become an oracle. */
    private const DENIED_MESSAGE = 'no workspace is available for this request';

    public function __construct(
        private readonly Database $db
    ) {
    }

    // ------------------------------------------------------------------
    // Capability map
    // ------------------------------------------------------------------

    public static function isKnownCapability(string $capability): bool
    {
        return isset(self::MEMBER_CAPABILITIES[$capability])
            || isset(self::ADMIN_CAPABILITIES[$capability]);
    }

    /**
     * May `$role` exercise `$capability`?
     *
     * An unknown role or an unknown capability returns false: the map is an
     * allowlist, and a missing entry denies rather than defaults.
     */
    public static function allows(string $role, string $capability): bool
    {
        if (!in_array($role, Roles::ALL, true)) {
            return false;
        }
        if (isset(self::ADMIN_CAPABILITIES[$capability])) {
            return $role === Roles::ADMIN;
        }
        if (isset(self::MEMBER_CAPABILITIES[$capability])) {
            return true;
        }
        return false;
    }

    private static function denied(): ApiException
    {
        return new ApiException(404, Codes::NOT_FOUND, self::DENIED_MESSAGE);
    }

    private static function roleForbidden(): ApiException
    {
        return new ApiException(403, Codes::ROLE_FORBIDDEN,
            'the acting role may not perform this workspace capability');
    }

    /**
     * A workspace left with no active admin can never be administered again —
     * assign, freeze and settings all require one — so the last one is
     * protected exactly as the platform protects its last operator.
     */
    private static function lastWorkspaceAdmin(): ApiException
    {
        return new ApiException(409, Codes::LAST_WORKSPACE_ADMIN,
            'the last active admin of a workspace cannot be removed');
    }

    private static function invalid(string $message): ApiException
    {
        return new ApiException(400, Codes::VALIDATION_FAILED, $message);
    }

    private static function stale(int $expected, int $actual): ApiException
    {
        return new ApiException(409, Codes::WORKSPACE_CONTEXT_STALE,
            'the workspace context has moved on since version ' . $actual
            . '; this caller still holds ' . $expected);
    }

    // ------------------------------------------------------------------
    // Lock-held lookup
    // ------------------------------------------------------------------

    private static function assertContextVersion(int $expected): void
    {
        if ($expected < 1) {
            throw self::invalid(
                'the expected context version must be a positive integer');
        }
    }

    /**
     * An identifier that is not a UUID is a malformed request, not a missing
     * workspace: it is refused as such, and only a WELL-FORMED id is allowed
     * to become a scoped denial.
     */
    private static function assertUuid(string $value, string $what): void
    {
        if (!Ids::isUuid($value)) {
            throw self::invalid('the ' . $what . ' must be identified by a UUID');
        }
    }

    /**
     * The tenant row, under `FOR UPDATE` when the caller is writing.
     *
     * A tenant id that is not a well-formed UUID answers exactly like a
     * tenant that does not exist.
     */
    private function lockTenantRow(Database $tx, string $tenantId,
                                   bool $write): ?array
    {
        if (!Ids::isUuid($tenantId)) {
            return null;
        }
        $row = $tx->queryOne(self::TENANT_ROW . ($write ? ' FOR UPDATE' : ''),
            [$tenantId]);
        return is_array($row) ? $row : null;
    }

    /** The ACTIVE membership role of one account in one tenant, or null. */
    private function membershipRole(Database $tx, string $accountId,
                                    string $tenantId): ?string
    {
        if (!Ids::isUuid($accountId)) {
            return null;
        }
        $role = $tx->queryValue(
            'SELECT role FROM account_workspace_memberships'
            . " WHERE account_id = ? AND tenant_id = ? AND status = 'active'",
            [$accountId, $tenantId]
        );
        return is_string($role) && in_array($role, Roles::ALL, true)
            ? $role : null;
    }

    /** A selectable workspace: in service, live, and owned by /v2. */
    private static function isAuthoritativeWorkspace(array $tenant): bool
    {
        return ($tenant['status'] ?? '') === self::STATUS_ACTIVE
            && ($tenant['lifecycle'] ?? '') === self::LIFECYCLE_ACTIVE
            && ($tenant['api_contract'] ?? '') === self::CONTRACT_V2;
    }

    /** Compare the caller's expected version against the LOCKED row. */
    private static function assertContext(array $tenant, int $expected): void
    {
        $actual = (int) $tenant['context_version'];
        if ($actual !== $expected) {
            throw self::stale($expected, $actual);
        }
    }

    /**
     * Bump `context_version`. Any change to the workspace the client is
     * holding — lifecycle, contract, or a freeze — makes its cached context
     * wrong, and the next call with the old version must fail rather than
     * silently operate on a different workspace.
     */
    private function bumpContext(Database $tx, string $tenantId): int
    {
        $tx->execute(
            'UPDATE tenants SET context_version = context_version + 1,'
            . ' updated_at = ? WHERE id = ?',
            [gmdate('Y-m-d H:i:s'), $tenantId]
        );
        return (int) $tx->queryValue(
            'SELECT context_version FROM tenants WHERE id = ?', [$tenantId]);
    }

    /**
     * Lock the workspace and prove this account may perform `$capability`.
     *
     * Call this as the FIRST statement of the caller's transaction, before
     * any tenant_state or business lock. The acting ACCOUNT is locked first
     * and must be active; the tenant is then locked, and the membership is
     * read last.
     *
     * @return array{tenant_id: string, slug: string, name: string,
     *   status: string, lifecycle: string, api_contract: string,
     *   context_version: int, role: string}
     * @throws ApiException NOT_FOUND | WORKSPACE_CONTEXT_STALE |
     *                      ROLE_FORBIDDEN | VALIDATION_FAILED
     */
    public function lockAuthorizedWithin(Database $tx, string $accountId,
                                         string $tenantId,
                                         int $expectedContextVersion,
                                         string $capability): array
    {
        self::assertContextVersion($expectedContextVersion);
        self::assertUuid($accountId, 'account');
        self::assertUuid($tenantId, 'workspace');
        if (!self::isKnownCapability($capability)) {
            throw self::invalid('the requested capability is not part of the'
                . ' workspace contract');
        }
        // Lock order: account, then tenant, then membership. The acting
        // account is part of the authorization decision, so its row is locked
        // and checked here — a disable that committed after an unlocked read
        // would otherwise leave the account with workspace authority that no
        // layer has rechecked.
        $account = $tx->queryOne(
            'SELECT id, status FROM accounts WHERE id = ? FOR UPDATE',
            [$accountId]
        );
        if ($account === null
            || ($account['status'] ?? '') !== self::ACCOUNT_ACTIVE) {
            throw self::denied();
        }
        $tenant = $this->lockTenantRow($tx, $tenantId, true);
        if ($tenant === null) {
            throw self::denied();
        }
        self::assertContext($tenant, $expectedContextVersion);
        if (!self::isAuthoritativeWorkspace($tenant)) {
            throw self::denied();
        }
        $role = $this->membershipRole($tx, $accountId, $tenantId);
        if ($role === null) {
            throw self::denied();
        }
        if (!self::allows($role, $capability)) {
            throw self::roleForbidden();
        }
        return $this->describe($tx, $tenant, $role);
    }

    /**
     * The /v1 gate.
     *
     * A workspace answers v1 only while it is in service, live and still on
     * the legacy contract. Anything else — staging, frozen, or already
     * authoritative_v2 — is indistinguishable from a workspace that does not
     * exist, so a v1 client cannot enumerate what has moved on.
     *
     * @return array{id: string, status: string, lifecycle: string,
     *   api_contract: string, context_version: int}
     * @throws ApiException NOT_FOUND
     */
    public function lockLegacyWithin(Database $tx, string $tenantId,
                                     bool $write): array
    {
        self::assertUuid($tenantId, 'workspace');
        $tenant = $this->lockTenantRow($tx, $tenantId, $write);
        if ($tenant === null
            || ($tenant['status'] ?? '') !== self::STATUS_ACTIVE
            || ($tenant['lifecycle'] ?? '') !== self::LIFECYCLE_ACTIVE
            || ($tenant['api_contract'] ?? '') !== self::CONTRACT_LEGACY) {
            throw self::denied();
        }
        return [
            'id' => (string) $tenant['id'],
            'status' => (string) $tenant['status'],
            'lifecycle' => (string) $tenant['lifecycle'],
            'api_contract' => (string) $tenant['api_contract'],
            'context_version' => (int) $tenant['context_version'],
        ];
    }

    /**
     * The locked row plus the caller's role, in the shape callers return.
     *
     * The gate fields come from the LOCKED row and are never re-read: a
     * consistent read inside the same transaction can serve an older snapshot,
     * and `status` / `lifecycle` / `api_contract` / `context_version` are
     * exactly what the lock exists to pin. Only the display columns are
     * fetched separately.
     */
    private function describe(Database $tx, array $tenant, string $role): array
    {
        $extra = $tx->queryOne('SELECT slug, name FROM tenants WHERE id = ?',
            [(string) $tenant['id']]);
        return [
            'tenant_id' => (string) $tenant['id'],
            'slug' => (string) ($extra['slug'] ?? ''),
            'name' => (string) ($extra['name'] ?? ''),
            'status' => (string) $tenant['status'],
            'lifecycle' => (string) $tenant['lifecycle'],
            'api_contract' => (string) $tenant['api_contract'],
            'context_version' => (int) $tenant['context_version'],
            'role' => $role,
        ];
    }

    // ------------------------------------------------------------------
    // Listing
    // ------------------------------------------------------------------

    /**
     * The workspaces one account may select, paged.
     *
     * The cursor is an opaque tenant id the caller echoes back; it is
     * VALIDATED here and never treated as authority — the membership join is
     * what decides what may be returned. The query asks for `limit + 1` rows
     * in tenant-id order, so the extra row only ever proves that another page
     * exists; the whole membership set is never loaded.
     *
     * @return array{items: list<array<string, mixed>>, next_id: ?string}
     * @throws ApiException VALIDATION_FAILED
     */
    public function listWithin(string $accountId, ?string $lastTenantId,
                               int $limit): array
    {
        self::assertUuid($accountId, 'account');
        if ($limit < 1 || $limit > 100) {
            throw self::invalid('the page size must be between 1 and 100');
        }
        if ($lastTenantId !== null) {
            self::assertUuid($lastTenantId, 'page cursor');
        }

        $sql = 'SELECT t.id AS id, t.slug, t.name, t.context_version, m.role'
            . ' FROM account_workspace_memberships m'
            . ' INNER JOIN tenants t ON t.id = m.tenant_id'
            . ' WHERE m.account_id = ? AND m.status = ' . "'" . self::MEMBERSHIP_ACTIVE . "'"
            . ' AND t.status = ' . "'" . self::STATUS_ACTIVE . "'"
            . ' AND t.lifecycle = ' . "'" . self::LIFECYCLE_ACTIVE . "'"
            . ' AND t.api_contract = ' . "'" . self::CONTRACT_V2 . "'"
            . ($lastTenantId === null ? '' : ' AND t.id > ?')
            . ' ORDER BY t.id ASC LIMIT ' . ($limit + 1);

        $params = [$accountId];
        if ($lastTenantId !== null) {
            $params[] = $lastTenantId;
        }
        $rows = $this->db->query($sql, $params);

        $hasMore = count($rows) > $limit;
        $page = $hasMore ? array_slice($rows, 0, $limit) : $rows;
        $items = [];
        foreach ($page as $row) {
            $items[] = [
                'id' => (string) $row['id'],
                'slug' => (string) $row['slug'],
                'name' => (string) $row['name'],
                'context_version' => (int) $row['context_version'],
                'role' => (string) $row['role'],
            ];
        }
        return [
            'items' => $items,
            // `next_id` is the LAST tenant id of this page — the value the
            // caller feeds back as `last_tenant_id`. The extra row the query
            // peeked at is never returned; it exists only so this page can say
            // honestly whether another one follows.
            'next_id' => $hasMore && $items !== []
                ? (string) $items[count($items) - 1]['id'] : null,
        ];
    }

    // ------------------------------------------------------------------
    // Lifecycle and membership
    // ------------------------------------------------------------------

    /**
     * A new PRIVATE workspace for one account, created inside the CALLER's
     * transaction.
     *
     * This is the primitive a transactional account-creation path needs: the
     * account row, the workspace and the admin membership all become visible
     * — or all disappear — together, because this method opens no transaction
     * of its own. `Database::transaction()` does not nest and has no
     * savepoints, so a caller that already holds `$tx` must come here rather
     * than through `createPrivate()`.
     *
     * Random UUID, non-personal slug, an explicit active/authoritative_v2
     * state (never a column default this phase cannot see) and an admin
     * membership for its owning account. The account's PLATFORM role is not
     * touched: creating a workspace does not make anyone an operator.
     *
     * @return array<string, mixed>
     * @throws ApiException NOT_FOUND
     */
    public function createPrivateWithin(Database $tx, string $accountId,
                                        ?string $name = null): array
    {
        self::assertUuid($accountId, 'account');
        $tenantId = Ids::v4();
        // Derived from the random tenant id, never from a login, a display
        // name or anything a person chose.
        $slug = 'ws-' . str_replace('-', '', $tenantId);
        // The label is bounded in CHARACTERS, which is what `varchar(120)`
        // under utf8mb4 holds. A byte truncation would cut a multibyte
        // character in half and store invalid UTF-8 — or fail the insert under
        // strict mode instead of creating the workspace.
        $label = is_string($name) && trim($name) !== ''
            ? mb_substr(trim($name), 0, 120, 'UTF-8') : 'Private workspace';

        // Lock order: account, then tenant, then membership.
        $account = $tx->queryOne(
            'SELECT id, status FROM accounts WHERE id = ? FOR UPDATE',
            [$accountId]
        );
        if ($account === null
            || ($account['status'] ?? '') !== self::ACCOUNT_ACTIVE) {
            throw self::denied();
        }
        $now = gmdate('Y-m-d H:i:s');
        $tx->insert('tenants', [
            'id' => $tenantId,
            'slug' => $slug,
            'name' => $label,
            'status' => self::STATUS_ACTIVE,
            'lifecycle' => self::LIFECYCLE_ACTIVE,
            'api_contract' => self::CONTRACT_V2,
            'context_version' => 1,
            'created_at' => $now,
            'updated_at' => $now,
        ]);
        $tx->insert('account_workspace_memberships', [
            'id' => Ids::v4(),
            'account_id' => (string) $account['id'],
            'tenant_id' => $tenantId,
            'role' => Roles::ADMIN,
            'status' => self::MEMBERSHIP_ACTIVE,
            'created_at' => $now,
            'updated_at' => $now,
        ]);
        return [
            'tenant_id' => $tenantId,
            'slug' => $slug,
            'name' => $label,
            'status' => self::STATUS_ACTIVE,
            'lifecycle' => self::LIFECYCLE_ACTIVE,
            'api_contract' => self::CONTRACT_V2,
            'context_version' => 1,
            'role' => Roles::ADMIN,
        ];
    }

    /**
     * A new PRIVATE workspace for one account, owning its own transaction.
     *
     * A caller that ALREADY holds a transaction must call
     * `createPrivateWithin()` instead: this connection has no nested
     * transactions and no savepoints, so opening a second one would fail.
     *
     * @return array<string, mixed>
     * @throws ApiException NOT_FOUND
     */
    public function createPrivate(string $accountId, ?string $name = null): array
    {
        return $this->db->transaction(
            fn (Database $tx): array => $this->createPrivateWithin(
                $tx, $accountId, $name));
    }

    /**
     * Grant (or re-grant) a workspace membership.
     *
     * @return array{account_id: string, tenant_id: string, role: string,
     *   status: string}
     * @throws ApiException NOT_FOUND | WORKSPACE_CONTEXT_STALE |
     *                      ROLE_FORBIDDEN | VALIDATION_FAILED
     */
    public function assign(string $actorAccountId, string $tenantId,
                           string $accountId, string $role,
                           int $expectedContextVersion): array
    {
        if (!in_array($role, Roles::ALL, true)) {
            throw self::invalid('the workspace role is not part of the'
                . ' workspace contract');
        }
        self::assertUuid($actorAccountId, 'acting account');
        self::assertUuid($accountId, 'account');
        self::assertUuid($tenantId, 'workspace');
        return $this->db->transaction(function (Database $tx) use (
            $actorAccountId, $tenantId, $accountId, $role, $expectedContextVersion
        ): array {
            $this->guardMembershipChange($tx, $actorAccountId, $tenantId,
                $accountId, $expectedContextVersion);
            $row = $tx->queryOne(
                'SELECT id FROM account_workspace_memberships'
                . ' WHERE account_id = ? AND tenant_id = ? FOR UPDATE',
                [$accountId, $tenantId]
            );
            $now = gmdate('Y-m-d H:i:s');
            if ($row === null) {
                $tx->insert('account_workspace_memberships', [
                    'id' => Ids::v4(),
                    'account_id' => $accountId,
                    'tenant_id' => $tenantId,
                    'role' => $role,
                    'status' => self::MEMBERSHIP_ACTIVE,
                    'created_at' => $now,
                    'updated_at' => $now,
                ]);
            } else {
                $tx->execute(
                    'UPDATE account_workspace_memberships'
                    . ' SET role = ?, status = ?, updated_at = ? WHERE id = ?',
                    [$role, self::MEMBERSHIP_ACTIVE, $now, (string) $row['id']]
                );
            }
            return ['account_id' => $accountId, 'tenant_id' => $tenantId,
                    'role' => $role, 'status' => self::MEMBERSHIP_ACTIVE];
        });
    }

    /**
     * Tombstone a workspace membership. The row is kept: who had access, and
     * until when, is evidence, and the unique key prevents a shadow copy.
     *
     * The LAST active admin of a workspace is never removed: a workspace with
     * no admin can never be administered again, and `freeze()` itself needs
     * one. The tenant row is locked before the membership row, so two
     * concurrent removals of the two admins serialise on that tenant lock and
     * the admin count cannot be read twice at one.
     *
     * @return array{account_id: string, tenant_id: string, role: string,
     *   status: string}
     * @throws ApiException NOT_FOUND | WORKSPACE_CONTEXT_STALE |
     *                      ROLE_FORBIDDEN | VALIDATION_FAILED |
     *                      LAST_WORKSPACE_ADMIN
     */
    public function remove(string $actorAccountId, string $tenantId,
                           string $accountId,
                           int $expectedContextVersion): array
    {
        self::assertUuid($actorAccountId, 'acting account');
        self::assertUuid($accountId, 'account');
        self::assertUuid($tenantId, 'workspace');
        return $this->db->transaction(function (Database $tx) use (
            $actorAccountId, $tenantId, $accountId, $expectedContextVersion
        ): array {
            $this->guardMembershipChange($tx, $actorAccountId, $tenantId,
                $accountId, $expectedContextVersion);
            $row = $tx->queryOne(
                'SELECT id, role, status FROM account_workspace_memberships'
                . ' WHERE account_id = ? AND tenant_id = ? FOR UPDATE',
                [$accountId, $tenantId]
            );
            if ($row === null) {
                throw self::denied();
            }
            if ((string) $row['role'] === Roles::ADMIN
                && (string) $row['status'] === self::MEMBERSHIP_ACTIVE
                && $this->activeAdminCount($tx, $tenantId) < 2) {
                throw self::lastWorkspaceAdmin();
            }
            $tx->execute(
                'UPDATE account_workspace_memberships'
                . ' SET status = ?, updated_at = ? WHERE id = ?',
                [self::MEMBERSHIP_REMOVED, gmdate('Y-m-d H:i:s'),
                 (string) $row['id']]
            );
            return ['account_id' => $accountId, 'tenant_id' => $tenantId,
                    'role' => (string) $row['role'],
                    'status' => self::MEMBERSHIP_REMOVED];
        });
    }

    /**
     * How many ACTIVE admins this workspace still has, counted on the row the
     * caller has just locked.
     */
    private function activeAdminCount(Database $tx, string $tenantId): int
    {
        return (int) $tx->queryValue(
            'SELECT COUNT(*) FROM account_workspace_memberships'
            . ' WHERE tenant_id = ? AND role = ' . "'" . Roles::ADMIN . "'"
            . ' AND status = ' . "'" . self::MEMBERSHIP_ACTIVE . "'",
            [$tenantId]
        );
    }

    /**
     * The shared precondition of assign/remove, in the required lock order:
     * account, then tenant, then (in the caller) membership.
     */
    private function guardMembershipChange(Database $tx, string $actorAccountId,
                                           string $tenantId, string $accountId,
                                           int $expectedContextVersion): void
    {
        self::assertContextVersion($expectedContextVersion);
        $account = $tx->queryOne(
            'SELECT id, status FROM accounts WHERE id = ? FOR UPDATE',
            [$accountId]
        );
        if ($account === null
            || ($account['status'] ?? '') !== self::ACCOUNT_ACTIVE) {
            throw self::denied();
        }
        $tenant = $this->lockTenantRow($tx, $tenantId, true);
        if ($tenant === null) {
            throw self::denied();
        }
        self::assertContext($tenant, $expectedContextVersion);
        if (!self::isAuthoritativeWorkspace($tenant)) {
            throw self::denied();
        }
        $actorRole = $this->membershipRole($tx, $actorAccountId, $tenantId);
        if ($actorRole !== Roles::ADMIN) {
            throw $actorRole === null ? self::denied() : self::roleForbidden();
        }
    }

    /**
     * Take a workspace out of service.
     *
     * The tenant row is locked BEFORE anything is returned, so a write that
     * has already been admitted under this lock finishes first, and every
     * write that starts afterwards is refused. Nothing is cancelled
     * mid-flight; the freeze simply wins the lock next.
     *
     * @return array<string, mixed>
     * @throws ApiException NOT_FOUND | WORKSPACE_CONTEXT_STALE |
     *                      ROLE_FORBIDDEN | VALIDATION_FAILED
     */
    public function freeze(string $actorAccountId, string $tenantId,
                           int $expectedContextVersion): array
    {
        self::assertUuid($actorAccountId, 'acting account');
        self::assertUuid($tenantId, 'workspace');
        return $this->db->transaction(function (Database $tx) use (
            $actorAccountId, $tenantId, $expectedContextVersion
        ): array {
            self::assertContextVersion($expectedContextVersion);
            $tenant = $this->lockTenantRow($tx, $tenantId, true);
            if ($tenant === null) {
                throw self::denied();
            }
            self::assertContext($tenant, $expectedContextVersion);
            if (!self::isAuthoritativeWorkspace($tenant)) {
                throw self::denied();
            }
            $actorRole = $this->membershipRole($tx, $actorAccountId, $tenantId);
            if ($actorRole !== Roles::ADMIN) {
                throw $actorRole === null ? self::denied() : self::roleForbidden();
            }
            // Leaving service invalidates the context every client holds, so
            // the version moves with it.
            $this->bumpContext($tx, $tenantId);
            $tx->execute(
                'UPDATE tenants SET status = ?, updated_at = ? WHERE id = ?',
                [self::STATUS_FROZEN, gmdate('Y-m-d H:i:s'), $tenantId]
            );
            // Re-read under the same lock: a locking read always returns the
            // latest row, so the caller is handed the state it just wrote
            // rather than the state it found.
            $after = $this->lockTenantRow($tx, $tenantId, true);
            return $this->describe($tx, is_array($after) ? $after : $tenant,
                $actorRole);
        });
    }
}
