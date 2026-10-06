import logging
import json
import os
import sys
import io
from plyer import notification

class TriageLogger:
    def __init__(self, log_file="data/triage.log"):
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        self.logger = logging.getLogger("TriageEngine")
        self.logger.setLevel(logging.INFO)

        if not self.logger.handlers:
            # File handler - explicitly UTF-8
            fh = logging.FileHandler(log_file, encoding='utf-8')
            fh.setLevel(logging.INFO)

            # Safe Console handler - reconfigures stdout encoding or wraps stream
            sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
            ch = logging.StreamHandler(sys.stdout)
            ch.setLevel(logging.INFO)

            # Formatter
            formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
            fh.setFormatter(formatter)
            ch.setFormatter(formatter)

            self.logger.addHandler(fh)
            self.logger.addHandler(ch)

    def log_ingest(self, count, source):
        self.logger.info(f"Ingested {count} raw tasks from [{source}]")

    def notify_desktop(self, title, message):
        """Triggers a native Windows toast notification."""
        try:
            notification.notify(
                title=title,
                message=message,
                app_name="AtlasAeon Triage Engine",
                timeout=8
            )
        except Exception as e:
            self.logger.error(f"Failed to trigger Windows toast notification: {e}")

    def log_matches(self, matched_tasks):
        if not matched_tasks:
            self.logger.info("Scan complete. Zero tasks matched rules criteria.")
            return

        match_count = len(matched_tasks)
        self.logger.info(f"MATCH ALERT: Found {match_count} eligible bounty opportunity(ies)!")

        # Trigger desktop alert for top match
        top_task = matched_tasks[0]
        toast_title = f"🎯 Bounty Match (${top_task.get('bounty_usd', 0.0)} USD)"
        toast_msg = f"{top_task.get('title', 'Untitled')}\nPayout: {top_task.get('payout_method', 'N/A').upper()}"

        if match_count > 1:
            toast_msg += f"\n(+{match_count - 1} more jobs triaged)"

        self.notify_desktop(toast_title, toast_msg)

        for task in matched_tasks:
            msg = f"  -> [{task.get('id', 'N/A')}] {task.get('title', 'Untitled')} | ${task.get('bounty_usd', 0.0)} USD | Method: {task.get('payout_method', 'N/A')}"
            self.logger.info(msg)

    def log_error(self, message):
        self.logger.error(message)