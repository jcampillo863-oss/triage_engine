import os
import json
import urllib.parse
import re
import requests
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from git_adapter import execute_git_delivery
from patcher import process_task_patch

# Legacy financial/webhook routes are retired.
PORT = 8080
MATCHED_JOBS_FILE = os.path.join("data", "matched_jobs.json")
LEDGER_FILE = os.path.join("data", "ledger.json")
PROPOSALS_DIR = os.path.join("data", "proposals")
PATCHES_DIR = os.path.join("data", "patches")
USD_TO_AUD_RATE = 1.50

class DashboardHandler(BaseHTTPRequestHandler):

    def _set_headers(self, content_type="text/html", status=200):
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path.rstrip('/')
        if not path:
            path = "/"

        if path == "/":
            print("[DEBUG] Root path hit successfully!")
            self._set_headers("text/html")
            self.wfile.write(self.render_dashboard().encode("utf-8"))

        elif path == "/api/jobs":
            self._set_headers("application/json")
            jobs = self.load_matched_jobs()
            self.wfile.write(json.dumps(jobs).encode("utf-8"))

        elif path == "/api/ledger":
            self._set_headers("application/json")
            ledger = self.load_ledger()
            self.wfile.write(json.dumps(ledger).encode("utf-8"))

        elif path == "/api/analytics":
            self._set_headers("application/json")
            jobs = self.load_matched_jobs()
            ledger = self.load_ledger()

            total_usd = sum(j.get("bounty_usd", 0) for j in jobs)
            high_value_count = sum(1 for j in jobs if j.get("bounty_usd", 0) >= 1000)

            settled_usd = sum(
                entry.get("bounty_usd", 0)
                for entry in ledger
                if isinstance(entry, dict) and entry.get("bounty_usd", 0) > 0
            )

            dispatched_count = len(ledger)

            analytics = {
                "total_jobs": len(jobs),
                "total_usd": total_usd,
                "total_aud": total_usd * USD_TO_AUD_RATE,
                "high_value_count": high_value_count,
                "settled_usd": settled_usd,
                "settled_aud": settled_usd * USD_TO_AUD_RATE,
                "dispatched_count": dispatched_count,
                "coherence_certificates_issued": sum(1 for entry in ledger if isinstance(entry, dict) and entry.get("certified", False))
            }
            self.wfile.write(json.dumps(analytics).encode("utf-8"))

        elif path.startswith("/api/certificate"):
            task_id = path.replace("/api/certificate/", "").strip()
            cert_path = os.path.join("data", "certificates", f"cert_{task_id}.json")
            self._set_headers("application/json")
            if os.path.exists(cert_path):
                with open(cert_path, "r", encoding="utf-8") as cf:
                    self.wfile.write(cf.read().encode("utf-8"))
            else:
                self.wfile.write(json.dumps({"status": "error", "message": "Certificate not found"}).encode("utf-8"))
        elif path.startswith("/api/proposal"):
            task_id = path.replace("/api/proposal/", "").strip()
            proposal_path = os.path.join(PROPOSALS_DIR, f"proposal_{task_id}.txt")

            self._set_headers("text/plain")
            if os.path.exists(proposal_path):
                with open(proposal_path, "r", encoding="utf-8") as pf:
                    self.wfile.write(pf.read().encode("utf-8"))
            else:
                self.wfile.write(f"No proposal draft found for task [{task_id}].".encode("utf-8"))

        elif path.startswith("/api/patch"):
            task_id = path.replace("/api/patch/", "").strip()
            jobs = self.load_matched_jobs()
            task_job = next((j for j in jobs if j.get("id") == task_id), {})
            title = task_job.get("title", "")

            patch_result = process_task_patch(task_id, title)

            patch_file = patch_result.get("patch_path", "")
            diff_text = ""
            if patch_file and os.path.exists(patch_file):
                with open(patch_file, "r", encoding="utf-8") as pf:
                    diff_text = pf.read()

            response_data = {
                "task_id": task_id,
                "diff": diff_text,
                "verified": patch_result.get("verified", False),
                "log": patch_result.get("log", "No verification log found.")
            }
            self._set_headers("application/json")
            self.wfile.write(json.dumps(response_data).encode("utf-8"))
        else:
            self._set_headers("text/plain", 404)
            self.wfile.write(b"404 Not Found")

    def do_POST(self):
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path.rstrip('/')

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        data = json.loads(body) if body else {}

        if path == "/api/dispatch":
            try:
                task_id = data.get("task_id")
                proposal_text = data.get("proposal_text", "")

                jobs = self.load_matched_jobs()
                task_job = next((j for j in jobs if j.get("id") == task_id), {})
                bounty_usd = task_job.get("bounty_usd", 350)

                pr_url = execute_git_delivery(task_id) if task_id else "N/A"

                ledger = self.load_ledger()
                dispatch_entry = {
                    "task_id": task_id,
                    "bounty_usd": bounty_usd,
                    "status": "DISPATCHED_WITH_PR",
                    "dispatched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "pr_url": pr_url,
                    "proposal_snippet": proposal_text[:150] + "..." if len(proposal_text) > 150 else proposal_text
                }
                ledger.append(dispatch_entry)
                self.save_ledger(ledger)

                self._set_headers("application/json")
                self.wfile.write(json.dumps({"status": "success", "message": f"Task [{task_id}] proposal & PR dispatched!"}).encode("utf-8"))
            except Exception as e:
                self._set_headers("application/json", 500)
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode("utf-8"))

        elif path in ("/api/settle", "/api/webhook/github", "/webhook/github"):
            self._set_headers("application/json", 410)
            self.wfile.write(b'{"error":"legacy_route_retired"}')
        else:
            self._set_headers("text/plain", 404)
            self.wfile.write(b"404 Not Found")

    def trigger_paypal_settlement(self, *args, **kwargs):
        raise RuntimeError("Legacy financial shortcut retired")

    def update_ledger_status(self, task_id, new_status):
        ledger_path = os.path.join("data", "ledger.json")
        if os.path.exists(ledger_path):
            try:
                with open(ledger_path, "r", encoding="utf-8") as f:
                    ledger = json.load(f)

                for entry in ledger:
                    if entry.get("id") == task_id or entry.get("task_id") == task_id:
                        entry["status"] = new_status
                        entry["certified"] = True

                with open(ledger_path, "w", encoding="utf-8") as f:
                    json.dump(ledger, f, indent=2)
                print(f"[*] Ledger updated: Task [{task_id}] marked as '{new_status}'.")
            except Exception as e:
                print(f"[-] Error updating ledger: {e}")

    def generate_coherence_certificate(self, task_id, pr_data):
        cert_dir = os.path.join("data", "certificates")
        os.makedirs(cert_dir, exist_ok=True)

        cert_path = os.path.join(cert_dir, f"cert_{task_id}.json")
        certificate_data = {
            "task_id": task_id,
            "status": "VERIFIED_AND_SETTLED",
            "merge_url": pr_data.get("html_url"),
            "merged_by": pr_data.get("merged_by", {}).get("login", "unknown"),
            "merged_at": pr_data.get("merged_at", "unknown"),
            "audit_type": "Human-Auditable Coherence Certificate"
        }

        with open(cert_path, "w", encoding="utf-8") as f:
            json.dump(certificate_data, f, indent=2)
        print(f"[*] Coherence certificate generated at: {cert_path}")

    def load_matched_jobs(self):
        if os.path.exists(MATCHED_JOBS_FILE):
            try:
                with open(MATCHED_JOBS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[-] Error reading {MATCHED_JOBS_FILE}: {e}")
        return []

    def load_ledger(self):
        if os.path.exists(LEDGER_FILE):
            try:
                with open(LEDGER_FILE, "r", encoding="utf-8") as lf:
                    loaded = json.load(lf)
                    return loaded if isinstance(loaded, list) else [loaded]
            except Exception as e:
                print(f"[-] Error reading ledger: {e}")
        return []

    def save_ledger(self, ledger):
        os.makedirs("data", exist_ok=True)
        with open(LEDGER_FILE, "w", encoding="utf-8") as lf:
            json.dump(ledger, lf, indent=2)

    def render_dashboard(self):
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>AtlasAeon Control Center</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #121212; color: #e0e0e0; margin: 0; padding: 20px; }
        .dashboard-header { display: flex; gap: 15px; max-width: 1400px; margin: 0 auto 20px auto; }
        .stat-card { background: #1e1e1e; border-radius: 8px; padding: 15px 20px; flex: 1; box-shadow: 0 4px 12px rgba(0,0,0,0.4); border-top: 3px solid #00e676; }
        .stat-card.settled { border-top-color: #00b0ff; }
        .stat-card.heavy { border-top-color: #ff9100; }
        .stat-label { font-size: 0.85em; color: #888; text-transform: uppercase; font-weight: bold; }
        .stat-value { font-size: 1.6em; font-weight: bold; color: #fff; margin-top: 5px; }
        .container { display: flex; gap: 20px; max-width: 1400px; margin: 0 auto 20px auto; }
        .panel { background: #1e1e1e; border-radius: 8px; padding: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.5); }
        .left-panel { flex: 1; max-width: 450px; }
        .right-panel { flex: 2; display: flex; flex-direction: column; }
        .ledger-panel { max-width: 1400px; margin: 0 auto; width: 100%; box-sizing: border-box; }
        h2 { margin-top: 0; color: #ffffff; }
        .controls { display: flex; gap: 10px; margin-bottom: 15px; }
        .search-input { background: #2a2a2a; border: 1px solid #444; color: #fff; padding: 8px 12px; border-radius: 4px; flex: 1; }
        .filter-btn { background: #2a2a2a; border: 1px solid #444; color: #ccc; padding: 8px 12px; border-radius: 4px; cursor: pointer; font-size: 0.85em; }
        .filter-btn.active { background: #00e676; color: #000; font-weight: bold; border-color: #00e676; }
        .job-card { background: #2a2a2a; border-left: 4px solid #00e676; padding: 12px 16px; margin-bottom: 12px; border-radius: 4px; cursor: pointer; transition: background 0.2s; }
        .job-card:hover { background: #333333; }
        .job-card.heavy { border-left-color: #ff9100; }
        .job-header { display: flex; justify-content: space-between; align-items: center; font-weight: bold; }
        .job-title { font-size: 0.95em; color: #ffffff; margin-top: 4px; }
        .badge { background: #00e676; color: #000; padding: 2px 6px; border-radius: 4px; font-size: 0.8em; font-weight: bold; }
        .badge.heavy { background: #ff9100; color: #000; }
        pre { background: #181818; color: #a9b7c6; padding: 14px; border-radius: 6px; overflow-x: auto; font-family: "Consolas", "Courier New", monospace; white-space: pre-wrap; flex: 1; max-height: 280px; margin-bottom: 15px; }
        .patch-preview { background: #15202b; border: 1px solid #1d9bf0; color: #e7e9ea; padding: 14px; border-radius: 6px; font-family: "Consolas", monospace; font-size: 0.85em; max-height: 200px; overflow-y: auto; margin-bottom: 15px; }
        .verify-banner { padding: 8px 12px; border-radius: 4px; font-weight: bold; margin-bottom: 10px; font-size: 0.85em; }
        .verify-banner.pass { background: rgba(0,230,118,0.2); color: #00e676; border: 1px solid #00e676; }
        .verify-banner.fail { background: rgba(255,23,68,0.2); color: #ff1744; border: 1px solid #ff1744; }
        .btn { background: #00e676; color: #000; font-weight: bold; border: none; padding: 12px 20px; border-radius: 6px; cursor: pointer; font-size: 1em; }
        .btn:hover { background: #00c853; }
        .btn-settle { background: #00b0ff; color: #000; font-weight: bold; border: none; padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 0.8em; }
        .btn-settle:hover { background: #0091ea; }
        .status-msg { margin-top: 10px; font-weight: bold; color: #00e676; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.9em; }
        th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid #333; }
        th { background: #2a2a2a; color: #00e676; }
        a { color: #00e676; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .job-card.dispatched { border-left-color: #00b0ff !important; }
        .badge.dispatched { background: #00b0ff; color: #000; }
    </style>
</head>
<body>
    <div class="dashboard-header">
        <div class="stat-card">
            <div class="stat-label">Active Opportunities</div>
            <div class="stat-value" id="stat-count">0</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Pipeline Value (USD)</div>
            <div class="stat-value" id="stat-usd">$0</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Est. Value (AUD)</div>
            <div class="stat-value" id="stat-aud">$0 AUD</div>
        </div>
        <div class="stat-card settled">
            <div class="stat-label">Realized Revenue (AUD)</div>
            <div class="stat-value" id="stat-settled" style="color: #00b0ff;">$0.00 AUD</div>
        </div>
        <div class="stat-card heavy">
            <div class="stat-label">High-Value Tasks ($1k+)</div>
            <div class="stat-value" id="stat-heavy">0</div>
        </div>
    </div>
    <div class="container">
        <div class="panel left-panel">
            <h2>Active Matched Jobs</h2>
            <div class="controls">
                <input type="text" id="search-box" class="search-input" placeholder="Search tasks..." oninput="renderJobs()">
                <button class="filter-btn active" id="btn-all" onclick="setFilter('all')">All</button>
                <button class="filter-btn" id="btn-heavy" onclick="setFilter('heavy')">$1k+</button>
            </div>
            <div id="job-list">Loading jobs...</div>
        </div>
        <div class="panel right-panel">
            <h2>Proposal & Patch Verification</h2>
            <pre id="proposal-view">Click on a job card on the left to preview its proposal draft and patch verification...</pre>
            
            <div id="patch-section" style="display:none;">
                <div id="verify-banner" class="verify-banner"></div>
                <div class="patch-preview" id="patch-view">Generating patch diff...</div>
            </div>

            <div id="actions" style="display: none;">
                <button class="btn" id="dispatch-btn" onclick="dispatchProposal()">Approve & Submit Proposal</button>
                <div id="status-msg" class="status-msg"></div>
            </div>
        </div>
    </div>

    <div class="panel ledger-panel">
        <h2>Dispatched & Settlement Ledger</h2>
        <table id="ledger-table">
            <thead>
                <tr>
                    <th>Task ID</th>
                    <th>Status</th>
                    <th>Dispatched At</th>
                    <th>Pull Request Link</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody id="ledger-body">
                <tr><td colspan="5" style="color:#888;">Loading ledger logs...</td></tr>
            </tbody>
        </table>
    </div>

    <script>
        let allJobs = [];
        let dispatchedTaskIds = new Set();
        let currentFilter = 'all';
        let currentTaskId = null;

        function setFilter(filter) {
            currentFilter = filter;
            document.getElementById('btn-all').classList.toggle('active', filter === 'all');
            document.getElementById('btn-heavy').classList.toggle('active', filter === 'heavy');
            renderJobs();
        }

        async function loadAnalytics() {
            try {
                const res = await fetch('/api/analytics');
                const data = await res.json();
                document.getElementById('stat-count').innerText = data.total_jobs;
                document.getElementById('stat-usd').innerText = '$' + data.total_usd.toLocaleString();
                document.getElementById('stat-aud').innerText = '$' + data.total_aud.toLocaleString('en-AU', {minimumFractionDigits: 2}) + ' AUD';
                document.getElementById('stat-settled').innerText = '$' + data.settled_aud.toLocaleString('en-AU', {minimumFractionDigits: 2}) + ' AUD';
                document.getElementById('stat-heavy').innerText = data.high_value_count;
            } catch (err) {
                console.error("Error loading analytics:", err);
            }
        }

        async function fetchJobs() {
            try {
                const res = await fetch('/api/jobs');
                allJobs = await res.json();
                renderJobs();
                loadAnalytics();
            } catch (err) {
                console.error("Error loading jobs:", err);
            }
        }
        async function fetchLedger() {
            try {
                const res = await fetch('/api/ledger');
                const ledger = await res.json();
                const body = document.getElementById('ledger-body');
                
                dispatchedTaskIds.clear();
                if (Array.isArray(ledger)) {
                    ledger.forEach(entry => {
                        if (entry.task_id) dispatchedTaskIds.add(entry.task_id);
                    });
                }

                renderJobs();

                if (!ledger || ledger.length === 0) {
                    body.innerHTML = '<tr><td colspan="5" style="color:#888;">No dispatched items in ledger yet.</td></tr>';
                    return;
                }

                body.innerHTML = ledger.map(entry => {
                    const isSettled = entry.status === 'SETTLED_PAID' || entry.status === 'SETTLED';
                    const statusColor = isSettled ? '#00b0ff' : '#00e676';
                    return `
                        <tr>
                            <td><strong>${entry.task_id || 'N/A'}</strong></td>
                            <td><span style="color:${statusColor}; font-weight:bold;">${entry.status || 'DISPATCHED'}</span></td>
                            <td>${entry.dispatched_at || entry.timestamp || 'N/A'}</td>
                            <td>${entry.pr_url && entry.pr_url !== 'N/A' ? `<a href="${entry.pr_url}" target="_blank">${entry.pr_url}</a>` : 'N/A'}</td>
                            <td>${!isSettled && entry.task_id ? `<button class="btn-settle" onclick="markSettled('${entry.task_id}')">Confirm Merge / Paid</button>` : '<span style="color:#888; font-size:0.85em;">Settled</span>'}</td>
                        </tr>
                    `;
                }).join('');
            } catch (err) {
                console.error("Error loading ledger:", err);
            }
        }

        async function markSettled(taskId) {
            try {
                const res = await fetch('/api/settle', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ task_id: taskId })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    fetchLedger();
                    loadAnalytics();
                } else {
                    alert(data.message || "Failed to settle task.");
                }
            } catch (err) {
                console.error("Error settling task:", err);
            }
        }

        function renderJobs() {
            const query = document.getElementById('search-box').value.toLowerCase();
            const container = document.getElementById('job-list');

            const filtered = allJobs.filter(job => {
                const matchesFilter = currentFilter === 'all' || job.bounty_usd >= 1000;
                const matchesQuery = job.title.toLowerCase().includes(query) || job.id.toLowerCase().includes(query);
                return matchesFilter && matchesQuery;
            });

            if (filtered.length === 0) {
                container.innerHTML = '<p style="color:#888;">No matching jobs found.</p>';
                return;
            }

            container.innerHTML = filtered.map(job => {
                const isHeavy = job.bounty_usd >= 1000;
                const isDispatched = dispatchedTaskIds.has(job.id);

                let cardClass = 'job-card';
                if (isDispatched) {
                    cardClass += ' dispatched';
                } else if (isHeavy) {
                    cardClass += ' heavy';
                }

                return `
                    <div class="${cardClass}" onclick="selectJob('${job.id}')">
                        <div class="job-header">
                            <span>[${job.id}]</span>
                            <div style="display:flex; gap:6px;">
                                ${isDispatched ? '<span class="badge dispatched">DISPATCHED</span>' : ''}
                                <span class="badge ${isHeavy ? 'heavy' : ''}">$${job.bounty_usd}</span>
                            </div>
                        </div>
                        <div class="job-title">${job.title}</div>
                    </div>
                `;
            }).join('');
        }

        async function selectJob(taskId) {
            currentTaskId = taskId;
            document.getElementById('status-msg').innerText = '';
            document.getElementById('actions').style.display = 'block';
            document.getElementById('patch-section').style.display = 'block';

            try {
                const res = await fetch('/api/proposal/' + taskId);
                const text = await res.text();
                document.getElementById('proposal-view').innerText = text;
            } catch (err) {
                document.getElementById('proposal-view').innerText = "Failed to load proposal draft.";
            }

            try {
                const patchRes = await fetch('/api/patch/' + taskId);
                const patchData = await patchRes.json();
                
                const banner = document.getElementById('verify-banner');
                banner.className = 'verify-banner ' + (patchData.verified ? 'pass' : 'fail');
                banner.innerText = patchData.log;

                document.getElementById('patch-view').innerText = patchData.diff || "No diff generated.";
            } catch (err) {
                document.getElementById('patch-view').innerText = "Failed to load candidate patch.";
            }
        }

        async function dispatchProposal() {
            if (!currentTaskId) return;

            const proposalText = document.getElementById('proposal-view').innerText;

            try {
                const res = await fetch('/api/dispatch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ task_id: currentTaskId, proposal_text: proposalText })
                });
                const data = await res.json();
                document.getElementById('status-msg').innerText = data.message;
                fetchLedger();
                loadAnalytics();
            } catch (err) {
                document.getElementById('status-msg').innerText = "Error dispatching proposal.";
            }
        }

        fetchJobs();
        fetchLedger();
        setInterval(() => {
            fetchJobs();
            fetchLedger();
        }, 15000);
    </script>
</body>
</html>"""

def run_server():
    server = HTTPServer(('localhost', PORT), DashboardHandler)
    print(f"[+] AtlasAeon Control Center live at http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Shutting down dashboard server.")

if __name__ == "__main__":
    run_server()