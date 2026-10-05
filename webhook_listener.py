raise RuntimeError("Retired legacy module; use canonical authorities")
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from pipeline import TriagePipeline
from revenue_engine import RevenueEngine, SettlementState

pipeline = TriagePipeline()
revenue_engine = RevenueEngine()

class WebhookHandler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, body: dict):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body, indent=2).encode("utf-8"))

    def do_GET(self):
        """Health check endpoint to verify server status on port 8080."""
        parsed_path = urlparse(self.path).path.rstrip("/")
        if parsed_path in ("", "/health"):
            self._send_json(200, {"status": "healthy", "service": "webhook_listener"})
        else:
            self._send_json(404, {"error": "Endpoint not found"})

    def do_POST(self):
        # Normalize path to ignore trailing slashes or query strings
        parsed_path = urlparse(self.path).path.rstrip("/")

        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length).decode("utf-8")

        try:
            payload = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON payload"})
            return

        # Endpoint 1: Incoming CI / GitHub Execution Webhook
        if parsed_path == "/webhook/github":
            task_id = payload.get("task_id", f"task_gh_{payload.get('after', 'commit')[:8]}")
            git_hash = payload.get("after") or payload.get("head_commit", {}).get("id", "c0ff3312345")
            pytest_code = payload.get("pytest_exit_code", 0)
            syntax_valid = payload.get("syntax_valid", True)
            compilation_valid = payload.get("compilation_valid", True)
            diff_policy_passed = payload.get("diff_policy_passed", True)
            amount_cents = payload.get("amount_cents", 2500)

            result = pipeline.process_task(
                task_id=task_id,
                git_commit_hash=git_hash,
                pytest_exit_code=pytest_code,
                syntax_valid=syntax_valid,
                compilation_valid=compilation_valid,
                diff_policy_passed=diff_policy_passed,
                amount_cents=amount_cents,
                raw_payload=payload
            )
            self._send_json(200, {"status": "processed", "pipeline_result": result})

        # Endpoint 2: Payment Provider Capture/Completion Webhook
        elif parsed_path == "/webhook/payment":
            settlement_id = payload.get("settlement_id")
            if not settlement_id:
                self._send_json(400, {"error": "Missing settlement_id in payload"})
                return

            try:
                updated_record = revenue_engine.process_provider_webhook(settlement_id, payload)
                self._send_json(200, {
                    "status": "reconciled",
                    "settlement_id": updated_record.settlement_id,
                    "state": updated_record.state.value,
                    "provider_tx_id": updated_record.provider_tx_id,
                    "updated_at": updated_record.updated_at
                })
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        else:
            self._send_json(404, {"error": "Endpoint not found"})

def run_server(port=8080):
    # Enable socket address reuse to prevent port-lock delays during rapid restarts
    HTTPServer.allow_reuse_address = True
    server = HTTPServer(("0.0.0.0", port), WebhookHandler)
    print(f"Webhook listener listening on http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer shutting down.")
        server.server_close()

if __name__ == "__main__":
    run_server(8080)