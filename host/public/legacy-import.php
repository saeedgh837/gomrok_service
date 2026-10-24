<?php
/**
 * legacy-import.php — browser shell for the reviewed legacy CRM migration.
 *
 * This page never opens the database. It only talks to the authenticated
 * legacy-import API exposed by index.php. The access token exists in browser
 * memory only and the page is deliberately non-cacheable.
 */
declare(strict_types=1);

ini_set('display_errors', '0');
ini_set('display_startup_errors', '0');
ini_set('html_errors', '0');
error_reporting(E_ALL);

define('HOST_ROOT', dirname(__DIR__));
require_once HOST_ROOT . '/src/Config.php';
require_once HOST_ROOT . '/src/Http.php';

function migrationSecurityHeaders(): void
{
    header('Cache-Control: no-store, no-cache, max-age=0, must-revalidate');
    header('Pragma: no-cache');
    header('Expires: 0');
    header('X-Content-Type-Options: nosniff');
    header('X-Frame-Options: DENY');
    header('Referrer-Policy: no-referrer');
    header('Cross-Origin-Opener-Policy: same-origin');
    header('Cross-Origin-Resource-Policy: same-origin');
    header('Permissions-Policy: camera=(), microphone=(), geolocation=()');
    header("Content-Security-Policy: default-src 'none'; script-src 'self'; "
        . "style-src 'self'; connect-src 'self'; img-src 'self'; "
        . "base-uri 'none'; form-action 'none'; frame-ancestors 'none'");
}

function h(string $value): string
{
    return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

migrationSecurityHeaders();

try {
    $config = Config::load();
} catch (Throwable $error) {
    unset($error);
    http_response_code(503);
    header('Content-Type: text/plain; charset=UTF-8');
    echo "Migration center is unavailable.\n";
    exit;
}

$forwardedProto = isset($_SERVER['HTTP_X_FORWARDED_PROTO'])
    && is_string($_SERVER['HTTP_X_FORWARDED_PROTO'])
    ? $_SERVER['HTTP_X_FORWARDED_PROTO'] : '';
$remoteAddr = isset($_SERVER['REMOTE_ADDR']) && is_string($_SERVER['REMOTE_ADDR'])
    ? $_SERVER['REMOTE_ADDR'] : '';
$https = isset($_SERVER['HTTPS']) && is_string($_SERVER['HTTPS'])
    ? $_SERVER['HTTPS'] : '';
if (!Request::isSecureTransport($forwardedProto, $remoteAddr, $https,
    is_array($config['trusted_proxies'] ?? null) ? $config['trusted_proxies'] : [],
    ($config['allow_loopback_http'] ?? false) === true)) {
    http_response_code(403);
    header('Content-Type: text/plain; charset=UTF-8');
    echo "HTTPS is required.\n";
    exit;
}

// The page itself is hidden whenever the API kill-switch is off. Reading the
// literal switch from the source makes a disable take effect on the next
// request even under an aggressive OPcache configuration.
if (!Config::freshBoolean('legacy_import_enabled', false)) {
    http_response_code(404);
    header('Content-Type: text/plain; charset=UTF-8');
    echo "Not found.\n";
    exit;
}

$basePath = $config['base_path'] ?? '/v1';
if (!is_string($basePath) || $basePath === '' || $basePath[0] !== '/'
    || str_contains($basePath, '..')) {
    $basePath = '/v1';
}
$basePath = rtrim($basePath, '/');
if ($basePath === '') {
    $basePath = '/v1';
}
$maxUpload = (int) ($config['limits']['max_import_body_bytes'] ?? 16777216);
if ($maxUpload < 1024) {
    $maxUpload = 16777216;
}
$maxUpload = min($maxUpload, 67108864);

header('Content-Type: text/html; charset=UTF-8');
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex,nofollow,noarchive">
  <title>IRICA Legacy Migration Center</title>
  <link rel="stylesheet" href="legacy-import.css">
  <script src="legacy-import.js" defer></script>
</head>
<body data-api-base="<?= h($basePath) ?>" data-max-upload-bytes="<?= (int) $maxUpload ?>">
  <main class="shell" id="migration-app">
    <header class="hero">
      <div>
        <p class="eyebrow">IRICA · Server CRM v2</p>
        <h1>Legacy Migration Center</h1>
        <p class="hero-copy">Review and migrate a local CRM JSON database from version 1.13.1 or older into the live server dataset.</p>
      </div>
      <div class="mode-badge" aria-label="Migration mode enabled">Migration mode enabled</div>
    </header>

    <section class="warning" aria-label="Safety notice">
      <strong>Temporary maintenance window.</strong>
      Back up the live database first. Keep this page open only while migrating, and set <code>legacy_import_enabled</code> back to <code>false</code> immediately after verification.
    </section>

    <nav class="workflow-tabs" aria-label="Migration workflows">
      <button id="tab-operator" class="tab-button tab-button-active workflow-tab" type="button" aria-selected="true" aria-controls="workflow-operator">
        Platform Operator — New private account
      </button>
      <button id="tab-legacy" class="tab-button workflow-tab" type="button" aria-selected="false" aria-controls="workflow-legacy">
        Legacy Admin — Shared tenant migration
      </button>
    </nav>

    <div id="global-status" class="status" role="status" aria-live="polite">Sign in with an operator or administrator account to begin.</div>

    <!-- Platform Operator Workflow -->
    <div id="workflow-operator" class="workflow-pane">
      <section class="card" aria-labelledby="operator-login-heading">
        <div class="section-heading">
          <div><span class="step">1</span><h2 id="operator-login-heading">Platform Operator sign-in</h2></div>
          <button id="operator-logout-button" class="button button-quiet" type="button" hidden>Sign out</button>
        </div>
        <form id="operator-login-form" class="form-grid" autocomplete="on">
          <label>Operator username
            <input id="operator-username" name="operator_username" type="text" autocomplete="section-operator username" maxlength="80" required>
          </label>
          <label>Operator password
            <input id="operator-password" name="operator_password" type="password" autocomplete="section-operator current-password" required>
          </label>
          <div class="form-action">
            <button class="button button-primary" type="submit">Sign in as operator</button>
          </div>
        </form>
        <p id="operator-session-note" class="muted" hidden>Authenticated. Operator session token is kept in memory only.</p>
      </section>

      <section class="card" aria-labelledby="operator-provision-heading">
        <div class="section-heading"><div><span class="step">2</span><h2 id="operator-provision-heading">New private account provisioning</h2></div></div>
        <fieldset id="operator-provision-fields" disabled>
          <form id="operator-provision-form" class="form-grid form-grid-wide">
            <label>Customer username
              <input id="cust-username" name="cust_username" type="text" autocomplete="section-customer username" maxlength="64" pattern="[a-zA-Z0-9][a-zA-Z0-9._-]{2,63}" placeholder="e.g. acme_corp" required>
              <span class="hint">3–64 characters; letters, digits, dot, underscore, dash.</span>
            </label>
            <label>Customer password
              <input id="cust-password" name="cust_password" type="password" autocomplete="section-customer new-password" required>
              <span class="hint">Initial password for desktop client sign-in.</span>
            </label>
            <label>Confirm password
              <input id="cust-password-confirm" name="cust_password_confirm" type="password" autocomplete="section-customer new-password" required>
              <span class="hint">Re-enter customer password.</span>
            </label>
            <label class="file-field">Legacy JSON file
              <input id="operator-source-file" name="operator_source_file" type="file" accept=".json,application/json" required>
              <span id="operator-file-limit" class="hint"></span>
            </label>
            <label>Source version <span class="optional">optional</span>
              <input id="operator-source-version" name="operator_source_version" type="text" maxlength="32" placeholder="e.g. 1.13.1">
              <span class="hint">Leave blank when the old store already contains a version.</span>
            </label>
            <div class="form-action">
              <button class="button button-primary" type="submit">Analyze and stage private account</button>
            </div>
          </form>
          <div class="divider"><span>or resume provision</span></div>
          <form id="operator-resume-form" class="resume-row">
            <label>Provision UUID
              <input id="operator-resume-id" type="text" maxlength="36" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" required>
            </label>
            <button class="button button-secondary" type="submit">Load provision</button>
          </form>
        </fieldset>
      </section>

      <section id="operator-result-section" class="card provision-result" aria-labelledby="provision-result-heading" hidden>
        <div class="section-heading"><div><span class="step">✓</span><h2 id="provision-result-heading">Private account active</h2></div></div>
        <dl class="job-meta">
          <div><dt>Customer username</dt><dd id="res-username">—</dd></div>
          <div><dt>Workspace / Tenant ID</dt><dd id="res-tenant">—</dd></div>
          <div><dt>Reconciliation</dt><dd id="res-reconciliation">—</dd></div>
          <div><dt>Status</dt><dd id="res-status">—</dd></div>
        </dl>
        <div class="connection-guide">
          <h3>Desktop Connection Guide</h3>
          <p>The customer can now launch the IRICA Windows desktop application and sign in with:</p>
          <ul>
            <li><strong>Username:</strong> <span id="res-guide-username">—</span></li>
            <li><strong>Password:</strong> The password chosen during provisioning (not displayed here for security)</li>
          </ul>
          <p class="muted">All devices using this single login synchronize strictly this customer's private dataset.</p>
        </div>
      </section>
    </div>

    <!-- Legacy Admin Shared Tenant Workflow -->
    <div id="workflow-legacy" class="workflow-pane" hidden>
      <section class="card" aria-labelledby="login-heading">
        <div class="section-heading">
          <div><span class="step">1</span><h2 id="login-heading">Administrator sign-in</h2></div>
          <button id="logout-button" class="button button-quiet" type="button" hidden>Sign out</button>
        </div>
        <form id="login-form" class="form-grid" autocomplete="on">
          <label>Username
            <input id="username" name="username" type="text" autocomplete="username" maxlength="80" required>
          </label>
          <label>Password
            <input id="password" name="password" type="password" autocomplete="current-password" required>
          </label>
          <div class="form-action">
            <button class="button button-primary" type="submit">Sign in</button>
          </div>
        </form>
        <p id="session-note" class="muted" hidden>Authenticated. The access token is kept only in this page's memory and is never stored in browser storage.</p>
      </section>

      <section class="card" aria-labelledby="upload-heading">
        <div class="section-heading"><div><span class="step">2</span><h2 id="upload-heading">Analyze a legacy database</h2></div></div>
        <fieldset id="migration-fields" disabled>
          <form id="upload-form" class="form-grid form-grid-wide">
            <label class="file-field">Legacy JSON file
              <input id="source-file" name="source_file" type="file" accept=".json,application/json" required>
              <span id="file-limit" class="hint"></span>
            </label>
            <label>Source version <span class="optional">optional</span>
              <input id="source-version" name="source_version" type="text" maxlength="32" placeholder="e.g. 1.13.1">
              <span class="hint">Leave blank when the old store already contains a version or had no formal version marker.</span>
            </label>
            <div class="form-action">
              <button class="button button-primary" type="submit">Analyze and stage</button>
            </div>
          </form>
          <div class="divider"><span>or resume</span></div>
          <form id="resume-form" class="resume-row">
            <label>Migration job UUID
              <input id="resume-job-id" type="text" maxlength="36" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" required>
            </label>
            <button class="button button-secondary" type="submit">Load review</button>
          </form>
        </fieldset>
      </section>
    </div>

    <section id="review-section" class="card" aria-labelledby="review-heading" hidden>
      <div class="section-heading">
        <div><span class="step">3</span><h2 id="review-heading">Review staged records</h2></div>
        <button id="refresh-review" class="button button-quiet" type="button">Refresh</button>
      </div>
      <dl class="job-meta">
        <div><dt>Job</dt><dd id="job-id">—</dd></div>
        <div><dt>Source version</dt><dd id="job-version">—</dd></div>
        <div><dt>Status</dt><dd id="job-status">—</dd></div>
        <div><dt>Unresolved</dt><dd id="job-unresolved">—</dd></div>
        <div class="hash-row"><dt>SHA-256</dt><dd id="job-hash">—</dd></div>
      </dl>
      <div id="count-grid" class="count-grid" aria-label="Migration counts"></div>
      <p class="muted">Possible conflicts are never merged automatically. Expand a row to inspect its normalized source data. Sensitive data is shown only in this non-cacheable administrator session.</p>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Entity</th><th>Source key</th><th>Source preview</th><th>Detected</th><th>Decision</th><th>Issues</th><th>Review action</th></tr></thead>
          <tbody id="records-body"></tbody>
        </table>
      </div>
    </section>

    <section id="apply-section" class="card apply-card" aria-labelledby="apply-heading" hidden>
      <div class="section-heading"><div><span class="step">4</span><h2 id="apply-heading">Apply to the live CRM</h2></div></div>
      <p>Apply is atomic: accepted rows commit together or the database transaction rolls back. Replaying the same completed job does not duplicate records.</p>
      <div class="apply-actions">
        <button id="apply-button" class="button button-danger" type="button" disabled>Apply reviewed migration</button>
        <button id="operator-cancel-button" class="button button-quiet" type="button" hidden>Cancel provision</button>
      </div>
      <p id="apply-note" class="muted">Resolve every conflict and invalid row before applying.</p>
    </section>
  </main>
</body>
</html>
