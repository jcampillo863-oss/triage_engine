"""Local source inventory and redacted secret audit; never stages or invokes providers."""
import ast
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'source_review'
ENV={**os.environ,'GIT_OPTIONAL_LOCKS':'0'}

def git(*args):
    result=subprocess.run(['git','-c','safe.directory='+ROOT.as_posix(),*args],cwd=ROOT,env=ENV,capture_output=True,check=True)
    return result.stdout

def paths(command):
    return [item.decode('utf-8') for item in git(*command).split(b'\0') if item]

CANONICAL=set("authenticated_work.py canonical_settlement_engine.py db.py external_acceptance.py github_acceptance.py github_webhook_receiver.py payment_authority.py paypal_adapter.py paypal_capture_service.py paypal_reconciliation_adapter.py paypal_reconciliation_observer.py paypal_sandbox_client.py service_principals.py settlement_eligibility.py validation.py work_delivery.py requirements-canonical-runtime.txt".split())
MANUAL=set("create_sandbox_auth.py paypal_sandbox_callback.py purge_task.py test_chaos.py test_paypal_auth.py test_live_paypal_auth.py test_paypal_capture_order.py test_paypal_create_order.py test_paypal_genuine_capture.py test_paypal_live_reconciliation_observer.py".split())
LEGACY=set("acceptance_policy.py pipeline.py revenue_engine.py settlement_engine.py settlement_webhook.py settlement_worker.py webhook_listener.py paypal_payouts.py dashboard.py git_adapter.py patcher.py proposal_generator.py report_gen.py run_daemon.py run_sweep_and_report.py run_triage.py test_harness.py".split())|MANUAL
OUTPUTS=['STAGING_MANIFEST.txt','EXCLUSION_MANIFEST.json','TRACKED_RUNTIME_REMOVAL_MANIFEST.txt','CLASSIFICATION.json','TRACKED_ANOMALIES.md','LEGACY_SOURCE_POLICY.md','SNAPSHOT_REVIEW_REPORT.md','SECRET_SCAN_REPORT.json','TEST_RESULTS.txt','HUMAN_REVIEW_COMMANDS.md']

def classify(path):
    if path=='data/test_ledger.json':
        return 4,'Generated seen-task ledger written by legacy test_harness; reproducible output, retain locally'
    if path=='data/raw_feed.json':
        return 4,'Historical fetched/mixed opportunity feed, not a curated source fixture; retain locally'
    if path in {'data/mock_feed.json','workspace/task_heavy_task_1500/sfloadmacro.py','workspace/task_heavy_task_1500/test_sfloadmacro.py'}:
        return 2,'Retained static synthetic fixture or supplemental legacy implementation/test pair; not runtime state'
    if path=='.env' or (path.startswith('.env.') and path!='.env.example') or path=='config/endpoints.json':
        return 5,'Local credentials/environment or machine-specific endpoint configuration; retain locally, never stage'
    if path.startswith(('data/','workspace/','sandbox/','.pytest_cache/','.mypy_cache/','.ruff_cache/','venv/','.venv/')) or '__pycache__/' in path or path.endswith(('.pyc','.db','.db-wal','.db-shm','.sqlite','.sqlite3','.log')):
        return 4,'Generated state, history/evidence, cache, virtual environment or temporary workspace; retain locally'
    if path in ('test_bounty.txt','test_trigger.txt'):
        return 4,'Legacy manual workflow input/artifact; not used by approved canonical regression runner; retain locally'
    if path in LEGACY or path.startswith('src/'):
        return 3,'Retain compatibility/manual source with explicit retirement policy; never start/deploy/run by discovery'
    if path in CANONICAL or path in ('.gitignore','config/rules.json'):
        return 1,'Canonical runtime/source or repository safety/static rules configuration'
    if path=='.env.example' or path.endswith(('.md','.sql','.txt')) or path.startswith('source_review/') or path.startswith(('test_','migration_','verify_','inspect_')) or path=='check_db.py' or path=='eligibility_test_support.py':
        return 2,'Tests, migration/schema, documentation or explicit offline/manual review tooling; execution limits apply'
    raise RuntimeError('Unclassified path; review must stop: '+path)

def snapshot():
    tracked=paths(('ls-files','-z'))
    # Includes ignored artifacts by name; ignores virtual environments/caches only for traversal cost/access.
    all_files=set(tracked)
    inaccessible=[]
    for base,dirs,files in os.walk(ROOT,topdown=True,onerror=lambda e:inaccessible.append(Path(e.filename).relative_to(ROOT).as_posix())):
        rel=Path(base).relative_to(ROOT)
        dirs[:]=[d for d in dirs if d not in {'.git','venv','.venv','.pytest_cache','__pycache__'}]
        for name in files:
            all_files.add((rel/name).as_posix())
    for name in ('.pytest_cache/','venv/','__pycache__/','src/__pycache__/'):
        all_files.add(name)
    for name in OUTPUTS:
        all_files.add('source_review/'+name)
    changed=paths(('diff','--name-only','-z','HEAD'))
    untracked=paths(('ls-files','--others','--exclude-standard','-z'))
    records=[]
    for path in sorted(all_files):
        category,reason=classify(path)
        records.append(dict(path=path,category=category,reason=reason,tracked=path in tracked,
            changed=path in changed,untracked_visible=path in untracked,
            disposition='propose_source' if category in (1,2,3) else 'exclude_retain_local'))
    status=git('status','--porcelain=v1','-z','--untracked-files=all')
    inventory_status=[]
    for entry in status.split(b'\0'):
        if not entry:continue
        text=entry.decode()
        if text[:2] not in {'??',' M','M ','A ',' D','D '}:
            raise RuntimeError('Unexpected/ambiguous Git status requires human review')
        path=text[3:]
        category,reason=classify(path)
        inventory_status.append(dict(status=text[:2],path=path,category=category,reason=reason))
    staging=[r['path'] for r in records if r['category'] in (1,2,3) and not r['path'].endswith('/')]
    exclusions=[r for r in records if r['category'] in (4,5)]
    removals=[r['path'] for r in exclusions if r['tracked']]
    (OUT/'STAGING_MANIFEST.txt').write_text('\n'.join(staging)+'\n',encoding='utf-8')
    (OUT/'TRACKED_RUNTIME_REMOVAL_MANIFEST.txt').write_text('\n'.join(removals)+'\n',encoding='utf-8')
    (OUT/'EXCLUSION_MANIFEST.json').write_text(json.dumps(exclusions,indent=2)+'\n',encoding='utf-8')
    (OUT/'CLASSIFICATION.json').write_text(json.dumps(dict(categories={1:'canonical source/runtime',2:'tests/migrations/documentation',3:'retired/guarded compatibility source',4:'runtime/generated/historical evidence',5:'secrets/local configuration'},git_status=inventory_status,files=records,inaccessible_paths=inaccessible),indent=2)+'\n',encoding='utf-8')
    return staging,removals,inventory_status


def secret_scan(staging):
    # Never import a credential-using module or load .env into the process environment.
    known=[]
    env_path=ROOT/'.env'
    if env_path.is_file():
        for line in env_path.read_text(encoding='utf-8-sig',errors='replace').splitlines():
            match=re.match(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)',line)
            if match and re.search(r'SECRET|TOKEN|CLIENT_ID|PASSWORD|API_KEY',match[1],re.I):
                value=match[2].strip().strip("\"'")
                if len(value)>=16:known.append(value)
    # Also identify old credential-shaped fallback literals without printing/persisting values.
    for path in ('dashboard.py','settlement_webhook.py'):
        tree=ast.parse(git('show','HEAD:'+path).decode('utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='getenv' and node.args:
                value=node.args[0]
                if isinstance(value,ast.Constant) and isinstance(value.value,str) and re.fullmatch(r'[A-Za-z0-9_-]{40,}',value.value):
                    known.append(value.value)
    patterns={
        'provider_token':re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{24,})'),
        'private_key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
        'credential_assignment':re.compile(r'(?i)(?:client_secret|github_token|access_token|password|api_key)\s*[:=]\s*[\"\'][A-Za-z0-9_/-]{24,}[\"\']'),
        'credential_shaped_env_key':re.compile(r'os\.getenv\([\"\'][A-Za-z0-9_-]{40,}[\"\']')}
    findings=[]
    scanned=[]
    for path in staging:
        file=ROOT/path
        if not file.exists():continue
        raw=file.read_bytes()
        text=raw.decode('utf-8-sig')
        scanned.append(dict(path=path,sha256=hashlib.sha256(raw).hexdigest()))
        for rule,pattern in patterns.items():
            for match in pattern.finditer(text):
                findings.append(dict(path=path,line=text.count('\n',0,match.start())+1,rule=rule))
        for value in known:
            pos=text.find(value)
            if pos>=0:findings.append(dict(path=path,line=text.count('\n',0,pos)+1,rule='known_local_or_old_credential_match'))
    report=dict(result='PASS' if not findings else 'BLOCKED',findings=findings,scanned_files=scanned,
        method='Current proposed working-tree files: token/private-key/literal patterns plus exact matches against local credential entries and old fallback values; no values or credential hashes reported',
        limitations='Not a proof of absence of unknown/encoded secrets. Reachable history and tracked binary/runtime artifacts are not certified; human index exclusions must be applied before a source-only tree exists.')
    (OUT/'SECRET_SCAN_REPORT.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('Source credential scan:',report['result'],'files:',len(scanned),'findings:',len(findings))
    for finding in findings:print(finding['path'],finding['line'],finding['rule'])
    if findings:raise RuntimeError('Credential finding blocks proposed staging')

if __name__=='__main__':
    staging,removals,status=snapshot()
    secret_scan(staging)
    print('Classified status entries:',len(status),'proposed source paths:',len(staging),'tracked exclusions:',len(removals))
