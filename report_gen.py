from pathlib import Path
import json

def generate_html_report(run_data: list, output_path: str = "workspace/triage_report.html"):
    rows = ""
    for item in run_data:
        badge_color = "#28a745" if item.get("status") == "PASSED" else "#dc3545"
        rows += f"""
        <tr>
            <td><code>{item.get('id')}</code></td>
            <td>{item.get('type', 'standard')}</td>
            <td>{item.get('action')}</td>
            <td><span style="color: white; background: {badge_color}; padding: 2px 6px; border-radius: 4px;">{item.get('status')}</span></td>
            <td>{item.get('notes', '')}</td>
        </tr>
        """

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>AtlasAeon Triage Audit Report</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 40px; color: #333; }}
        h1 {{ border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
        th {{ background-color: #f8f9fa; }}
        @media print {{ body {{ margin: 0; }} }}
    </style>
</head>
<body>
    <h1>AtlasAeon Batch Triage Report</h1>
    <p>Generated: Local Workspace Scan</p>
    <table>
        <thead>
            <tr>
                <th>Task ID</th>
                <th>Classification</th>
                <th>Action Taken</th>
                <th>Gate</th>
                <th>Notes</th>
            </tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>
</body>
</html>
"""
    Path(output_path).write_text(html, encoding="utf-8")
    print(f"[REPORT] Saved printable HTML report to {output_path}")