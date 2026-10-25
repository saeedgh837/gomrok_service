<?php
/**
 * Http.php — request parsing, the error envelope and the response writer.
 *
 * Contract: lab/docs/CLOUD_SYNC_SPEC.md §6 (envelope), §10.6 (body size cap,
 * strict Content-Type, prepared inputs), §10.7 (CORS closed), §2 (HTTPS is
 * refused unless native TLS or an explicitly trusted proxy confirms HTTPS).
 *
 * Every failure leaves this module as
 *   {"ok":false,"error":{"code","message","request_id"}}
 * where `code` matches §15.8's grammar — `^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$` and
 * at most 40 characters — and `message` is operator text for server logs. The
 * client never displays it, so nothing here may put a password, a token, a
 * national id, a phone number or a name into one.
 */

declare(strict_types=1);

final class ApiException extends RuntimeException
{
    public function __construct(
        public readonly int $status,
        public readonly string $errorCode,
        string $message,
        public readonly ?int $retryAfter = null,
        public readonly ?array $payload = null
    ) {
        parent::__construct($message);
    }
}

final class Codes
{
    /** Grammar of spec §15.8 — asserted on every code before it is sent. */
    public const PATTERN = '/^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$/';
    public const MAX_LENGTH = 40;

    public const BAD_PAYLOAD = 'BAD_PAYLOAD';
    public const HTTP_400 = 'HTTP_400';
    public const HTTP_404 = 'HTTP_404';
    public const HTTP_405 = 'HTTP_405';
    public const HTTP_413 = 'HTTP_413';
    public const HTTP_415 = 'HTTP_415';
    public const HTTP_429 = 'HTTP_429';
    public const HTTP_500 = 'HTTP_500';
    public const UNSUPPORTED_MEDIA_TYPE = 'UNSUPPORTED_MEDIA_TYPE';
    public const PAYLOAD_TOO_LARGE = 'PAYLOAD_TOO_LARGE';
    public const AUTH_REQUIRED = 'AUTH_REQUIRED';
    public const AUTH_EXPIRED = 'AUTH_EXPIRED';
    public const AUTH_REVOKED = 'AUTH_REVOKED';
    public const ROLE_FORBIDDEN = 'ROLE_FORBIDDEN';
    public const PERMISSION_DENIED = 'PERMISSION_DENIED';
    public const RATE_LIMITED = 'RATE_LIMITED';
    public const NOT_FOUND = 'NOT_FOUND';
    public const VERSION_CONFLICT = 'VERSION_CONFLICT';
    public const DATASET_NOT_EMPTY = 'DATASET_NOT_EMPTY';
    public const IMPORT_CONFLICT = 'IMPORT_CONFLICT';
    public const VALIDATION_FAILED = 'VALIDATION_FAILED';
    public const DEVICE_REVOKED = 'DEVICE_REVOKED';
    public const HTTPS_REQUIRED = 'HTTPS_REQUIRED';
    public const CONFIG_INVALID = 'CONFIG_INVALID';
    public const DB_UNAVAILABLE = 'DB_UNAVAILABLE';
    public const SERVER_ERROR = 'SERVER_ERROR';
    public const IDEMPOTENCY_MISMATCH = 'IDEMPOTENCY_MISMATCH';
    public const DATASET_EPOCH_MISMATCH = 'DATASET_EPOCH_MISMATCH';

    /** Every code this package may emit — one list, one grammar check. */
    public const ALL = [
        self::BAD_PAYLOAD, self::HTTP_400, self::HTTP_404, self::HTTP_405,
        self::HTTP_413, self::HTTP_415, self::HTTP_429, self::HTTP_500,
        self::UNSUPPORTED_MEDIA_TYPE, self::PAYLOAD_TOO_LARGE,
        self::AUTH_REQUIRED, self::AUTH_EXPIRED, self::AUTH_REVOKED,
        self::ROLE_FORBIDDEN, self::PERMISSION_DENIED, self::RATE_LIMITED,
        self::NOT_FOUND, self::VERSION_CONFLICT, self::DATASET_NOT_EMPTY,
        self::IMPORT_CONFLICT, self::VALIDATION_FAILED, self::DEVICE_REVOKED,
        self::HTTPS_REQUIRED, self::CONFIG_INVALID, self::DB_UNAVAILABLE,
        self::SERVER_ERROR, self::IDEMPOTENCY_MISMATCH,
        self::DATASET_EPOCH_MISMATCH,
    ];

    public static function isValid(string $code): bool
    {
        return strlen($code) <= self::MAX_LENGTH
            && preg_match(self::PATTERN, $code) === 1;
    }

    /**
     * @throws InvalidArgumentException when a hand-written code breaks §15.8
     */
    public static function assertValid(string $code): string
    {
        if (!self::isValid($code)) {
            throw new InvalidArgumentException(
                'error code does not match the spec §15.8 grammar: ' . $code
            );
        }
        return $code;
    }

    private function __construct()
    {
    }
}

final class Request
{
    public const MAX_BODY_BYTES = 1048576;

    /**
     * @param array<string, mixed> $query
     * @param array<string, string> $headers lower-cased names
     */
    public function __construct(
        public readonly string $method,
        public readonly string $path,
        public readonly array $query,
        public readonly array $headers,
        public readonly string $rawBody,
        public readonly string $remoteAddr,
        public readonly string $requestId
    ) {
    }

    /**
     * Build the request from PHP's superglobals.
     *
     * The body is read exactly once, after the size cap has rejected anything
     * larger than the route allows — an oversized payload is refused from
     * Content-Length alone and never buffered (spec §10.6).
     *
     * @param array<string, mixed> $server
     * @param array<string, mixed> $query
     * @throws ApiException
     */
    public static function capture(array $server, array $query,
                                   int $maxBodyBytes): self
    {
        $uri = isset($server['REQUEST_URI']) && is_string($server['REQUEST_URI'])
            ? $server['REQUEST_URI'] : '/';
        $path = (string) parse_url($uri, PHP_URL_PATH);
        if ($path === '' || $path === false) {
            $path = '/';
        }
        $method = strtoupper(isset($server['REQUEST_METHOD'])
            && is_string($server['REQUEST_METHOD'])
            ? $server['REQUEST_METHOD'] : 'GET');

        $headers = [];
        foreach ($server as $key => $value) {
            if (!is_string($value)) {
                continue;
            }
            if (strncmp($key, 'HTTP_', 5) === 0) {
                $headers[strtolower(str_replace('_', '-', substr($key, 5)))] = $value;
            }
        }
        if (isset($server['CONTENT_TYPE']) && is_string($server['CONTENT_TYPE'])) {
            $headers['content-type'] = $server['CONTENT_TYPE'];
        }
        if (isset($server['CONTENT_LENGTH'])
            && is_string($server['CONTENT_LENGTH'])) {
            $headers['content-length'] = $server['CONTENT_LENGTH'];
        }

        $length = $headers['content-length'] ?? '';
        if ($length !== '' && (!ctype_digit($length) || (int) $length < 0)) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'content-length is not a non-negative integer');
        }
        $size = $length === '' ? 0 : (int) $length;
        if ($size > $maxBodyBytes) {
            throw new ApiException(413, Codes::PAYLOAD_TOO_LARGE,
                'request body exceeds the configured limit');
        }

        $body = '';
        if ($method === 'POST' || $method === 'PUT' || $method === 'PATCH') {
            $raw = file_get_contents('php://input');
            if ($raw === false) {
                throw new ApiException(400, Codes::BAD_PAYLOAD,
                    'request body could not be read');
            }
            if (strlen($raw) > $maxBodyBytes) {
                throw new ApiException(413, Codes::PAYLOAD_TOO_LARGE,
                    'request body exceeds the configured limit');
            }
            $body = $raw;
        }

        return new self(
            $method,
            $path,
            is_array($query) ? $query : [],
            $headers,
            $body,
            isset($server['REMOTE_ADDR']) && is_string($server['REMOTE_ADDR'])
                ? $server['REMOTE_ADDR'] : '',
            Ids::v4()
        );
    }

    public function header(string $name): string
    {
        return $this->headers[strtolower($name)] ?? '';
    }

    /**
     * The bearer credential, with the scheme stripped.
     *
     * `X-Acting-User` carries the optional user credential of spec §6
     * ("device access (+user context if acting for a user)"); both spellings
     * of the header value are accepted.
     */
    public function bearer(string $headerName = 'authorization'): string
    {
        $value = trim($this->header($headerName));
        if ($value === '') {
            return '';
        }
        if (preg_match('/^Bearer[ \t]+(\S+)$/i', $value, $matches) === 1) {
            return $matches[1];
        }
        // X-Acting-User is also accepted as a bare token.
        if (strcasecmp($headerName, 'authorization') !== 0) {
            return $value;
        }
        return '';
    }

    /**
     * Decode a JSON object body.
     *
     * @throws ApiException 415 when the Content-Type is not JSON, 400 when the
     *         body is empty or is not a JSON object
     * @return array<string, mixed>
     */
    public function jsonBody(bool $required = true): array
    {
        // An empty body is answered before Content-Type is examined, which is
        // what makes `POST /auth/logout` and `/sync-devices/enroll` work with
        // a client that sends no payload at all.
        if ($this->rawBody === '') {
            if ($required) {
                throw new ApiException(400, Codes::BAD_PAYLOAD,
                    'request body is empty or malformed');
            }
            return [];
        }
        $contentType = strtolower($this->header('content-type'));
        $mediaType = trim(explode(';', $contentType, 2)[0]);
        if ($mediaType !== 'application/json') {
            throw new ApiException(415, Codes::UNSUPPORTED_MEDIA_TYPE,
                'request content-type must be application/json');
        }
        try {
            // Decoded as an object first: `{}` is a list to the associative
            // decoder, and an empty object is a perfectly valid body.
            $object = json_decode($this->rawBody, false, 32,
                JSON_BIGINT_AS_STRING | JSON_THROW_ON_ERROR);
        } catch (JsonException $error) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'request body is not valid JSON');
        }
        if (!$object instanceof stdClass) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'request body must be a JSON object');
        }
        $parsed = json_decode($this->rawBody, true, 32, JSON_BIGINT_AS_STRING);
        if (!is_array($parsed)) {
            throw new ApiException(400, Codes::BAD_PAYLOAD,
                'request body must be a JSON object');
        }
        /** @var array<string, mixed> $parsed */
        return $parsed;
    }

    /** Exact binary peer matching; forwarding headers from other peers have no authority. */
    public static function isSecureTransport(string $forwardedProto,
                                             string $remoteAddr,
                                             string $httpsFlag,
                                             array $trustedProxies = [],
                                             bool $allowLoopbackHttp = false): bool
    {
        $peer = @inet_pton($remoteAddr);
        if ($peer === false) {
            return false;
        }
        if (strtolower($httpsFlag) === 'on') {
            return true;
        }
        foreach ($trustedProxies as $proxy) {
            if (is_string($proxy) && @inet_pton($proxy) === $peer) {
                // A local proxy must not fall through to the development exception.
                return strtolower(trim($forwardedProto)) === 'https';
            }
        }
        return $allowLoopbackHttp && self::isLoopback($remoteAddr);
    }

    public static function isLoopback(string $remoteAddr): bool
    {
        $address = @inet_pton($remoteAddr);
        if ($address === false) {
            return false;
        }
        if (strlen($address) === 4) {
            return ord($address[0]) === 127;
        }
        return $address === inet_pton('::1')
            || (substr($address, 0, 12) === hex2bin('00000000000000000000ffff')
                && ord($address[12]) === 127);
    }
}

final class Response
{
    /**
     * Emit a success payload: {"ok":true, …}.
     *
     * @param array<string, mixed> $payload
     */
    public static function ok(array $payload = []): void
    {
        $body = $payload;
        $body['ok'] = true;
        self::emit(200, $body);
    }

    /**
     * Emit a failure envelope (spec §6).
     *
     * @param array<string, mixed>|null $extra merged into the payload, never
     *        into `error`
     */
    public static function fail(ApiException $error, string $requestId,
                                ?array $extra = null): void
    {
        $code = Codes::assertValid($error->errorCode);
        $payload = [
            'ok' => false,
            'error' => [
                'code' => $code,
                'message' => $error->getMessage(),
                'request_id' => $requestId,
            ],
        ];
        if ($extra !== null) {
            foreach ($extra as $key => $value) {
                $payload[$key] = $value;
            }
        }
        if ($error->retryAfter !== null) {
            self::emit($error->status, $payload, [
                'Retry-After' => (string) $error->retryAfter,
            ]);
            return;
        }
        self::emit($error->status, $payload);
    }

    /**
     * Emit a JSON body with the security headers of spec §10.
     *
     * There is deliberately no `Access-Control-Allow-Origin` anywhere: the
     * desktop client is not a browser and CORS must stay closed (§10.7).
     *
     * @param array<string, mixed> $payload
     * @param array<string, string> $headers
     */
    public static function emit(int $status, array $payload,
                                array $headers = []): void
    {
        if (headers_sent()) {
            return;
        }
        http_response_code($status);
        header('Content-Type: application/json; charset=utf-8');
        header('Cache-Control: no-store');
        header('X-Content-Type-Options: nosniff');
        header('Referrer-Policy: no-referrer');
        header('Pragma: no-cache');
        foreach ($headers as $name => $value) {
            header($name . ': ' . $value);
        }
        $json = json_encode($payload, JSON_UNESCAPED_SLASHES
            | JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE);
        if ($json === false) {
            $json = '{"ok":false,"error":{"code":"SERVER_ERROR",'
                . '"message":"response could not be encoded","request_id":""}}';
        }
        echo $json;
    }
}
