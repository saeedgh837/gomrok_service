<?php
/** Independent platform credentials; these principals never authorize CRM access. */
declare(strict_types=1);

final class OperatorPrincipal
{
    public function __construct(
        public readonly string $operatorId,
        public readonly string $sessionId,
        public readonly int $tokenVersion
    ) {
    }
}

final class OperatorAuth
{
    public const KIND = 'operator';
    public const AUDIENCE = 'platform';
    private readonly Tokens $tokens;
    private readonly RateLimit $rateLimit;
    private static ?string $decoyHash = null;

    public function __construct(
        private readonly Database $db,
        private readonly Log $log,
        private readonly array $cfg
    ) {
        $this->tokens = new Tokens((string) $cfg['jwt_secret']);
        $this->rateLimit = new RateLimit($cfg['rate_limit'] ?? []);
    }

    /** Host-controlled creation. Bootstrap and CLI share a durable singleton lock. */
    public static function create(Database $db, string $username, string $hash,
                                  bool $bootstrapOnly = false): array
    {
        $name = Workspace::canonical($username);
        return $db->transaction(function (Database $tx) use ($name, $hash, $bootstrapOnly): array {
            $gate = $tx->queryOne('SELECT bootstrap_consumed FROM platform_operator_state'
                . ' WHERE id = 1 FOR UPDATE');
            if ($gate === null || ($bootstrapOnly && ((int) $gate['bootstrap_consumed'] !== 0
                || (int) $tx->queryValue('SELECT COUNT(*) FROM platform_operators') !== 0))) {
                throw new ApiException(403, Codes::PERMISSION_DENIED, 'operator bootstrap is closed');
            }
            $id = Ids::v4();
            $now = gmdate('Y-m-d H:i:s');
            $tx->execute('INSERT INTO platform_operators'
                . ' (id, login_name, password_hash, enabled, token_version, created_at, updated_at)'
                . ' VALUES (?, ?, ?, 1, 1, ?, ?)', [$id, $name, $hash, $now, $now]);
            $tx->execute('UPDATE platform_operator_state SET bootstrap_consumed = 1 WHERE id = 1');
            return ['operator_id' => $id, 'username' => $name];
        });
    }

    /** Host-controlled exact-name recovery, never a customer promotion or activation. */
    public static function rotatePassword(Database $db, string $username, string $hash): array
    {
        $name = Workspace::canonical($username);
        return $db->transaction(function (Database $tx) use ($name, $hash): array {
            $row = $tx->queryOne('SELECT id FROM platform_operators WHERE login_name = ? FOR UPDATE', [$name]);
            if ($row === null) {
                throw new ApiException(403, Codes::PERMISSION_DENIED, 'the exact operator must already exist');
            }
            $now = gmdate('Y-m-d H:i:s');
            $tx->execute('UPDATE platform_operators SET password_hash = ?, token_version = token_version + 1,'
                . ' updated_at = ? WHERE id = ?', [$hash, $now, $row['id']]);
            $tx->execute('UPDATE operator_sessions SET revoked_at = ?, revoked_reason = ?'
                . ' WHERE operator_id = ? AND revoked_at IS NULL', [$now, 'password_rotated', $row['id']]);
            return ['operator_id' => $row['id'], 'username' => $name];
        });
    }

    public function login(array $body, string $ip, string $requestId): array
    {
        $username = is_string($body['username'] ?? null) ? $body['username'] : '';
        $password = is_string($body['password'] ?? null) ? $body['password'] : '';
        if ($username === '' || strlen($username) > 256 || $password === '' || strlen($password) > 4096) {
            throw new ApiException(400, Codes::BAD_PAYLOAD, 'username and password are required');
        }
        $account = 'operator:' . hash('sha256', strtolower(trim($username)));
        $now = gmdate('Y-m-d H:i:s');
        $this->rateLimit->check($this->db, $account, $ip, $now);
        try {
            $name = Workspace::canonical($username);
        } catch (ApiException $error) {
            $name = '';
        }
        $row = $this->db->queryOne('SELECT id, password_hash, enabled, token_version'
            . ' FROM platform_operators WHERE login_name = ?', [$name]);
        if (self::$decoyHash === null) {
            self::$decoyHash = Auth::hashPassword(Tokens::newOpaque());
        }
        $valid = Auth::verifyPassword($password, $row !== null
            ? (string) $row['password_hash'] : self::$decoyHash);
        if (!$valid || $row === null || (int) $row['enabled'] !== 1) {
            $this->rateLimit->record($this->db, Workspace::PLATFORM_TENANT,
                $account, $ip, 'failure', $now);
            $this->audit('operator.login', null, $requestId, $ip, 'failure');
            throw new ApiException(401, Codes::AUTH_REQUIRED, 'operator credentials are not valid');
        }
        $payload = $this->db->transaction(function (Database $tx) use ($row): array {
            $current = $this->lockOperator($tx, (string) $row['id']);
            if ((int) $current['token_version'] !== (int) $row['token_version']) {
                throw new ApiException(401, Codes::AUTH_REVOKED, 'operator credential changed');
            }
            $session = $this->insertSession($tx, (string) $row['id']);
            $tx->execute('UPDATE platform_operators SET last_login_at = ? WHERE id = ?',
                [gmdate('Y-m-d H:i:s'), $row['id']]);
            return $this->payload($session, (int) $current['token_version']);
        });
        $this->rateLimit->record($this->db, Workspace::PLATFORM_TENANT,
            $account, $ip, 'success', $now);
        $this->audit('operator.login', (string) $row['id'], $requestId, $ip, 'ok');
        return $payload;
    }

    public function authenticate(Request $request): OperatorPrincipal
    {
        return $this->principalFor($request->bearer('authorization'));
    }

    public function principalFor(string $jwt): OperatorPrincipal
    {
        $claims = $this->tokens->verify($jwt, time());
        if ($claims['kind'] !== self::KIND || $claims['aud'] !== self::AUDIENCE
            || $claims['tid'] !== Workspace::PLATFORM_TENANT
            || !Ids::isUuid($claims['sub']) || !Ids::isUuid($claims['sid'])
            || !is_int($claims['ver'] ?? null)) {
            throw new ApiException(401, Codes::AUTH_REQUIRED, 'an operator credential is required');
        }
        $row = $this->db->queryOne('SELECT o.token_version, o.enabled, s.revoked_at,'
            . ' s.absolute_expires_at FROM platform_operators o'
            . ' JOIN operator_sessions s ON s.operator_id = o.id'
            . ' WHERE o.id = ? AND s.id = ?', [$claims['sub'], $claims['sid']]);
        if ($row === null || (int) $row['enabled'] !== 1 || $row['revoked_at'] !== null
            || (int) $row['token_version'] !== $claims['ver']
            || strtotime($row['absolute_expires_at']) <= time()) {
            throw new ApiException(401, Codes::AUTH_REVOKED, 'operator credential is no longer active');
        }
        return new OperatorPrincipal($claims['sub'], $claims['sid'], $claims['ver']);
    }

    public function refresh(array $body, string $ip, string $requestId): array
    {
        $token = is_string($body['refresh_token'] ?? null) ? $body['refresh_token'] : '';
        if ($token === '' || strlen($token) > 256) {
            throw new ApiException(400, Codes::BAD_PAYLOAD, 'refresh_token is required');
        }
        $hash = Tokens::hashOpaque($token);
        // Resolve only the lock target here; all authority is re-read under locks.
        $candidate = $this->db->queryOne('SELECT operator_id FROM operator_sessions WHERE token_hash = ?', [$hash]);
        if ($candidate === null) {
            throw new ApiException(401, Codes::AUTH_REQUIRED, 'operator refresh token is not recognised');
        }
        $result = $this->db->transaction(function (Database $tx) use ($hash, $candidate): array {
            $operator = $this->lockOperator($tx, (string) $candidate['operator_id']);
            $row = $tx->queryOne('SELECT * FROM operator_sessions WHERE token_hash = ? FOR UPDATE', [$hash]);
            if ($row === null || $row['operator_id'] !== $candidate['operator_id'] || $row['revoked_at'] !== null) {
                throw new ApiException(401, Codes::AUTH_REVOKED, 'operator session has been revoked');
            }
            if ($row['used_at'] !== null) {
                $this->revokeFamily($tx, (string) $row['family_id'], 'reuse_detected');
                return ['reuse' => true]; // Commit revocation before the rejection.
            }
            if (strtotime($row['idle_expires_at']) <= time()
                || strtotime($row['absolute_expires_at']) <= time()) {
                throw new ApiException(401, Codes::AUTH_EXPIRED, 'operator session has expired');
            }
            $next = $this->insertSession($tx, (string) $row['operator_id'], $row);
            $tx->execute('UPDATE operator_sessions SET used_at = ?, rotated_to = ? WHERE id = ?',
                [gmdate('Y-m-d H:i:s'), $next['id'], $row['id']]);
            return ['payload' => $this->payload($next, (int) $operator['token_version'])];
        });
        if ($result['reuse'] ?? false) {
            $this->audit('operator.refresh.reuse', (string) $candidate['operator_id'], $requestId, $ip, 'revoked');
            throw new ApiException(401, Codes::AUTH_REVOKED, 'operator refresh reuse revoked the family');
        }
        return $result['payload'];
    }

    public function logout(OperatorPrincipal $principal, string $requestId, string $ip): array
    {
        $this->db->transaction(function (Database $tx) use ($principal): void {
            $this->lockOperator($tx, $principal->operatorId);
            $row = $tx->queryOne('SELECT family_id FROM operator_sessions'
                . ' WHERE id = ? AND operator_id = ? FOR UPDATE', [$principal->sessionId, $principal->operatorId]);
            if ($row === null) {
                throw new ApiException(401, Codes::AUTH_REVOKED, 'operator session is unavailable');
            }
            $this->revokeFamily($tx, $row['family_id'], 'logout');
        });
        $this->audit('operator.logout', $principal->operatorId, $requestId, $ip, 'ok');
        return ['logged_out' => true];
    }

    private function lockOperator(Database $tx, string $id): array
    {
        $row = $tx->queryOne('SELECT token_version, enabled FROM platform_operators WHERE id = ? FOR UPDATE', [$id]);
        if ($row === null || (int) $row['enabled'] !== 1) {
            throw new ApiException(401, Codes::AUTH_REVOKED, 'operator is not active');
        }
        return $row;
    }

    private function insertSession(Database $tx, string $operatorId, ?array $parent = null): array
    {
        $now = time();
        $window = $this->cfg['operator_refresh'] ?? [];
        $idle = max(60, (int) ($window['idle_seconds'] ?? 3600));
        $absolute = $parent['absolute_expires_at'] ?? gmdate('Y-m-d H:i:s', $now + max($idle, (int) ($window['absolute_seconds'] ?? 28800)));
        $row = ['id' => Ids::v4(), 'operator_id' => $operatorId,
            'family_id' => $parent['family_id'] ?? Ids::v4(), 'token' => Tokens::newOpaque(),
            'absolute_expires_at' => $absolute];
        $tx->execute('INSERT INTO operator_sessions (id, operator_id, family_id, token_hash, parent_id,'
            . ' issued_at, idle_expires_at, absolute_expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [$row['id'], $operatorId, $row['family_id'], Tokens::hashOpaque($row['token']),
                $parent['id'] ?? null, gmdate('Y-m-d H:i:s', $now),
                gmdate('Y-m-d H:i:s', min($now + $idle, strtotime($absolute))), $absolute, gmdate('Y-m-d H:i:s', $now)]);
        return $row;
    }

    private function payload(array $session, int $version): array
    {
        $now = time();
        $ttl = min(900, max(60, (int) ($this->cfg['jwt_ttl_seconds'] ?? 900)));
        return ['access_token' => $this->tokens->sign([
            'sub' => $session['operator_id'], 'tid' => Workspace::PLATFORM_TENANT,
            'sid' => $session['id'], 'kind' => self::KIND, 'aud' => self::AUDIENCE,
            'ver' => $version, 'iat' => $now, 'nbf' => $now, 'exp' => min($now + $ttl, strtotime($session['absolute_expires_at'])),
        ]), 'refresh_token' => $session['token'], 'expires_in' => min($ttl, strtotime($session['absolute_expires_at']) - $now),
            'operator_id' => $session['operator_id']];
    }

    private function revokeFamily(Database $tx, string $family, string $reason): void
    {
        $tx->execute('UPDATE operator_sessions SET revoked_at = ?, revoked_reason = ?'
            . ' WHERE family_id = ? AND revoked_at IS NULL', [gmdate('Y-m-d H:i:s'), $reason, $family]);
    }

    private function audit(string $action, ?string $operatorId, string $requestId, string $ip, string $outcome): void
    {
        $this->log->audit($this->db, ['tenant_id' => Workspace::PLATFORM_TENANT,
            'actor_type' => self::KIND, 'action' => $action, 'request_id' => $requestId,
            'ip' => $ip, 'outcome' => $outcome, 'metadata' => ['operator_id' => $operatorId]]);
    }
}
