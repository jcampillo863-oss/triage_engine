import os
import requests

class GitHubAdapter:
    def __init__(self, owner: str, repo: str, label: str = "triage", token_env: str = "GITHUB_TOKEN"):
        self.owner = owner
        self.repo = repo
        self.label = label
        self.token = os.getenv(token_env)
        self.base_url = f"https://api.github.com/repos/{owner}/{repo}"

    def fetch_pending(self) -> list[dict]:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}" if self.token else "",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        url = f"{self.base_url}/issues?labels={self.label}&state=open"
        res = requests.get(url, headers=headers)
        if res.status_code != 200:
            return []

        normalized = []
        for issue in res.json():
            # Skip pull requests returned in issues endpoint if desired
            if "pull_request" in issue:
                continue
            normalized.append({
                "task_id": f"gh:{issue['number']}",
                "target_repo": f"{self.owner}/{self.repo}",
                "objective_text": f"{issue.get('title', '')}\n\n{issue.get('body', '')}",
                "valuation": 350  # default baseline or parse from label text
            })
        return normalized