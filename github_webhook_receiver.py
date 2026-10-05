"""Authenticated contractual acceptance only. No financial authority."""
import hashlib
import logging
import os
import re
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, request
import db
from external_acceptance import ExternalAcceptanceError, process_github_acceptance_event
from github_acceptance import GitHubAuthenticationError, GitHubPayloadError
from github_acceptance import parse_authenticated_payload, normalize_pull_request_event

STOP_FILE = Path(__file__).resolve().parent / 'data' / 'STOP'
audit = logging.getLogger('canonical.github.acceptance')

def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=1024 * 1024,
        GITHUB_REPOSITORY=os.getenv('GITHUB_REPOSITORY', ''),
        GITHUB_WEBHOOK_SECRET=os.getenv('GITHUB_WEBHOOK_SECRET', ''),
        ACCEPTANCE_ENVIRONMENT=os.getenv('ACCEPTANCE_ENVIRONMENT', ''),
        GITHUB_ACCEPTANCE_ENABLED=os.getenv('GITHUB_ACCEPTANCE_ENABLED') == 'true',
    )
    if config:
        app.config.update(config)

    def ready():
        c = app.config
        if STOP_FILE.exists() or not c['GITHUB_ACCEPTANCE_ENABLED']:
            return False
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', c['GITHUB_REPOSITORY']):
            return False
        if not c['GITHUB_WEBHOOK_SECRET'] or c['ACCEPTANCE_ENVIRONMENT'] not in {'TEST', 'SANDBOX', 'PRODUCTION'}:
            return False
        try:
            if not Path(db.DB_PATH).is_file():
                return False
            with db.get_db() as conn:
                for table in ('work_deliveries', 'external_acceptance_events', 'contract_acceptance_decisions'):
                    conn.execute(f'SELECT 1 FROM {table} LIMIT 1')
            return True
        except sqlite3.Error:
            return False

    @app.get('/health')
    def health():
        status = ready()
        return jsonify(service='canonical-github-acceptance', ready=status), 200 if status else 503

    @app.post('/webhook/github')
    def github():
        status = 503
        try:
            if not ready():
                return jsonify(error='acceptance_unavailable'), status
            raw = request.get_data(cache=False)
            payload = parse_authenticated_payload(raw, request.headers.get('X-Hub-Signature-256', ''), app.config['GITHUB_WEBHOOK_SECRET'])
            if request.headers.get('X-GitHub-Event') != 'pull_request':
                status = 400
                return jsonify(error='unsupported_event'), status
            delivery_id = request.headers.get('X-GitHub-Delivery', '')
            if not re.fullmatch(r'[A-Za-z0-9-]{1,128}', delivery_id):
                raise GitHubPayloadError('Invalid delivery ID')
            event = normalize_pull_request_event(payload, delivery_id=delivery_id, expected_repository=app.config['GITHUB_REPOSITORY'])
            # Exact authenticated bytes retained independently of policy conclusions.
            event['github_raw_body'] = raw.decode('utf-8')
            event['github_raw_body_sha256'] = hashlib.sha256(raw).hexdigest()
            if STOP_FILE.exists():
                return jsonify(error='acceptance_unavailable'), status
            result = process_github_acceptance_event(event, environment=app.config['ACCEPTANCE_ENVIRONMENT'])
            status = 200
            return jsonify(event_id=result['event']['event_id'], decision_id=result['decision']['decision_id'], accepted=bool(result['decision']['accepted'])), status
        except GitHubAuthenticationError:
            status = 401
            return jsonify(error='authentication_failed'), status
        except (GitHubPayloadError, TypeError, AttributeError, UnicodeError):
            status = 400
            return jsonify(error='invalid_payload'), status
        except ExternalAcceptanceError:
            status = 409
            return jsonify(error='binding_or_replay_conflict'), status
        except sqlite3.Error:
            status = 503
            return jsonify(error='storage_unavailable'), status

    @app.after_request
    def log_result(response):
        # No bodies, signatures, secrets, or caller-controlled headers.
        if request.method == 'POST':
            audit.info('github_acceptance status=%s', response.status_code)
        return response

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify(error='payload_too_large'), 413
    return app

if __name__ == '__main__':
    from waitress import serve
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    serve(create_app(), host='127.0.0.1', port=8091, threads=4, channel_timeout=30)
