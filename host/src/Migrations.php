<?php
/**
 * Migrations.php — the forward-only, recoverable migration runner.
 *
 * Run as the PRIVILEGED database account (config `db_migrate`); the application
 * account holds no DDL. host/bin/migrate.php is a thin adapter over this class.
 *
 * What makes this recoverable
 *   * A NAMED, CHECKSUMMED STEP JOURNAL (`migration_steps`). Every executable
 *     statement of a new migration file carries a stable `-- irica-step: <id>`
 *     marker; the runner records started / applied / verified / failed for each
 *     step together with the SHA-256 of its step block.
 *   * RECOVERY TRUSTS ENGINE METADATA, NOT THE LEDGER. MySQL DDL cannot be
 *     rolled back, so a crash can leave a `started` row next to a committed
 *     table. Every resume first compares normalised information_schema facts
 *     (MigrationPreflight) with the conditions declared in
 *     Schema::migrationSteps(): complete post-state -> reconciled verified;
 *     complete pre-state -> execute; anything else is MIGRATION_STATE_AMBIGUOUS
 *     and the runner stops without repairing anything.
 *   * AN ADVISORY LOCK keyed by the target schema and the migration set,
 *     acquired with GET_LOCK(name, 0) and released in `finally`. A second
 *     runner fails immediately instead of racing; a hard-killed process has its
 *     lock released by the server when the connection drops.
 *
 * What this class deliberately does NOT do
 *   * Claim DDL transactionality. No wrapped rollback is offered for DDL.
 *   * Drop, truncate, or "repair" a populated database. Recovery is reconcile,
 *     resume, or restore from backup (host/INSTALL.md).
 *   * Emit row values. Every structured report it returns is counts-only
 *     (code / table / count / state): never a person's name, a national id, a
 *     customer row, a credential or a raw server log line.
 *
 * Exit codes for the CLI adapter: 0 success · 1 refusal or failure · 2 usage.
 */

declare(strict_types=1);

/** Thrown only by the fixture's fault-injection hook, never by product code. */
final class MigrationSimulatedCrash extends RuntimeException
{
}

/**
 * Thrown when the scoped-relationship reference gate refuses an installation.
 *
 * The message is a stable code plus the version, exactly like every other
 * refusal here. The structured, counts-only blocker report rides on the object
 * instead, so a caller can show WHICH object is at fault and HOW MANY rows are
 * involved without the message ever carrying a row value.
 */
final class MigrationBlockedReference extends RuntimeException
{
    /** @var list<array{code: string, table: string, constraint: string, count: int}> */
    private array $blockers;

    /**
     * @param list<array{code: string, table: string, constraint: string, count: int}> $blockers
     */
    public function __construct(string $message, array $blockers = [])
    {
        parent::__construct($message);
        $this->blockers = $blockers;
    }

    /** @return list<array{code: string, table: string, constraint: string, count: int}> */
    public function blockers(): array
    {
        return $this->blockers;
    }
}

final class Migrations
{
    // Fault-injection points. The hook is a constructor argument supplied by
    // the test adapter; it is never read from configuration, an environment
    // variable or an HTTP flag, so it cannot be switched on in production.
    public const EVENT_BEFORE_DDL = 'before_ddl';
    public const EVENT_AFTER_DDL = 'after_ddl';
    public const EVENT_BEFORE_JOURNAL_COMPLETE = 'before_journal_complete';
    public const EVENT_BEFORE_TRACKER_INSERT = 'before_tracker_insert';

    // Stable refusal codes. Messages carry a code plus object names and an
    // errno at most — never SQL text, values or a credential.
    public const E_LOCK_HELD = 'MIGRATION_LOCK_HELD';
    public const E_MISSING_FILE = 'MIGRATION_MISSING_FILE';
    public const E_CHECKSUM_DRIFT = 'MIGRATION_CHECKSUM_DRIFT';
    public const E_UNKNOWN_JOURNAL = 'MIGRATION_UNKNOWN_JOURNAL';
    public const E_UNTRACKED_OBJECTS = 'MIGRATION_UNTRACKED_OBJECTS';
    public const E_AMBIGUOUS = 'MIGRATION_STATE_AMBIGUOUS';
    public const E_STEP_FAILED = 'MIGRATION_STEP_FAILED';
    public const E_NO_FILES = 'MIGRATION_NO_FILES';
    public const E_BLOCKED_REFERENCE = 'MIGRATION_BLOCKED_REFERENCE';

    /**
     * The file that installs the six same-tenant composite foreign keys.
     * Named once, here, so the gate in applyScopedBusinessKeys() cannot drift
     * from the file it is meant to protect.
     */
    public const SCOPED_VERSION = '003_scoped_business_keys';

    /** The synthetic step id a markerless legacy file (001) is journaled as. */
    public const LEGACY_STEP_ID = 'legacy_file';

    /** Sentinel step id used for whole-file events (the tracker insert). */
    public const VERSION_STEP = '__version__';

    /** Hard bound on keyset chunks: a runaway loop must stop, not hang. */
    private const MAX_KEYSET_CHUNKS = 10000;

    private Database $db;
    private MigrationPreflight $preflight;
    /** @var callable|null fixture-only fault hook; never typed (PHP forbids `callable` properties) */
    private $fault = null;
    private ?string $lock = null;
    /** Versions adopted by the most recent apply(); 0 when none were. */
    private int $adopted = 0;

    public function __construct(Database $db, string $directory, ?callable $fault = null)
    {
        $this->db = $db;
        $this->preflight = new MigrationPreflight($db, $directory);
        $this->fault = $fault;
    }

    public function preflight(): MigrationPreflight
    {
        return $this->preflight;
    }

    /** The advisory lock name; deterministic for one schema + migration set. */
    public function lockName(): string
    {
        $files = $this->preflight->files();
        ksort($files, SORT_STRING);
        $schema = (string) $this->db->queryValue('SELECT DATABASE()');
        return 'irica_migrate_' . substr(hash('sha256', $schema . '|' . json_encode($files)), 0, 40);
    }

    /**
     * Counts-only status. NEVER mutates: no DDL, no DML, no lock, no journal.
     *
     * @return array{state: string, safe: bool,
     *   blockers: list<array{code: string, table: string, count: int}>,
     *   migration_status: array<string, int|string>}
     */
    public function status(): array
    {
        $report = $this->preflight->inspect();
        return [
            'state' => $report['migration_status']['state'],
            'safe' => $report['safe'],
            'blockers' => $report['blockers'],
            'migration_status' => $report['migration_status'],
        ];
    }

    /**
     * Apply every pending migration (or only `$versions`).
     *
     * Walking the chain in file order means the prerequisites of each file
     * exist before that file is judged. The one file whose statements can die
     * half-way on a populated database — the scoped-relationship 003 — is
     * therefore gated inside the walk, right where its own prerequisites are
     * known to exist and before a single one of its statements runs.
     *
     * @param list<string>|null $versions restrict to these filename stems
     * @return array same counts-only shape as status()
     * @throws MigrationBlockedReference when 003 is in scope and the target
     *         already carries a reference the new constraints would refuse
     * @throws RuntimeException a stable refusal code, see the E_* constants
     */
    public function apply(?array $versions = null): array
    {
        // Refusals that need no write happen first, so a drifted or unknown
        // recorded file can never reach DDL.
        $this->assertRecordedIntegrity();
        $this->acquireLock();
        try {
            // Re-read under the lock: another runner may have finished.
            $this->preflight->invalidate();
            $this->assertRecordedIntegrity();
            $this->inventoryFirstRun();
            $this->adopted = $this->tryAdopt();
            if ($this->adopted === 0) {
                $this->ensureJournal();
                $this->assertNoUntrackedObjects();
                foreach ($this->preflight->files() as $version => $checksum) {
                    if ($versions !== null && !in_array($version, $versions, true)) {
                        continue;
                    }
                    // NO PATH reaches the scoped-relationship steps without the
                    // reference gate. This sits inside the walk, so every
                    // earlier file of the chain (001, 002) already exists when
                    // 003's gate is judged, and it sits before the
                    // already-recorded skip, so the gate is consulted even when
                    // 003 is pending no longer — a target holding a bad
                    // reference is reported instead of silently passed over.
                    if ($version === self::SCOPED_VERSION) {
                        $this->assertScopedReferencesSafe($version);
                    }
                    $recorded = $this->preflight->tracker()[$version] ?? null;
                    if ($recorded !== null && hash_equals($recorded, $checksum)) {
                        continue;
                    }
                    $this->applyVersion($version, $checksum);
                }
            }
        } finally {
            $this->releaseLock();
        }
        $report = $this->status();
        if ($this->adopted > 0) {
            $report['migration_status']['adopted_versions'] = $this->adopted;
        }
        return $report;
    }

    /**
     * Install the same-tenant composite relationships (003) behind a read-only
     * reference gate.
     *
     * Adding a constraint to a populated database either succeeds or dies on
     * the first bad row, and a death halfway through leaves an unpredictable
     * mix of installed and not-installed objects. So
     * MigrationPreflight::scopedIntegrity() judges the whole relationship set
     * FIRST — every orphan, every cross-tenant reference, every missing
     * supporting index — and refuses before a single ALTER runs.
     *
     * apply() consults the same gate itself (see assertScopedReferencesSafe()),
     * so this entry point is a convenience with an early, lock-free check, not
     * the only defended door: the authoritative gate runs under the advisory
     * lock immediately before 003 would be touched.
     *
     * A refusal changes NO business record. Nothing is repaired, nothing is
     * deleted, nothing is truncated, and the file is not recorded as applied.
     * The operator gets a counts-only report (code / table / constraint /
     * count) and decides how the bad rows are resolved. Once the target is
     * clean the same call installs the constraints normally.
     *
     * @param string $version the scoped-relationship migration file stem
     * @return array same counts-only shape as apply()
     * @throws MigrationBlockedReference when the target already carries a
     *         reference the new constraints would refuse to install
     * @throws RuntimeException a stable refusal code, see the E_* constants
     */
    public function applyScopedBusinessKeys(string $version = self::SCOPED_VERSION): array
    {
        if (!isset($this->preflight->files()[$version])) {
            throw new RuntimeException(self::E_MISSING_FILE . ' ' . $version);
        }
        $this->assertScopedReferencesSafe($version);
        return $this->apply([$version]);
    }

    /**
     * The reference gate as a refusal: run it, then throw when it is not safe.
     *
     * Every path that can reach 003's steps goes through this — apply() calls
     * it immediately before the scoped version is considered, under the
     * advisory lock, and applyScopedBusinessKeys() calls it before taking the
     * lock so a blocked target fails fast. Same derivation, same counts-only
     * blockers, same MIGRATION_BLOCKED_REFERENCE message.
     *
     * @throws MigrationBlockedReference when the target already carries a
     *         reference the new constraints would refuse to install
     */
    private function assertScopedReferencesSafe(string $version): void
    {
        $gate = $this->scopedGate($version);
        if (!$gate['safe']) {
            throw new MigrationBlockedReference(self::E_BLOCKED_REFERENCE . ' ' . $version,
                $gate['blockers']);
        }
    }

    /**
     * The reference gate for the scoped-relationship file, with the supporting
     * parent keys that THIS file creates counted as provided.
     *
     * 003 installs its four unique parent keys (s01..s04) before the composite
     * constraints that need them, so on a target that has not run the file yet
     * those keys are legitimately absent — "pending", not "missing". Deriving
     * the provided set from the file's own declared step postconditions keeps
     * the gate and the file from ever drifting apart.
     *
     * @return array{safe: bool,
     *   blockers: list<array{code: string, table: string, constraint: string, count: int}>,
     *   relationships: list<array{constraint: string, table: string,
     *     parent_key: string, orphan: int, cross_tenant: int}>}
     */
    public function scopedGate(string $version = self::SCOPED_VERSION): array
    {
        return $this->preflight->scopedIntegrity(
            MigrationPreflight::providedIndexes([$version]));
    }

    /**
     * Ruling 17 — adopt a fully applied database that has no step journal.
     *
     * `host/public/setup.php` (INSTALL.md Option A) applies every
     * `migrations/*.sql` through its own splitter and writes only
     * `schema_migrations`. Such a target is healthy and complete, but with no
     * journal every step is pending and neither its pre- nor its post-state
     * holds, so the ordinary walk reports MIGRATION_STATE_AMBIGUOUS and the
     * operator is told to restore a backup over a database that is already
     * correct.
     *
     * Adoptable only when ALL of the following hold:
     *   - the journal holds no rows (table absent, or empty);
     *   - every known migration file has a tracker row at an exactly matching
     *     checksum — assertRecordedIntegrity() has already refused drift and
     *     a recorded version with no file before we reach this point;
     *   - every version declares steps;
     *   - every step postcondition verifies, judged by OBJECT SUPERSESSION: a
     *     currently-failing fact key of step i is excused only when some LATER
     *     step in the chain DECLARES the same fact key (the key appears in that
     *     later step's post fact array, whether or not the later step's own
     *     evaluation passes). A fact key no later step declares must hold now.
     *     That is the rule a `verified` ledger row actually encodes, and it is
     *     required because postconditions are not monotone — 002 s07
     *     deliberately flips the column default that 002 s02 created, so a
     *     correct database can never satisfy every historical postcondition at
     *     the same time. Nothing excuses the last step's own keys, because no
     *     later step exists to declare them: they hold outright by
     *     construction. Object scoping is what keeps the excuse from becoming
     *     a chain-wide pardon — a middle step whose column, index or foreign
     *     key no later step re-states is refused, and MigrationPreflight has
     *     no column/index/foreign-key blocker that could catch it afterwards.
     *   - with TWO deliberate exceptions, both implemented as predicates that
     *     take a CLASS of fact key out of this gate and out of this gate
     *     only. Nothing else about a step's postcondition is weakened, and
     *     the ordinary apply path below still evaluates every key of every
     *     postcondition, `rows.*` included, at the moment it matters.
     *
     *     (1) The file tracker's own COLUMN shape is not part of the
     *     contract. Ruling 17 requires both the 5-column 001 shape and the
     *     3-column `host/public/setup.php` shape to be adoptable, and 001's
     *     postcondition declares all five columns, so a genuine setup.php
     *     target would fail on `description`/`applied_by` with no later step
     *     to declare them and would never be adopted. `tracker()` reads only
     *     `version` and `checksum`, and `trackerRow()` projects the recorded
     *     row onto whichever columns exist, so those keys are excluded from
     *     the postcondition gate (`isTrackerShapeKey()`). The tracker's
     *     EXISTENCE still gates: with no table there are no rows to compare
     *     and adoption declines above.
     *
     *     (2) ROW-CONTENT facts (`rows.*`) are not part of the contract
     *     either, because they are the one class of postcondition that
     *     ordinary product use invalidates. A DDL claim — this column exists
     *     with this type and this default, this index, this foreign key —
     *     stays true for the life of the schema; a row-content claim is only
     *     trustworthy immediately after the step that wrote those rows ran,
     *     which is exactly when the ordinary path re-checks it. Concretely,
     *     002 s06's `rows.tenants` predicate refuses any tenant that is not
     *     `lifecycle=active AND api_contract=legacy_v1 AND context_version=1`,
     *     and `Workspaces::createWithin()` — whose whole purpose is to produce
     *     V2 workspaces — inserts `api_contract=authoritative_v2`. s06 is the
     *     ONLY step in the ledger that declares `rows.tenants`, so
     *     object-scoped supersession has no later declarer to excuse it with
     *     and the key can never pass. On the ruling-17 target — complete file
     *     tracker, empty journal, which is precisely what `setup.php` Option A
     *     produces — that one legitimate workspace would therefore send every
     *     later run to MIGRATION_STATE_AMBIGUOUS with advice to restore a
     *     backup over a database that is already correct. See
     *     `isRowContentKey()`.
     *   - no object exists that no known step explains.
     * Then the journal rows are recorded retroactively as applied+verified and
     * no DDL is executed. Anything short of that returns 0 and the ordinary
     * path runs unchanged, so a partially applied database can never be
     * adopted into a false COMPLETE.
     *
     * @return int versions adopted; 0 means "not adopted, fall through"
     */
    private function tryAdopt(): int
    {
        if ($this->journalRowCount() > 0) {
            return 0;
        }
        $files = $this->preflight->files();
        $tracker = $this->preflight->tracker();
        if ($files === [] || count($tracker) !== count($files)) {
            return 0;
        }
        /** @var list<array{version: string, step: array}> $chain */
        $chain = [];
        foreach ($files as $version => $checksum) {
            $recorded = $tracker[$version] ?? null;
            if ($recorded === null || !hash_equals($checksum, $recorded)) {
                return 0;
            }
            $steps = Schema::migrationSteps($version);
            if ($steps === []) {
                return 0;
            }
            foreach ($this->parseSteps($version) as $step) {
                $chain[] = ['version' => $version, 'step' => $step];
            }
        }
        if ($chain === []) {
            return 0;
        }
        // Object-scoped supersession. `$laterDeclares[$i]` is the set of fact
        // keys every step AFTER $i declares, independent of whether those
        // steps' own claims currently pass: declaring a key is taking
        // responsibility for that object, and a later claim that also fails is
        // itself excused or refused by the same rule, ending at the last step,
        // whose keys nothing can excuse.
        $declared = [];
        foreach ($chain as $link) {
            $declared[] = MigrationPreflight::declaredKeys($link['step']['post']);
        }
        $laterDeclares = [];
        $suffix = [];
        for ($i = count($chain) - 1; $i >= 0; $i--) {
            $laterDeclares[$i] = $suffix;
            foreach ($declared[$i] as $key) {
                $suffix[$key] = true;
            }
        }
        foreach ($chain as $i => $link) {
            foreach ($this->preflight->evaluate($link['step']['post']) as $key) {
                if (self::isTrackerShapeKey($key) || self::isRowContentKey($key)) {
                    continue;
                }
                if (!isset($laterDeclares[$i][$key])) {
                    // No later step re-states this object, so nothing explains
                    // its absence: incomplete, and not an adoptable target.
                    // Fall through to the ordinary path.
                    return 0;
                }
            }
        }
        // The metadata is right; only the ledger is behind. Refuse before the
        // ledger is written if anything in the target is unexplained.
        $this->assertNoUntrackedObjects();
        $this->ensureJournal();
        $now = gmdate('Y-m-d H:i:s');
        foreach ($chain as $link) {
            $this->db->execute(
                'INSERT INTO ' . $this->db->table(MigrationPreflight::JOURNAL_TABLE)
                . ' (migration_version, step_id, checksum, status, cursor_value, started_at, completed_at)'
                . ' VALUES (:version, :step, :checksum, \'verified\', NULL, :started, :completed)',
                ['version' => $link['version'], 'step' => $link['step']['step_id'],
                 'checksum' => $link['step']['checksum'], 'started' => $now, 'completed' => $now]);
        }
        $this->preflight->invalidate();
        return count($files);
    }

    /**
     * A fact key about the file tracker's own COLUMN shape.
     *
     * 001 creates the five-column `schema_migrations` that the runner writes,
     * so 001's postcondition declares `description` and `applied_by`. Ruling
     * 17 requires the three-column shape `host/public/setup.php` creates to be
     * adoptable as well, and no later step ever re-states those columns, so
     * the object-scoped rule above would refuse that target forever. The
     * tracker's column shape is therefore outside the adoption gate, exactly
     * as it already is for the reader (`tracker()` selects only `version` and
     * `checksum`) and the writer (`trackerRow()` intersects the desired row
     * with the columns that exist). Keys about any other object are untouched,
     * and `tables.schema_migrations` still gates, because the table is what
     * makes a tracker row readable in the first place.
     */
    private static function isTrackerShapeKey(string $key): bool
    {
        return str_starts_with($key, 'columns.' . MigrationPreflight::TRACKER_TABLE . '.');
    }

    /**
     * A fact key about ROW CONTENT rather than about schema structure.
     *
     * `rows.<table>` is a count predicate over live data
     * (MigrationPreflight::rowCountMismatch()): "no tenant is still shaped
     * like the legacy backfill left it", "the identity row was seeded". Such
     * a claim is trustworthy at exactly one moment — immediately after the
     * step that wrote the rows ran — because ordinary product use rewrites
     * those rows by design. A DDL claim by contrast is stable for the life of
     * the schema: a column does not stop existing, and its type and default
     * do not change, because a user opened a workspace.
     *
     * Adoption asks a different question from the ordinary walk: not "did
     * this step's own effect survive?" but "is this database the complete
     * database those files describe?". Judging that on row content is both
     * unsound and one-directional. Unsound because 002 s06's
     * `rows.tenants` predicate refuses exactly the row
     * `Workspaces::createWithin()` is written to produce
     * (`api_contract = authoritative_v2`), and s06 is the only step in the
     * ledger that declares `rows.tenants`, so no later step can ever excuse
     * it through object-scoped supersession. One-directional because a
     * missing row can be seeded by the ordinary path while an extra row can
     * not be un-created by anything: leaving `rows.*` in the gate turns a
     * healthy, legitimately populated database into an unadoptable one and
     * sends the operator to MIGRATION_STATE_AMBIGUOUS — "restore a backup"
     * — over data the product itself wrote.
     *
     * So `rows.*` keys are excluded from the ADOPTION gate only, alongside
     * the tracker-shape keys above. The ordinary apply path is untouched:
     * applyStep() and executeStep() still evaluate the full postcondition,
     * `rows.*` included, immediately after their own step ran, which is the
     * only moment the claim means anything.
     */
    private static function isRowContentKey(string $key): bool
    {
        return str_starts_with($key, 'rows.');
    }

    // ------------------------------------------------------------------
    // Target gates
    // ------------------------------------------------------------------

    private function assertRecordedIntegrity(): void
    {
        $files = $this->preflight->files();
        foreach ($this->preflight->tracker() as $version => $checksum) {
            if (!isset($files[$version])) {
                throw new RuntimeException(self::E_MISSING_FILE . ' ' . $version);
            }
            if (!hash_equals($files[$version], $checksum)) {
                throw new RuntimeException(self::E_CHECKSUM_DRIFT . ' ' . $version);
            }
            // Step checksums are checked for every recorded version, not only
            // for a pending one: a rewritten step block on a database that is
            // already installed is the same class of drift.
            $rows = $this->preflight->journalRows($version);
            if ($rows === [] || Schema::migrationSteps($version) === []) {
                continue;
            }
            foreach ($this->parseSteps($version) as $step) {
                $row = $rows[$step['step_id']] ?? null;
                if ($row !== null && !hash_equals($step['checksum'], (string) $row['checksum'])) {
                    throw new RuntimeException(self::E_CHECKSUM_DRIFT . ' ' . $version . '/' . $step['step_id']);
                }
            }
        }
    }

    /**
     * 001's pristine-install safeguard, kept but narrowed by Ruling 4: only a
     * target that has NEVER been written to by this runner must be empty. A
     * later run against a populated, fully recorded schema is legitimate.
     */
    private function inventoryFirstRun(): void
    {
        if ($this->preflight->tracker() !== [] || $this->journalRowCount() > 0) {
            return;
        }
        $tables = $this->preflight->tables();
        if ($tables !== [] && $tables !== [MigrationPreflight::JOURNAL_TABLE]) {
            throw new RuntimeException(self::E_UNTRACKED_OBJECTS);
        }
    }

    private function journalRowCount(): int
    {
        if (!$this->preflight->journalPresent()) {
            return 0;
        }
        return (int) $this->db->queryValue(
            'SELECT COUNT(*) FROM ' . $this->db->table(MigrationPreflight::JOURNAL_TABLE));
    }

    /** Create the journal only when absent; an existing incompatible one blocks. */
    private function ensureJournal(): void
    {
        if ($this->preflight->journalPresent()) {
            if (!$this->preflight->journalCompatible($this->preflight->journalColumns())) {
                throw new RuntimeException(self::E_UNKNOWN_JOURNAL);
            }
            return;
        }
        $this->db->execute(
            'CREATE TABLE IF NOT EXISTS ' . $this->db->table(MigrationPreflight::JOURNAL_TABLE) . ' (
  migration_version VARCHAR(64) NOT NULL,
  step_id           VARCHAR(64) NOT NULL,
  checksum          CHAR(64)    NOT NULL,
  status            VARCHAR(16) NOT NULL,
  cursor_value      VARCHAR(64) NULL,
  started_at        DATETIME    NOT NULL,
  completed_at      DATETIME    NULL,
  PRIMARY KEY (migration_version, step_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci');
        $this->preflight->invalidate();
    }

    /** Arbitrary untracked objects always block; only a journal can explain known ones. */
    private function assertNoUntrackedObjects(): void
    {
        $owned = Schema::tables();
        foreach ($this->preflight->tables() as $table) {
            if (!in_array($table, $owned, true)) {
                throw new RuntimeException(self::E_UNTRACKED_OBJECTS . ' ' . $table);
            }
        }
    }

    // ------------------------------------------------------------------
    // Advisory lock
    // ------------------------------------------------------------------

    private function acquireLock(): void
    {
        $name = $this->lockName();
        $acquired = $this->db->queryValue('SELECT GET_LOCK(:name, 0)', ['name' => $name]);
        if ((int) $acquired !== 1) {
            throw new RuntimeException(self::E_LOCK_HELD);
        }
        $this->lock = $name;
    }

    private function releaseLock(): void
    {
        if ($this->lock === null) {
            return;
        }
        $name = $this->lock;
        $this->lock = null;
        try {
            $this->db->queryValue('SELECT RELEASE_LOCK(:name)', ['name' => $name]);
        } catch (Throwable $ignored) {
            // A dropped connection already released the server-side lock.
        }
    }

    // ------------------------------------------------------------------
    // Versions and steps
    // ------------------------------------------------------------------

    private function applyVersion(string $version, string $fileChecksum): void
    {
        $steps = $this->parseSteps($version);
        if ($steps === []) {
            throw new RuntimeException(self::E_AMBIGUOUS . ' ' . $version);
        }
        $rows = $this->preflight->journalRows($version);
        foreach ($steps as $step) {
            $row = $rows[$step['step_id']] ?? null;
            if ($row !== null && !hash_equals($step['checksum'], (string) $row['checksum'])) {
                throw new RuntimeException(self::E_CHECKSUM_DRIFT . ' ' . $version . '/' . $step['step_id']);
            }
        }
        foreach ($steps as $step) {
            $this->applyStep($version, $step);
            $this->preflight->invalidate();
        }
        // The file tracker is written only after every step postcondition holds.
        $this->hook(self::EVENT_BEFORE_TRACKER_INSERT, $version, self::VERSION_STEP);
        if (!isset($this->preflight->tracker()[$version])) {
            // Only columns this tracker actually has are written. setup.php
            // creates schema_migrations with three columns and 001 adds
            // description and applied_by, so a fixed five-column INSERT would
            // fail exactly when a fresh setup.php install needs a new file
            // recorded.
            $row = MigrationPreflight::trackerRow($this->preflight->trackerColumns(), [
                'version' => $version,
                'description' => $version . '.sql',
                'checksum' => $fileChecksum,
                'applied_at' => gmdate('Y-m-d H:i:s'),
                'applied_by' => substr((string) $this->db->queryValue('SELECT CURRENT_USER()'), 0, 64),
            ]);
            if ($row !== []) {
                $this->db->insert(MigrationPreflight::TRACKER_TABLE, $row);
            }
        }
    }

    private function applyStep(string $version, array $step): void
    {
        $id = $step['step_id'];
        $row = $this->preflight->journalRows($version)[$id] ?? null;
        $status = $row === null ? '' : (string) $row['status'];

        // A step already recorded as verified is DONE. Its postcondition was
        // proven at the moment it was recorded, and later steps may legitimately
        // supersede an intermediate one (002 s07 flips the column default that
        // 002 s02 created), so re-deriving "verified" from metadata alone would
        // turn a completed migration into MIGRATION_STATE_AMBIGUOUS. Metadata
        // is the tie-breaker for work the ledger could NOT have finished.
        if ($status === 'verified') {
            return;
        }

        if ($this->preflight->satisfies($step['post'])) {
            // Metadata proves the statement completed; the ledger is behind.
            // This is the crash-after-DDL / crash-before-journal path.
            $this->ensureJournalRow($version, $step, 'applied', $row);
            $this->hook(self::EVENT_BEFORE_JOURNAL_COMPLETE, $version, $id);
            $this->markVerified($version, $step);
            return;
        }

        if ($this->preflight->satisfies($step['pre'])) {
            if ($status === 'applied') {
                // The ledger claims the statement ran, yet the pre-state is
                // still what it was before. Neither explanation is safe.
                throw new RuntimeException(self::E_AMBIGUOUS . ' ' . $version . '/' . $id);
            }
            $this->ensureJournalRow($version, $step, 'started', $row);
            $this->hook(self::EVENT_BEFORE_DDL, $version, $id);
            $this->executeStep($version, $step);
            $this->hook(self::EVENT_AFTER_DDL, $version, $id);
            $this->markApplied($version, $step);
            $this->hook(self::EVENT_BEFORE_JOURNAL_COMPLETE, $version, $id);
            if (!$this->preflight->satisfies($step['post'])) {
                throw new RuntimeException(self::E_AMBIGUOUS . ' ' . $version . '/' . $id);
            }
            $this->markVerified($version, $step);
            return;
        }

        throw new RuntimeException(self::E_AMBIGUOUS . ' ' . $version . '/' . $id);
    }

    private function executeStep(string $version, array $step): void
    {
        if ($step['mode'] === 'file') {
            foreach ($step['statements'] as $sql) {
                $this->runStatement($version, $step, $sql);
            }
            return;
        }
        if ($step['mode'] === 'keyset') {
            $cursor = $this->journalCursor($version, $step['step_id']);
            $chunks = 0;
            while (true) {
                $next = (string) $this->db->queryValue($step['cursor_select'], ['cursor' => $cursor]);
                if ($next === '') {
                    break;
                }
                $this->runStatement($version, $step, $step['statements'][0],
                    ['cursor' => $cursor, 'next_cursor' => $next]);
                // The cursor is persisted only AFTER the chunk returns, so a
                // crash replays the chunk instead of skipping its rows.
                $this->persistCursor($version, $step, $next);
                $cursor = $next;
                if (++$chunks > self::MAX_KEYSET_CHUNKS) {
                    throw new RuntimeException(self::E_AMBIGUOUS . ' ' . $version . '/' . $step['step_id']);
                }
                if ($this->preflight->satisfies($step['post'])) {
                    break;
                }
            }
            return;
        }
        $this->runStatement($version, $step, $step['statements'][0]);
    }

    private function runStatement(string $version, array $step, string $sql, array $params = []): void
    {
        try {
            $this->db->execute($sql, $params);
        } catch (PDOException $error) {
            $info = $error->errorInfo ?? null;
            $errno = is_array($info) && isset($info[1]) ? (int) $info[1] : 0;
            $this->markFailed($version, $step);
            throw new RuntimeException(self::E_STEP_FAILED . ' ' . $version . '/' . $step['step_id']
                . ' errno=' . $errno);
        }
        // The statement just changed the schema: every cached information_schema
        // fact is now stale, and a stale fact would turn a successful step into
        // a false MIGRATION_STATE_AMBIGUOUS.
        $this->preflight->invalidate();
    }

    // ------------------------------------------------------------------
    // Journal writes (all individually committed; none claims atomicity)
    // ------------------------------------------------------------------

    private function journalCursor(string $version, string $stepId): string
    {
        $row = $this->preflight->journalRows($version)[$stepId] ?? null;
        $value = $row === null ? null : $row['cursor_value'];
        return $value === null ? '' : (string) $value;
    }

    private function ensureJournalRow(string $version, array $step, string $status, ?array $row): void
    {
        $table = $this->db->table(MigrationPreflight::JOURNAL_TABLE);
        if ($row === null) {
            $this->db->execute(
                'INSERT INTO ' . $table
                . ' (migration_version, step_id, checksum, status, cursor_value, started_at, completed_at)'
                . ' VALUES (:version, :step, :checksum, :status, NULL, :started, NULL)',
                ['version' => $version, 'step' => $step['step_id'],
                 'checksum' => $step['checksum'], 'status' => $status,
                 'started' => gmdate('Y-m-d H:i:s')]);
            return;
        }
        $this->db->execute(
            'UPDATE ' . $table . ' SET status = :status, checksum = :checksum'
            . ' WHERE migration_version = :version AND step_id = :step',
            ['status' => $status, 'checksum' => $step['checksum'],
             'version' => $version, 'step' => $step['step_id']]);
    }

    private function markApplied(string $version, array $step, ?string $cursor = null): void
    {
        $table = $this->db->table(MigrationPreflight::JOURNAL_TABLE);
        if ($cursor === null) {
            $this->db->execute(
                'UPDATE ' . $table . " SET status = 'applied'"
                . ' WHERE migration_version = :version AND step_id = :step',
                ['version' => $version, 'step' => $step['step_id']]);
            return;
        }
        $this->db->execute(
            'UPDATE ' . $table . " SET status = 'applied', cursor_value = :cursor"
            . ' WHERE migration_version = :version AND step_id = :step',
            ['cursor' => $cursor, 'version' => $version, 'step' => $step['step_id']]);
    }

    private function markVerified(string $version, array $step): void
    {
        $this->db->execute(
            'UPDATE ' . $this->db->table(MigrationPreflight::JOURNAL_TABLE)
            . " SET status = 'verified', completed_at = :completed"
            . ' WHERE migration_version = :version AND step_id = :step',
            ['completed' => gmdate('Y-m-d H:i:s'), 'version' => $version,
             'step' => $step['step_id']]);
    }

    private function persistCursor(string $version, array $step, string $cursor): void
    {
        $this->db->execute(
            'UPDATE ' . $this->db->table(MigrationPreflight::JOURNAL_TABLE)
            . ' SET cursor_value = :cursor'
            . ' WHERE migration_version = :version AND step_id = :step'
            . " AND status <> 'verified'",
            ['cursor' => $cursor, 'version' => $version, 'step' => $step['step_id']]);
    }

    /** Record `failed` only while the journal connection is still usable. */
    private function markFailed(string $version, array $step): void
    {
        try {
            $this->db->execute(
                'UPDATE ' . $this->db->table(MigrationPreflight::JOURNAL_TABLE)
                . " SET status = 'failed'"
                . ' WHERE migration_version = :version AND step_id = :step',
                ['version' => $version, 'step' => $step['step_id']]);
        } catch (Throwable $unusable) {
            unset($unusable);
        }
    }

    private function hook(string $event, string $version, string $step): void
    {
        if ($this->fault === null) {
            return;
        }
        ($this->fault)($event, $version, $step);
    }

    // ------------------------------------------------------------------
    // Step discovery
    // ------------------------------------------------------------------

    /**
     * @return list<array{step_id: string, mode: string, statements: list<string>,
     *   cursor_select: ?string, checksum: string, pre: array, post: array}>
     */
    private function parseSteps(string $version): array
    {
        $declared = Schema::migrationSteps($version);
        if ($declared === []) {
            return [];
        }
        $path = $this->preflight->directory() . '/' . $version . '.sql';
        $raw = @file_get_contents($path);
        if ($raw === false) {
            throw new RuntimeException(self::E_MISSING_FILE . ' ' . $version);
        }
        $blocks = self::parseMarkers($raw);
        $byId = [];
        foreach ($declared as $step) {
            $byId[$step['step_id']] = $step;
        }

        if ($blocks === []) {
            // Markerless legacy file: one unit step, checksummed as the file.
            $only = $declared[0] ?? null;
            if (count($declared) !== 1 || $only['step_id'] !== self::LEGACY_STEP_ID
                || $only['mode'] !== 'file') {
                throw new RuntimeException(self::E_AMBIGUOUS . ' ' . $version);
            }
            return [[
                'step_id' => self::LEGACY_STEP_ID, 'mode' => 'file',
                'statements' => self::splitStatements($raw),
                'cursor_select' => null, 'checksum' => hash('sha256', $raw),
                'pre' => $only['pre'], 'post' => $only['post'],
            ]];
        }

        $ids = array_column($blocks, 'id');
        if ($ids !== array_column($declared, 'step_id')) {
            throw new RuntimeException(self::E_AMBIGUOUS . ' ' . $version);
        }
        $out = [];
        foreach ($blocks as $block) {
            $spec = $byId[$block['id']] ?? null;
            if ($spec === null) {
                throw new RuntimeException(self::E_AMBIGUOUS . ' ' . $version . '/' . $block['id']);
            }
            if ($spec['mode'] === 'keyset' && $block['cursor_select'] === null) {
                throw new RuntimeException(self::E_AMBIGUOUS . ' ' . $version . '/' . $block['id']);
            }
            $statements = self::splitStatements(self::stripCommentLines($block['body']));
            if (count($statements) !== 1) {
                throw new RuntimeException(self::E_AMBIGUOUS . ' ' . $version . '/' . $block['id']);
            }
            $out[] = [
                'step_id' => $block['id'], 'mode' => $spec['mode'],
                'statements' => $statements,
                'cursor_select' => $block['cursor_select'],
                'checksum' => hash('sha256', $block['body']),
                'pre' => $spec['pre'], 'post' => $spec['post'],
            ];
        }
        return $out;
    }

    /**
     * Split a migration file into `-- irica-step:` blocks.
     *
     * @return list<array{id: string, body: string, cursor_select: ?string}>
     */
    public static function parseMarkers(string $sql): array
    {
        $blocks = [];
        $current = null;
        foreach (preg_split("/\r\n|\n|\r/", $sql) as $line) {
            if (preg_match('/^--\s*irica-step:\s*([A-Za-z0-9_.-]{1,64})\s*$/D', $line, $parts) === 1) {
                if ($current !== null) {
                    $blocks[] = $current;
                }
                $current = ['id' => $parts[1], 'lines' => [], 'cursor_select' => null];
                continue;
            }
            if ($current === null) {
                continue;
            }
            if (preg_match('/^--\s*irica-cursor-select:\s*(.+)$/D', $line, $parts) === 1) {
                $current['cursor_select'] = trim($parts[1]);
            }
            $current['lines'][] = $line;
        }
        if ($current !== null) {
            $blocks[] = $current;
        }
        $out = [];
        foreach ($blocks as $block) {
            $out[] = ['id' => $block['id'], 'body' => implode("\n", $block['lines']),
                      'cursor_select' => $block['cursor_select']];
        }
        return $out;
    }

    /** Remove whole-line SQL comments; only the executable text is prepared. */
    public static function stripCommentLines(string $sql): string
    {
        $kept = [];
        foreach (preg_split("/\r\n|\n|\r/", $sql) as $line) {
            if (preg_match('/^\s*--/', $line) === 1 || preg_match('/^\s*#/', $line) === 1) {
                continue;
            }
            $kept[] = $line;
        }
        return implode("\n", $kept);
    }

    /**
     * Split SQL into executable statements without relying on multi-statement
     * execution, so a failure can name the step it belongs to.
     *
     * @return list<string>
     */
    public static function splitStatements(string $sql): array
    {
        $statements = [];
        $buffer = '';
        $length = strlen($sql);
        $state = 'normal';
        for ($i = 0; $i < $length; $i++) {
            $char = $sql[$i];
            $next = $i + 1 < $length ? $sql[$i + 1] : '';

            if ($state === 'line_comment') {
                $buffer .= $char;
                if ($char === "\n") {
                    $state = 'normal';
                }
                continue;
            }
            if ($state === 'block_comment') {
                $buffer .= $char;
                if ($char === '*' && $next === '/') {
                    $buffer .= '/';
                    $i++;
                    $state = 'normal';
                }
                continue;
            }
            if ($state === 'single' || $state === 'double' || $state === 'backtick') {
                $buffer .= $char;
                if ($char === '\\' && $state !== 'backtick') {
                    if ($i + 1 < $length) {
                        $buffer .= $sql[$i + 1];
                        $i++;
                    }
                    continue;
                }
                $close = $state === 'single' ? "'" : ($state === 'double' ? '"' : '`');
                if ($char === $close) {
                    if ($next === $close && $state !== 'backtick') {
                        $buffer .= $next;
                        $i++;
                        continue;
                    }
                    if ($state === 'backtick' && $next === '`') {
                        $buffer .= $next;
                        $i++;
                        continue;
                    }
                    $state = 'normal';
                }
                continue;
            }

            if ($char === '-' && $next === '-') {
                $state = 'line_comment';
                $buffer .= $char;
                continue;
            }
            if ($char === '#' && ($next === ' ' || $next === "\t")) {
                $state = 'line_comment';
                $buffer .= $char;
                continue;
            }
            if ($char === '/' && $next === '*') {
                $state = 'block_comment';
                $buffer .= $char . $next;
                $i++;
                continue;
            }
            if ($char === "'") {
                $state = 'single';
                $buffer .= $char;
                continue;
            }
            if ($char === '"') {
                $state = 'double';
                $buffer .= $char;
                continue;
            }
            if ($char === '`') {
                $state = 'backtick';
                $buffer .= $char;
                continue;
            }
            if ($char === ';') {
                $trimmed = trim($buffer);
                if ($trimmed !== '') {
                    $statements[] = $trimmed;
                }
                $buffer = '';
                continue;
            }
            $buffer .= $char;
        }
        $trimmed = trim($buffer);
        if ($trimmed !== '') {
            $statements[] = $trimmed;
        }
        return $statements;
    }
}
