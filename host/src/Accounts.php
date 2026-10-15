<?php
/**
 * Accounts.php — central platform accounts: creation, passwords, status,
 * operator protection and reviewed legacy linkage.
 *
 * Contract: Phase B identity foundation (002_identity_foundation.sql). An
 * account is GLOBAL: `accounts.login` is unique and `ascii_bin`, so it is
 * compared byte for byte and never scoped to a workspace. The legacy
 * tenant-bound username validator is therefore NOT the oracle here.
 *
 * Invariants this module owns
 *
 *   * Login. trim + lowercase + `[a-z0-9._-]{1,64}`, no silent truncation.
 *     Because the column is `ascii_bin`, canonicalLogin's strtolower IS the
 *     case-insensitive uniqueness rule — no second collation trick is added.
 *   * Password. Valid UTF-8, no NUL, >= 12 characters (mb, never bytes) and
 *     <= 72 BYTES (the documented bcrypt ceiling), always hashed by
 *     `Auth::hashPassword()` OUTSIDE the write transaction, and never stored,
 *     returned, logged or copied in any other form. The string is checked
 *     exactly as supplied: it is never trimmed, truncated or Unicode-
 *     normalised, because a silently rewritten password can never be typed
 *     again by the person who chose it.
 *   * Atomicity. Account + private workspace + admin membership commit in ONE
 *     transaction by calling `Workspaces::createPrivateWithin()` with the
 *     CALLER's transaction handle. `Database::transaction()` has no nesting
 *     and no savepoints, so the transactional `create()` must never be called
 *     from inside another transaction: `createWithin()` is the primitive for
 *     that case (seedFirstOperator uses it).
 *   * Sessions. A password reset or a disable invalidates every existing
 *     session by bumping `security_version`, which admission compares against
 *     the live account row. No session table is scanned to decide
 *     authorization; the physical `revoked_at` sweep is bounded housekeeping
 *     for one account only.
 *   * Operators. `platform_role` is a closed {member, operator} enum reachable
 *     only from private internal/CLI creation in this phase. Status and role
 *     changes serialize on `identity_state` BEFORE the account lock, and the
 *     last ACTIVE operator can never be disabled or demoted.
 *   * Legacy linkage. A mapping names an existing (tenant, legacy user) pair
 *     and a reviewed UUID. The target must already hold freshly set
 *     credentials with `must_change_password = 1` — normally because it was
 *     reset through the reviewed service — so an unrestricted account can
 *     never be blessed silently, and ONE account holds at most ONE mapping
 *     (enforced in PHP: 002 is applied and checksummed, so no unique index may
 *     be added to legacy_account_map). No legacy password hash is ever
 *     copied, and no membership is granted by inferred username. Membership
 *     assignment is a separate explicit operation (Workspaces::assign()).
 *   * Display names. Valid UTF-8, bounded to 120 UNICODE code points (mb,
 *     never bytes) and never used as record identity.
 *
 * Engine matrix: MariaDB 10.3.39 is the only engine this phase exercises.
 * MySQL 5.7 is BLOCKED and UNRUN; no support, compatibility or PASS is
 * claimed for it.
 */

declare(strict_types=1);

final class Accounts
{
    // ------------------------------------------------------------------
    // The closed vocabularies. Nothing outside these sets is ever stored.
    // ------------------------------------------------------------------

    public const STATUS_ACTIVE = 'active';
    public const STATUS_DISABLED = 'disabled';

    /** Mirrors the SQL CHECK on `accounts.status`. */
    public const STATUSES = [self::STATUS_ACTIVE, self::STATUS_DISABLED];

    public const ROLE_MEMBER = 'member';
    public const ROLE_OPERATOR = 'operator';

    /** Mirrors the SQL CHECK on `accounts.platform_role`. */
    public const PLATFORM_ROLES = [self::ROLE_MEMBER, self::ROLE_OPERATOR];

    /**
     * The global login grammar. ASCII only, no unicode case folding, no
     * truncation: an input that does not match is refused, never rewritten.
     */
    private const LOGIN_PATTERN = '/\A[a-z0-9._-]{1,64}\z/D';

    /** Character floor (mb) and byte ceiling (bcrypt's 72-byte limit). */
    private const PASSWORD_MIN_CHARS = 12;
    private const PASSWORD_MAX_BYTES = 72;

    /** `accounts.display_name varchar(120)` holds UNICODE code points. */
    private const DISPLAY_NAME_MAX = 120;

    /** The singleton `identity_state` row (002 s16). */
    private const IDENTITY_STATE_ID = 1;

    private ?Workspaces $workspaces = null;

    public function __construct(
        private readonly Database $db
    ) {
    }

    // ------------------------------------------------------------------
    // Validation core (spec/plan: the binding bodies, not a sketch)
    // ------------------------------------------------------------------

    /**
     * trim + lowercase, then the exact ASCII grammar. No silent truncation.
     *
     * @throws ApiException 422 VALIDATION_FAILED
     */
    public static function canonicalLogin(string $input): string
    {
        $login = strtolower(trim($input, " \t\r\n"));
        if (preg_match(self::LOGIN_PATTERN, $login) !== 1) {
            throw new ApiException(422, Codes::VALIDATION_FAILED, 'login is invalid');
        }
        return $login;
    }

    /**
     * Valid UTF-8, no NUL, at least 12 UNICODE characters, at most 72 BYTES.
     *
     * The password is the EXACT string the operator supplied: it is never
     * trimmed, case-folded or Unicode-normalised, so what is validated is what
     * is hashed and what must be typed again at the next sign-in.
     *
     * @throws ApiException 422 VALIDATION_FAILED
     */
    public static function validateNewPassword(string $password): void
    {
        if (!mb_check_encoding($password, 'UTF-8') || str_contains($password, "\0")
            || mb_strlen($password, 'UTF-8') < self::PASSWORD_MIN_CHARS
            || strlen($password) > self::PASSWORD_MAX_BYTES) {
            throw new ApiException(422, Codes::VALIDATION_FAILED, 'password is invalid');
        }
    }

    /**
     * Valid UTF-8, no NUL, bounded to 120 UNICODE code points. Truncation is
     * in CHARACTERS, so a multibyte name is never cut mid-character; the
     * display name is a label and is never record identity.
     *
     * @throws ApiException 422 VALIDATION_FAILED
     */
    public static function validateDisplayName(?string $name): string
    {
        $value = $name === null ? '' : trim($name);
        if ($value === '') {
            return '';
        }
        if (!mb_check_encoding($value, 'UTF-8') || str_contains($value, "\0")) {
            throw new ApiException(422, Codes::VALIDATION_FAILED,
                'display name is invalid');
        }
        return mb_substr($value, 0, self::DISPLAY_NAME_MAX, 'UTF-8');
    }

    // ------------------------------------------------------------------
    // Creation
    // ------------------------------------------------------------------

    /**
     * Create an account AND its private workspace in ONE transaction.
     *
     * The password is hashed BEFORE the transaction opens (hashing is a CPU
     * cost, not a database step), and the workspace is created through
     * `Workspaces::createPrivateWithin()` with this transaction's handle, so
     * a failed workspace leaves no account and a failed account leaves no
     * workspace. A duplicate canonical login is ACCOUNT_LOGIN_CONFLICT —
     * never a raw database message.
     *
     * `platformRole` is a closed enum, reachable only from private
     * internal/CLI creation in this phase: no HTTP surface in Phase B calls it.
     *
     * @return array{account_id: string, login: string, workspace_id: string,
     *   platform_role: string, status: string, must_change_password: bool}
     * @throws ApiException 422 VALIDATION_FAILED | ACCOUNT_LOGIN_CONFLICT,
     *                      500 SERVER_ERROR
     */
    public function create(string $login, string $password, ?string $displayName,
                           string $platformRole, int $now): array
    {
        self::canonicalLogin($login);
        self::validateNewPassword($password);
        self::validateDisplayName($displayName);
        self::assertPlatformRole($platformRole);
        // Outside the write transaction: never hash while holding a row lock.
        $hash = Auth::hashPassword($password);
        return $this->db->transaction(fn (Database $tx): array => $this->createWithin(
            $tx, $login, $hash, $displayName, $platformRole, $now));
    }

    /**
     * The caller-transaction primitive. Same body as `create()` minus the
     * transaction and the hashing, so a caller that already holds `$tx`
     * (seedFirstOperator, or any future private CLI) never nests a
     * transaction and never opens a savepoint.
     *
     * @return array{account_id: string, login: string, workspace_id: string,
     *   platform_role: string, status: string, must_change_password: bool}
     * @throws ApiException 422 VALIDATION_FAILED | ACCOUNT_LOGIN_CONFLICT,
     *                      500 SERVER_ERROR
     */
    public function createWithin(Database $tx, string $login, string $hash,
                                 ?string $displayName, string $platformRole,
                                 int $now): array
    {
        $canonical = self::canonicalLogin($login);
        self::assertPlatformRole($platformRole);
        if (!is_string($hash) || $hash === '') {
            throw self::invalid('the password hash is missing');
        }
        $label = self::validateDisplayName($displayName);
        $accountId = Ids::v4();
        $stamp = gmdate('Y-m-d H:i:s', $now);
        try {
            $tx->insert('accounts', [
                'id' => $accountId,
                'login' => $canonical,
                'password_hash' => $hash,
                'display_name' => $label,
                'status' => self::STATUS_ACTIVE,
                'must_change_password' => 1,
                'security_version' => 1,
                'platform_role' => $platformRole,
                'created_at' => $stamp,
                'updated_at' => $stamp,
            ]);
        } catch (\PDOException $error) {
            throw self::translateInsert($error);
        }
        // Account, private workspace and admin membership: ONE transaction.
        $workspace = $this->workspaces()->createPrivateWithin($tx, $accountId, null);
        $this->record($tx, 'account_created', $accountId, null, $now);
        return [
            'account_id' => $accountId,
            'login' => $canonical,
            'workspace_id' => (string) $workspace['tenant_id'],
            'platform_role' => $platformRole,
            'status' => self::STATUS_ACTIVE,
            'must_change_password' => true,
        ];
    }

    // ------------------------------------------------------------------
    // Password lifecycle
    // ------------------------------------------------------------------

    /**
     * Reset the password of ONE account, addressed by account UUID only.
     *
     * There is no tenant, no legacy user and no administrator parameter: a
     * reset can never be aimed at a tenant administrator by inference. The
     * hash is computed outside the transaction; the transaction then locks the
     * account, replaces the hash, increments `security_version` and sets
     * `must_change_password = 1`, all at once. Every session the account still
     * holds is immediately stale by version comparison at admission — the
     * physical `revoked_at` sweep below is bounded housekeeping for this one
     * account and is NEVER the authorization dependency.
     *
     * @return array{account_id: string, security_version: int,
     *   must_change_password: bool}
     * @throws ApiException 404 NOT_FOUND | 422 VALIDATION_FAILED
     */
    public function resetPassword(string $accountId, string $newPassword,
                                  int $now): array
    {
        self::assertUuid($accountId, 'account');
        self::validateNewPassword($newPassword);
        $hash = Auth::hashPassword($newPassword);
        return $this->db->transaction(function (Database $tx) use (
            $accountId, $hash, $now
        ): array {
            $account = $tx->queryOne(
                'SELECT id, security_version FROM accounts WHERE id = ? FOR UPDATE',
                [$accountId]);
            if ($account === null) {
                throw self::notFound();
            }
            $version = (int) $account['security_version'] + 1;
            $stamp = gmdate('Y-m-d H:i:s', $now);
            $tx->update('accounts', [
                'password_hash' => $hash,
                'must_change_password' => 1,
                'security_version' => $version,
                'updated_at' => $stamp,
            ], 'id = ?', [$accountId]);
            // Bounded housekeeping: one account's still-live session rows.
            $tx->execute('UPDATE account_sessions SET revoked_at = ?'
                . ' WHERE account_id = ? AND revoked_at IS NULL',
                [$stamp, $accountId]);
            $this->record($tx, 'password_reset', $accountId, null, $now);
            return [
                'account_id' => $accountId,
                'security_version' => $version,
                'must_change_password' => true,
            ];
        });
    }

    // ------------------------------------------------------------------
    // Status and platform role
    // ------------------------------------------------------------------

    /**
     * Enable or disable an account. Disabling logically invalidates every
     * session by incrementing `security_version`, and records the matching
     * security event in the same transaction.
     *
     * Lock order is `identity_state` THEN the account, so status and operator
     * changes serialize on one another instead of racing the operator count.
     *
     * @return array{account_id: string, status: string, security_version: int}
     * @throws ApiException 404 NOT_FOUND | 409 LAST_OPERATOR_REQUIRED |
     *                      422 VALIDATION_FAILED
     */
    public function setStatus(string $accountId, string $status, int $now): array
    {
        self::assertUuid($accountId, 'account');
        if (!in_array($status, self::STATUSES, true)) {
            throw self::invalid('the account status is not part of the account contract');
        }
        return $this->db->transaction(function (Database $tx) use (
            $accountId, $status, $now
        ): array {
            self::lockIdentityState($tx);
            $account = $tx->queryOne(
                'SELECT id, status, platform_role, security_version'
                . ' FROM accounts WHERE id = ? FOR UPDATE', [$accountId]);
            if ($account === null) {
                throw self::notFound();
            }
            $version = (int) $account['security_version'];
            $disabling = $status === self::STATUS_DISABLED
                && $account['status'] === self::STATUS_ACTIVE;
            if ($disabling) {
                self::assertOperatorRemains($tx, $accountId, $account);
                $version++;
            }
            $tx->update('accounts', [
                'status' => $status,
                'security_version' => $version,
                'updated_at' => gmdate('Y-m-d H:i:s', $now),
            ], 'id = ?', [$accountId]);
            if ($disabling) {
                $this->record($tx, 'account_disabled', $accountId, null, $now);
            } elseif ($status === self::STATUS_ACTIVE
                && $account['status'] === self::STATUS_DISABLED) {
                $this->record($tx, 'account_enabled', $accountId, null, $now);
            }
            return ['account_id' => $accountId, 'status' => $status,
                    'security_version' => $version];
        });
    }

    /**
     * Promote or demote the PLATFORM role. Membership of a workspace is a
     * different, explicit operation and is never implied here.
     *
     * @return array{account_id: string, platform_role: string}
     * @throws ApiException 404 NOT_FOUND | 409 LAST_OPERATOR_REQUIRED |
     *                      422 VALIDATION_FAILED
     */
    public function setPlatformRole(string $accountId, string $platformRole,
                                    int $now): array
    {
        self::assertUuid($accountId, 'account');
        self::assertPlatformRole($platformRole);
        return $this->db->transaction(function (Database $tx) use (
            $accountId, $platformRole, $now
        ): array {
            self::lockIdentityState($tx);
            $account = $tx->queryOne(
                'SELECT id, status, platform_role FROM accounts WHERE id = ? FOR UPDATE',
                [$accountId]);
            if ($account === null) {
                throw self::notFound();
            }
            if ($platformRole !== self::ROLE_OPERATOR) {
                self::assertOperatorRemains($tx, $accountId, $account);
            }
            $tx->update('accounts', [
                'platform_role' => $platformRole,
                'updated_at' => gmdate('Y-m-d H:i:s', $now),
            ], 'id = ?', [$accountId]);
            return ['account_id' => $accountId, 'platform_role' => $platformRole];
        });
    }

    /**
     * Seed the FIRST platform operator. Owns its SINGLE transaction: it locks
     * `identity_state`, refuses a prior seed, and creates the account through
     * `createWithin()` — never by nesting the transactional `create()`.
     *
     * @return array{account_id: string, login: string, workspace_id: string,
     *   platform_role: string, status: string, must_change_password: bool}
     * @throws ApiException 409 VERSION_CONFLICT | 422 VALIDATION_FAILED |
     *                      500 SERVER_ERROR
     */
    public function seedFirstOperator(string $login, string $password,
                                      ?string $displayName, int $now): array
    {
        self::canonicalLogin($login);
        self::validateNewPassword($password);
        self::validateDisplayName($displayName);
        $hash = Auth::hashPassword($password);
        return $this->db->transaction(function (Database $tx) use (
            $login, $hash, $displayName, $now
        ): array {
            $state = self::lockIdentityState($tx);
            if (($state['first_operator_id'] ?? null) !== null
                && $state['first_operator_id'] !== '') {
                // Reseeding would silently mint a second platform root.
                throw new ApiException(409, Codes::VERSION_CONFLICT,
                    'the first operator is already seeded');
            }
            $receipt = $this->createWithin($tx, $login, $hash, $displayName,
                self::ROLE_OPERATOR, $now);
            $tx->update('identity_state', [
                'first_operator_id' => $receipt['account_id'],
                'row_version' => (int) $state['row_version'] + 1,
            ], 'id = ?', [self::IDENTITY_STATE_ID]);
            return $receipt;
        });
    }

    // ------------------------------------------------------------------
    // Reviewed legacy linkage
    // ------------------------------------------------------------------

    /**
     * Link an EXISTING legacy (tenant, user) pair to an account, recording the
     * review that authorised it.
     *
     * The composite key must name a real pair — never a username inference —
     * the account must exist and must not already hold a mapping, the review
     * id must be a UUID, and the target
     * must already hold freshly set credentials with
     * `must_change_password = 1` (normally because the reviewed reset service
     * was run on it). An unrestricted target is refused and left untouched, so
     * a legacy account can never be silently blessed into an active credential.
     * One account holds ONE legacy identity: a second reviewed link onto an
     * already linked account is refused (ACCOUNT_ALREADY_LINKED) and the
     * existing row is left unchanged.
     * No legacy password hash is read or copied, and no membership is granted
     * here: membership assignment is a separate explicit operation.
     *
     * @return array{tenant_id: string, legacy_user_id: string,
     *   account_id: string, review_id: string, confirmed_at: string}
     * @throws ApiException 404 NOT_FOUND | 409 VERSION_CONFLICT |
     *                      409 ACCOUNT_ALREADY_LINKED |
     *                      422 VALIDATION_FAILED
     */
    public function linkLegacy(string $tenantId, string $legacyUserId,
                               string $accountId, string $reviewId,
                               int $now): array
    {
        self::assertUuid($tenantId, 'workspace');
        self::assertUuid($legacyUserId, 'legacy user');
        self::assertUuid($accountId, 'account');
        self::assertUuid($reviewId, 'review');
        return $this->db->transaction(function (Database $tx) use (
            $tenantId, $legacyUserId, $accountId, $reviewId, $now
        ): array {
            // The composite key must name a real (tenant, legacy user) pair.
            $legacy = $tx->queryOne(
                'SELECT id FROM users WHERE tenant_id = ? AND id = ? FOR UPDATE',
                [$tenantId, $legacyUserId]);
            if ($legacy === null) {
                throw self::notFound();
            }
            $account = $tx->queryOne(
                'SELECT id, must_change_password FROM accounts WHERE id = ? FOR UPDATE',
                [$accountId]);
            if ($account === null) {
                throw self::notFound();
            }
            // ONE account holds ONE legacy identity. `legacy_account_map` has
            // no unique index on account_id (002 is applied and checksummed,
            // so no migration may be added here), so the invariant is enforced
            // in PHP before any write: a second reviewed link onto an already
            // linked account would merge two legacy identities into one central
            // account — exactly the inferred merge this phase exists to stop.
            $claimed = $tx->queryOne(
                'SELECT tenant_id, legacy_user_id FROM legacy_account_map'
                . ' WHERE account_id = ? FOR UPDATE',
                [$accountId]);
            if ($claimed !== null) {
                throw new ApiException(409, Codes::ACCOUNT_ALREADY_LINKED,
                    'the target account is already linked to a legacy identity');
            }
            if ((int) $account['must_change_password'] !== 1) {
                // An unrestricted target must be reset through the reviewed
                // service first; the mapper never copies or blesses a hash.
                throw self::invalid(
                    'the target account must hold freshly set credentials before linkage');
            }
            $existing = $tx->queryOne(
                'SELECT account_id FROM legacy_account_map'
                . ' WHERE tenant_id = ? AND legacy_user_id = ? FOR UPDATE',
                [$tenantId, $legacyUserId]);
            if ($existing !== null) {
                throw new ApiException(409, Codes::VERSION_CONFLICT,
                    'this legacy user is already linked');
            }
            $confirmedAt = gmdate('Y-m-d H:i:s', $now);
            $tx->insert('legacy_account_map', [
                'tenant_id' => $tenantId,
                'legacy_user_id' => $legacyUserId,
                'account_id' => $accountId,
                'review_id' => $reviewId,
                'confirmed_at' => $confirmedAt,
            ]);
            $this->record($tx, 'legacy_linked', $accountId, $tenantId, $now);
            return [
                'tenant_id' => $tenantId,
                'legacy_user_id' => $legacyUserId,
                'account_id' => $accountId,
                'review_id' => $reviewId,
                'confirmed_at' => $confirmedAt,
            ];
        });
    }

    // ------------------------------------------------------------------
    // Internals
    // ------------------------------------------------------------------

    private function workspaces(): Workspaces
    {
        return $this->workspaces ??= new Workspaces($this->db);
    }

    /**
     * Lock the identity_state singleton. Every status/operator change takes
     * this lock BEFORE any account lock, so two of them can never interleave
     * around the operator count.
     *
     * @return array<string, mixed>
     * @throws ApiException 500 SERVER_ERROR
     */
    private static function lockIdentityState(Database $tx): array
    {
        $state = $tx->queryOne(
            'SELECT id, first_operator_id, row_version FROM identity_state'
            . ' WHERE id = ? FOR UPDATE', [self::IDENTITY_STATE_ID]);
        if ($state === null) {
            // A missing singleton row is a broken schema, not a caller error.
            throw new ApiException(500, Codes::SERVER_ERROR,
                'the identity state row is missing');
        }
        return $state;
    }

    /**
     * Removing the LAST active platform operator is refused: an installation
     * with no operator could never be administered again.
     *
     * @param array<string, mixed> $account
     * @throws ApiException 409 LAST_OPERATOR_REQUIRED
     */
    private static function assertOperatorRemains(Database $tx, string $accountId,
                                                  array $account): void
    {
        if (($account['platform_role'] ?? '') !== self::ROLE_OPERATOR
            || ($account['status'] ?? '') !== self::STATUS_ACTIVE) {
            return;
        }
        $others = (int) $tx->queryValue(
            'SELECT COUNT(*) FROM accounts WHERE platform_role = ?'
            . ' AND status = ? AND id <> ?',
            [self::ROLE_OPERATOR, self::STATUS_ACTIVE, $accountId]);
        if ($others === 0) {
            throw new ApiException(409, Codes::LAST_OPERATOR_REQUIRED,
                'the last active platform operator cannot be removed');
        }
    }

    /**
     * A security lifecycle event, written inside the SAME transaction as the
     * state change it describes (never through a best-effort logger). Metadata
     * only: no login, no password, no hash, no token, no IP.
     */
    private function record(Database $tx, string $eventType, string $accountId,
                            ?string $tenantId, int $now): void
    {
        $tx->insert('account_security_events', [
            'id' => Ids::v4(),
            'actor_account_id' => null,
            'subject_account_id' => $accountId,
            'tenant_id' => $tenantId,
            'actor_kind' => 'private_cli',
            'event_type' => $eventType,
            'outcome' => 'success',
            'created_at' => gmdate('Y-m-d H:i:s', $now),
        ]);
    }

    /**
     * Map a storage failure on the account INSERT to a code, without ever
     * carrying a database message out of this module.
     *
     * Only the login uniqueness becomes ACCOUNT_LOGIN_CONFLICT; any other
     * duplicate key is a server-side defect and stays SERVER_ERROR.
     */
    private static function translateInsert(\PDOException $error): ApiException
    {
        $info = $error->errorInfo ?? null;
        $sqlstate = is_array($info) && isset($info[0])
            ? (string) $info[0] : (string) $error->getCode();
        $errno = is_array($info) && isset($info[1]) ? (int) $info[1] : 0;
        $detail = is_array($info) && isset($info[2]) ? (string) $info[2] : '';
        if ($errno === 1062 || $sqlstate === '23000') {
            if (str_contains($detail, 'uq_accounts_login')) {
                throw new ApiException(422, Codes::ACCOUNT_LOGIN_CONFLICT,
                    'the login is already in use');
            }
        }
        return new ApiException(500, Codes::SERVER_ERROR,
            'the account could not be stored');
    }

    private static function assertPlatformRole(string $platformRole): void
    {
        if (!in_array($platformRole, self::PLATFORM_ROLES, true)) {
            throw self::invalid(
                'the platform role is not part of the account contract');
        }
    }

    private static function assertUuid(string $value, string $what): void
    {
        if (!Ids::isUuid($value)) {
            // The value itself is never echoed: it is caller-supplied data.
            throw self::invalid('the ' . $what . ' is not a valid identifier');
        }
    }

    private static function invalid(string $message): ApiException
    {
        return new ApiException(422, Codes::VALIDATION_FAILED, $message);
    }

    private static function notFound(): ApiException
    {
        return new ApiException(404, Codes::NOT_FOUND, 'no such account');
    }
}
