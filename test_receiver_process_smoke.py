"""Real localhost HTTP + process restart; synthetic signed fixture, no providers."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

root=Path(__file__).resolve().parent
sys.path.insert(0,str(root))
import db
from work_delivery import create_delivery
with tempfile.TemporaryDirectory(prefix='receiver-smoke-') as tmp:
    path=Path(tmp)/'settlement.db'
    src=sqlite3.connect(Path(db.DB_PATH).as_uri()+'?mode=ro',uri=True)
    dest=sqlite3.connect(path)
    src.backup(dest)
    src.close()
    dest.close()
    db.DB_PATH=str(path)
    db.DB_DIR=tmp
    create_delivery(task_id='__http_smoke__',repository='example-owner/example-repo',pull_request_number=998877,
        pull_request_url='https://github.com/example-owner/example-repo/pull/998877',environment='TEST')
    body=json.dumps({'action':'closed','repository':{'full_name':'example-owner/example-repo'},
        'pull_request':{'number':998877,'html_url':'https://github.com/example-owner/example-repo/pull/998877',
            'merged':True,'merge_commit_sha':'a'*40}}).encode()
    sig='sha256='+hmac.new(b'http-smoke-secret',body,hashlib.sha256).hexdigest()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    config=dict(GITHUB_REPOSITORY='example-owner/example-repo',GITHUB_WEBHOOK_SECRET='http-smoke-secret',
        ACCEPTANCE_ENVIRONMENT='TEST',GITHUB_ACCEPTANCE_ENABLED=True)
    code='import db; db.DB_PATH='+repr(str(path))+'; db.DB_DIR='+repr(tmp)+'; from github_webhook_receiver import create_app; from waitress import serve; serve(create_app('+repr(config)+'),host="127.0.0.1",port='+str(port)+')'
    results=[]
    for run in range(2):
        process=subprocess.Popen([str(root/'venv/Scripts/python.exe'),'-c',code],cwd=root,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            url='http://127.0.0.1:'+str(port)
            for attempt in range(50):
                try:
                    with urllib.request.urlopen(url+'/health',timeout=1) as response:
                        assert response.status==200
                    break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError('Receiver startup failed')
                    time.sleep(.1)
            else:
                raise RuntimeError('Receiver health timeout')
            req=urllib.request.Request(url+'/webhook/github',data=body,headers={
                'X-Hub-Signature-256':sig,'X-GitHub-Delivery':'localhost-smoke-delivery','X-GitHub-Event':'pull_request'})
            with urllib.request.urlopen(req,timeout=3) as response:
                assert response.status==200
                result=json.load(response)
                assert result['accepted'] is True
                results.append(result)
        finally:
            process.terminate()
            process.wait(timeout=10)
    assert results[0]==results[1]
    c=sqlite3.connect(path)
    assert c.execute("SELECT count(*) FROM external_acceptance_events WHERE external_event_id='localhost-smoke-delivery'").fetchone()[0]==1
    assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    c.close()
    print('Waitress localhost startup, health, signed HTTP, process restart replay, shutdown: PASS')
    print('External GitHub delivery: NOT exercised; payload was a synthetic signed fixture')
