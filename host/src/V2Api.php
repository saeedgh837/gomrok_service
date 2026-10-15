<?php
/**
 * V2Api.php — the exact `/v2` surface: five routes, and nothing else.
 *
 * Contract: Phase B Task 7. This module is a ROUTING and ADAPTATION layer over
 * the Task 4-6 services; it owns no business mutation of its own and exposes no
 * operator service. The five routes are the whole public surface:
 *
 *   POST /v2/auth/login            JSON login/password only
 *   GET  /v2/auth/me               live minimal account, passive, never renews
 *   POST /v2/auth/logout           revokes exactly this session
 *   POST /v2/auth/change-password  old/new, revokes every session
 *   GET  /v2/workspaces            live desktop membership page
 *
 * Design rules that are deliberately structural rather than conventional:
 *
 *   * VERSION FIRST. `isVersion2Request()` classifies the RAW request target
 *     before `Request::capture()` runs, so a v2 body is refused at 16 KiB
 *     instead of being buffered at the legacy import limit, and an apparent v2
 *     path is never allowed to fall through into the v1 handlers. A configured
 *     legacy `base_path` that collides with `/v2` is `CONFIG_INVALID` — a
 *     configuration error, not permission to serve v1 under a v2 name.
 *   * TRUSTED TRANSPORT ONLY. `assertTransport()` takes the SERVER's own TLS
 *     indication and the peer address. It has no forwarded-header parameter at
 *     all, so `X-Forwarded-Proto` cannot reach it. Only the guarded loopback
 *     CLI-server fixture adapter may pass `fixture=true`, and only for an exact
 *     `127.0.0.1`/`::1` peer; production always passes `false`.
 *   * NO BODY LOGGING. The constructor takes a `Log` that accepts only a fixed
 *     set of scalar fields; a request body, a header, a query string and a
 *     credential never reach it. Route labels come from the known-route table
 *     only, so user input is never written to a log line.
 *   * PURPOSE MISMATCH IS ITS OWN CODE. `AccountSessions` answers
 *     `AUTH_REQUIRED` for a wrong-purpose credential (a shared, credential-free
 *     refusal). At the HTTP edge the session is looked up by its own hash and a
 *     live row of another purpose is re-pointed to `SESSION_PURPOSE_MISMATCH`,
 *     which says exactly what happened without saying anything about the
 *     credential. An unknown, malformed or legacy token still answers
 *     `AUTH_REQUIRED`.
 */

declare(strict_types=1);

final class V2Api
{
    /** The only version prefix this class serves. */
    public const PREFIX = '/v2';

    /**
     * The v2 auth body cap. Deliberately NOT the legacy `max_body_bytes`: a
     * login body is a few hundred bytes, and an oversized one must be refused
     * from Content-Length alone rather than buffered at 16 MiB.
     */
    public const MAX_BODY_BYTES = 16384;

    /** Every route on this surface is a desktop-purpose session. */
    public const PURPOSE = AccountSessions::PURPOSE_DESKTOP;

    public const DEFAULT_PAGE_SIZE = 50;
    public const MAX_PAGE_SIZE = 100;

    /**
     * The complete route table. A path that exists under another verb answers
     * 405; a path that does not exist here answers 404 on every verb.
     *
     * @var array<string, list<string>>
     */
    private const ROUTES = [
        '/v2/auth/login' => ['POST'],
        '/v2/auth/me' => ['GET'],
        '/v2/auth/logout' => ['POST'],
        '/v2/auth/change-password' => ['POST'],
        '/v2/workspaces' => ['GET'],
    ];

    private readonly RateLimit $rateLimit;

    private ?AccountSessions $sessions = null;

    private ?Workspaces $workspaces = null;

    /**
     * @param array<string, mixed> $config the loaded host configuration; read
     *        here and never re-read from disk
     * @throws ApiException 500 CONFIG_INVALID when the legacy base path
     *         collides with the /v2 namespace
     */
    public function __construct(
        private readonly Database $db,
        private readonly Log $log,
        private readonly array $config
    ) {
        self::assertNoBasePathCollision($config);
        $rate = isset($config['rate_limit']) && is_array($config['rate_limit'])
            ? $config['rate_limit'] : [];
        $this->rateLimit = new RateLimit($rate);
    }

    // ------------------------------------------------------------------
    // Version classification and transport
    // ------------------------------------------------------------------

    /** The path component of a raw request target, query and fragment dropped. */
    public static function rawPath(string $requestUri): string
    {
        $end = strcspn($requestUri, "?#");
        return substr($requestUri, 0, $end);
    }

    /**
     * Does this RAW request target address `/v2`?
     *
     * Case-insensitive on purpose: `/V2/auth/me` must be answered by this
     * module (as a 404), never handed to the v1 route table. `/v2x` and
     * `/v20/...` are NOT version paths — they do not share the `/v2/` segment
     * boundary — so they keep their ordinary legacy answer.
     *
     * `%` right after the marker is treated as apparent: `/v2%2Fauth` is an
     * encoded separator smuggling itself into the version namespace, and it
     * must be refused here rather than decoded by someone downstream.
     */
    public static function isVersion2Request(string $requestUri): bool
    {
        $path = strtolower(self::rawPath($requestUri));
        if ($path === self::PREFIX) {
            return true;
        }
        return str_starts_with($path, self::PREFIX . '/')
            || str_starts_with($path, self::PREFIX . '%');
    }

    /**
     * A route label that can never carry user input: only a path already in
     * the known-route table is ever written, anything else is `<unknown>`.
     */
    public static function logLabel(string $method, string $requestUri): string
    {
        $path = self::rawPath($requestUri);
        return strtoupper($method) . ' '
            . (isset(self::ROUTES[$path]) ? $path : '<unknown>');
    }

    /** A known route name, or ''. Never derived from anything user-supplied. */
    public static function knownRoute(string $path): ?string
    {
        return isset(self::ROUTES[$path]) ? $path : null;
    }

    /**
     * The transport gate.
     *
     * @param string $remoteAddr the peer PHP itself observed
     * @param bool   $serverTls  the server's OWN indication that this request
     *                           arrived over TLS — not a header, not a proxy's
     *                           claim, and structurally unreachable from
     *                           `X-Forwarded-Proto` because no such parameter
     *                           exists here
     * @param bool   $fixture    ONLY the guarded CLI-server fixture adapter may
     *                           pass true, and only for an exact loopback peer
     * @throws ApiException 403 HTTPS_REQUIRED
     */
    public static function assertTransport(string $remoteAddr, bool $serverTls,
                                           bool $fixture = false): void
    {
        if ($fixture) {
            if ($remoteAddr !== '127.0.0.1' && $remoteAddr !== '::1') {
                throw new ApiException(403, Codes::HTTPS_REQUIRED,
                    'the fixture adapter serves an exact loopback peer only');
            }
            return;
        }
        if (!$serverTls) {
            throw new ApiException(403, Codes::HTTPS_REQUIRED,
                'this API is reachable only over HTTPS');
        }
    }

    /**
     * A configured legacy `base_path` inside the `/v2` namespace would make
     * one path mean two different things.
     *
     * @param array<string, mixed> $config
     * @throws ApiException 500 CONFIG_INVALID
     */
    public static function assertNoBasePathCollision(array $config): void
    {
        $raw = isset($config['base_path']) && is_string($config['base_path'])
            ? $config['base_path'] : '/v1';
        $base = strtolower(rtrim($raw, '/'));
        if ($base === '') {
            $base = '/v1';
        }
        if ($base === self::PREFIX
            || str_starts_with($base, self::PREFIX . '/')) {
            throw new ApiException(500, Codes::CONFIG_INVALID,
                'the configured legacy base_path collides with the /v2 routes');
        }
    }

    // ------------------------------------------------------------------
    // Dispatch
    // ------------------------------------------------------------------

    /**
     * Route one already-captured request.
     *
     * Nothing is emitted here: the caller receives `{status, body, headers}`
     * and writes it through the existing `Response::emit`, so the v2 edge and
     * the v1 edge share one writer, one security-header set and one log shape.
     *
     * @return array{status: int, body: array<string, mixed>,
     *               headers: array<string, string>}
     */
    public function dispatch(Request $request, int $now): array
    {
        try {
            return $this->route($request, $now);
        } catch (ApiException $error) {
            return self::failure(
                $error->status,
                Codes::isValid($error->errorCode) ? $error->errorCode
                    : Codes::SERVER_ERROR,
                $error->getMessage(),
                $request->requestId,
                $error->retryAfter
            );
        } catch (\PDOException $error) {
            unset($error);
            $this->log->line(['status' => 500, 'code' => Codes::DB_UNAVAILABLE,
                'level' => 'error']);
            return self::failure(500, Codes::DB_UNAVAILABLE,
                'the database is unavailable', $request->requestId);
        } catch (\Throwable $error) {
            // Class and location only: an exception message may quote a value.
            $this->log->line(['status' => 500, 'code' => Codes::SERVER_ERROR,
                'level' => 'error'] + Log::describe($error));
            return self::failure(500, Codes::SERVER_ERROR,
                'internal server error', $request->requestId);
        }
    }

    /**
     * @return array{status: int, body: array<string, mixed>,
     *               headers: array<string, string>}
     * @throws ApiException
     */
    private function route(Request $request, int $now): array
    {
        $path = $request->path;
        // Apparent, ambiguous or re-cased version paths are answered here and
        // never fall through to the v1 handlers.
        if (!self::isVersion2Request($request->path)
            || !self::isServable($path)
            || !isset(self::ROUTES[$path])) {
            throw new ApiException(404, Codes::HTTP_404, 'no such endpoint');
        }
        if (!in_array($request->method, self::ROUTES[$path], true)) {
            throw new ApiException(405, Codes::HTTP_405,
                'this endpoint does not accept the request method');
        }

        return match ($path) {
            '/v2/auth/login' => $this->login($request, $now),
            '/v2/auth/me' => $this->me($request, $now),
            '/v2/auth/logout' => $this->logout($request, $now),
            '/v2/auth/change-password' => $this->changePassword($request, $now),
            '/v2/workspaces' => $this->workspaces($request, $now),
            default => throw new ApiException(404, Codes::HTTP_404,
                'no such endpoint'),
        };
    }

    /**
     * One raw path is servable only when nothing about it is ambiguous: no
     * percent-encoding, no dot segment, no repeated separator, no backslash.
     * Refusing here means no downstream layer ever has to guess.
     */
    private static function isServable(string $path): bool
    {
        if ($path === '' || $path === '/' || $path === self::PREFIX) {
            return $path === self::PREFIX;
        }
        return !str_contains($path, '%')
            && !str_contains($path, '..')
            && !str_contains($path, '//')
            && !str_contains($path, '\\');
    }

    // ------------------------------------------------------------------
    // Routes
    // ------------------------------------------------------------------

    /**
     * POST /v2/auth/login — the desktop credential, JSON login/password only.
     *
     * `purpose`, `role`, `workspace`, `platform_role` and every other field
     * that is not part of this endpoint's contract are REFUSED, not ignored:
     * a silently dropped field is how a client ends up believing it asked for
     * something it never got.
     *
     * @throws ApiException
     */
    private function login(Request $request, int $now): array
    {
        $body = $request->jsonBody();
        self::onlyFields($body, ['login', 'password']);
        $login = $body['login'] ?? null;
        $password = $body['password'] ?? null;
        if (!is_string($login) || $login === ''
            || !is_string($password) || $password === '') {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'login and password are required');
        }
        return self::success($this->sessions()->loginDesktop(
            $login, $password, $request->remoteAddr, $now));
    }

    /**
     * GET /v2/auth/me — live account receipt, PASSIVE.
     *
     * Admission never writes `last_activity_at` or `idle_expires_at`, so a
     * polling client cannot keep a session alive by looking at it. A forced
     * change is allowed here: this IS the self-service set.
     *
     * @throws ApiException
     */
    private function me(Request $request, int $now): array
    {
        $token = self::requireToken($request);
        return self::success($this->withSession($token, fn (): array => $this
            ->sessions()
            ->withAccount($token, self::PURPOSE, $now, true,
                static fn (Database $tx, AccountPrincipal $principal): array => [
                    'account' => [
                        'id' => $principal->accountId,
                        'display_name' => $principal->displayName,
                        'platform_role' => $principal->platformRole,
                        'must_change_password' => $principal->mustChangePassword,
                    ],
                ])));
    }

    /**
     * POST /v2/auth/logout — revoke EXACTLY this session, never echo the token.
     *
     * @throws ApiException
     */
    private function logout(Request $request, int $now): array
    {
        $token = self::requireToken($request);
        // An empty body is legal; a JSON object body is legal too.
        $request->jsonBody(false);
        return self::success($this->withSession($token, fn (): array => $this
            ->sessions()->logout($token, self::PURPOSE, $now)));
    }

    /**
     * POST /v2/auth/change-password — old/new, forced-change allowed.
     *
     * The service revokes EVERY session this account holds and returns
     * `reauthentication_required`; no replacement token is minted here, because
     * silently replacing the credential that was just invalidated would defeat
     * invalidating it.
     *
     * @throws ApiException
     */
    private function changePassword(Request $request, int $now): array
    {
        $token = self::requireToken($request);
        $body = $request->jsonBody();
        self::onlyFields($body, ['old_password', 'new_password']);
        $current = $body['old_password'] ?? null;
        $fresh = $body['new_password'] ?? null;
        if (!is_string($current) || $current === ''
            || !is_string($fresh) || $fresh === '') {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'old_password and new_password are required');
        }
        $this->withSession($token, fn (): array => $this->sessions()
            ->changePassword($token, self::PURPOSE, $now, $current, $fresh));
        return self::success(['reauthentication_required' => true]);
    }

    /**
     * GET /v2/workspaces — live desktop membership, no forced change.
     *
     * The page is a VALUE read inside the admission transaction: it authorizes
     * nothing after it returns. Membership join plus the active/active-v2
     * tenant predicate is the authority, so a cursor — even one belonging to
     * another tenant — can only shift the window, never widen it.
     *
     * @throws ApiException
     */
    private function workspaces(Request $request, int $now): array
    {
        $token = self::requireToken($request);
        $limit = self::intQuery($request, 'limit', self::DEFAULT_PAGE_SIZE);
        $cursor = self::cursorQuery($request);
        $page = $this->withSession($token, fn (): array => $this->sessions()
            ->withAccount($token, self::PURPOSE, $now, false,
                fn (Database $tx,
                    AccountPrincipal $principal): array => $this->workspaceService()
                    ->listWithin($principal->accountId, $cursor, $limit)));
        return self::success([
            'items' => $page['items'],
            'next_cursor' => $page['next_id'],
            'page_size' => $limit,
        ]);
    }

    // ------------------------------------------------------------------
    // Adaptation helpers
    // ------------------------------------------------------------------

    /**
     * The bearer credential, or a 401. Checked BEFORE the body is read, so a
     * request without a session credential never reaches a body parser.
     *
     * @throws ApiException 401 AUTH_REQUIRED
     */
    private static function requireToken(Request $request): string
    {
        $token = $request->bearer();
        if ($token === '') {
            throw new ApiException(401, Codes::AUTH_REQUIRED,
                'a session credential is required for this endpoint');
        }
        return $token;
    }

    /**
     * Run one session-bound operation, re-pointing a WRONG-PURPOSE refusal.
     *
     * `AccountSessions` answers `AUTH_REQUIRED` for every credential that is
     * not valid for the request — an unknown token, a legacy token, a revoked
     * one and a wrong-purpose one all share it, deliberately, so the service
     * layer never becomes an oracle. At the HTTP edge the distinction is
     * available from one lookup the caller was going to make anyway, and it is
     * a distinction worth reporting: "this session belongs to another purpose"
     * is actionable, "not valid for this request" is not.
     *
     * An unknown token still answers `AUTH_REQUIRED` — the lookup finds no row.
     *
     * @template T
     * @param callable(): T $fn
     * @return T
     * @throws ApiException
     */
    private function withSession(string $token, callable $fn): mixed
    {
        try {
            return $fn();
        } catch (ApiException $error) {
            if ($error->errorCode !== Codes::AUTH_REQUIRED
                || !$this->issuedForAnotherPurpose($token)) {
                throw $error;
            }
            throw new ApiException(401, Codes::SESSION_PURPOSE_MISMATCH,
                'this session was issued for a different purpose');
        }
    }

    /**
     * Is there a live session row under this credential's OWN hash whose
     * purpose is not ours? Read-only, parameterised, and it never sees a
     * credential that has not already been shape-validated by the service.
     */
    private function issuedForAnotherPurpose(string $token): bool
    {
        if (preg_match('/\A[0-9a-f]{64}\z/D', $token) !== 1) {
            return false;
        }
        try {
            $row = $this->db->queryOne(
                'SELECT purpose FROM account_sessions WHERE token_hash = ?',
                [Tokens::hashOpaque($token)]);
        } catch (\Throwable $error) {
            // Never let the diagnostic swallow the original refusal.
            unset($error);
            return false;
        }
        return $row !== null && (string) $row['purpose'] !== self::PURPOSE;
    }

    /**
     * Reject any field outside this endpoint's contract.
     *
     * @param array<string, mixed> $body
     * @param list<string> $allowed
     * @throws ApiException 400 BAD_PAYLOAD
     */
    private static function onlyFields(array $body, array $allowed): void
    {
        foreach (array_keys($body) as $key) {
            if (!is_string($key) || !in_array($key, $allowed, true)) {
                throw new ApiException(400, Codes::BAD_PAYLOAD,
                    'the request body carries a field this endpoint does not accept');
            }
        }
    }

    /**
     * An integer page size in [1, 100]. No coercion: `limit=1e1`, `limit=abc`
     * and `limit[]` are client bugs and are refused rather than guessed at.
     *
     * @throws ApiException 400 BAD_PAYLOAD
     */
    private static function intQuery(Request $request, string $key,
                                     int $default): int
    {
        if (!array_key_exists($key, $request->query)) {
            return $default;
        }
        $raw = $request->query[$key];
        if (is_array($raw) || !is_string($raw) || !preg_match('/\A\d+\z/D', $raw)) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'the page size must be between 1 and ' . self::MAX_PAGE_SIZE);
        }
        $value = (int) $raw;
        if ($value < 1 || $value > self::MAX_PAGE_SIZE) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'the page size must be between 1 and ' . self::MAX_PAGE_SIZE);
        }
        return $value;
    }

    /**
     * The opaque page cursor: echoed back by the caller, validated as a
     * workspace identifier by the service, never treated as authority.
     *
     * @throws ApiException 400 VALIDATION_FAILED
     */
    private static function cursorQuery(Request $request): ?string
    {
        if (!array_key_exists('cursor', $request->query)) {
            return null;
        }
        $raw = $request->query['cursor'];
        if (is_array($raw) || !is_string($raw)) {
            throw new ApiException(400, Codes::VALIDATION_FAILED,
                'the page cursor must be identified by a UUID');
        }
        return $raw;
    }

    private function sessions(): AccountSessions
    {
        return $this->sessions ??= new AccountSessions($this->db,
            $this->rateLimit);
    }

    private function workspaceService(): Workspaces
    {
        return $this->workspaces ??= new Workspaces($this->db);
    }

    /**
     * A success envelope in the shape `Response::ok` would have produced.
     *
     * @param array<string, mixed> $payload
     * @return array{status: int, body: array<string, mixed>, headers: array{}}
     */
    private static function success(array $payload): array
    {
        $payload['ok'] = true;
        return ['status' => 200, 'body' => $payload, 'headers' => []];
    }

    /**
     * A failure envelope in the shape `Response::fail` would have produced.
     *
     * No header is ever added here beyond an optional `Retry-After`: no
     * `Set-Cookie`, no CORS header, and the shared writer supplies
     * `Cache-Control: no-store`.
     *
     * @return array{status: int, body: array<string, mixed>,
     *               headers: array<string, string>}
     */
    private static function failure(int $status, string $code, string $message,
                                    string $requestId,
                                    ?int $retryAfter = null): array
    {
        $headers = [];
        if ($retryAfter !== null) {
            $headers['Retry-After'] = (string) $retryAfter;
        }
        return [
            'status' => $status,
            'body' => [
                'ok' => false,
                'error' => [
                    'code' => $code,
                    'message' => $message,
                    'request_id' => $requestId,
                ],
            ],
            'headers' => $headers,
        ];
    }
}
