<?php
/**
 * AccountSessions.php — Phase B purpose-bound opaque sessions and live
 * authorization.
 *
 * Contract: the Phase B identity foundation (002_identity_foundation.sql
 * `account_sessions`) and the Task 6 brief.
 *
 * What this module guarantees
 *
 *   * ONE credential shape. A session credential is EXACTLY 64 lowercase
 *     hexadecimal characters, validated BEFORE any lookup. A legacy compact
 *     JWS, a bare acting-user value, uppercase hex and a wrong length all
 *     answer AUTH_REQUIRED without touching a row. Nothing else is accepted,
 *     so there is no second credential path to reason about.
 *   * Only the hash is stored. `Tokens::hashOpaque($raw)` is the sole persisted
 *     form; the raw token leaves this module EXACTLY ONCE, in the successful
 *     login response envelope. It never enters `AccountPrincipal`, an
 *     exception message, a security event, a rate-limit row, a log line, a URL
 *     or any durable server-side column.
 *   * Purposes are immutable. Only `desktop` and `admin_web` may be issued
 *     (`issueWithin` refuses anything else), and a purpose recorded at issuance
 *     is compared exactly at admission. A desktop session can never satisfy an
 *     admin_web route and vice versa.
 *   * Admission is a LOCKED RECHECK, not a cached decision. Locate by token
 *     hash, open ONE transaction, lock the ACCOUNT, then lock the SESSION and
 *     re-read both. Only then is the live `status`, the exact
 *     `security_version`, the exact purpose, `revoked_at IS NULL` and BOTH
 *     deadlines — STRICTLY after `now`, so exactly-at-deadline is expired —
 *     evaluated. A reset, a disable or a password change that committed while
 *     the request was in flight is therefore always seen.
 *   * The idle window is 1800 s and the absolute window is 28800 s. NOTHING in
 *     this module ever extends either one: admission, `withWorkspace` and any
 *     future worker heartbeat are read-only with respect to
 *     `last_activity_at` / `idle_expires_at`, and there is no UPDATE of those
 *     columns anywhere in this file. A future human-command route, and only a
 *     route name that is explicitly authorized for it, may later adopt
 *     `idle_expires_at = min(now + 1800, absolute_expires_at)`; until that
 *     route exists the formula is documented here and deliberately unimplemented,
 *     because a passive poll that keeps a session alive is an unbounded
 *     session.
 *   * A forced change is a real gate. While `must_change_password` is set the
 *     session is admitted ONLY for the self-service routes (me / logout /
 *     change-password). An ordinary workspace or business call answers
 *     PASSWORD_CHANGE_REQUIRED.
 *   * `withWorkspace()` performs the same admission and then calls
 *     `Workspaces::lockAuthorizedWithin()` INSIDE the same transaction, so the
 *     snapshot the callback receives is the state the lock pinned. That
 *     snapshot is a VALUE, never a capability: once the callback returns, the
 *     transaction is committed and the snapshot authorizes nothing — every
 *     later mutation must be admitted again.
 *   * `loginDesktop()` serializes check / credential verification / record on a
 *     bounded account+IP advisory lock, records every attempt under a rate
 *     limit key of `v2:` + SHA-256 of the canonical login (never the login
 *     itself), verifies against a DECOY hash for a missing or disabled
 *     credential so a miss costs what a hit costs, and then locks and rechecks
 *     the CURRENT password hash and `security_version` before issuing. A
 *     password reset that lands in between is a generic failure, not a login.
 *     The attempt lock is released in `finally`; a contended lock returns
 *     RATE_LIMITED with a Retry-After rather than waiting without a bound.
 *   * `changePassword()` verifies the current password, validates and hashes
 *     the new one OUTSIDE the transaction, rechecks account and session under
 *     locks, clears `must_change_password`, bumps `security_version` — which
 *     invalidates ALL of the account's sessions, the caller's included — and
 *     returns `reauthentication_required = true` WITHOUT minting a replacement
 *     session. The physical `revoked_at` sweep is bounded (per account, with an
 *     explicit LIMIT) housekeeping; version comparison at admission is the
 *     authorization dependency, never a table scan.
 *   * Logout revokes EXACTLY the known session it was addressed with, after
 *     checking the purpose, so a wrong-purpose token can never touch another
 *     purpose's session. Repeating a logout on a session this module already
 *     revoked is a documented idempotent SUCCESS, because the caller's intent —
 *     "this credential must be dead" — is already true. Like every other path
 *     here it locks the ACCOUNT before the session row: the security event it
 *     writes has a foreign key onto `accounts`, so that parent lock is not
 *     optional.
 *   * Operator rights are read from the LIVE `accounts` row inside the lock and
 *     copied onto `AccountPrincipal`. Neither the session purpose nor any
 *     client-supplied role field can ever grant one.
 *
 * Lock order (extends Workspaces' account → tenant → membership):
 *   account → session → tenant → membership.
 * Every path here locks the account before any session row, so two session
 * operations can never form a cycle with the workspace helpers.
 *
 * Engine matrix: MariaDB 10.3.39 is the only engine this phase exercises.
 * MySQL 5.7 is BLOCKED and UNRUN; no support, compatibility or PASS is claimed
 * for it.
 */

declare(strict_types=1);

/**
 * The admitted caller, built ONLY from the live `accounts` row that admission
 * locked and re-read.
 *
 * Deliberately carries no credential of any kind: there is no token, no hash,
 * no password and no client-supplied role field on this object, so a route
 * cannot accidentally echo one back or persist one.
 */
final class AccountPrincipal
{
    public function __construct(
        public readonly string $accountId,
        public readonly string $purpose,
        public readonly string $displayName,
        public readonly string $platformRole,
        public readonly bool $mustChangePassword,
        public readonly int $securityVersion
    ) {
    }
}

final class AccountSessions
{
    // ------------------------------------------------------------------
    // The closed vocabularies and the two windows. Nothing outside these
    // values is ever stored or admitted.
    // ------------------------------------------------------------------

    public const PURPOSE_DESKTOP = 'desktop';
    public const PURPOSE_ADMIN_WEB = 'admin_web';

    /** Mirrors the SQL CHECK on `account_sessions.purpose`. */
    public const PURPOSES = [
        self::PURPOSE_DESKTOP => true,
        self::PURPOSE_ADMIN_WEB => true,
    ];

    /** Idle window: no admitted request extends it (see the class contract). */
    public const IDLE_SECONDS = 1800;

    /** Absolute window: the hard ceiling, unreachable by any amount of use. */
    public const ABSOLUTE_SECONDS = 28800;

    /**
     * The ONLY credential shape. Exactly 64 lowercase hex characters — which
     * is precisely what `Tokens::newOpaque()` emits — validated before any
     * lookup, so a legacy JWS or a bare acting-user value never reaches SQL.
     */
    private const TOKEN_PATTERN = '/\A[0-9a-f]{64}\z/D';

    /**
     * Bounded wait for the per (login, ip) attempt lock. A login storm must
     * queue for a moment and then be refused, never queue forever.
     */
    private const LOCK_WAIT_SECONDS = 2;

    /** MySQL/MariaDB cap a lock name at 64 characters; keep well under it. */
    private const LOCK_PREFIX = 'irica_b_login_';

    /**
     * The physical `revoked_at` sweep is BOUNDED housekeeping for ONE account.
     * Version comparison at admission is the authorization dependency, so a
     * session beyond this bound is still dead the moment its account moves on.
     */
    private const REVOKE_SWEEP = 500;

    /**
     * The single generic login refusal. Identical for an unknown account, a
     * disabled account and a wrong password, and it never carries a login, a
     * password, a hash or an account id.
     */
    private const LOGIN_FAILURE_MESSAGE = 'login or password is incorrect';

    private const DENIED_MESSAGE = 'the session credential is not valid for this request';

    private readonly RateLimit $rateLimit;

    private ?Workspaces $workspaces = null;

    private static ?string $decoyHash = null;

    public function __construct(
        private readonly Database $db,
        ?RateLimit $rateLimit = null
    ) {
        $this->rateLimit = $rateLimit ?? new RateLimit([]);
    }

    // ------------------------------------------------------------------
    // Errors. Every code is one Http::Codes constant; no string literal is
    // ever returned as an error code.
    // ------------------------------------------------------------------

    private static function authRequired(): ApiException
    {
        return new ApiException(401, Codes::AUTH_REQUIRED, self::DENIED_MESSAGE);
    }

    private static function authRevoked(): ApiException
    {
        return new ApiException(401, Codes::AUTH_REVOKED, self::DENIED_MESSAGE);
    }

    private static function authExpired(): ApiException
    {
        return new ApiException(401, Codes::AUTH_EXPIRED, self::DENIED_MESSAGE);
    }

    private static function invalid(string $message): ApiException
    {
        return new ApiException(422, Codes::VALIDATION_FAILED, $message);
    }

    // ------------------------------------------------------------------
    // Credential handling
    // ------------------------------------------------------------------

    /**
     * Validate the credential shape, then hash it. Nothing else in this module
     * ever sees a token that has not passed this gate, and nothing ever stores
     * or logs the value itself.
     *
     * @throws ApiException 401 AUTH_REQUIRED
     */
    private static function hash(string $token): string
    {
        if (preg_match(self::TOKEN_PATTERN, $token) !== 1) {
            throw self::authRequired();
        }
        return Tokens::hashOpaque($token);
    }

    /**
     * The attempt-lock name for one (login, IP) pair.
     *
     * Public as a pure derivation — no secret, no lookup, no side effect — so
     * the fixture suite can hold the very lock a real attempt would take and
     * prove the wait is bounded.
     */
    public static function loginLockName(string $canonicalLogin, string $ip): string
    {
        return self::LOCK_PREFIX
            . substr(hash('sha256', $canonicalLogin . "\0" . $ip), 0, 48);
    }

    /**
     * A throw-away hash so a miss costs what a hit costs. Computed once per
     * process; it is never a person's credential and is never stored.
     */
    private static function decoy(): string
    {
        if (self::$decoyHash === null) {
            self::$decoyHash = (string) password_hash(
                bin2hex(random_bytes(16)), Auth::passwordAlgo());
        }
        return self::$decoyHash;
    }

    /** A SQL DATETIME (UTC, because the connection pins +00:00) to Unix time. */
    private static function at(mixed $sqlDateTime): int
    {
        if ($sqlDateTime instanceof DateTimeInterface) {
            return $sqlDateTime->getTimestamp();
        }
        if (!is_string($sqlDateTime) || $sqlDateTime === '') {
            return 0;
        }
        $timestamp = strtotime($sqlDateTime . ' UTC');
        return $timestamp === false ? 0 : $timestamp;
    }

    // ------------------------------------------------------------------
    // Issuance
    // ------------------------------------------------------------------

    /**
     * Mint one session inside the CALLER's transaction, from a live account row
     * the caller has already locked and checked.
     *
     * Purposes are a closed set: anything other than `desktop` or `admin_web`
     * is refused, and once written the purpose is immutable for the life of the
     * row. The raw token is generated here and returned to the caller ONCE; the
     * only thing persisted is its SHA-256.
     *
     * @param array<string, mixed> $account the LOCKED live `accounts` row
     * @return array{token: string, expires_at: string, idle_expires_at: string,
     *   account: array{id: string, display_name: string, platform_role: string,
     *   must_change_password: bool}}
     * @throws ApiException 422 VALIDATION_FAILED | 401 AUTH_REVOKED
     */
    public function issueWithin(Database $tx, array $account, string $purpose,
                                int $now): array
    {
        if (!isset(self::PURPOSES[$purpose])) {
            throw self::invalid('the session purpose is not part of the session contract');
        }
        $accountId = (string) ($account['id'] ?? '');
        if (!Ids::isUuid($accountId)) {
            throw self::invalid('the session must be issued to a valid account');
        }
        if (($account['status'] ?? '') !== Accounts::STATUS_ACTIVE) {
            throw self::authRevoked();
        }
        $version = (int) ($account['security_version'] ?? 0);
        if ($version < 1) {
            throw self::invalid('the account carries no usable security version');
        }

        $raw = Tokens::newOpaque();
        $stamp = gmdate('Y-m-d H:i:s', $now);
        $idleAt = min($now + self::IDLE_SECONDS, $now + self::ABSOLUTE_SECONDS);
        $absoluteAt = $now + self::ABSOLUTE_SECONDS;
        $idleStamp = gmdate('Y-m-d H:i:s', $idleAt);
        $absoluteStamp = gmdate('Y-m-d H:i:s', $absoluteAt);

        $tx->insert('account_sessions', [
            'id' => Ids::v4(),
            'account_id' => $accountId,
            'token_hash' => Tokens::hashOpaque($raw),
            'purpose' => $purpose,
            'security_version' => $version,
            'issued_at' => $stamp,
            'last_activity_at' => $stamp,
            'idle_expires_at' => $idleStamp,
            'absolute_expires_at' => $absoluteStamp,
            'revoked_at' => null,
        ]);
        $this->record($tx, 'login_success', $accountId, $now);

        return [
            'token' => $raw,
            'expires_at' => Schema::toWireTimestamp($absoluteStamp),
            'idle_expires_at' => Schema::toWireTimestamp($idleStamp),
            'account' => [
                'id' => $accountId,
                'display_name' => (string) ($account['display_name'] ?? ''),
                'platform_role' => (string) ($account['platform_role'] ?? ''),
                'must_change_password'
                    => (int) ($account['must_change_password'] ?? 0) === 1,
            ],
        ];
    }

    // ------------------------------------------------------------------
    // POST /v2/auth/login — the desktop credential
    // ------------------------------------------------------------------

    /**
     * Issue a `desktop` session for a login and password.
     *
     * Order is deliberate and is the whole point of the method:
     *
     *   1. canonicalize the login (an unusable login is refused before any
     *      database work, and is never truncated to fit);
     *   2. take the bounded per (login, IP) advisory lock — check, verify and
     *      record are serialized on it, so two concurrent attempts cannot both
     *      spend the same failure budget;
     *   3. ask the rate limiter, which answers RATE_LIMITED with Retry-After;
     *   4. look the account up and verify, using a DECOY hash when the account
     *      is missing or disabled so that the three failure modes cost the same
     *      and reveal nothing;
     *   5. open ONE transaction, LOCK the account, and re-read the CURRENT
     *      password hash and security_version — a reset that landed between the
     *      verification and the lock is a generic failure, never a login;
     *   6. only then issue, inside that same transaction.
     *
     * The advisory lock is released in `finally`, so a failure path can never
     * leave the key held. A contended lock is refused immediately with a
     * Retry-After rather than waited on without a bound.
     *
     * @throws ApiException 401 AUTH_REQUIRED | 422 VALIDATION_FAILED |
     *                      429 RATE_LIMITED
     */
    public function loginDesktop(string $login, string $password, string $ip,
                                 int $now): array
    {
        $canonical = Accounts::canonicalLogin($login);
        // Two DIFFERENT namespaced keys, deliberately: the advisory lock is
        // scoped to one (login, IP) pair, while the rate limiter is keyed on
        // the account alone so a throttle follows the account across addresses.
        $lockName = self::loginLockName($canonical, $ip);
        $rateKey = self::rateLimitKey($canonical);
        $acquired = false;
        try {
            $acquired = (int) $this->db->queryValue(
                'SELECT GET_LOCK(?, ?) AS acquired',
                [$lockName, self::LOCK_WAIT_SECONDS]) === 1;
            if (!$acquired) {
                throw new ApiException(429, Codes::RATE_LIMITED,
                    'login attempts are being throttled', self::LOCK_WAIT_SECONDS);
            }
            return $this->attemptLocked($canonical, $password, $ip, $lockName,
                $rateKey, $now);
        } finally {
            if ($acquired) {
                try {
                    $this->db->queryValue('SELECT RELEASE_LOCK(?) AS released',
                        [$lockName]);
                } catch (Throwable $releaseError) {
                    // Losing the release must never mask the login outcome.
                    unset($releaseError);
                }
            }
        }
    }

    /**
     * The rate-limiter key for one account: `v2:` plus the SHA-256 of the
     * CANONICAL login. The plaintext login is never a key, so it is never
     * stored, indexed or returned by the throttle.
     */
    private static function rateLimitKey(string $canonicalLogin): string
    {
        return 'v2:' . hash('sha256', $canonicalLogin);
    }

    /**
     * The body of a login attempt, executed with the attempt lock held.
     *
     * @throws ApiException 401 AUTH_REQUIRED | 429 RATE_LIMITED
     */
    private function attemptLocked(string $canonical, string $password,
                                   string $ip, string $lockName, string $rateKey,
                                   int $now): array
    {
        $stamp = gmdate('Y-m-d H:i:s', $now);
        $this->rateLimit->check($this->db, $rateKey, $ip, $stamp);

        $account = $this->db->queryOne(
            'SELECT id, login, display_name, status, must_change_password,'
            . ' security_version, platform_role, password_hash'
            . ' FROM accounts WHERE login = ? LIMIT 2', [$canonical]);

        $ok = false;
        if ($account !== null
            && ($account['status'] ?? '') === Accounts::STATUS_ACTIVE) {
            $ok = password_verify($password, (string) $account['password_hash']);
        } else {
            // Same work as a real verification, so a miss is not a probe.
            password_verify($password, self::decoy());
        }
        if (!$ok || $account === null) {
            $this->rateLimit->record($this->db, null, $rateKey, $ip, 'failure',
                $stamp);
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                self::LOGIN_FAILURE_MESSAGE);
        }

        $accountId = (string) $account['id'];
        $seenHash = (string) $account['password_hash'];
        $seenVersion = (int) $account['security_version'];

        try {
            return $this->db->transaction(function (Database $tx) use (
                $accountId, $seenHash, $seenVersion, $rateKey, $ip, $stamp, $now
            ): array {
                // Lock and re-read: only the CURRENT credential may issue.
                $live = $tx->queryOne(
                    'SELECT id, login, display_name, status, must_change_password,'
                    . ' security_version, platform_role, password_hash'
                    . ' FROM accounts WHERE id = ? FOR UPDATE', [$accountId]);
                if ($live === null
                    || ($live['status'] ?? '') !== Accounts::STATUS_ACTIVE
                    || (string) $live['password_hash'] !== $seenHash
                    || (int) $live['security_version'] !== $seenVersion) {
                    throw new ApiException(401, Codes::AUTH_REQUIRED,
                        self::LOGIN_FAILURE_MESSAGE);
                }
                $this->rateLimit->record($this->db, null, $rateKey, $ip, 'success',
                    $stamp);
                return $this->issueWithin($tx, $live, self::PURPOSE_DESKTOP, $now);
            });
        } catch (ApiException $error) {
            if ($error->errorCode === Codes::AUTH_REQUIRED) {
                // A password-reset race is recorded as a generic failure, the
                // same as any other refused attempt.
                $this->rateLimit->record($this->db, null, $rateKey, $ip, 'failure',
                    $stamp);
            }
            throw $error;
        }
    }

    // ------------------------------------------------------------------
    // Admission
    // ------------------------------------------------------------------

    /**
     * Admit one credential for one purpose and hand the transaction and the
     * live principal to `$fn`.
     *
     * `$allowForcedChange` says whether this route belongs to the self-service
     * set (me / logout / change-password). Everything else — every ordinary
     * workspace and business call — is refused with PASSWORD_CHANGE_REQUIRED
     * while the account still forces a password change.
     *
     * Nothing here extends the idle window: admission is read-only with
     * respect to `last_activity_at` and `idle_expires_at`.
     *
     * @template T
     * @param \Closure(Database, AccountPrincipal): T $fn
     * @return T
     * @throws ApiException 401 AUTH_REQUIRED | AUTH_REVOKED | AUTH_EXPIRED,
     *                      403 PASSWORD_CHANGE_REQUIRED
     */
    public function withAccount(string $token, string $purpose, int $now,
                                bool $allowForcedChange,
                                callable $fn): mixed
    {
        $hash = self::hash($token);
        $located = $this->locate($hash);
        return $this->db->transaction(function (Database $tx) use (
            $hash, $located, $purpose, $now, $allowForcedChange, $fn
        ): mixed {
            $principal = $this->admitWithin($tx, $hash, $located, $purpose, $now,
                $allowForcedChange);
            return $fn($tx, $principal);
        });
    }

    /**
     * Admit, then lock and authorize ONE workspace inside the SAME transaction.
     *
     * The snapshot `$fn` receives is the state the tenant lock pinned. It is a
     * VALUE, never a capability: the moment the callback returns, the
     * transaction commits and the snapshot authorizes nothing — a later
     * mutation must be admitted again, against whatever the workspace looks
     * like then.
     *
     * A forced-change session is refused here (PASSWORD_CHANGE_REQUIRED) before
     * the workspace is even looked at: a workspace call is not self-service.
     *
     * @template T
     * @param \Closure(Database, AccountPrincipal, array<string, mixed>): T $fn
     * @return T
     * @throws ApiException 401 AUTH_* | 403 PASSWORD_CHANGE_REQUIRED |
     *                      ROLE_FORBIDDEN | 404 NOT_FOUND |
     *                      409 WORKSPACE_CONTEXT_STALE
     */
    public function withWorkspace(string $token, string $purpose, int $now,
                                  string $tenantId, int $expectedContextVersion,
                                  string $capability, callable $fn): mixed
    {
        $hash = self::hash($token);
        $located = $this->locate($hash);
        return $this->db->transaction(function (Database $tx) use (
            $hash, $located, $purpose, $now, $tenantId, $expectedContextVersion,
            $capability, $fn
        ): mixed {
            $principal = $this->admitWithin($tx, $hash, $located, $purpose, $now,
                false);
            // Inside this transaction, after the account and session locks:
            // Workspaces re-checks the live account status, the tenant and the
            // membership itself, so no earlier read can be smuggled in.
            $snapshot = $this->workspaces()->lockAuthorizedWithin($tx,
                $principal->accountId, $tenantId, $expectedContextVersion,
                $capability);
            return $fn($tx, $principal, $snapshot);
        });
    }

    /** The account id a token hash names, read OUTSIDE the write transaction. */
    private function locate(string $hash): string
    {
        $session = $this->db->queryOne(
            'SELECT id, account_id FROM account_sessions WHERE token_hash = ?',
            [$hash]);
        if ($session === null) {
            throw self::authRequired();
        }
        return (string) $session['account_id'];
    }

    /**
     * The locked recheck. Account first, then session, then the six conditions,
     * in a fixed order so two simultaneous refusals always answer alike.
     *
     * @throws ApiException AUTH_REQUIRED | AUTH_REVOKED | AUTH_EXPIRED |
     *                      PASSWORD_CHANGE_REQUIRED
     */
    private function admitWithin(Database $tx, string $hash, string $accountId,
                                 string $purpose, int $now,
                                 bool $allowForcedChange): AccountPrincipal
    {
        $account = $tx->queryOne(
            'SELECT id, login, display_name, status, must_change_password,'
            . ' security_version, platform_role FROM accounts WHERE id = ? FOR UPDATE',
            [$accountId]);
        if ($account === null) {
            throw self::authRequired();
        }
        $session = $tx->queryOne(
            'SELECT id, account_id, purpose, security_version, revoked_at,'
            . ' idle_expires_at, absolute_expires_at FROM account_sessions'
            . ' WHERE token_hash = ? FOR UPDATE', [$hash]);
        if ($session === null
            || (string) $session['account_id'] !== $accountId) {
            throw self::authRequired();
        }
        if (($account['status'] ?? '') !== Accounts::STATUS_ACTIVE) {
            throw self::authRevoked();
        }
        if ((int) $session['security_version']
            !== (int) $account['security_version']) {
            // A reset or a disable moves the account's version; every session
            // it left behind is stale by comparison alone, with no table scan.
            throw self::authRevoked();
        }
        if ((string) $session['purpose'] !== $purpose) {
            throw self::authRequired();
        }
        if ($session['revoked_at'] !== null) {
            throw self::authRevoked();
        }
        // STRICTLY after now: at exactly either deadline the session is over.
        if (self::at($session['idle_expires_at']) <= $now
            || self::at($session['absolute_expires_at']) <= $now) {
            throw self::authExpired();
        }
        if ((int) ($account['must_change_password'] ?? 0) === 1
            && !$allowForcedChange) {
            throw new ApiException(403, Codes::PASSWORD_CHANGE_REQUIRED,
                'the password must be changed before this request can be served');
        }

        return new AccountPrincipal(
            $accountId,
            (string) $session['purpose'],
            (string) ($account['display_name'] ?? ''),
            (string) ($account['platform_role'] ?? ''),
            (int) ($account['must_change_password'] ?? 0) === 1,
            (int) $account['security_version']
        );
    }

    // ------------------------------------------------------------------
    // Credential lifecycle
    // ------------------------------------------------------------------

    /**
     * Change the password behind a live session.
     *
     * The new password is validated and hashed OUTSIDE the transaction (never
     * while holding a row lock), then the account and the session are locked and
     * re-checked, then the CURRENT password is verified against the locked row.
     * Only then does one transaction replace the hash, clear
     * `must_change_password` and bump `security_version` — which invalidates
     * EVERY session this account holds, the caller's included, by version
     * comparison at admission. The physical `revoked_at` sweep is bounded to
     * this one account and to `REVOKE_SWEEP` rows: it is housekeeping, never
     * the authorization dependency.
     *
     * The result deliberately contains NO token. The caller must re-login; a
     * replacement session is never minted here, because silently replacing a
     * credential the change just invalidated would defeat the point of
     * invalidating it.
     *
     * @return array{reauthentication_required: bool, account_id: string,
     *   security_version: int}
     * @throws ApiException 401 AUTH_REQUIRED | AUTH_REVOKED | AUTH_EXPIRED,
     *                      422 VALIDATION_FAILED
     */
    public function changePassword(string $token, string $purpose, int $now,
                                   string $currentPassword,
                                   string $newPassword): array
    {
        $hash = self::hash($token);
        Accounts::validateNewPassword($newPassword);
        $newHash = Auth::hashPassword($newPassword);
        $accountId = $this->locate($hash);

        return $this->db->transaction(function (Database $tx) use (
            $hash, $accountId, $purpose, $now, $currentPassword, $newHash
        ): array {
            $account = $tx->queryOne(
                'SELECT id, status, password_hash, must_change_password,'
                . ' security_version FROM accounts WHERE id = ? FOR UPDATE',
                [$accountId]);
            if ($account === null) {
                throw self::authRequired();
            }
            $session = $tx->queryOne(
                'SELECT id, account_id, purpose, security_version, revoked_at,'
                . ' idle_expires_at, absolute_expires_at FROM account_sessions'
                . ' WHERE token_hash = ? FOR UPDATE', [$hash]);
            if ($session === null
                || (string) $session['account_id'] !== $accountId) {
                throw self::authRequired();
            }
            if ((string) $session['purpose'] !== $purpose) {
                throw self::authRequired();
            }
            if ((int) $session['security_version']
                !== (int) $account['security_version']) {
                throw self::authRevoked();
            }
            if ($session['revoked_at'] !== null) {
                throw self::authRevoked();
            }
            if (self::at($session['idle_expires_at']) <= $now
                || self::at($session['absolute_expires_at']) <= $now) {
                throw self::authExpired();
            }
            if (($account['status'] ?? '') !== Accounts::STATUS_ACTIVE) {
                throw self::authRevoked();
            }
            if (!password_verify($currentPassword,
                (string) $account['password_hash'])) {
                throw new ApiException(401, Codes::AUTH_REQUIRED,
                    self::LOGIN_FAILURE_MESSAGE);
            }

            $version = (int) $account['security_version'] + 1;
            $stamp = gmdate('Y-m-d H:i:s', $now);
            $tx->update('accounts', [
                'password_hash' => $newHash,
                'must_change_password' => 0,
                'security_version' => $version,
                'updated_at' => $stamp,
            ], 'id = ?', [$accountId]);
            // Bounded, account-scoped housekeeping only.
            $tx->execute('UPDATE account_sessions SET revoked_at = ?'
                . ' WHERE account_id = ? AND revoked_at IS NULL LIMIT '
                . self::REVOKE_SWEEP, [$stamp, $accountId]);
            $this->record($tx, 'password_changed', $accountId, $now);

            return [
                'reauthentication_required' => true,
                'account_id' => $accountId,
                'security_version' => $version,
            ];
        });
    }

    /**
     * Revoke EXACTLY the session this credential names, after checking the
     * purpose.
     *
     * A wrong-purpose token is refused before anything is written, so it can
     * never kill a session belonging to another purpose. A credential whose
     * session this module has already revoked answers SUCCESS with
     * `already_revoked = true`: the caller's intent is already true, the state
     * is idempotent, and turning a repeat into an error would make a retried
     * logout look like a failure. An EXPIRED session is still revocable —
     * revoking a known credential is always safe and never an oracle.
     *
     * Lock order is the module's own: the token hash is resolved OUTSIDE the
     * transaction, then the ACCOUNT row is locked, then the SESSION row, and
     * only then is the session re-read and re-checked. `record()` below inserts
     * into `account_security_events`, whose foreign keys reference `accounts`,
     * so this transaction must already hold the account row before it writes:
     * locking the session first would be a reachable AB-BA against
     * `admitWithin()` and `changePassword()` on the same account.
     *
     * @return array{revoked: bool, already_revoked: bool}
     * @throws ApiException 401 AUTH_REQUIRED
     */
    public function logout(string $token, string $purpose, int $now): array
    {
        $hash = self::hash($token);
        $accountId = $this->locate($hash);
        return $this->db->transaction(function (Database $tx) use (
            $hash, $accountId, $purpose, $now
        ): array {
            $account = $tx->queryOne(
                'SELECT id FROM accounts WHERE id = ? FOR UPDATE', [$accountId]);
            if ($account === null) {
                throw self::authRequired();
            }
            $session = $tx->queryOne(
                'SELECT id, account_id, purpose, revoked_at FROM account_sessions'
                . ' WHERE token_hash = ? FOR UPDATE', [$hash]);
            if ($session === null
                || (string) $session['account_id'] !== $accountId) {
                throw self::authRequired();
            }
            if ((string) $session['purpose'] !== $purpose) {
                throw self::authRequired();
            }
            $already = $session['revoked_at'] !== null;
            if (!$already) {
                $stamp = gmdate('Y-m-d H:i:s', $now);
                $tx->update('account_sessions',
                    ['revoked_at' => $stamp],
                    'id = ? AND revoked_at IS NULL', [(string) $session['id']]);
                $this->record($tx, 'logout', (string) $session['account_id'], $now);
            }
            return ['revoked' => true, 'already_revoked' => $already];
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
     * A security lifecycle event, written inside the SAME transaction as the
     * state change it describes (never through a best-effort logger, whose
     * write is swallowed on failure). Metadata only: no login, no password, no
     * hash, no token, no IP, no request payload.
     */
    private function record(Database $tx, string $eventType, string $accountId,
                            int $now): void
    {
        $tx->insert('account_security_events', [
            'id' => Ids::v4(),
            'actor_account_id' => $accountId,
            'subject_account_id' => $accountId,
            'tenant_id' => null,
            'actor_kind' => 'account',
            'event_type' => $eventType,
            'outcome' => 'success',
            'created_at' => gmdate('Y-m-d H:i:s', $now),
        ]);
    }
}
