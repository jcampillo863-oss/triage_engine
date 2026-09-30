import os
import json
import urllib.request
from datetime import datetime
from dotenv import load_dotenv

# Load variables from .env file
load_dotenv()

class TaskDispatcher:
    def __init__(self, ledger_path="data/ledger.json"):
        self.ledger_path = ledger_path
        self.github_token = os.getenv("GITHUB_TOKEN", "").strip()
        self.paypal_email = os.getenv("PAYPAL_EMAIL", "jcampillo863@gmail.com")
        self.currency = os.getenv("PREFERRED_CURRENCY", "AUD")
        self._ensure_ledger()

    def _ensure_ledger(self):
        os.makedirs("data", exist_ok=True)
        if not os.path.exists(self.ledger_path):
            with open(self.ledger_path, "w", encoding="utf-8") as f:
                json.dump({"dispatched_proposals": [], "total_projected_aud": 0.0}, f, indent=2)

    def verify_github_connection(self):
        if not self.github_token:
            print("[-] Error: GITHUB_TOKEN not found in environment.")
            return False

        url = "https://api.github.com/user"
        headers = {
            "Authorization": f"token {self.github_token}",
            "User-Agent": "AtlasAeon-Triage-Engine",
            "Accept": "application/vnd.github.v3+json"
        }

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    print(f"[+] GitHub Auth Successful! Logged in as: {data.get('login')}")
                    return True
        except Exception as e:
            print(f"[-] GitHub Authentication failed: {e}")
            return False

    def dispatch_proposal(self, task_id, title, bounty_usd, repo_owner=None, repo_name=None, issue_number=None):
        # 1. Load generated proposal text
        proposal_file = f"data/proposals/proposal_{task_id}.txt"
        proposal_body = ""
        if os.path.exists(proposal_file):
            with open(proposal_file, "r", encoding="utf-8") as f:
                proposal_body = f.read()

        # Append payout details to draft
        proposal_body += f"\n\n---\n**Payout Target:** {self.paypal_email} ({self.currency})"

        # 2. Post directly to GitHub API if repo details exist
        github_submitted = False
        if repo_owner and repo_name and issue_number and self.github_token:
            url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{issue_number}/comments"
            headers = {
                "Authorization": f"token {self.github_token}",
                "User-Agent": "AtlasAeon-Triage-Engine",
                "Accept": "application/vnd.github.v3+json"
            }
            payload = json.dumps({"body": proposal_body}).encode("utf-8")

            try:
                req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
                with urllib.request.urlopen(req) as resp:
                    if resp.status in (200, 201):
                        github_submitted = True
                        print(f"[+] Posted comment directly to GitHub Issue #{issue_number} on {repo_owner}/{repo_name}")
            except Exception as e:
                print(f"[-] GitHub issue posting failed: {e}")

        # 3. Record in local persistent ledger
        with open(self.ledger_path, "r+", encoding="utf-8") as f:
            ledger = json.load(f)
            
            record = {
                "task_id": task_id,
                "title": title,
                "bounty_usd": bounty_usd,
                "payout_target": self.paypal_email,
                "currency": self.currency,
                "github_comment_posted": github_submitted,
                "status": "DISPATCHED_TO_PLATFORM" if github_submitted else "APPROVED_AND_QUEUED",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
            ledger["dispatched_proposals"].append(record)
            ledger["total_projected_aud"] += bounty_usd
            
            f.seek(0)
            json.dump(ledger, f, indent=2)
            f.truncate()

        # 4. Remove dispatched task from matched_jobs.json
        matched_jobs_path = "data/matched_jobs.json"
        if os.path.exists(matched_jobs_path):
            with open(matched_jobs_path, "r+", encoding="utf-8") as f:
                try:
                    jobs = json.load(f)
                    updated_jobs = [j for j in jobs if str(j.get("id")) != str(task_id)]
                    f.seek(0)
                    json.dump(updated_jobs, f, indent=2)
                    f.truncate()
                except Exception as e:
                    print(f"[-] Warning updating matched_jobs.json: {e}")

        print(f"[+] Dispatched task [{task_id}] | Target: {self.paypal_email} [{self.currency}]")
        return record

if __name__ == "__main__":
    dispatcher = TaskDispatcher()
    dispatcher.verify_github_connection()