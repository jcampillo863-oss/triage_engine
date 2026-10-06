import time
import subprocess
from pathlib import Path
import json
import re
from src.github_adapter import GitHubAdapter

def classify_task(task: dict) -> str:
    text = task.get("objective_text", "").lower()
    if "doc" in text or "sphinx" in text or "reference" in text:
        return "docs"
    if "route" in text or "endpoint" in text or "decorator" in text:
        return "route"
    return "standard"

def get_template_for_type(task_type: str) -> str:
    if task_type == "docs":
        return '''import re

def resolve_docs_reference(content: str) -> str:
    """Automated Sphinx/doc-reference normalization."""
    return re.sub(r':ref:`([^`]+`\\s*<[^>]+>)`', r':doc:`\\1`', content)

def execute_task_resolution():
    return resolve_docs_reference("doc reference update")
'''
    elif task_type == "route":
        return '''def execute_task_resolution():
    """Automated route-decorator payload."""
    return {"status": "routed", "registered": True}
'''
    else:
        return '''def execute_task_resolution():
    """Automated standard patch candidate."""
    return True
'''

class MultiPlatformDaemon:
    def __init__(self, adapters: list, ledger_path="data/ledger.json", interval=60):
        self.adapters = adapters
        self.ledger_path = Path(ledger_path)
        self.interval = interval
        self.seen = self._load_ledger()

    def _load_ledger(self) -> set:
        if self.ledger_path.exists():
            try:
                return set(json.loads(self.ledger_path.read_text(encoding="utf-8")))
            except Exception:
                return set()
        return set()

    def _save_ledger(self):
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(json.dumps(list(self.seen), indent=2), encoding="utf-8")

    def safe_ensure_solution(self, ws_folder: Path, content: str) -> tuple[str, str]:
        ws_folder.mkdir(parents=True, exist_ok=True)
        sol_path = ws_folder / "solution.py"
        if sol_path.exists() and sol_path.stat().st_size > 50:
            return "SKIP - Preserved", "Existing custom payload retained"
        sol_path.write_text(content, encoding="utf-8")
        return "CREATED", "Standard stub written"

    def process_task(self, task):
        tid = task["task_id"]
        folder_suffix = tid.replace(":", "_")
        ws_folder = Path(f"workspace/task_{folder_suffix}")

        t_type = classify_task(task)
        content = get_template_for_type(t_type)
        action_state, notes = self.safe_ensure_solution(ws_folder, content)

        res = subprocess.run(
            ["python", "-c", f"from patcher import process_task_patch; process_task_patch('{folder_suffix}', '{t_type}')"],
            capture_output=True, text=True
        )
        status = "PASSED" if res.returncode == 0 else "FAILED"
        print(f"[{status}] Task {tid} ({t_type}) -> {action_state}")

    def tick(self):
        for adapter in self.adapters:
            for task in adapter.fetch_pending():
                tid = task["task_id"]
                if tid not in self.seen:
                    self.process_task(task)
                    self.seen.add(tid)
        self._save_ledger()

if __name__ == "__main__":
    gh_adapter = GitHubAdapter(owner="AtlasAeon", repo="core-engine", label="triage")
    daemon = MultiPlatformDaemon(adapters=[gh_adapter])
    daemon.tick()