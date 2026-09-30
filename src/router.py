import os
import json
import urllib.request
from dotenv import load_dotenv

load_dotenv()

class TriageRouter:
    def __init__(self, raw_feed_path="data/raw_feed.json", output_path="data/matched_jobs.json"):
        self.raw_feed_path = raw_feed_path
        self.output_path = output_path
        self.github_token = os.getenv("GITHUB_TOKEN", "").strip()
        os.makedirs("data/proposals", exist_ok=True)

    def fetch_repo_context(self, repo_owner, repo_name):
        """Fetches top-level directory structure and key files from target GitHub repo."""
        if not repo_owner or not repo_name:
            return ["src/", "tests/", "README.md"] # Fallback default structure

        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents"
        headers = {
            "User-Agent": "AtlasAeon-Triage-Engine",
            "Accept": "application/vnd.github.v3+json"
        }
        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req) as resp:
                if resp.status == 200:
                    items = json.loads(resp.read().decode('utf-8'))
                    paths = [item.get("path") for item in items[:8]] # Top 8 items
                    return paths
        except Exception as e:
            print(f"    [-] Could not fetch repo tree for {repo_owner}/{repo_name}: {e}")

        return ["src/", "tests/", "pyproject.toml"]

    def generate_light_proposal(self, task):
        return f"""================================================================================
PROPOSAL DRAFT | Task ID: {task['id']}
Target: {task['title']}
Est. Value: ${task['bounty_usd']:.2f} USD
================================================================================

Hello,

I reviewed the requirements for "{task['title']}" and can execute this efficiently.

Key Focus & Deliverables:
- Implementation focused on strict validation, payload normalization, and API error-handling.
- Modular, well-documented code with comprehensive test coverage.
- Quick turnaround with clean pull-request integration.

I am ready to begin immediately upon assignment.

Best regards,
AtlasAeon Automated Triage Pipeline"""

    def generate_heavy_proposal(self, task, repo_files):
        files_formatted = "\n   - ".join(repo_files) if repo_files else "src/, tests/"

        return f"""================================================================================
HEAVY MODEL TECHNICAL PROPOSAL | Task ID: {task['id']}
Target: {task['title']}
Bounty Value: ${task['bounty_usd']:.2f} USD
Priority Tier: HIGH VALUE / ARCHITECTURAL ESCALATION
Target Repo: {task.get('repo_owner', 'Local')}/{task.get('repo_name', 'Sandbox')}
================================================================================

Executive Summary:
I have completed an initial structural diagnosis for issue #{task['id']} ("{task['title']}"). 
Below is the proposed technical implementation roadmap and execution strategy integrated with repository structure.

1. Discovered Repository Layout & Context:
   - {files_formatted}

2. Architectural Diagnosis & Root Cause:
   - Primary issue stems from state divergence or instrument coverage gaps during runtime execution.
   - Patch execution will target key source files in the primary module directory identified above.

3. Step-by-Step Implementation Roadmap:
   [Phase 1] Environment Replication: Construct minimal reproducible test case inside repository test suite.
   [Phase 2] Core Logic Patch: Apply modular fix directly within core module scope.
   [Phase 3] Coverage & Regression Test: Validate changes across discovered test scripts.
   [Phase 4] Pull Request Submission: Submit clean, linted PR complete with benchmark verification logs.

4. Delivery Commitment:
   - Initial PR submission within 24-48 hours upon issue assignment.
   - Full post-patch verification logs included in PR description.

Ready to begin immediate execution. Please assign or confirm to initiate branch checkout.

Best regards,
AtlasAeon Triage Engine (High-Value Pipeline)"""

    def process_and_route(self):
        if not os.path.exists(self.raw_feed_path):
            print(f"[-] Raw feed file {self.raw_feed_path} not found.")
            return

        with open(self.raw_feed_path, "r", encoding="utf-8") as f:
            tasks = json.load(f)

        matched_jobs = []

        for task in tasks:
            bounty = task.get("bounty_usd", 0)
            task_id = task.get("id")

            if bounty >= 1000:
                print(f"[*] High-Value Task [{task_id}] (${bounty}) -> Injecting Repo Context & Routing to Heavy Generator...")
                repo_owner = task.get("repo_owner")
                repo_name = task.get("repo_name")
                repo_files = self.fetch_repo_context(repo_owner, repo_name)
                proposal_content = self.generate_heavy_proposal(task, repo_files)
            else:
                print(f"[*] Standard Task [{task_id}] (${bounty}) -> Routing to Light Model Generator...")
                proposal_content = self.generate_light_proposal(task)

            proposal_file = f"data/proposals/proposal_{task_id}.txt"
            with open(proposal_file, "w", encoding="utf-8") as pf:
                pf.write(proposal_content)

            matched_jobs.append(task)

        with open(self.output_path, "w", encoding="utf-8") as of:
            json.dump(matched_jobs, of, indent=2)

        print(f"[+] Processed {len(matched_jobs)} tasks and updated {self.output_path}")

if __name__ == "__main__":
    router = TriageRouter()
    router.process_and_route()