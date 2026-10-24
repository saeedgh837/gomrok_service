# -*- coding: utf-8 -*-
"""Private-account authorization against disposable SQL and real PHP services.

Removing a workspace/owner/registry check must make these scenarios fail. Only
synthetic identities on loopback are used; no shared fixture globals or servers.
"""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "host"
MYSQL_CANDIDATE = (ROOT.parents[2] / ".tools" / "mysql" /
                   "mariadb-11.4.4-winx64" / "bin" / "mariadb.exe")


class PrivateAccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.php = os.environ.get("IRICA_PHP") or shutil.which("php")
        cls.mysql = (os.environ.get("IRICA_MARIADB") or
                     shutil.which("mariadb") or shutil.which("mysql"))
        if not cls.mysql and MYSQL_CANDIDATE.is_file():
            cls.mysql = str(MYSQL_CANDIDATE)
        cls.password = os.environ.get("IRICA_DB_ROOT_PASSWORD")
        cls.port = int(os.environ.get("IRICA_DB_PORT", "3307"))
        credential_file = Path(os.environ.get("IRICA_TEST_DB_JSON") or
                               str(Path(tempfile.gettempdir()) / "irica_test_db.json"))
        if not cls.password and credential_file.is_file():
            data = json.loads(credential_file.read_text(encoding="utf-8"))
            cls.password = data.get("root_password")
            cls.port = int(data.get("port") or cls.port)
        if not cls.php or not cls.mysql or not cls.password:
            raise unittest.SkipTest("Disposable MariaDB/PHP service is not configured")
        cls.dbname = "irica_private_" + uuid.uuid4().hex[:12]
        cls.appuser = "priv_" + uuid.uuid4().hex[:12]
        cls.app_password = secrets.token_urlsafe(24)
        cls.customer_password = secrets.token_urlsafe(24)
        cls.jwt_secret = secrets.token_hex(32)
        cls.directory = Path(tempfile.mkdtemp(prefix="irica_private_"))
        cls.addClassCleanup(shutil.rmtree, cls.directory, True)
        cls.account_host = os.environ.get("IRICA_TEST_DB_ACCOUNT_HOST", "127.0.0.1")
        if cls.account_host not in ("127.0.0.1", "%"):
            raise AssertionError("Invalid disposable database account host")
        cls._sql("CREATE DATABASE `%s` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" % cls.dbname,
                 select_db=False)
        cls.addClassCleanup(cls._drop)
        cls._sql("CREATE USER '%s'@'%s' IDENTIFIED BY '%s'; "
                 "GRANT SELECT, INSERT, UPDATE, DELETE ON `%s`.* TO '%s'@'%s';" % (
                     cls.appuser, cls.account_host, cls.app_password,
                     cls.dbname, cls.appuser, cls.account_host), select_db=False)
        cls.config = cls.directory / "config.php"
        cls.config.write_text("""<?php
$settings = ['host'=>'127.0.0.1','port'=>(int)getenv('IRICA_PRIVATE_PORT'),
 'dbname'=>getenv('IRICA_PRIVATE_DB'),'user'=>getenv('IRICA_PRIVATE_USER'),
 'password'=>getenv('IRICA_PRIVATE_PASSWORD')];
$migrate=$settings; $migrate['user']='root';
$migrate['password']=getenv('IRICA_PRIVATE_ROOT_PASSWORD');
return ['env'=>'test','base_path'=>'/v1','db'=>$settings,'db_migrate'=>$migrate,
 'jwt_secret'=>getenv('IRICA_PRIVATE_JWT'),'setup_key'=>getenv('IRICA_PRIVATE_JWT'),
 'log_path'=>null,
 'rate_limit'=>['delay_after'=>1000,'lock_after'=>1000]];
""", encoding="utf-8")
        run = cls._run([cls.php, str(HOST / "bin" / "migrate.php"), "--quiet"])
        if run.returncode:
            raise AssertionError("Disposable migration failed: " + cls._redact(run.stderr))

    @classmethod
    def _redact(cls, text):
        for value in (cls.password, cls.app_password, cls.customer_password, cls.jwt_secret):
            text = text.replace(value, "***")
        return text

    @classmethod
    def _env(cls):
        env = dict(os.environ)
        env.update({"IRICA_HOST_CONFIG": str(cls.config),
                    "IRICA_PRIVATE_DB": cls.dbname, "IRICA_PRIVATE_USER": cls.appuser,
                    "IRICA_PRIVATE_PORT": str(cls.port),
                    "IRICA_PRIVATE_PASSWORD": cls.app_password,
                    "IRICA_PRIVATE_ROOT_PASSWORD": cls.password,
                    "IRICA_PRIVATE_CUSTOMER_PASSWORD": cls.customer_password,
                    "IRICA_PRIVATE_JWT": cls.jwt_secret})
        return env

    @classmethod
    def _run(cls, command, extra_env=None):
        env = cls._env()
        env.update(extra_env or {})
        return subprocess.run(command, cwd=str(ROOT), env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=180)

    def _cli(self, script, *arguments, password=None):
        supplied = password or self.customer_password
        run = self._run([self.php, str(HOST / "bin" / script), *arguments],
                        {"IRICA_ADMIN_PASSWORD": supplied, "IRICA_USER_PASSWORD": supplied})
        self.assertFalse(supplied in run.stdout + run.stderr, "Supplied password was disclosed")
        return run

    @classmethod
    def _sql(cls, sql, *, select_db=True):
        command = [cls.mysql, "-h", "127.0.0.1", "-P", str(cls.port), "-u", "root",
                   "--protocol=TCP", "--default-character-set=utf8mb4", "--batch",
                   "--skip-column-names"]
        if "mariadb" in Path(cls.mysql).name.lower():
            command.append("--ssl=FALSE")
        if select_db:
            command.append(cls.dbname)
        env = dict(os.environ, MYSQL_PWD=cls.password)
        run = subprocess.run(command, input=sql, env=env, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=180)
        if run.returncode:
            raise AssertionError("SQL failed: " + cls._redact(run.stderr)[:1000])
        return run.stdout.strip()

    @classmethod
    def _drop(cls):
        cls._sql("DROP DATABASE IF EXISTS `%s`; DROP USER IF EXISTS '%s'@'%s';" % (
            cls.dbname, cls.appuser, cls.account_host), select_db=False)

    def _scenario(self, code, extra_env=None):
        source = ["<?php", "declare(strict_types=1);"]
        for name in ("Schema", "Database", "Http", "Ids", "Config", "Log", "Tokens",
                     "Roles", "RateLimit", "Workspace", "Auth", "OperatorAuth", "Devices", "Sync",
                     "LegacyImport", "LegacyVerify", "LegacyApply", "ProvisionImportContext",
                     "AccountProvision"):
            path = HOST / "src" / (name + ".php")
            if path.is_file():
                source.append("require_once %s;" % json.dumps(path.as_posix()))
        source.append("try {")
        source.append(r"""
$cfg=Config::load(); $db=Database::fromConfig($cfg['db']); $log=new Log(null);
$now=gmdate('Y-m-d H:i:s'); $pw=getenv('IRICA_PRIVATE_CUSTOMER_PASSWORD');
$nonce=substr(str_replace('-','',Ids::v4()),0,12);
$ta=Ids::v4(); $tb=Ids::v4(); $ua=Ids::v4(); $ub=Ids::v4(); $ux=Ids::v4();
$na='a-'.$nonce; $nb='b-'.$nonce; $nx='x-'.$nonce;
$hash=Auth::hashPassword($pw);
foreach ([[$ta,$na],[$tb,$nb]] as [$tid,$name]) {
 $db->insert('tenants',['id'=>$tid,'slug'=>$name,'name'=>'Synthetic account',
  'status'=>'active','created_at'=>$now,'updated_at'=>$now]);
}
foreach ([[$ua,$ta,$na],[$ub,$tb,$nb],[$ux,$ta,$nx]] as [$uid,$tid,$name]) {
 $db->insert('users',['id'=>$uid,'tenant_id'=>$tid,'username'=>$name,'password_hash'=>$hash,
  'display_name'=>'Synthetic','must_change_password'=>0,'status'=>'active','token_version'=>1,
  'row_version'=>1,'created_at'=>$now,'updated_at'=>$now]);
 $db->insert('memberships',['id'=>Ids::v4(),'tenant_id'=>$tid,'user_id'=>$uid,'role'=>'admin',
  'created_at'=>$now,'updated_at'=>$now]);
 $db->execute('INSERT INTO login_names (canonical,user_id,state,created_at,updated_at) VALUES (?,?,?,?,?)',
  [$name,$uid,'active',$now,$now]);
}
foreach ([[$ta,$ua],[$tb,$ub]] as [$tid,$uid]) {
 $db->execute('INSERT INTO account_workspaces (tenant_id,owner_user_id,state,created_at,updated_at) VALUES (?,?,?,?,?)',
  [$tid,$uid,'active',$now,$now]);
}
$cfg['tenant_slug']=$na; $auth=new Auth($db,$log,$cfg);
function outcome(callable $fn): array {
 try {return ['status'=>200,'value'=>$fn()];}
 catch (ApiException $e) {return ['status'=>$e->status,'code'=>$e->errorCode];}
}
$login=fn(string $name)=>$auth->login(['username'=>$name,'password'=>$pw],'127.0.0.1',Ids::v4());
$refresh=fn(string $token,string $kind)=>$auth->refresh(['refresh_token'=>$token,'kind'=>$kind],'127.0.0.1',Ids::v4());
function requestWith(string $token): Request {
 return new Request('POST','/v1/sync/mutations',[],['x-acting-user'=>'Bearer '.$token],'{}','127.0.0.1',Ids::v4());
}
""")
        source.append(code)
        source.append("} catch (Throwable $error) { fwrite(STDERR, get_class($error).': '.$error->getMessage()); exit(1); }")
        path = self.directory / (uuid.uuid4().hex + ".php")
        path.write_text("\n".join(source), encoding="utf-8")
        run = self._run([self.php, str(path)], extra_env)
        self.assertEqual(run.returncode, 0, self._redact(run.stderr)[:1800])
        try:
            return json.loads(run.stdout)
        except ValueError:
            self.fail("Probe did not return JSON: " + self._redact(run.stdout)[:500])

    def _provision(self, code):
        # Omitting durable provisioning must fail the public behavior assertions.
        return self._scenario(r"""
if (!class_exists('AccountProvision')) {echo json_encode(['implemented'=>false]); exit;}
$cfg['legacy_import_enabled']=true;
$cfg['private_account_activation_enabled']=true;
$oa=new OperatorAuth($db,$log,$cfg);
$operator=OperatorAuth::create($db,'operator-'.$nonce,$hash);
$tokens=$oa->login(['username'=>'operator-'.$nonce,'password'=>$pw],'127.0.0.1',Ids::v4());
$op=$oa->principalFor($tokens['access_token']);
$service=new AccountProvision($db,$log,$cfg);
$body=['provision_id'=>Ids::v4(),'username'=>'new-'.$nonce,'password'=>$pw,
 'password_confirmation'=>$pw,'filename'=>'crm_store.json',
 'source_text'=>json_encode(['version'=>'1.13.1','customers'=>[
  ['id'=>'cust0001','full_name'=>'Synthetic customer','created'=>'2026-01-01 12:00:00',
   'transactions'=>[['at'=>'2026-01-02 12:00:00','amount'=>1200,'kind'=>'receipt','note'=>'fixture']]]],
  'owners'=>[],'identities'=>[],'devices'=>[],'periods'=>[]])];
$start=fn(array $input)=>$service->analyze($op,$input,Ids::v4(),'127.0.0.1');
$publish=fn(string $id,string $digest)=>$service->apply($op,$id,$digest,Ids::v4(),'127.0.0.1');
""" + code)

    def test_provision_analysis_reserves_one_private_owner_without_live_data(self):
        result = self._provision(r"""
$r=$start($body); $p=$r['provision'];
$row=$db->queryOne('SELECT w.state,n.state AS login_state,u.password_hash,u.must_change_password '
 .'FROM account_workspaces w JOIN users u ON u.id=w.owner_user_id JOIN login_names n ON n.user_id=u.id '
 .'WHERE w.tenant_id=?',[$p['tenant_id']]);
echo json_encode(['state'=>$p['state'],'workspace'=>$row['state'],'registry'=>$row['login_state'],
 'password'=>Auth::verifyPassword($pw,$row['password_hash']),'must_change'=>(int)$row['must_change_password'],
 'login'=>outcome(fn()=>$login($body['username']))['status'],
 'customers'=>(int)$db->queryValue('SELECT COUNT(*) FROM customers WHERE tenant_id=?',[$p['tenant_id']]),
 'records'=>count($r['review']['records']),
 'bound'=>$p['source_sha256']===hash('sha256',$body['source_text']),
 'secret_leaked'=>str_contains(json_encode($r),$pw),
 'real_actor'=>$db->queryValue('SELECT created_by_operator FROM legacy_import_jobs WHERE id=?',[$p['legacy_job_id']])===$op->operatorId,
 'user_actor'=>$db->queryValue('SELECT created_by FROM legacy_import_jobs WHERE id=?',[$p['legacy_job_id']])]);
""")
        self.assertEqual(result, {"state": "analyzed", "workspace": "pending", "registry": "reserved",
                                  "password": True, "must_change": 0, "login": 403,
                                  "customers": 0, "records": 2, "bound": True,
                                  "secret_leaked": False, "real_actor": True, "user_actor": None})

    def test_provision_retry_is_stable_and_changed_input_never_resets_password(self):
        result = self._provision(r"""
$a=$start($body); $uid=$a['provision']['owner_user_id'];
$before=$db->queryValue('SELECT password_hash FROM users WHERE id=?',[$uid]);
$b=$start($body); $codes=[];
foreach (['password','source_text','username'] as $field) {
 $changed=$body;
 if ($field==='password') {$changed[$field]=$pw.'changed';$changed['password_confirmation']=$changed[$field];}
 elseif ($field==='source_text') {$changed[$field].=' ';} else {$changed[$field].='x';}
 $codes[]=outcome(fn()=>$start($changed))['status'];
}
echo json_encode(['same'=>$a['provision']===$b['provision'],'changed'=>$codes,
 'hash_unchanged'=>$before===$db->queryValue('SELECT password_hash FROM users WHERE id=?',[$uid]),
 'jobs'=>(int)$db->queryValue('SELECT COUNT(*) FROM account_provisions WHERE owner_user_id=?',[$uid])]);
""")
        self.assertEqual(result, {"same": True, "changed": [409, 409, 409],
                                  "hash_unchanged": True, "jobs": 1})

    def test_provision_rejects_malformed_upload_and_forged_targets_without_reservations(self):
        result = self._provision(r"""
$before=(int)$db->queryValue('SELECT COUNT(*) FROM tenants'); $codes=[];
foreach (['{','[]','null','{"version":"1.14.0"}'] as $source) {
 $bad=$body; $bad['source_text']=$source; $codes[]=outcome(fn()=>$start($bad))['status'];
}
foreach (['tenant_id'=>$ta,'owner_user_id'=>$ua,'job_id'=>Ids::v4()] as $field=>$value) {
 $bad=$body; $bad[$field]=$value; $codes[]=outcome(fn()=>$start($bad))['status'];
}
echo json_encode(['codes'=>$codes,'tenants_unchanged'=>$before===(int)$db->queryValue('SELECT COUNT(*) FROM tenants'),
 'no_name'=>$db->queryValue('SELECT user_id FROM login_names WHERE canonical=?',[$body['username']])===null]);
""")
        self.assertEqual(result, {"codes": [400] * 7, "tenants_unchanged": True, "no_name": True})

    def test_provision_publication_reconciles_and_replay_never_duplicates_finance(self):
        result = self._provision(r"""
$r=$start($body); $p=$r['provision']; $a=$publish($p['id'],$r['review_sha256']);
$b=$publish($p['id'],$r['review_sha256']); $signed=$login($body['username']);
$tid=$p['tenant_id'];
echo json_encode(['state'=>$a['provision']['state'],'passed'=>$a['verification']['passed'],
 'same_result'=>$a===$b,'login_tenant'=>$signed['tenant_id']===$tid,
 'customers'=>(int)$db->queryValue('SELECT COUNT(*) FROM customers WHERE tenant_id=?',[$tid]),
 'finance'=>(int)$db->queryValue('SELECT SUM(amount) FROM customer_transactions WHERE tenant_id=?',[$tid]),
 'changes'=>(int)$db->queryValue('SELECT COUNT(*) FROM change_log WHERE tenant_id=?',[$tid]),
 'real_actor'=>(int)$db->queryValue('SELECT COUNT(*) FROM change_log WHERE tenant_id=? AND actor_type=? '
 .'AND actor_operator_id=? AND actor_user_id IS NULL',[$tid,'operator',$op->operatorId]),
 'audit'=>(int)$db->queryValue('SELECT COUNT(*) FROM audit_events WHERE tenant_id=? AND action=? '
 .'AND actor_operator_id=?',[$tid,'account_provision.active',$op->operatorId])]);
""")
        self.assertEqual(result, {"state": "active", "passed": True, "same_result": True,
                                  "login_tenant": True, "customers": 1, "finance": 1200,
                                  "changes": 2, "real_actor": 2, "audit": 1})

    def test_provision_stale_review_and_normalization_mismatch_block_publication(self):
        result = self._provision(r"""
$r=$start($body); $id=$r['provision']['id']; $tid=$r['provision']['tenant_id'];
$record=$r['review']['records'][0]['id'];
$d=$service->decide($op,$id,['record_id'=>$record,'resolution'=>'reject'],Ids::v4(),'127.0.0.1');
$stale=outcome(fn()=>$publish($id,$r['review_sha256']))['status'];
$actor=$db->queryOne('SELECT decided_by,decided_by_operator FROM legacy_import_decisions WHERE record_id=?',[$record]);
$db->execute('UPDATE account_provisions SET normalization_version=? WHERE id=?',['future-version',$id]);
$version=outcome(fn()=>$publish($id,$d['review_sha256']))['status'];
echo json_encode(['stale'=>$stale,'version'=>$version,
 'customers'=>(int)$db->queryValue('SELECT COUNT(*) FROM customers WHERE tenant_id=?',[$tid]),
 'workspace'=>$db->queryValue('SELECT state FROM account_workspaces WHERE tenant_id=?',[$tid]),
 'real_actor'=>$actor['decided_by']===null && $actor['decided_by_operator']===$op->operatorId]);
""")
        self.assertEqual(result, {"stale": 409, "version": 409, "customers": 0,
                                  "workspace": "pending", "real_actor": True})

    def test_provision_cancel_and_revoked_operator_fail_closed(self):
        result = self._provision(r"""
$r=$start($body); $p=$r['provision'];
$other=OperatorAuth::create($db,'other-'.$nonce,$hash);
$ot=$oa->login(['username'=>'other-'.$nonce,'password'=>$pw],'127.0.0.1',Ids::v4());
$foreign=$oa->principalFor($ot['access_token']);
$denied=outcome(fn()=>$service->review($foreign,$p['id']))['status'];
$c=$service->cancel($op,$p['id'],Ids::v4(),'127.0.0.1');
$cancelled=outcome(fn()=>$publish($p['id'],$r['review_sha256']))['status'];
$body['provision_id']=Ids::v4(); $duplicate=outcome(fn()=>$start($body))['status'];
OperatorAuth::rotatePassword($db,'operator-'.$nonce,Auth::hashPassword($pw.'reset'));
$revoked=outcome(fn()=>$service->review($op,$p['id']))['status'];
echo json_encode(['foreign'=>$denied,'state'=>$c['provision']['state'],'apply'=>$cancelled,
 'reserved'=>$duplicate,'revoked'=>$revoked,'login'=>outcome(fn()=>$login($body['username']))['status']]);
""")
        self.assertEqual(result, {"foreign": 404, "state": "cancelled", "apply": 409,
                                  "reserved": 409, "revoked": 401, "login": 401})

    def test_provision_failed_reconciliation_rolls_back_every_publication_effect(self):
        result = self._provision(r"""
$r=$start($body); $r=$service->review($op,$r['provision']['id']);
$id=$r['provision']['id']; $tid=$r['provision']['tenant_id'];
// A database-side data defect must be detected before any account is published.
$root=Database::fromConfig($cfg['db_migrate']);
$root->execute('CREATE TRIGGER test_provision_bad_amount BEFORE INSERT ON customer_transactions '
 .'FOR EACH ROW SET NEW.amount = NEW.amount + 1');
try {$failure=outcome(fn()=>$publish($id,$r['review_sha256']))['status'];}
finally {$root->execute('DROP TRIGGER test_provision_bad_amount');}
$state=$db->queryOne('SELECT state,review_sha256 FROM account_provisions WHERE id=?',[$id]);
$job=$db->queryValue('SELECT status FROM legacy_import_jobs WHERE id=?',[$r['provision']['legacy_job_id']]);
$counts=[]; foreach (['customers','customer_transactions','change_log','legacy_import_verifications'] as $table) {
 $counts[]=(int)$db->queryValue('SELECT COUNT(*) FROM '.$table.' WHERE tenant_id=?',[$tid]);
}
$fixed=$publish($id,$r['review_sha256']);
echo json_encode(['failure'=>$failure,'state'=>$state['state'],'job'=>$job,'counts'=>$counts,
 'retried'=>$fixed['provision']['state'],'passed'=>$fixed['verification']['passed']]);
""")
        self.assertEqual(result, {"failure": 409, "state": "reviewed", "job": "ready",
                                  "counts": [0, 0, 0, 0], "retried": "active", "passed": True})

    def test_provision_activation_kill_switch_keeps_pending_account_closed(self):
        result = self._provision(r"""
$r=$start($body); $cfg['private_account_activation_enabled']=false;
$closed=new AccountProvision($db,$log,$cfg);
$code=outcome(fn()=>$closed->apply($op,$r['provision']['id'],$r['review_sha256'],Ids::v4(),'127.0.0.1'))['status'];
echo json_encode(['status'=>$code,'login'=>outcome(fn()=>$login($body['username']))['status'],
 'workspace'=>$db->queryValue('SELECT state FROM account_workspaces WHERE tenant_id=?',[$r['provision']['tenant_id']])]);
""")
        self.assertEqual(result, {"status": 503, "login": 403, "workspace": "pending"})

    def _credential_worker(self):
        """Use pipes for synthetic credentials; never persist them in worker scripts."""
        path = self.directory / (uuid.uuid4().hex + ".php")
        source = ["<?php", "declare(strict_types=1);"]
        for name in ("Schema", "Database", "Http", "Ids", "Config", "Log", "Tokens",
                     "Roles", "RateLimit", "Workspace", "Auth", "OperatorAuth"):
            source.append("require_once %s;" % json.dumps((HOST / "src" / (name + ".php")).as_posix()))
        source.append(r"""
try {
 $cfg=Config::load(); $db=Database::fromConfig($cfg['db']); $log=new Log(null);
 echo $db->queryValue('SELECT CONNECTION_ID()')."\n"; flush();
 $args=json_decode(fgets(STDIN),true,512,JSON_THROW_ON_ERROR);
 try {
  if ($args['action']==='reset') {
   Workspace::rotatePassword($db,$args['name'],$args['hash']);
   $result=['status'=>200];
  } else {
   $auth=new Auth($db,$log,$cfg);
   $value=$auth->refresh(['kind'=>$args['kind'],'refresh_token'=>$args['token']],'127.0.0.1',Ids::v4());
   $result=['status'=>200];
   // Wait for reset before exercising a rotation whose response arrived late.
   if ($args['action']==='refresh_late') {
    echo "rotated\n"; flush(); fgets(STDIN);
    try {$auth->principalFor($value['access_token']); $result['late_access']=200;}
    catch (ApiException $e) {$result['late_access']=$e->status;}
    try {$auth->refresh(['kind'=>$args['kind'],'refresh_token'=>$value['refresh_token']],'127.0.0.1',Ids::v4()); $result['late_refresh']=200;}
    catch (ApiException $e) {$result['late_refresh']=$e->status;}
   }
  }
 } catch (ApiException $e) {$result=['status'=>$e->status];}
 echo json_encode($result); flush();
} catch (Throwable $e) {fwrite(STDERR,get_class($e)); exit(1);}
""")
        path.write_text("\n".join(source), encoding="utf-8")
        return path

    def test_refresh_waits_for_owner_without_holding_the_session_lock(self):
        # Taking the session first would block recovery while waiting for its owner.
        worker = self._credential_worker()
        result = self._scenario(r"""
$monitor=Database::fromConfig($cfg['db_migrate']);
$sessionProbe=Database::fromConfig($cfg['db']);
$sessionProbe->execute('SET SESSION innodb_lock_wait_timeout=1');
function waitForLock(Database $monitor,int $id): bool {
 $deadline=microtime(true)+3;
 do {
  if ((int)$monitor->queryValue('SELECT COUNT(*) FROM information_schema.INNODB_LOCK_WAITS w'
    .' JOIN information_schema.INNODB_TRX t ON t.trx_id=w.requesting_trx_id'
    .' WHERE t.trx_mysql_thread_id=?',[$id])>0
   || (int)$monitor->queryValue("SELECT COUNT(*) FROM information_schema.PROCESSLIST"
    ." WHERE ID=? AND COMMAND='Execute' AND STATE='Statistics' AND INFO LIKE '%FOR UPDATE%'",[$id])>0) {return true;}
  usleep(10000);
 } while (microtime(true)<$deadline);
 return false;
}
$out=[];
foreach (['user','device'] as $kind) {
 $user=$login($na);
 $token=$user['refresh_token'];
 if ($kind==='device') {
  $enrolled=$auth->enroll($auth->principalFor($user['access_token']),[],Ids::v4(),'127.0.0.1');
  $token=$enrolled['device_refresh_token'];
 }
 $session=$db->queryValue('SELECT id FROM auth_sessions WHERE token_hash=?',[Tokens::hashOpaque($token)]);
 $db->pdo()->beginTransaction();
 $db->queryOne('SELECT id FROM users WHERE id=? FOR UPDATE',[$ua]);
 $process=proc_open([PHP_BINARY,__WORKER__],[['pipe','r'],['pipe','w'],['pipe','w']],$pipes);
 stream_set_timeout($pipes[1],12);
 $id=(int)trim(fgets($pipes[1]));
 fwrite($pipes[0],json_encode(['action'=>'refresh','kind'=>$kind,'token'=>$token])."\n"); fflush($pipes[0]);
 try {
  $waiting=waitForLock($monitor,$id);
  $sessionProbe->pdo()->beginTransaction();
  try {
   $sessionProbe->queryOne('SELECT id FROM auth_sessions WHERE id=? FOR UPDATE',[$session]);
   $out[$kind]=$waiting;
  } catch (PDOException $e) {
   if ((int)($e->errorInfo[1]??0)!==1205) {throw $e;}
   $out[$kind]=false;
  } finally {$sessionProbe->pdo()->rollBack();}
 } finally {
  $db->pdo()->rollBack();
  fclose($pipes[0]); $reply=stream_get_contents($pipes[1]);
  fclose($pipes[1]); fclose($pipes[2]); $exit=proc_close($process);
 }
 if ($exit!==0 || (json_decode($reply,true)['status']??null)!==200) {
  throw new RuntimeException('refresh worker failed after release');
 }
}
echo json_encode($out);
""".replace("__WORKER__", json.dumps(worker.as_posix())))
        self.assertEqual(result, {"user": True, "device": True}, json.dumps(result))

    def _credential_race(self, code):
        worker = self._credential_worker()
        return self._scenario((r"""
function startWorker(array $args): array {
 $process=proc_open([PHP_BINARY,__WORKER__],[['pipe','r'],['pipe','w'],['pipe','w']],$pipes);
 stream_set_timeout($pipes[1],12);
 $id=(int)trim(fgets($pipes[1]));
 fwrite($pipes[0],json_encode($args)."\n"); fflush($pipes[0]);
 return [$process,$pipes,$id];
}
function finishWorker(array $worker): array {
 [$process,$pipes]=$worker; fclose($pipes[0]);
 $reply=stream_get_contents($pipes[1]); fclose($pipes[1]); fclose($pipes[2]);
 if (proc_close($process)!==0) {throw new RuntimeException('credential worker failed');}
 return json_decode($reply,true,512,JSON_THROW_ON_ERROR);
}
function waitForLock(Database $monitor,int $id,string $label): void {
 $deadline=microtime(true)+3;
 do {
  if ((int)$monitor->queryValue('SELECT COUNT(*) FROM information_schema.INNODB_LOCK_WAITS w'
    .' JOIN information_schema.INNODB_TRX t ON t.trx_id=w.requesting_trx_id'
    .' WHERE t.trx_mysql_thread_id=?',[$id])>0
   || (int)$monitor->queryValue("SELECT COUNT(*) FROM information_schema.PROCESSLIST"
    ." WHERE ID=? AND COMMAND='Execute' AND STATE<>''"
    ." AND ((?='reset' AND INFO LIKE 'UPDATE auth_sessions SET revoked_at%')"
    ." OR (?='refresh' AND INFO LIKE 'SELECT id FROM users%FOR UPDATE%'))",[$id,$label,$label])>0) {return;}
  usleep(10000);
 } while (microtime(true)<$deadline);
 $state=$monitor->queryOne('SELECT COMMAND,STATE FROM information_schema.PROCESSLIST WHERE ID=?',[$id]);
 throw new RuntimeException($label.' did not reach the lock barrier: '.json_encode($state));
}
""" + code).replace("__WORKER__", json.dumps(worker.as_posix())))

    def test_reset_holding_owner_rejects_waiting_user_and_device_refresh(self):
        # Reset's owner lock must win before a blocked refresh can create a session.
        for kind in ("user", "device"):
            with self.subTest(kind=kind):
                result = self._credential_race(r"""
$kind=__KIND__; $monitor=Database::fromConfig($cfg['db_migrate']);
$user=$login($na); $other=$login($nb);
$ops=new OperatorAuth($db,$log,$cfg); OperatorAuth::create($db,$na,$hash);
$operator=$ops->login(['username'=>$na,'password'=>$pw],'127.0.0.1',Ids::v4());
$enrolled=$auth->enroll($auth->principalFor($user['access_token']),[],Ids::v4(),'127.0.0.1');
$device=$refresh($enrolled['device_refresh_token'],'device');
$target=$kind==='user'?$user:$device;
$newPassword=$pw.'-new'; $newHash=Auth::hashPassword($newPassword);
$session=$db->queryValue('SELECT id FROM auth_sessions WHERE token_hash=?',[Tokens::hashOpaque($target['refresh_token'])]);
$db->pdo()->beginTransaction();
$db->queryOne('SELECT id FROM auth_sessions WHERE id=? FOR UPDATE',[$session]);
$reset=startWorker(['action'=>'reset','name'=>$na,'hash'=>$newHash]);
try {
 waitForLock($monitor,$reset[2],'reset');
 $rotation=startWorker(['action'=>'refresh','kind'=>$kind,'token'=>$target['refresh_token']]);
 waitForLock($monitor,$rotation[2],'refresh');
} finally {$db->pdo()->rollBack();}
$resetResult=finishWorker($reset); $refreshResult=finishWorker($rotation);
echo json_encode(['reset'=>$resetResult['status'],'refresh'=>$refreshResult['status'],
 'old_access'=>outcome(fn()=>$auth->principalFor($user['access_token']))['status'],
 'old_refresh'=>outcome(fn()=>$refresh($user['refresh_token'],'user'))['status'],
 'device_access'=>outcome(fn()=>$auth->principalFor($device['access_token']))['status'],
 'device_refresh'=>outcome(fn()=>$refresh($device['refresh_token'],'device'))['status'],
 'new_password'=>outcome(fn()=>$auth->login(['username'=>$na,'password'=>$newPassword],'127.0.0.1',Ids::v4()))['status'],
 'other'=>outcome(fn()=>$auth->principalFor($other['access_token']))['status'],
 'operator'=>outcome(fn()=>$ops->principalFor($operator['access_token']))['status']]);
""".replace("__KIND__", json.dumps(kind)))
                self.assertEqual(result, {"reset": 200, "refresh": 401,
                                          "old_access": 401, "old_refresh": 401,
                                          "device_access": 401, "device_refresh": 401,
                                          "new_password": 200, "other": 200, "operator": 200})

    def test_reset_revokes_refresh_winner_even_when_its_response_arrives_late(self):
        # A successful rotation is not usable after a later reset commits.
        for kind in ("user", "device"):
            with self.subTest(kind=kind):
                result = self._credential_race(r"""
$kind=__KIND__; $user=$login($na); $target=$user;
if ($kind==='device') {
 $e=$auth->enroll($auth->principalFor($user['access_token']),[],Ids::v4(),'127.0.0.1');
 $target=$refresh($e['device_refresh_token'],'device');
}
$rotation=startWorker(['action'=>'refresh_late','kind'=>$kind,'token'=>$target['refresh_token']]);
if (trim(fgets($rotation[1][1]))!=='rotated') {throw new RuntimeException('rotation did not commit');}
Workspace::rotatePassword($db,$na,Auth::hashPassword($pw.'-new'));
fwrite($rotation[1][0],"continue\n"); fflush($rotation[1][0]);
echo json_encode(finishWorker($rotation));
""".replace("__KIND__", json.dumps(kind)))
                self.assertEqual(result, {"status": 200, "late_access": 401, "late_refresh": 401})

    def test_operator_recovery_revokes_only_the_exact_operator_realm(self):
        result = self._scenario("""
$ops=new OperatorAuth($db,$log,$cfg); $a=OperatorAuth::create($db,$na,$hash);
OperatorAuth::create($db,$nb,$hash);
$s=$ops->login(['username'=>$na,'password'=>$pw],'127.0.0.1',Ids::v4());
$other=$ops->login(['username'=>$nb,'password'=>$pw],'127.0.0.1',Ids::v4()); $customer=$login($na);
$newPassword=$pw.'-operator';
$changed=OperatorAuth::rotatePassword($db,' '.strtoupper($na).' ',Auth::hashPassword($newPassword));
echo json_encode(['same'=>$changed['operator_id']===$a['operator_id'],
 'access'=>outcome(fn()=>$ops->principalFor($s['access_token']))['status'],
 'refresh'=>outcome(fn()=>$ops->refresh(['refresh_token'=>$s['refresh_token']],'127.0.0.1',Ids::v4()))['status'],
 'old_password'=>outcome(fn()=>$ops->login(['username'=>$na,'password'=>$pw],'127.0.0.1',Ids::v4()))['status'],
 'new_password'=>outcome(fn()=>$ops->login(['username'=>$na,'password'=>$newPassword],'127.0.0.1',Ids::v4()))['status'],
 'other'=>outcome(fn()=>$ops->principalFor($other['access_token']))['status'],
 'customer'=>outcome(fn()=>$auth->principalFor($customer['access_token']))['status'],
 'customer_password'=>outcome(fn()=>$login($na))['status']]);
""")
        self.assertEqual(result, {"same": True, "access": 401, "refresh": 401,
                                  "old_password": 401, "new_password": 200,
                                  "other": 200, "customer": 200, "customer_password": 200})

    def test_operator_recovery_never_creates_promotes_or_enables_an_identity(self):
        result = self._scenario("""
$o=OperatorAuth::create($db,$na,$hash);
$db->execute('UPDATE platform_operators SET enabled=0 WHERE id=?',[$o['operator_id']]);
OperatorAuth::rotatePassword($db,$na,$hash);
$missing=outcome(fn()=>OperatorAuth::rotatePassword($db,$nb,$hash));
echo json_encode(['enabled'=>(int)$db->queryValue('SELECT enabled FROM platform_operators WHERE id=?',[$o['operator_id']]),
 'missing'=>$missing['status'],'promoted'=>(int)$db->queryValue('SELECT COUNT(*) FROM platform_operators WHERE login_name=?',[$nb])]);
""")
        self.assertEqual(result, {"enabled": 0, "missing": 403, "promoted": 0})

    def test_password_hashing_rejects_invalid_boundaries_without_trimming(self):
        result = self._scenario(r"""
$out=[];
foreach (['short',str_repeat('x',4097),"valid-password\0suffix"] as $value) {
 try {Auth::hashPassword($value); $out[]=200;}
 catch (ApiException $error) {$out[]=$error->status;}
 catch (Throwable $error) {$out[]=500;}
}
$max=Auth::passwordAlgo()===PASSWORD_BCRYPT?72:4096;
$value=' '.str_repeat('x',$max-2).' ';
$boundary=Auth::hashPassword($value);
echo json_encode(['invalid'=>$out,'boundary'=>password_verify($value,$boundary),
 'trimmed'=>password_verify(trim($value),$boundary)]);
""")
        self.assertEqual(result, {"invalid": [400, 400, 400], "boundary": True, "trimmed": False})

    def test_bcrypt_password_suffixes_never_authenticate_either_realm(self):
        result = self._scenario(r"""
$exact=str_repeat('x',72); $bcrypt=password_hash($exact,PASSWORD_BCRYPT);
$db->execute('UPDATE users SET password_hash=? WHERE id=?',[$bcrypt,$ua]);
OperatorAuth::create($db,$na,$bcrypt); $ops=new OperatorAuth($db,$log,$cfg);
$out=[];
foreach (['customer'=>$auth,'operator'=>$ops] as $realm=>$service) {
 $out[$realm]=[];
 foreach ([$exact,$exact.'suffix',$exact."\0suffix"] as $value) {
  $out[$realm][]=outcome(fn()=>$service->login(['username'=>$na,'password'=>$value],
   '127.0.0.1',Ids::v4()))['status'];
 }
}
echo json_encode($out);
""")
        self.assertEqual(result, {"customer": [200, 401, 401], "operator": [200, 401, 401]})

    def test_customer_cli_refuses_password_arguments_before_creating_accounts(self):
        for script in ("create-admin.php", "create-user.php"):
            with self.subTest(script=script):
                name = "args-" + uuid.uuid4().hex[:12]
                run = self._cli(script, "--username=" + name,
                                "--password=synthetic-not-a-real-secret")
                self.assertNotEqual(run.returncode, 0)
                self.assertNotIn("synthetic-not-a-real-secret", run.stdout + run.stderr)
                self.assertEqual(self._sql("SELECT COUNT(*) FROM users WHERE username='%s';" % name), "0")

    def test_customer_cli_accepts_redirected_password_without_printing_it(self):
        for script in ("create-admin.php", "create-user.php"):
            with self.subTest(script=script):
                name = "stdin-" + uuid.uuid4().hex[:12]
                env = self._env()
                env.update({"IRICA_ADMIN_PASSWORD": "", "IRICA_USER_PASSWORD": ""})
                run = subprocess.run([self.php, str(HOST / "bin" / script),
                                      "--username=" + name, "--password-stdin"],
                                     input=self.customer_password + "\n", cwd=str(ROOT), env=env,
                                     capture_output=True, text=True, encoding="utf-8", timeout=30)
                self.assertEqual(run.returncode, 0, self._redact(run.stderr))
                self.assertFalse(self.customer_password in run.stdout + run.stderr,
                                 "Supplied password was disclosed")
                self.assertNotIn("PASSWORD (shown once)", run.stdout)
                self.assertEqual(self._scenario("echo json_encode(outcome(fn()=>$login(%s))['status']);"
                                               % json.dumps(name)), 200)

    def test_operator_cli_uses_private_password_input_and_realm_specific_recovery(self):
        name = "operator-" + uuid.uuid4().hex[:12]
        password = self.customer_password + "-operator"
        env = {"IRICA_OPERATOR_PASSWORD": password}
        created = self._run([self.php, str(HOST / "bin" / "create-operator.php"),
                             "--username=" + name], env)
        self.assertEqual(created.returncode, 0, self._redact(created.stderr))
        self.assertFalse(password in created.stdout + created.stderr, "Supplied password was disclosed")
        self.assertIn("operator created", created.stdout)
        self.assertEqual(self._sql("SELECT COUNT(*) FROM users WHERE username='%s';" % name), "0")
        self.assertEqual(self._sql("SELECT bootstrap_consumed FROM platform_operator_state WHERE id=1;"), "1")
        reset = self._run([self.php, str(HOST / "bin" / "create-operator.php"),
                           "--username=" + name.upper(), "--reset-password"], env)
        self.assertEqual(reset.returncode, 0, self._redact(reset.stderr))
        self.assertFalse(password in reset.stdout + reset.stderr, "Supplied password was disclosed")
        self.assertEqual(self._sql("SELECT token_version FROM platform_operators WHERE login_name='%s';" % name), "2")
        rejected = self._run([self.php, str(HOST / "bin" / "create-operator.php"),
                              "--username=" + name, "--password=synthetic-not-a-secret"], env)
        self.assertNotEqual(rejected.returncode, 0)

    def test_operator_credentials_are_independent_and_cannot_enter_customer_auth(self):
        result = self._scenario("""
$ops=new OperatorAuth($db,$log,$cfg);
$operator=OperatorAuth::create($db,$na,$hash);
$user=$login($na);
$session=$ops->login(['username'=>' '.strtoupper($na).' ','password'=>$pw],'127.0.0.1',Ids::v4());
$p=$ops->principalFor($session['access_token']);
$claims=(new Tokens($cfg['jwt_secret']))->verify($session['access_token'],time());
$device=$auth->enroll($auth->principalFor($user['access_token']),[],Ids::v4(),'127.0.0.1');
$d=$refresh($device['device_refresh_token'],'device');
$denials=[outcome(fn()=>$auth->principalFor($session['access_token']))['status'],
 outcome(fn()=>$ops->principalFor($user['access_token']))['status'],
 outcome(fn()=>$ops->principalFor($d['access_token']))['status'],
 outcome(fn()=>$refresh($session['refresh_token'],'user'))['status'],
 outcome(fn()=>$ops->refresh(['refresh_token'=>$user['refresh_token']],'127.0.0.1',Ids::v4()))['status']];
echo json_encode(['same_operator'=>$p->operatorId===$operator['operator_id'],
 'claims'=>[$claims['kind'],$claims['aud'],$claims['tid']], 'denials'=>$denials,
 'customers'=>(int)$db->queryValue('SELECT COUNT(*) FROM users WHERE tenant_id=?',[$ta])]);
""")
        self.assertEqual(result, {"same_operator": True,
                                 "claims": ["operator", "platform", "00000000-0000-4000-8000-000000000001"],
                                 "denials": [401, 401, 401, 401, 401], "customers": 2})

    def test_operator_refresh_rotates_once_and_reuse_revokes_the_family(self):
        result = self._scenario("""
$ops=new OperatorAuth($db,$log,$cfg); $o=OperatorAuth::create($db,$na,$hash);
$s=$ops->login(['username'=>$na,'password'=>$pw],'127.0.0.1',Ids::v4());
$r=$ops->refresh(['refresh_token'=>$s['refresh_token']],'127.0.0.1',Ids::v4());
$before=outcome(fn()=>$ops->principalFor($r['access_token']))['status'];
$reuse=outcome(fn()=>$ops->refresh(['refresh_token'=>$s['refresh_token']],'127.0.0.1',Ids::v4()));
echo json_encode(['rotated'=>$s['refresh_token']!==$r['refresh_token'],'before'=>$before,
 'reuse'=>$reuse['status'],'after'=>outcome(fn()=>$ops->principalFor($r['access_token']))['status'],
 'live'=>(int)$db->queryValue('SELECT COUNT(*) FROM operator_sessions WHERE operator_id=? AND revoked_at IS NULL',[$o['operator_id']])]);
""")
        self.assertEqual(result, {"rotated": True, "before": 200, "reuse": 401, "after": 401, "live": 0})

    def test_disabling_an_operator_stops_access_and_refresh_without_customer_effects(self):
        result = self._scenario("""
$ops=new OperatorAuth($db,$log,$cfg); $o=OperatorAuth::create($db,$na,$hash);
$s=$ops->login(['username'=>$na,'password'=>$pw],'127.0.0.1',Ids::v4());
$c=$login($na);
$db->execute('UPDATE platform_operators SET enabled=0 WHERE id=?',[$o['operator_id']]);
echo json_encode(['access'=>outcome(fn()=>$ops->principalFor($s['access_token']))['status'],
 'refresh'=>outcome(fn()=>$ops->refresh(['refresh_token'=>$s['refresh_token']],'127.0.0.1',Ids::v4()))['status'],
 'login'=>outcome(fn()=>$ops->login(['username'=>$na,'password'=>$pw],'127.0.0.1',Ids::v4()))['status'],
 'customer'=>outcome(fn()=>$auth->principalFor($c['access_token']))['status']]);
""")
        self.assertEqual(result, {"access": 401, "refresh": 401, "login": 401, "customer": 200})

    def test_operator_logout_revokes_only_its_own_family(self):
        result = self._scenario("""
$ops=new OperatorAuth($db,$log,$cfg); OperatorAuth::create($db,$na,$hash);
$s=$ops->login(['username'=>$na,'password'=>$pw],'127.0.0.1',Ids::v4());
$other=$ops->login(['username'=>$na,'password'=>$pw],'127.0.0.1',Ids::v4());
$p=$ops->principalFor($s['access_token']); $ops->logout($p,Ids::v4(),'127.0.0.1');
echo json_encode(['closed'=>outcome(fn()=>$ops->principalFor($s['access_token']))['status'],
 'other'=>outcome(fn()=>$ops->principalFor($other['access_token']))['status']]);
""")
        self.assertEqual(result, {"closed": 401, "other": 200})

    def test_bootstrap_first_operator_is_one_shot(self):
        scratch = "irica_bootstrap_" + uuid.uuid4().hex[:12]
        self._sql("CREATE DATABASE `%s` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" % scratch,
                  select_db=False)
        env = {"IRICA_PRIVATE_DB": scratch, "IRICA_PRIVATE_USER": "root",
               "IRICA_PRIVATE_PASSWORD": self.password}
        try:
            run = self._run([self.php, str(HOST / "bin" / "migrate.php"), "--quiet"], env)
            self.assertEqual(run.returncode, 0, self._redact(run.stderr))
            result = self._scenario("""
$first=OperatorAuth::create($db,$na,$hash,true);
$again=outcome(fn()=>OperatorAuth::create($db,$nb,$hash,true));
echo json_encode(['first'=>Ids::isUuid($first['operator_id']),'again'=>$again['status']]);
""", env)
            self.assertEqual(result, {"first": True, "again": 403})
        finally:
            self._sql("DROP DATABASE `%s`;" % scratch, select_db=False)

    def test_operator_bootstrap_is_consumed_by_host_creation_and_never_rearms(self):
        result = self._scenario("""
$o=OperatorAuth::create($db,$na,$hash);
$second=outcome(fn()=>OperatorAuth::create($db,$nb,$hash,true));
$db->execute('UPDATE platform_operators SET enabled=0 WHERE id=?',[$o['operator_id']]);
$disabled=outcome(fn()=>OperatorAuth::create($db,$nb,$hash,true));
echo json_encode(['second'=>$second['status'],'disabled'=>$disabled['status'],
 'consumed'=>(int)$db->queryValue('SELECT bootstrap_consumed FROM platform_operator_state WHERE id=1')]);
""")
        self.assertEqual(result, {"second": 403, "disabled": 403, "consumed": 1})

    def test_operator_tokens_require_exact_realm_binding_and_live_version(self):
        result = self._scenario("""
$ops=new OperatorAuth($db,$log,$cfg); $o=OperatorAuth::create($db,$na,$hash);
$s=$ops->login(['username'=>$na,'password'=>$pw],'127.0.0.1',Ids::v4());
$signer=new Tokens($cfg['jwt_secret']); $claims=$signer->verify($s['access_token'],time()); $out=[];
foreach (['kind'=>'user','aud'=>'sync','tid'=>$ta,'sub'=>$ua,'ver'=>999] as $key=>$value) {
 $altered=$claims; $altered[$key]=$value;
 $out[]=outcome(fn()=>$ops->principalFor($signer->sign($altered)))['status'];
}
$db->execute('UPDATE platform_operators SET token_version=token_version+1 WHERE id=?',[$o['operator_id']]);
$out[]=outcome(fn()=>$ops->principalFor($s['access_token']))['status'];
echo json_encode($out);
""")
        self.assertEqual(result, [401, 401, 401, 401, 401, 401])

    def test_canonical_login_resolves_private_owner_not_configured_tenant(self):
        result = self._scenario("""
$a=outcome(fn()=>$login('  '.strtoupper($na).'  ')); $b=outcome(fn()=>$login($nb));
echo json_encode(['a'=>($a['value']['tenant_id']??null)===$ta,'b'=>($b['value']['tenant_id']??null)===$tb,
 'user'=>($b['value']['user']['id']??null)===$ub,'identity'=>($b['value']['user_id']??null)===$ub]);
""")
        self.assertEqual(result, {"a": True, "b": True, "user": True, "identity": True})

    def test_unknown_login_does_not_depend_on_configured_tenant(self):
        result = self._scenario("""
$cfg['tenant_slug']='not-created'; $auth=new Auth($db,$log,$cfg);
echo json_encode(outcome(fn()=>$auth->login(['username'=>'missing-'.$nonce,'password'=>$pw],
 '127.0.0.1',Ids::v4())));
""")
        self.assertEqual(result, {"status": 401, "code": "AUTH_REQUIRED"})

    def test_missing_pending_suspended_workspace_never_issues_session(self):
        result = self._scenario("""
$out=[];
foreach (['pending','suspended','missing'] as $state) {
 if ($state==='missing') {$db->execute('DELETE FROM account_workspaces WHERE tenant_id=?',[$ta]);}
 else {$db->execute('UPDATE account_workspaces SET state=? WHERE tenant_id=?',[$state,$ta]);}
 $out[$state]=outcome(fn()=>$login($na))['status'];
}
$out['sessions']=(int)$db->queryValue('SELECT COUNT(*) FROM auth_sessions WHERE tenant_id=?',[$ta]);
$out['last_login']=$db->queryValue('SELECT last_login_at FROM users WHERE id=?',[$ua]);
echo json_encode($out);
""")
        for state in ("pending", "suspended", "missing"):
            self.assertGreaterEqual(result[state], 400, state)
        self.assertEqual(result["sessions"], 0)
        self.assertIsNone(result["last_login"])

    def test_wrong_password_does_not_reveal_workspace_state(self):
        result = self._scenario("""
$db->execute('UPDATE account_workspaces SET state=? WHERE tenant_id=?',['pending',$ta]);
echo json_encode(outcome(fn()=>$auth->login(['username'=>$na,'password'=>'wrong-synthetic'],
 '127.0.0.1',Ids::v4())));
""")
        self.assertEqual(result, {"status": 401, "code": "AUTH_REQUIRED"})

    def test_second_login_in_owner_tenant_cannot_authenticate(self):
        result = self._scenario("echo json_encode(outcome(fn()=>$login($nx))['status']);")
        self.assertGreaterEqual(result, 400)

    def test_reserved_and_blocked_registry_entries_cannot_login(self):
        result = self._scenario("""
$out=[]; foreach (['reserved','blocked'] as $state) {
 $db->execute('UPDATE login_names SET state=? WHERE canonical=?',[$state,$na]);
 $out[$state]=outcome(fn()=>$login($na))['status'];
} echo json_encode($out);
""")
        for state in ("reserved", "blocked"):
            self.assertGreaterEqual(result[state], 400, state)

    def test_workspace_change_stops_user_device_access_and_refresh(self):
        result = self._scenario("""
$a=$login($na); $p=$auth->principalFor($a['access_token']);
$d=$auth->enroll($p,[],Ids::v4(),'127.0.0.1');
$dr=$refresh($d['device_refresh_token'],'device');
$db->execute('UPDATE account_workspaces SET state=? WHERE tenant_id=?',['suspended',$ta]);
$out=[];
$out['user']=outcome(fn()=>$auth->principalFor($a['access_token']))['status'];
$out['device']=outcome(fn()=>$auth->principalFor($dr['access_token']))['status'];
$out['user_refresh']=outcome(fn()=>$refresh($a['refresh_token'],'user'))['status'];
$out['device_refresh']=outcome(fn()=>$refresh($dr['refresh_token'],'device'))['status'];
$out['refresh_claimed']=(int)$db->queryValue('SELECT COUNT(*) FROM auth_sessions WHERE tenant_id=? AND used_at IS NOT NULL',[$ta]);
echo json_encode($out);
""")
        for kind in ("user", "device", "user_refresh", "device_refresh"):
            self.assertGreaterEqual(result[kind], 400, kind)
        self.assertEqual(result["refresh_claimed"], 1)

    def test_disabled_owner_stops_device_refresh_and_access(self):
        result = self._scenario("""
$a=$login($na); $p=$auth->principalFor($a['access_token']);
$d=$auth->enroll($p,[],Ids::v4(),'127.0.0.1'); $dr=$refresh($d['device_refresh_token'],'device');
$db->execute('UPDATE users SET status=? WHERE id=?',['disabled',$ua]);
echo json_encode(['access'=>outcome(fn()=>$auth->principalFor($dr['access_token']))['status'],
 'refresh'=>outcome(fn()=>$refresh($dr['refresh_token'],'device'))['status']]);
""")
        self.assertGreaterEqual(result["access"], 400)
        self.assertGreaterEqual(result["refresh"], 400)

    def test_mixed_device_and_foreign_user_denied_before_device_touch(self):
        result = self._scenario("""
$a=$login($na);
// Mint a valid B session independently of login resolution, which has its own test.
$cfgB=$cfg; $cfgB['tenant_slug']=$nb; $authB=new Auth($db,$log,$cfgB);
$b=$authB->login(['username'=>$nb,'password'=>$pw],'127.0.0.1',Ids::v4());
$p=$auth->principalFor($a['access_token']);
$d=$auth->enroll($p,[],Ids::v4(),'127.0.0.1'); $dr=$refresh($d['device_refresh_token'],'device');
$device=$auth->principalFor($dr['access_token']);
$good=outcome(fn()=>$auth->actingUser(requestWith($a['access_token']),$device))['status'];
$db->execute('UPDATE sync_devices SET last_seen_at=NULL,last_auth_at=NULL WHERE id=?',[$device->deviceId]);
$request=new Request('POST','/v1/sync/mutations',[],
 ['authorization'=>'Bearer '.$dr['access_token'],'x-acting-user'=>'Bearer '.$b['access_token']],
 '{}','127.0.0.1',Ids::v4());
$bad=outcome(function() use ($auth,$request) {
 $device=$auth->authenticate($request); return $auth->actingUser($request,$device);
})['status'];
$touch=$db->queryOne('SELECT last_seen_at,last_auth_at FROM sync_devices WHERE id=?',[$device->deviceId]);
echo json_encode(['same'=>$good,'foreign'=>$bad,'touch'=>$touch]);
""")
        self.assertEqual(result["same"], 200)
        self.assertGreaterEqual(result["foreign"], 400)
        self.assertEqual(result["touch"], {"last_seen_at": None, "last_auth_at": None})

    def test_customer_admin_cannot_trigger_whole_database_backup(self):
        index_path = json.dumps((HOST / "public" / "index.php").as_posix())
        result = self._scenario("""
$a=$login($na); $p=$auth->principalFor($a['access_token']);
$d=$auth->enroll($p,[],Ids::v4(),'127.0.0.1'); $dr=$refresh($d['device_refresh_token'],'device');
$db->execute('UPDATE sync_devices SET last_seen_at=NULL,last_auth_at=NULL WHERE id=?',[$d['sync_device_id']]);
// Never permit the legacy backup path to contact an external recipient in RED.
putenv('TELEGRAM_BOT_TOKEN='); putenv('TELEGRAM_CHAT_ID=');
$_SERVER=['REQUEST_METHOD'=>'POST','REQUEST_URI'=>'/v1/admin/backup','HTTPS'=>'on',
 'REMOTE_ADDR'=>'127.0.0.1','HTTP_AUTHORIZATION'=>'Bearer '.$dr['access_token'],
 'HTTP_X_ACTING_USER'=>'Bearer '.$a['access_token']]; $_GET=[];
ob_start(); require %s; $body=json_decode(ob_get_clean(),true);
$touch=$db->queryOne('SELECT last_seen_at,last_auth_at FROM sync_devices WHERE id=?',[$d['sync_device_id']]);
echo json_encode(['status'=>http_response_code(),'code'=>$body['error']['code']??null,'touch'=>$touch]);
""" % index_path)
        self.assertEqual(result["status"], 403)
        self.assertEqual(result["code"], "PERMISSION_DENIED")
        self.assertEqual(result["touch"], {"last_seen_at": None, "last_auth_at": None})

    def test_cli_creates_distinct_private_workspaces(self):
        first, second = "cli-" + uuid.uuid4().hex[:12], "cli-" + uuid.uuid4().hex[:12]
        for script, name in (("create-admin.php", first), ("create-user.php", second)):
            run = self._cli(script, "--username=" + name)
            self.assertEqual(run.returncode, 0, self._redact(run.stderr))
        result = self._scenario("""
$out=[]; foreach (%s as $name) {
 $result=outcome(fn()=>$login($name));
 $out[]=['status'=>$result['status'],'tenant'=>$result['value']['tenant_id']??null,
  'role'=>$result['value']['user']['role']??null,'change'=>$result['value']['must_change_password']??null];
} echo json_encode($out);
""" % json.dumps([first, second]))
        self.assertEqual([row["status"] for row in result], [200, 200])
        self.assertNotEqual(result[0]["tenant"], result[1]["tenant"])
        self.assertEqual([row["role"] for row in result], ["admin", "user"])
        self.assertEqual([row["change"] for row in result], [False, False])

    def test_cli_cannot_join_an_existing_workspace(self):
        seeded = self._scenario("echo json_encode(['slug'=>$na,'tenant'=>$ta]);")
        name = "cli-" + uuid.uuid4().hex[:12]
        run = self._cli("create-user.php", "--username=" + name,
                        "--tenant-slug=" + seeded["slug"])
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(self._sql("SELECT COUNT(*) FROM users WHERE username='%s';" % name), "0")

    def test_cli_case_duplicate_does_not_leave_an_orphan_tenant(self):
        seeded = self._scenario("echo json_encode(['name'=>$na]);")
        slug = "unused-" + uuid.uuid4().hex[:12]
        run = self._cli("create-admin.php", "--username=" + seeded["name"].upper(),
                        "--tenant-slug=" + slug)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(self._sql("SELECT COUNT(*) FROM tenants WHERE slug='%s';" % slug), "0")

    def test_cli_reset_targets_exact_owner_and_revokes_devices_without_elevation(self):
        seeded = self._scenario("""
$db->execute('UPDATE memberships SET role=? WHERE tenant_id=? AND user_id=?',['user',$ta,$ua]);
$a=$login($na); $p=$auth->principalFor($a['access_token']);
$d=(new Devices($db,$log,$cfg))->selfEnroll($p,[],Ids::v4(),'127.0.0.1');
echo json_encode(['name'=>$na,'user'=>$ua,'tenant'=>$ta,'device'=>$d['sync_device_id']]);
""")
        run = self._cli("create-admin.php", "--username=" + seeded["name"],
                        "--reset-password", password=self.customer_password + "-rotated")
        self.assertEqual(run.returncode, 0, self._redact(run.stderr))
        result = self._scenario("""
$name=%s; $u=$db->queryOne('SELECT * FROM users WHERE username=?',[$name]);
$w=$db->queryOne('SELECT * FROM account_workspaces WHERE owner_user_id=?',[$u['id']]);
$d=$db->queryOne('SELECT status FROM sync_devices WHERE id=?',[%s]);
echo json_encode(['user'=>$u['id'],'tenant'=>$u['tenant_id'],'role'=>$auth->roleOf($u['tenant_id'],$u['id']),
 'owner'=>$w['owner_user_id'],'state'=>$w['state'],'password'=>password_verify($pw.'-rotated',$u['password_hash']),
 'change'=>(bool)$u['must_change_password'],'device'=>$d['status'],
 'live_sessions'=>(int)$db->queryValue('SELECT COUNT(*) FROM auth_sessions WHERE tenant_id=? AND revoked_at IS NULL',[$u['tenant_id']])]);
""" % (json.dumps(seeded["name"]), json.dumps(seeded["device"])))
        self.assertEqual(result, {"user": seeded["user"], "tenant": seeded["tenant"],
                                 "role": "user", "owner": seeded["user"], "state": "active",
                                 "password": True, "change": False, "device": "revoked",
                                 "live_sessions": 0})

    def test_cli_reset_unknown_name_never_renames_an_existing_admin(self):
        seeded = self._scenario("echo json_encode(['slug'=>$na]);")
        name = "missing-" + uuid.uuid4().hex[:12]
        run = self._cli("create-admin.php", "--username=" + name, "--reset-password",
                        "--tenant-slug=" + seeded["slug"])
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(self._sql("SELECT COUNT(*) FROM users WHERE username='%s';" % name), "0")

    def test_cli_reset_mismatch_is_side_effect_free(self):
        seeded = self._scenario("echo json_encode(['name'=>$na,'user'=>$ua]);")
        before = self._sql("SELECT password_hash,token_version FROM users WHERE id='%s';" % seeded["user"])
        for option in ("--role=user", "--tenant-slug=not-the-account"):
            with self.subTest(option=option):
                run = self._cli("create-admin.php", "--username=" + seeded["name"],
                                "--reset-password", option)
                self.assertNotEqual(run.returncode, 0)
                after = self._sql("SELECT password_hash,token_version FROM users WHERE id='%s';" % seeded["user"])
                self.assertTrue(before == after, "Rejected reset mutated the credential")

    def test_cli_reset_does_not_activate_a_suspended_workspace(self):
        seeded = self._scenario("""
$db->execute('UPDATE account_workspaces SET state=? WHERE tenant_id=?',['suspended',$ta]);
echo json_encode(['name'=>$na,'tenant'=>$ta]);
""")
        run = self._cli("create-admin.php", "--username=" + seeded["name"], "--reset-password")
        self.assertEqual(run.returncode, 0, self._redact(run.stderr))
        result = self._scenario("echo json_encode(outcome(fn()=>$login(%s))['status']);" % json.dumps(seeded["name"]))
        self.assertEqual(result, 403)
        self.assertEqual(self._sql("SELECT state FROM account_workspaces WHERE tenant_id='%s';" % seeded["tenant"]), "suspended")

    def test_concurrent_cli_same_canonical_name_has_one_atomic_winner(self):
        import concurrent.futures
        name = "race-" + uuid.uuid4().hex[:12]
        before = int(self._sql("SELECT COUNT(*) FROM tenants;"))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self._cli, "create-user.php", "--username=" + value)
                       for value in (name, name.upper())]
            runs = [future.result() for future in futures]
        self.assertEqual(sorted(run.returncode for run in runs), [0, 1])
        self.assertEqual(int(self._sql("SELECT COUNT(*) FROM tenants;")), before + 1)
        self.assertEqual(self._sql("SELECT COUNT(*) FROM login_names WHERE canonical='%s';" % name), "1")

    def test_migration_reserves_trimmed_case_collisions_without_choosing_owner(self):
        scratch = "irica_names_" + uuid.uuid4().hex[:12]
        self._sql("CREATE DATABASE `%s` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" % scratch,
                  select_db=False)
        try:
            for path in sorted((HOST / "migrations").glob("00[1-4]_*.sql")):
                self._sql("USE `%s`;\n%s" % (scratch, path.read_text(encoding="utf-8")), select_db=False)
            # Let the actual runner apply only 005 against this pre-existing legacy schema.
            import hashlib
            for path in sorted((HOST / "migrations").glob("00[1-4]_*.sql")):
                checksum = hashlib.sha256(path.read_bytes()).hexdigest()
                self._sql("USE `%s`; INSERT INTO schema_migrations (version,description,checksum,applied_at) "
                          "VALUES ('%s','fixture','%s',UTC_TIMESTAMP());" % (scratch, path.stem, checksum), select_db=False)
            seed = "USE `%s`;" % scratch
            for name in (" Sample-User ", "sample-user", " Valid-User ", "invalid name", "café",
                         "\x00\t Control-User\r\n\v", "bad\tinner", "\fnot-trimmed", "Kelvin"):
                tenant, user = str(uuid.uuid4()), str(uuid.uuid4())
                encoded_name = name.encode("utf-8").hex()
                seed += "INSERT INTO tenants (id,slug,name,status,created_at,updated_at) VALUES ('%s','%s','fixture','active',UTC_TIMESTAMP(),UTC_TIMESTAMP());" % (tenant, tenant)
                seed += "INSERT INTO users (id,tenant_id,username,password_hash,created_at,updated_at) VALUES ('%s','%s',CONVERT(0x%s USING utf8mb4),'not-a-credential',UTC_TIMESTAMP(),UTC_TIMESTAMP());" % (user, tenant, encoded_name)
            self._sql(seed, select_db=False)
            run = self._run([self.php, str(HOST / "bin" / "migrate.php"), "--quiet"],
                            {"IRICA_PRIVATE_DB": scratch})
            self.assertEqual(run.returncode, 0, self._redact(run.stderr))
            rows = self._sql("USE `%s`; SELECT canonical,state,user_id IS NULL FROM login_names ORDER BY canonical;" % scratch,
                             select_db=False)
            self.assertEqual(rows.splitlines(), ["control-user\treserved\t0", "sample-user\tblocked\t1", "valid-user\treserved\t0"])
        finally:
            self._sql("DROP DATABASE `%s`;" % scratch, select_db=False)

    def test_workspace_cannot_bind_a_foreign_tenant_owner(self):
        result = self._scenario("""
$db->execute('DELETE FROM account_workspaces WHERE tenant_id IN (?,?)',[$ta,$tb]);
$rejected=false;
try {$db->execute('INSERT INTO account_workspaces (tenant_id,owner_user_id,state,created_at,updated_at) VALUES (?,?,?,?,?)',
 [$ta,$ub,'active',$now,$now]);
} catch (PDOException $error) {$rejected=true;}
echo json_encode(['rejected'=>$rejected]);
""")
        self.assertEqual(result, {"rejected": True})

    def test_workspace_owner_and_tenant_are_immutable(self):
        result = self._scenario("""
$out=[];
foreach (['owner_user_id'=>$ux,'tenant_id'=>Ids::v4()] as $column=>$value) {
 try {$db->execute('UPDATE account_workspaces SET '.$column.'=? WHERE tenant_id=?',[$value,$ta]); $out[$column]=false;}
 catch (PDOException $error) {$out[$column]=true;}
}
echo json_encode($out);
""")
        self.assertEqual(result, {"owner_user_id": True, "tenant_id": True})

    def test_password_reset_rejects_stale_session_issuance(self):
        result = self._scenario("""
Workspace::rotatePassword($db,$na,Auth::hashPassword($pw.'-rotated'));
// Reproduce a password check completed before reset reaching issuance afterward.
$issue=new ReflectionMethod(Auth::class,'issue'); $issue->setAccessible(true);
$result=outcome(fn()=>$issue->invoke($auth,'user',$ta,$ua,null,1,null));
echo json_encode(['status'=>$result['status'],
 'sessions'=>(int)$db->queryValue('SELECT COUNT(*) FROM auth_sessions WHERE tenant_id=?',[$ta])]);
""")
        self.assertEqual(result, {"status": 401, "sessions": 0})

    def test_password_reset_rejects_stale_principal_device_enrollment(self):
        result = self._scenario("""
$a=$login($na); $p=$auth->principalFor($a['access_token']);
Workspace::rotatePassword($db,$na,Auth::hashPassword($pw.'-rotated'));
$out=[]; foreach ([$auth,new Devices($db,$log,$cfg)] as $service) {
 $out[]=outcome(fn()=>($service instanceof Auth ? $service->enroll($p,[],Ids::v4(),'127.0.0.1')
  : $service->selfEnroll($p,[],Ids::v4(),'127.0.0.1')))['status'];
}
echo json_encode(['statuses'=>$out,
 'devices'=>(int)$db->queryValue('SELECT COUNT(*) FROM sync_devices WHERE tenant_id=?',[$ta])]);
""")
        self.assertEqual(result, {"statuses": [401, 401], "devices": 0})

    def test_setup_key_alone_cannot_create_customers_or_echo_passwords(self):
        setup_path = json.dumps((HOST / "public" / "setup.php").as_posix())
        for action in ("create_admin", "create_user"):
            with self.subTest(action=action):
                result = self._scenario("""
$name='setup-'.$nonce;
$_GET=[]; $_POST=['key'=>getenv('IRICA_PRIVATE_JWT'),'action'=>%s,
 'username'=>$name,'password'=>$pw,'tenant_slug'=>$na];
$_SERVER=['REQUEST_METHOD'=>'POST'];
ob_start(); require %s; $html=ob_get_clean();
echo json_encode(['status'=>http_response_code(),'leaked'=>str_contains($html,$pw),
 'created'=>(int)$db->queryValue('SELECT COUNT(*) FROM users WHERE username=?',[$name])]);
""" % (json.dumps(action), setup_path))
                self.assertEqual(result, {"status": 404, "leaked": False, "created": 0})

    def test_two_devices_of_same_owner_authenticate_to_same_dataset(self):
        result = self._scenario("""
$a=$login($na); $p=$auth->principalFor($a['access_token']); $out=[];
foreach (['one','two'] as $name) {
 $d=$auth->enroll($p,['name'=>$name],Ids::v4(),'127.0.0.1');
 $r=$refresh($d['device_refresh_token'],'device'); $dp=$auth->principalFor($r['access_token']);
 $out[]=['tenant'=>$dp->tenantId===$ta,'user'=>$dp->userId===$ua,'id'=>$dp->deviceId];
} echo json_encode($out);
""")
        self.assertTrue(all(row["tenant"] and row["user"] for row in result))
        self.assertNotEqual(result[0]["id"], result[1]["id"])

    def test_tenant_suspension_stops_existing_credentials(self):
        result = self._scenario("""
$a=$login($na); $db->execute('UPDATE tenants SET status=? WHERE id=?',['suspended',$ta]);
echo json_encode(['access'=>outcome(fn()=>$auth->principalFor($a['access_token']))['status'],
 'refresh'=>outcome(fn()=>$refresh($a['refresh_token'],'user'))['status']]);
""")
        self.assertGreaterEqual(result["access"], 400)
        self.assertGreaterEqual(result["refresh"], 400)

    def test_device_with_foreign_owner_cannot_refresh(self):
        result = self._scenario("""
$a=$login($na); $p=$auth->principalFor($a['access_token']);
$d=$auth->enroll($p,[],Ids::v4(),'127.0.0.1');
$db->execute('UPDATE sync_devices SET owner_user_id=? WHERE id=?',[$ub,$d['sync_device_id']]);
echo json_encode(outcome(fn()=>$refresh($d['device_refresh_token'],'device'))['status']);
""")
        self.assertGreaterEqual(result, 400)

    def test_removed_registry_stops_existing_credentials(self):
        result = self._scenario("""
$a=$login($na); $db->execute('DELETE FROM login_names WHERE user_id=?',[$ua]);
echo json_encode(['access'=>outcome(fn()=>$auth->principalFor($a['access_token']))['status'],
 'refresh'=>outcome(fn()=>$refresh($a['refresh_token'],'user'))['status']]);
""")
        self.assertGreaterEqual(result["access"], 400)
        self.assertGreaterEqual(result["refresh"], 400)


if __name__ == "__main__":
    unittest.main()
