import os
import re
import time
import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

MATCHED_JOBS_FILE = os.path.join("data", "matched_jobs.json")
PROPOSALS_DIR = os.path.join("data", "proposals")
WORKSPACE_DIR = os.path.join("workspace")

# Multi-Platform Feed Registry - Updated for Live Bounty Hunting
SOURCES = [
    {
        "name": "GitHub Open Bounty Issues",
        "type": "atom",
        # This targets open issues across popular repositories tagged with bounty or reward
        "url": "https://github.com/issues?q=is%3Aopen+is%3Aissue+label%3Abounty",
        "default_bounty": 500
    },
    {
        "name": "GitHub Help Wanted Feed",
        "type": "atom",
        "url": "https://github.com/issues?q=is%3Aopen+is%3Aissue+label%3A%22help+wanted%22",
        "default_bounty": 250
    }
]

class BaseFetcher:
    """Base class providing workspace setup, proposal generation, and job caching."""
    def __init__(self):
        os.makedirs("data", exist_ok=True)
        os.makedirs(PROPOSALS_DIR, exist_ok=True)
        os.makedirs(WORKSPACE_DIR, exist_ok=True)

    def load_existing_jobs(self):
        if os.path.exists(MATCHED_JOBS_FILE):
            try:
                with open(MATCHED_JOBS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[-] Error reading {MATCHED_JOBS_FILE}: {e}")
        return []

    def save_jobs(self, jobs):
        with open(MATCHED_JOBS_FILE, "w", encoding="utf-8") as f:
            json.dump(jobs, f, indent=2)

    def parse_bounty_value(self, title, default_val):
        """Extracts explicit monetary values (e.g., $1,500, $500, 1000 USD) from issue titles."""
        match = re.search(r'\$(\d{1,3}(?:,\d{3})*|\d+)', title)
        if match:
            clean_num = match.group(1).replace(',', '')
            return int(clean_num)
        return default_val

    def generate_proposal_draft(self, task_id, title, source_name):
        """Generates a structured technical proposal for new opportunities."""
        proposal_path = os.path.join(PROPOSALS_DIR, f"proposal_{task_id}.txt")
        if not os.path.exists(proposal_path):
            content = f"""====================================================================
ATLASAEON AUTOMATED TECHNICAL PROPOSAL
Task ID: {task_id}
Generated At: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Source: {source_name}
====================================================================

PROBLEM ANALYSIS:
Target Issue: {title}

PROPOSED RESOLUTION PIPELINE:
1. Isolated environment setup in workspace/task_{task_id}.
2. Targeted fix applied to affected modules via AST/Rule-based patcher.
3. Local verification via automated test execution (unittest/pytest).
4. Automated PR submission upon verification pass.

====================================================================
"""
            with open(proposal_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"[+] Proposal draft auto-generated: proposal_{task_id}.txt")

    def setup_task_workspace(self, task_id):
        """Provisions local workspace directory for instant patching."""
        task_ws = os.path.join(WORKSPACE_DIR, f"task_{task_id}")
        if not os.path.exists(task_ws):
            os.makedirs(task_ws, exist_ok=True)
            print(f"[+] Provisioned workspace directory: workspace/task_{task_id}")


class MultiPlatformFetcher(BaseFetcher):
    def poll_github_api_issues(self, source_info):
            print(f"[*] Querying live API source: {source_info['name']}...")
            fetched_items = []
            try:
                # Query GitHub REST API for open issues with bounty or help-wanted labels
                api_url = "https://api.github.com/search/issues?q=is:open+is:issue+label:bounty&sort=created&order=desc"
                
                headers = {
                    'User-Agent': 'AtlasAeon-BountyHunter',
                    'Accept': 'application/vnd.github.v3+json'
                }
                
                req = urllib.request.Request(api_url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as response:
                    data = json.loads(response.read().decode('utf-8'))
                    
                items = data.get('items', [])
                for issue in items[:10]: # Grab top 10 freshest opportunities
                    title_text = issue.get('title', '')
                    issue_id = str(issue.get('id', time.time()))
                    clean_id = f"live_{abs(hash(issue_id)) % 100000}"
                    
                    # Extract dollar amount from title if present, otherwise use default
                    bounty = self.parse_bounty_value(title_text, source_info['default_bounty'])
                    
                    fetched_items.append({
                        "id": clean_id,
                        "title": title_text,
                        "bounty_usd": bounty,
                        "source": source_info['name'],
                        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "url": issue.get('html_url', '')
                    })
            except Exception as e:
                print(f"[-] Failed to fetch live GitHub issues: {e}")
                
            return fetched_items

    def sync_all_sources(self):
            existing_jobs = self.load_existing_jobs()
            existing_ids = {j.get("id") for j in existing_jobs}
            
            new_count = 0
            for src in SOURCES:
                # Call our live GitHub API fetcher instead of atom
                items = self.poll_github_api_issues(src)
                for item in items:
                    task_id = item["id"]
                    self.generate_proposal_draft(task_id, item["title"], item["source"])
                    self.setup_task_workspace(task_id)

                    if task_id not in existing_ids:
                        existing_jobs.insert(0, item)
                        existing_ids.add(task_id)
                        new_count += 1

            if new_count > 0:
                self.save_jobs(existing_jobs)
                print(f"[+] Sync complete: {new_count} new opportunities added to matched_jobs.json!")
            else:
                print("[*] Sync complete: No new jobs found.")

    # FIX: Added interface aliases for daemon and triage runner compatibility
    def fetch_all(self):
        self.sync_all_sources()
        return self.load_existing_jobs()

    def run_ingestion(self):
        return self.fetch_all()


def start_polling_loop(interval_seconds=60):
    fetcher = MultiPlatformFetcher()
    print(f"[+] AtlasAeon Multi-Source Poller active (polling every {interval_seconds}s)...")
    try:
        while True:
            fetcher.sync_all_sources()
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("\n[*] Stopping live poller.")


# FIX: Export class aliases at the bottom to support external imports
TaskFetcher = MultiPlatformFetcher
MultiChannelFetcher = MultiPlatformFetcher


if __name__ == "__main__":
    start_polling_loop(interval_seconds=60)