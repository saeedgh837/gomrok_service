<?php
/**
 * Auth.php — login, refresh rotation, enrolment and request authentication.
 *
 * Contract: lab/docs/CLOUD_SYNC_SPEC.md §5 (two credential families, lifetimes,
 * one-time-use refresh families, revocation), §7 (roles), §10.4 (Argon2id),
 * §10.5 (rate limiting).
 *
 * What this file guarantees
 *   * passwords are verified with Argon2id (PASSWORD_DEFAULT only when the
 *     build has no argon2 — spec §10.4), and never compared directly;
 *   * an unknown username and a wrong password answer identically, after the
 *     same amount of work;
 *   * a refresh token is used exactly once: the row is claimed with
 *     `used_at IS NULL`, so two concurrent refreshes cannot both win, and a
 *     second presentation of a consumed token revokes the whole family;
 *   * every access token is re-checked against the live row it names on every
 *     request, so setting sync_devices.status='revoked' or bumping
 *     token_version stops in-flight tokens immediately (spec §5);
 *   * the /v1 workspace gate is rechecked on every credential path here —
 *     tenant resolution, bearer validation and refresh rotation — so a token
 *     minted before a workspace moved to staging, was frozen, or moved to
 *     authoritative_v2 stops working at once, and the refusal never says which.
 */

declare(strict_types=1);

// The workspace/legacy-contract gate this module rechecks on every legacy
// credential path.
require_once __DIR__ . '/Workspaces.php';

/**
 * The identity an authenticated request carries.
 *
 * `role` is '' when no membership exists — every permission check then fails
 * closed rather than defaulting to something permissive.
 */
final class Principal
{
    public function __construct(
        public readonly string $kind,
        public readonly string $id,
        public readonly string $tenantId,
        public readonly string $role,
        public readonly string $userId,
        public readonly string $deviceId,
        public readonly string $sessionId,
        public readonly int $tokenVersion
    ) {
    }

    public function isUser(): bool
    {
        return $this->kind === Tokens::KIND_USER;
    }

    public function isDevice(): bool
    {
        return $this->kind === Tokens::KIND_DEVICE;
    }
}

final class Auth
{
    private readonly Tokens $tokens;
    private readonly RateLimit $rateLimit;

    /** @var array<string, mixed>|null */
    private ?array $tenantRow = null;

    /** A pre-computed Argon2id hash used only to equalise a failed lookup. */
    private static ?string $decoyHash = null;

    /**
     * @param array<string, mixed> $cfg
     */
    public function __construct(
        private readonly \Database $db,
        private readonly Log $log,
        private readonly array $cfg
    ) {
        $this->tokens = new Tokens((string) $cfg['jwt_secret']);
        $this->rateLimit = new RateLimit(
            is_array($cfg['rate_limit'] ?? null) ? $cfg['rate_limit'] : []
        );
    }

    public function tokens(): Tokens
    {
        return $this->tokens;
    }

    // ------------------------------------------------------------------
    // Configuration helpers
    // ------------------------------------------------------------------

    private function intOf(string $key, int $default): int
    {
        $value = $this->cfg[$key] ?? $default;
        return is_int($value) && $value > 0 ? $value : $default;
    }

    private function refreshWindow(string $kind): array
    {
        $prefix = $kind === Tokens::KIND_DEVICE ? 'device' : 'user';
        $idle = $this->intOf('refresh.' . $prefix . '_idle_seconds',
            $kind === Tokens::KIND_DEVICE ? 7776000 : 28800);
        $absolute = $this->intOf('refresh.' . $prefix . '_absolute_seconds',
            $kind === Tokens::KIND_DEVICE ? 31536000 : 86400);
        return [$idle, max($idle, $absolute)];
    }

    private function tenantSlug(): string
    {
        $slug = $this->cfg['tenant_slug'] ?? 'default';
        return is_string($slug) && $slug !== '' ? $slug : 'default';
    }

    /** The /v1 workspace gate, reused by every legacy credential path here. */
    private function workspaces(): Workspaces
    {
        return new Workspaces($this->db);
    }

    /**
     * The single shared tenant (spec §7).
     *
     * Resolution answers ONLY for a workspace that is still in service, live
     * and on the legacy contract. A staging, frozen or already
     * authoritative_v2 workspace is refused exactly like one that does not
     * exist, so a configured slug can never be used to discover what has
     * moved on.
     *
     * @throws ApiException 500 CONFIG_INVALID when no tenant has been created yet
     * @throws ApiException 404 NOT_FOUND when the resolved tenant is not /v1's
     * @return array<string, mixed>
     */
    public function tenant(): array
    {
        if ($this->tenantRow !== null) {
            return $this->tenantRow;
        }
        $row = $this->db->queryOne(
            'SELECT id, slug, name, status FROM tenants WHERE slug = ?',
            [$this->tenantSlug()]
        );
        if ($row === null) {
            throw new ApiException(500, Codes::CONFIG_INVALID,
                'the tenant does not exist — run bin/create-admin.php first');
        }
        // The gate: read-only, so no FOR UPDATE, but the same conditions.
        $this->workspaces()->lockLegacyWithin($this->db, (string) $row['id'], false);
        $this->tenantRow = $row;
        return $row;
    }

    public function tenantId(): string
    {
        return (string) $this->tenant()['id'];
    }

    // ------------------------------------------------------------------
    // Passwords (spec §10.4)
    // ------------------------------------------------------------------

    /** Argon2id when the build has it, PASSWORD_DEFAULT otherwise. */
    public static function passwordAlgo(): string|int
    {
        if (defined('PASSWORD_ARGON2ID')
            && in_array('argon2id', password_algos(), true)) {
            return PASSWORD_ARGON2ID;
        }
        return PASSWORD_DEFAULT;
    }

    public static function hashPassword(string $password): string
    {
        $hash = password_hash($password, self::passwordAlgo());
        if (!is_string($hash) || $hash === '') {
            throw new ApiException(500, Codes::SERVER_ERROR,
                'password hashing is unavailable on this PHP build');
        }
        return $hash;
    }

    /**
     * A throw-away hash so a lookup miss costs what a verify costs.
     * Computed once per process; it is never a user's credential.
     */
    private static function decoy(): string
    {
        if (self::$decoyHash === null) {
            self::$decoyHash = (string) password_hash(
                bin2hex(random_bytes(16)), self::passwordAlgo()
            );
        }
        return self::$decoyHash;
    }

    // ------------------------------------------------------------------
    // POST /v1/auth/login
    // ------------------------------------------------------------------

    /**
     * @param array<string, mixed> $body
     * @return array<string, mixed>
     * @throws ApiException
     */
    public function login(array $body, string $ip, string $requestId): array
    {
        $username = isset($body['username']) && is_string($body['username'])
            ? substr(trim($body['username']), 0, 64) : '';
        $password = isset($body['password']) && is_string($body['password'])
            ? $body['password'] : '';
        if ($username === '' || $password === '' || strlen($password) > 4096) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'username and password are required');
        }

        $now = gmdate('Y-m-d H:i:s');
        $this->rateLimit->check($this->db, $username, $ip, $now);

        $tenant = $this->tenant();
        $user = $this->db->queryOne(
            'SELECT id, username, password_hash, display_name,'
            . ' must_change_password, status, token_version'
            . ' FROM users WHERE tenant_id = ? AND username = ?'
            . ' AND deleted_at IS NULL LIMIT 2',
            [$tenant['id'], $username]
        );

        $ok = false;
        if ($user !== null && ($user['status'] ?? '') === 'active') {
            $ok = password_verify($password, (string) $user['password_hash']);
        } else {
            // Same work as a real verification, so a miss is not a probe.
            password_verify($password, self::decoy());
            if ($user !== null) {
                $this->log->line([
                    'request_id' => $requestId, 'action' => 'auth.login',
                    'outcome' => 'disabled', 'code' => Codes::AUTH_REQUIRED,
                ]);
            }
        }

        if (!$ok || $user === null) {
            $this->rateLimit->record($this->db, $tenant['id'], $username, $ip,
                'failure', $now);
            $this->log->line([
                'request_id' => $requestId, 'action' => 'auth.login',
                'outcome' => 'failure', 'code' => Codes::AUTH_REQUIRED,
                'tenant_id' => (string) $tenant['id'],
            ]);
            $this->log->audit($this->db, [
                'tenant_id' => (string) $tenant['id'],
                'action' => 'auth.login',
                'actor_type' => 'user',
                'actor_user_id' => $user !== null
                    ? (string) $user['id'] : null,
                'request_id' => $requestId,
                'ip' => $ip,
                'outcome' => 'failure',
                'metadata' => ['reason' => 'bad_credentials'],
            ]);
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'username or password is incorrect');
        }

        $role = $this->roleOf($tenant['id'], (string) $user['id']);
        if ($role === '') {
            $this->rateLimit->record($this->db, $tenant['id'], $username, $ip,
                'failure', $now);
            throw new ApiException(403, Codes::ROLE_FORBIDDEN,
                'the account has no role in this tenant');
        }

        $this->rateLimit->record($this->db, $tenant['id'], $username, $ip,
            'success', $now);
        $issued = $this->issue(Tokens::KIND_USER, $tenant['id'],
            (string) $user['id'], null, (int) $user['token_version'], null);

        $this->db->execute(
            'UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?',
            [$now, $now, (string) $user['id']]
        );

        $this->log->line([
            'request_id' => $requestId, 'action' => 'auth.login',
            'outcome' => 'success', 'tenant_id' => (string) $tenant['id'],
            'user_id' => (string) $user['id'],
        ]);
        $this->log->audit($this->db, [
            'tenant_id' => (string) $tenant['id'],
            'action' => 'auth.login',
            'actor_type' => 'user',
            'actor_user_id' => (string) $user['id'],
            'request_id' => $requestId,
            'ip' => $ip,
            'outcome' => 'ok',
            'metadata' => ['role' => $role],
        ]);

        return [
            'access_token' => $issued['access_token'],
            'refresh_token' => $issued['refresh_token'],
            'expires_in' => $issued['expires_in'],
            'must_change_password' => (int) $user['must_change_password'] === 1,
            'user' => [
                'username' => (string) $user['username'],
                'role' => $role,
            ],
            'tenant_id' => (string) $tenant['id'],
        ];
    }

    // ------------------------------------------------------------------
    // POST /v1/auth/refresh
    // ------------------------------------------------------------------

    /**
     * Rotate a refresh token. One-time-use per family (spec §5).
     *
     * @param array<string, mixed> $body
     * @return array<string, mixed>
     * @throws ApiException
     */
    public function refresh(array $body, string $ip, string $requestId): array
    {
        $token = isset($body['refresh_token']) && is_string($body['refresh_token'])
            ? trim($body['refresh_token']) : '';
        $kind = isset($body['kind']) && is_string($body['kind'])
            ? $body['kind'] : '';
        if ($token === '' || strlen($token) > 256
            || !in_array($kind, [Tokens::KIND_USER, Tokens::KIND_DEVICE], true)) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'refresh_token and kind are required');
        }
        $hash = Tokens::hashOpaque($token);
        $now = gmdate('Y-m-d H:i:s');
        $nowEpoch = time();

        try {
            $result = $this->db->transaction(function (\Database $tx) use (
                $hash, $kind, $now, $nowEpoch
            ): array {
                // Resolve the tenant WITHOUT a lock first, so the workspace
                // gate can be taken before the session row lock and this
                // module keeps one lock order (tenant, then what it authorises).
                $holder = $tx->queryOne(
                    'SELECT tenant_id FROM auth_sessions WHERE token_hash = ?',
                    [$hash]
                );
                if ($holder !== null) {
                    // A refresh token keeps working only while its workspace is
                    // still in service, live and on the legacy contract.
                    $this->workspaces()->lockLegacyWithin(
                        $tx, (string) $holder['tenant_id'], true);
                }
                $row = $tx->queryOne(
                    'SELECT * FROM auth_sessions WHERE token_hash = ? FOR UPDATE',
                    [$hash]
                );
                if ($row === null || ($row['kind'] ?? '') !== $kind) {
                    throw new ApiException(401, Codes::AUTH_REQUIRED,
                        'refresh token is not recognised');
                }
                if ($row['revoked_at'] !== null) {
                    throw new ApiException(401, Codes::AUTH_REVOKED,
                        'the token family has been revoked');
                }
                if ($row['used_at'] !== null) {
                    // Spec §5: a consumed token must never be accepted again.
                    // The family is revoked by the caller AFTER this
                    // transaction: a throw here would roll the fix back.
                    return $this->reuseResult($row, $kind);
                }
                if (strtotime((string) $row['idle_expires_at']) <= $nowEpoch
                    || strtotime((string) $row['absolute_expires_at']) <= $nowEpoch) {
                    throw new ApiException(401, Codes::AUTH_EXPIRED,
                        'refresh token has expired');
                }

                // Atomic claim: two concurrent refreshes of one token can
                // never both succeed.
                $claimed = $tx->execute(
                    'UPDATE auth_sessions SET used_at = ? WHERE id = ?'
                    . ' AND used_at IS NULL',
                    [$now, (string) $row['id']]
                );
                if ($claimed === 0) {
                    return $this->reuseResult($row, $kind);
                }

                $subject = $this->assertSubject($tx, $row);
                [$idle, $absolute] = $this->refreshWindow($kind);
                unset($absolute);
                $issued = $this->insertSession($tx, [
                    'tenant_id' => (string) $row['tenant_id'],
                    'family_id' => (string) $row['family_id'],
                    'kind' => $kind,
                    'user_id' => $subject['user_id'],
                    'sync_device_id' => $subject['sync_device_id'],
                    'parent_id' => (string) $row['id'],
                    'idle_expires_at' => gmdate('Y-m-d H:i:s', $nowEpoch + $idle),
                    // The absolute deadline never moves: it belongs to the
                    // family, not to one link of it (spec §5).
                    'absolute_expires_at' => (string) $row['absolute_expires_at'],
                ]);
                $tx->execute(
                    'UPDATE auth_sessions SET rotated_to = ? WHERE id = ?',
                    [$issued['id'], (string) $row['id']]
                );

                $access = $this->signAccess($issued['id'], $kind,
                    (string) $row['tenant_id'], (string) $subject['sub'],
                    (int) $subject['token_version']);
                return [
                    'outcome' => 'rotated',
                    'payload' => [
                        'access_token' => $access,
                        'refresh_token' => $issued['token'],
                        'expires_in' => $this->intOf('jwt_ttl_seconds', 900),
                        'tenant_id' => (string) $row['tenant_id'],
                    ],
                ];
            });
        } catch (ApiException $error) {
            $this->log->line([
                'request_id' => $requestId, 'action' => 'auth.refresh',
                'outcome' => 'failure', 'code' => $error->errorCode,
            ]);
            throw $error;
        }

        if (($result['outcome'] ?? '') === 'reuse') {
            // Committed outside the transaction above so the revocation cannot
            // be undone by the very rollback that detected the replay.
            $this->db->execute(
                'UPDATE auth_sessions SET revoked_at = ?, revoked_reason = ?'
                . ' WHERE family_id = ? AND revoked_at IS NULL',
                [$now, 'reuse_detected', (string) $result['family_id']]
            );
            $this->log->line([
                'request_id' => $requestId, 'action' => 'auth.refresh',
                'outcome' => 'reuse_detected', 'code' => Codes::AUTH_REVOKED,
                'tenant_id' => (string) $result['tenant_id'],
            ]);
            $this->log->audit($this->db, [
                'tenant_id' => (string) $result['tenant_id'],
                'action' => 'auth.refresh.reuse',
                'actor_type' => $kind,
                'actor_user_id' => $result['user_id'],
                'actor_device_id' => $result['device_id'],
                'request_id' => $requestId,
                'ip' => $ip,
                'outcome' => 'revoked',
                'metadata' => ['family' => (string) $result['family_id']],
            ]);
            throw new ApiException(401, Codes::AUTH_REVOKED,
                'refresh token reuse invalidated the token family');
        }

        /** @var array<string, mixed> $payload */
        $payload = $result['payload'];
        $this->log->line([
            'request_id' => $requestId, 'action' => 'auth.refresh',
            'outcome' => 'success', 'tenant_id' => $payload['tenant_id'],
        ]);
        return $payload;
    }

    /**
     * What a replayed refresh token must produce: the family-kill context.
     *
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function reuseResult(array $row, string $kind): array
    {
        return [
            'outcome' => 'reuse',
            'family_id' => (string) $row['family_id'],
            'tenant_id' => (string) $row['tenant_id'],
            'user_id' => $row['user_id'] ?? null,
            'device_id' => $row['sync_device_id'] ?? null,
            'kind' => $kind,
        ];
    }

    /**
     * Load the subject a session belongs to and prove it is still usable.
     *
     * @param array<string, mixed> $row
     * @return array{sub: string, user_id: ?string, sync_device_id: ?string,
     *               token_version: int}
     * @throws ApiException
     */
    private function assertSubject(\Database $tx, array $row): array
    {
        if (($row['kind'] ?? '') === Tokens::KIND_DEVICE) {
            $device = $tx->queryOne(
                'SELECT id, status, token_version, enrolled_by, deleted_at'
                . ' FROM sync_devices WHERE id = ? AND tenant_id = ?',
                [(string) $row['sync_device_id'], (string) $row['tenant_id']]
            );
            if ($device === null || $device['deleted_at'] !== null) {
                throw new ApiException(401, Codes::AUTH_REQUIRED,
                    'the device is not enrolled');
            }
            if (($device['status'] ?? '') !== 'active') {
                throw new ApiException(401, Codes::AUTH_REVOKED,
                    'the device has been revoked');
            }
            return [
                'sub' => (string) $device['id'],
                'user_id' => null,
                'sync_device_id' => (string) $device['id'],
                'token_version' => (int) $device['token_version'],
            ];
        }
        $user = $tx->queryOne(
            'SELECT id, status, token_version, deleted_at FROM users'
            . ' WHERE id = ? AND tenant_id = ?',
            [(string) $row['user_id'], (string) $row['tenant_id']]
        );
        if ($user === null || $user['deleted_at'] !== null
            || ($user['status'] ?? '') !== 'active') {
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'the account is not active');
        }
        return [
            'sub' => (string) $user['id'],
            'user_id' => (string) $user['id'],
            'sync_device_id' => null,
            'token_version' => (int) $user['token_version'],
        ];
    }

    // ------------------------------------------------------------------
    // POST /v1/auth/logout
    // ------------------------------------------------------------------

    /**
     * @return array<string, mixed>
     * @throws ApiException
     */
    public function logout(Principal $principal, string $requestId,
                           string $ip): array
    {
        $now = gmdate('Y-m-d H:i:s');
        // The family id is read first: MySQL refuses a subquery on the very
        // table an UPDATE is writing (error 1093).
        $familyId = $this->db->queryValue(
            'SELECT family_id FROM auth_sessions WHERE id = ?',
            [$principal->sessionId]
        );
        if (!is_string($familyId) || $familyId === '') {
            throw new ApiException(401, Codes::AUTH_REVOKED,
                'the session behind this token no longer exists');
        }
        $this->db->execute(
            'UPDATE auth_sessions SET revoked_at = ?, revoked_reason = ?'
            . ' WHERE family_id = ? AND revoked_at IS NULL',
            [$now, 'logout', $familyId]
        );
        $this->log->line([
            'request_id' => $requestId, 'action' => 'auth.logout',
            'outcome' => 'success', 'tenant_id' => $principal->tenantId,
            'user_id' => $principal->userId,
        ]);
        $this->log->audit($this->db, [
            'tenant_id' => $principal->tenantId,
            'action' => 'auth.logout',
            'actor_type' => 'user',
            'actor_user_id' => $principal->userId !== ''
                ? $principal->userId : null,
            'request_id' => $requestId,
            'ip' => $ip,
            'outcome' => 'ok',
        ]);
        return [];
    }

    // ------------------------------------------------------------------
    // POST /v1/sync-devices/enroll
    // ------------------------------------------------------------------

    /**
     * @param array<string, mixed> $body
     * @return array<string, mixed>
     * @throws ApiException
     */
    public function enroll(Principal $principal, array $body,
                           string $requestId, string $ip): array
    {
        if (!$principal->isUser()) {
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'a user access token is required to enrol a device');
        }
        Roles::requireEnroll($principal->role);

        $name = isset($body['name']) && is_string($body['name'])
            ? substr(trim($body['name']), 0, 120) : '';
        if ($name === '') {
            $name = 'device-' . substr(Ids::v4(), 0, 8);
        }

        $now = gmdate('Y-m-d H:i:s');
        $nowEpoch = time();
        $deviceId = Ids::v4();
        $familyId = Ids::v4();

        $token = $this->db->transaction(function (\Database $tx) use (
            $deviceId, $familyId, $name, $principal, $now, $nowEpoch
        ): string {
            $tx->insert('sync_devices', [
                'id' => $deviceId,
                'tenant_id' => $principal->tenantId,
                'name' => $name,
                'status' => 'active',
                'token_version' => 1,
                'created_at' => $now,
                'updated_at' => $now,
                'enrolled_by' => $principal->userId !== ''
                    ? $principal->userId : null,
            ]);
            [$idle, $absolute] = $this->refreshWindow(Tokens::KIND_DEVICE);
            $session = $this->insertSession($tx, [
                'tenant_id' => $principal->tenantId,
                'family_id' => $familyId,
                'kind' => Tokens::KIND_DEVICE,
                'user_id' => null,
                'sync_device_id' => $deviceId,
                'parent_id' => null,
                'idle_expires_at' => gmdate('Y-m-d H:i:s', $nowEpoch + $idle),
                'absolute_expires_at' => gmdate('Y-m-d H:i:s',
                    $nowEpoch + $absolute),
            ]);
            return $session['token'];
        });

        $this->log->line([
            'request_id' => $requestId, 'action' => 'device.enroll',
            'outcome' => 'success', 'tenant_id' => $principal->tenantId,
            'user_id' => $principal->userId, 'device_id' => $deviceId,
        ]);
        $this->log->audit($this->db, [
            'tenant_id' => $principal->tenantId,
            'action' => 'device.enroll',
            'actor_type' => 'user',
            'actor_user_id' => $principal->userId !== ''
                ? $principal->userId : null,
            'actor_device_id' => $deviceId,
            'entity' => 'sync_devices',
            'entity_id' => $deviceId,
            'request_id' => $requestId,
            'ip' => $ip,
            'outcome' => 'ok',
        ]);

        return [
            'sync_device_id' => $deviceId,
            'device_refresh_token' => $token,
            'tenant_id' => $principal->tenantId,
        ];
    }

    // ------------------------------------------------------------------
    // Request authentication
    // ------------------------------------------------------------------

    /**
     * Authenticate the `Authorization` bearer credential (spec §6).
     *
     * @throws ApiException 401
     */
    public function authenticate(Request $request): Principal
    {
        $jwt = $request->bearer('authorization');
        if ($jwt === '') {
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'a bearer access token is required');
        }
        return $this->principalFor($jwt);
    }

    /**
     * The optional user credential of spec §6 ("+user context if acting for a
     * user"): `X-Acting-User`. Returns null when the header is absent.
     *
     * @throws ApiException 401 when the header is present but unusable
     */
    public function actingUser(Request $request): ?Principal
    {
        $jwt = $request->bearer('x-acting-user');
        if ($jwt === '') {
            if (trim($request->header('x-acting-user')) !== '') {
                throw new ApiException(401, Codes::AUTH_REQUIRED,
                    'the acting user credential is malformed');
            }
            return null;
        }
        $principal = $this->principalFor($jwt);
        if (!$principal->isUser()) {
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'the acting user credential must be a user access token');
        }
        return $principal;
    }

    /**
     * Verify a compact JWS against the live rows it names.
     *
     * @throws ApiException 401
     */
    public function principalFor(string $jwt): Principal
    {
        $claims = $this->tokens->verify($jwt, time());
        $kind = (string) $claims['kind'];
        if (!in_array($kind, [Tokens::KIND_USER, Tokens::KIND_DEVICE], true)) {
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'access token has an unknown credential kind');
        }
        $audience = $kind === Tokens::KIND_DEVICE
            ? Tokens::AUDIENCE_DEVICE : Tokens::AUDIENCE_USER;
        if (($claims['aud'] ?? '') !== $audience) {
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'access token audience does not match its kind');
        }

        $sessionId = (string) $claims['sid'];
        $session = $this->db->queryOne(
            'SELECT id, tenant_id, kind, user_id, sync_device_id,'
            . ' absolute_expires_at, revoked_at, family_id'
            . ' FROM auth_sessions WHERE id = ?',
            [$sessionId]
        );
        if ($session === null || ($session['kind'] ?? '') !== $kind
            || $session['revoked_at'] !== null) {
            throw new ApiException(401, Codes::AUTH_REVOKED,
                'the session behind this token has been revoked');
        }
        if ($session['tenant_id'] !== $claims['tid']) {
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'access token tenant does not match its session');
        }
        if (strtotime((string) $session['absolute_expires_at']) <= time()) {
            throw new ApiException(401, Codes::AUTH_EXPIRED,
                'the session behind this token has expired');
        }

        // The tenant gate is rechecked on EVERY authenticated request, not once
        // at login: a token minted before a workspace moved to staging or to
        // authoritative_v2 must stop working immediately, and the refusal must
        // not reveal which of those happened.
        $this->workspaces()->lockLegacyWithin(
            $this->db, (string) $session['tenant_id'], false);

        if ($kind === Tokens::KIND_DEVICE) {
            $device = $this->db->queryOne(
                'SELECT id, status, token_version, enrolled_by, deleted_at'
                . ' FROM sync_devices WHERE id = ? AND tenant_id = ?',
                [(string) $claims['sub'], (string) $claims['tid']]
            );
            if ($device === null || $device['deleted_at'] !== null) {
                throw new ApiException(401, Codes::AUTH_REQUIRED,
                    'the device is not enrolled');
            }
            // Spec §5: revocation and token_version bump stop every in-flight
            // device token on the very next request, not at expiry.
            if (($device['status'] ?? '') !== 'active') {
                throw new ApiException(401, Codes::AUTH_REVOKED,
                    'the device has been revoked');
            }
            if ((int) $device['token_version'] !== (int) ($claims['tv'] ?? -1)) {
                throw new ApiException(401, Codes::AUTH_REVOKED,
                    'the device token version has been rotated');
            }
            if ($session['sync_device_id'] !== $device['id']) {
                throw new ApiException(401, Codes::AUTH_REQUIRED,
                    'access token does not belong to this device session');
            }
            $role = $device['enrolled_by'] !== null
                ? $this->roleOf((string) $claims['tid'], (string) $device['enrolled_by'])
                : '';
            $this->touchDevice((string) $device['id']);
            return new Principal(
                Tokens::KIND_DEVICE,
                (string) $device['id'],
                (string) $claims['tid'],
                $role,
                $device['enrolled_by'] !== null ? (string) $device['enrolled_by'] : '',
                (string) $device['id'],
                $sessionId,
                (int) $device['token_version']
            );
        }

        $user = $this->db->queryOne(
            'SELECT id, username, status, token_version, deleted_at,'
            . ' must_change_password FROM users WHERE id = ? AND tenant_id = ?',
            [(string) $claims['sub'], (string) $claims['tid']]
        );
        if ($user === null || $user['deleted_at'] !== null
            || ($user['status'] ?? '') !== 'active') {
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'the account is not active');
        }
        if ((int) $user['token_version'] !== (int) ($claims['tv'] ?? -1)) {
            throw new ApiException(401, Codes::AUTH_REVOKED,
                'the account token version has been rotated');
        }
        if ($session['user_id'] !== $user['id']) {
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'access token does not belong to this user session');
        }
        return new Principal(
            Tokens::KIND_USER,
            (string) $user['id'],
            (string) $claims['tid'],
            $this->roleOf((string) $claims['tid'], (string) $user['id']),
            (string) $user['id'],
            '',
            $sessionId,
            (int) $user['token_version']
        );
    }

    /**
     * Resolve the effective role of a request that carries both credentials.
     *
     * Spec §6 gives mutations "device access (+user context if acting for a
     * user)". With no user context the machine speaks with the authority of
     * whoever enrolled it, which is an admin by construction (spec §7 makes
     * enrolment admin-only); when a user context is present that user's role
     * is the one that is enforced, so a non-admin can never widen its own
     * rights by dropping the header.
     *
     * @param array{device: ?Principal, user: ?Principal} $context
     */
    public function effectiveRole(array $context): string
    {
        if (isset($context['user']) && $context['user'] !== null) {
            return $context['user']->role;
        }
        if (isset($context['device']) && $context['device'] !== null) {
            return $context['device']->role;
        }
        return '';
    }

    /**
     * The membership role of one user, or '' when there is none.
     * An unknown role fails every permission check (spec §7, fail closed).
     */
    public function roleOf(string $tenantId, string $userId): string
    {
        $role = $this->db->queryValue(
            'SELECT role FROM memberships WHERE tenant_id = ? AND user_id = ?',
            [$tenantId, $userId]
        );
        return is_string($role) && Roles::isKnown($role) ? $role : '';
    }

    // ------------------------------------------------------------------
    // Session helpers
    // ------------------------------------------------------------------

    /**
     * Create the refresh row of a new family and the access token beside it.
     *
     * @return array{access_token: string, refresh_token: string,
     *               expires_in: int, id: string}
     * @throws ApiException
     */
    private function issue(string $kind, string $tenantId, string $subjectId,
                           ?string $deviceId, int $tokenVersion,
                           ?string $familyId = null): array
    {
        [$idle, $absolute] = $this->refreshWindow($kind);
        $nowEpoch = time();
        $issued = $this->db->transaction(function (\Database $tx) use (
            $kind, $tenantId, $subjectId, $deviceId, $idle, $absolute,
            $nowEpoch, $familyId
        ): array {
            $session = $this->insertSession($tx, [
                'tenant_id' => $tenantId,
                'family_id' => $familyId ?? Ids::v4(),
                'kind' => $kind,
                'user_id' => $kind === Tokens::KIND_USER ? $subjectId : null,
                'sync_device_id' => $deviceId,
                'parent_id' => null,
                'idle_expires_at' => gmdate('Y-m-d H:i:s', $nowEpoch + $idle),
                'absolute_expires_at' => gmdate('Y-m-d H:i:s',
                    $nowEpoch + $absolute),
            ]);
            return $session;
        });
        $access = $this->signAccess($issued['id'], $kind, $tenantId,
            $subjectId, $tokenVersion);
        $this->pruneSessions();
        return [
            'id' => $issued['id'],
            'access_token' => $access,
            'refresh_token' => $issued['token'],
            'expires_in' => $this->intOf('jwt_ttl_seconds', 900),
        ];
    }

    /**
     * Insert one auth_sessions row and return its id and plaintext token.
     * The plaintext exists only in the return value — the row stores sha256.
     *
     * @param array<string, mixed> $fields
     * @return array{id: string, token: string}
     */
    private function insertSession(\Database $tx, array $fields): array
    {
        $token = Tokens::newOpaque();
        $now = gmdate('Y-m-d H:i:s');
        $id = Ids::v4();
        $tx->insert('auth_sessions', [
            'id' => $id,
            'tenant_id' => $fields['tenant_id'],
            'family_id' => $fields['family_id'],
            'kind' => $fields['kind'],
            'token_hash' => Tokens::hashOpaque($token),
            'user_id' => $fields['user_id'],
            'sync_device_id' => $fields['sync_device_id'],
            'parent_id' => $fields['parent_id'],
            'issued_at' => $now,
            'idle_expires_at' => $fields['idle_expires_at'],
            'absolute_expires_at' => $fields['absolute_expires_at'],
            'used_at' => null,
            'revoked_at' => null,
            'revoked_reason' => '',
            'rotated_to' => null,
            'created_at' => $now,
        ]);
        return ['id' => $id, 'token' => $token];
    }

    private function signAccess(string $sessionId, string $kind,
                                string $tenantId, string $subjectId,
                                int $tokenVersion): string
    {
        $now = time();
        $ttl = $this->intOf('jwt_ttl_seconds', 900);
        return $this->tokens->sign([
            'sub' => $subjectId,
            'aud' => $kind === Tokens::KIND_DEVICE
                ? Tokens::AUDIENCE_DEVICE : Tokens::AUDIENCE_USER,
            'kind' => $kind,
            'tid' => $tenantId,
            'tv' => $tokenVersion,
            'sid' => $sessionId,
            'iat' => $now,
            'nbf' => $now,
            'exp' => $now + $ttl,
        ]);
    }

    /** Refresh `last_seen_at` / `last_auth_at` at most once a minute. */
    private function touchDevice(string $deviceId): void
    {
        try {
            $this->db->execute(
                'UPDATE sync_devices SET last_seen_at = ?, last_auth_at = ?'
                . ' WHERE id = ? AND (last_seen_at IS NULL'
                . ' OR last_seen_at < ?)',
                [
                    gmdate('Y-m-d H:i:s'),
                    gmdate('Y-m-d H:i:s'),
                    $deviceId,
                    gmdate('Y-m-d H:i:s', time() - 60),
                ]
            );
        } catch (\Throwable $error) {
            // Bookkeeping only; it must never fail an authenticated request.
            unset($error);
        }
    }

    /** Housekeeping: drop sessions whose absolute deadline passed long ago. */
    private function pruneSessions(): void
    {
        static $last = 0;
        if (time() - $last < 3600) {
            return;
        }
        $last = time();
        try {
            $this->db->delete('auth_sessions', 'absolute_expires_at < ?',
                [gmdate('Y-m-d H:i:s', time() - 86400 * 7)]);
        } catch (\Throwable $error) {
            unset($error);
        }
    }
}
