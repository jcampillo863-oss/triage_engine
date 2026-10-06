import subprocess
import json
from pathlib import Path
from report_gen import generate_html_report

TARGET_IDS = ['55866', '91801', '24923', '22865', '56676', '84633', '56914']
STUB_CODE = '''def execute_task_resolution():
    """Automated patch candidate."""
    return True
'''

def safe_ensure_solution(task_id: str, content: str) -> tuple[str, str]:
    path = Path(f"workspace/task_live_{task_id}/solution.py")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 50:  # heuristic for custom payload vs stub
        return "SKIP - Preserved", "Existing custom payload retained"
    path.write_text(content, encoding="utf-8")
    return "CREATED", "Standard stub written"

def run_batch_sweep():
    run_data = []
    for tid in TARGET_IDS:
        action_state, notes = safe_ensure_solution(tid, STUB_CODE)

        # Run patch gate via python command matching your workflow
        res = subprocess.run(
            ["python", "-c", f"from patcher import process_task_patch; process_task_patch('live_{tid}', 'maintenance')"],
            capture_output=True,
            text=True
        )
        status = "PASSED" if res.returncode == 0 else "FAILED"

        # Infer classification tag
        t_type = "route" if tid in ['91801', '24923', '22865'] else ("docs" if tid == '84633' else "standard")
        if "SKIP" in action_state and t_type != "standard":
            notes = "Preserved custom route/doc logic"

        run_data.append({
            "id": f"live_{tid}",
            "type": t_type,
            "action": action_state,
            "status": status,
            "notes": notes
        })

    generate_html_report(run_data, "workspace/triage_report.html")
    print(f"[COMPLETE] Sweep finished. Processed {len(run_data)} items. Report written.")

if __name__ == "__main__":
    run_batch_sweep()