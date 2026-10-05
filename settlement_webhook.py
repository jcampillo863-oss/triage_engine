raise RuntimeError("Retired legacy module; use canonical authorities")
import os
import re
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import settlement_engine as se

load_dotenv(override=True)

app = Flask(__name__)

@app.route('/webhook/github', methods=['POST'])
def github_webhook():
    # Use silent=True so invalid/missing Content-Type headers return {} instead of throwing a 400 exception
    data = request.get_json(silent=True) or {}
    
    action = data.get("action")
    pull_request = data.get("pull_request") or {}
    
    # 1. Verify event action is a merged PR safely
    if action == "closed" and pull_request.get("merged") is True:
        pr_title = pull_request.get("title", "")
        pr_url = pull_request.get("html_url", "")
        
        # 2. Extract Task ID from PR title
        match = re.search(r'(live_\d+|heavy_task_\d+)', pr_title)
        task_id = match.group(1) if match else "unknown_task"
        
        if task_id == "unknown_task":
            return jsonify({"status": "rejected", "reason": "Unknown task ID format"}), 400

        # 3. Idempotently record or fetch AUTHORIZED settlement in SQLite
        try:
            settlement = se.get_or_create_settlement(task_id, pr_url)
            return jsonify({
                "status": "settlement_queued",
                "settlement_id": settlement["settlement_id"],
                "state": settlement["state"]
            }), 200
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({"status": "ignored", "event": data.get("action", "ping")}), 200

if __name__ == '__main__':
    print("[FLASK] Starting Webhook Listener on port 8080...")
    app.run(host='0.0.0.0', port=8080, debug=False)