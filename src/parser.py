import json
import os
import re

class TaskParser:
    def __init__(self, rules_path="config/rules.json"):
        with open(rules_path, "r") as f:
            self.rules = json.load(f)

    def extract_bounty_from_text(self, text):
        if not text:
            return 0.0

        pattern = r'\$\s*([0-9]{1,3}(?:,[0-9]{3})+|\d+)(?:\.(\d{1,2}))?\b'
        matches = re.finditer(pattern, text)

        for match in matches:
            full_num = match.group(1).replace(',', '')
            decimals = match.group(2) if match.group(2) else "0"
            raw_str = f"{full_num}.{decimals}"

            try:
                val = float(raw_str)
                if 0.0 < val <= 50000.0:
                    return val
            except ValueError:
                continue

        return 0.0

    def normalize_task(self, raw_task):
        task_id = raw_task.get("id") or raw_task.get("number") or raw_task.get("node_id") or "unknown_id"
        title = raw_task.get("title") or raw_task.get("name") or raw_task.get("subject") or ""

        description = (
            raw_task.get("description") or
            raw_task.get("body") or
            raw_task.get("summary") or
            raw_task.get("content") or ""
        )

        bounty = 0.0
        for field in ["bounty_usd", "bounty", "reward", "price", "amount", "value"]:
            if field in raw_task and isinstance(raw_task[field], (int, float)):
                bounty = float(raw_task[field])
                break

        if bounty == 0.0:
            bounty = self.extract_bounty_from_text(f"{title} {description}")

        payout_method = (
            raw_task.get("payout_method") or
            raw_task.get("payment_type") or
            raw_task.get("payout") or
            "paypal"
        )

        url = raw_task.get("url") or raw_task.get("html_url") or raw_task.get("link") or "N/A"

        return {
            "id": str(task_id),
            "title": title,
            "description": description,
            "bounty_usd": bounty,
            "payout_method": str(payout_method).lower(),
            "url": url,
            "raw_payload": raw_task
        }

    def is_match(self, normalized_task):
        allowed_methods = self.rules.get("payout_preferences", {}).get("preferred_methods", ["paypal"])
        if normalized_task["payout_method"] not in allowed_methods:
            return False

        min_bounty = self.rules.get("filtering_rules", {}).get("min_bounty_usd", 10.0)
        if normalized_task["bounty_usd"] < min_bounty:
            return False

        full_text = f"{normalized_task['title']} {normalized_task['description']}".lower()
        for excluded in self.rules.get("filtering_rules", {}).get("exclude_keywords", []):
            if excluded in full_text:
                return False

        target_keywords = self.rules.get("filtering_rules", {}).get("target_keywords", [])
        if not target_keywords:
            return True

        for target in target_keywords:
            if target in full_text:
                return True

        return False

    def parse_feed(self, raw_tasks):
        matched = []
        seen_ids = set()
        seen_title_prefixes = set()

        for raw in raw_tasks:
            norm_task = self.normalize_task(raw)

            # Strip bracketed tags and retain core title characters
            clean_title = re.sub(r'\[.*?\]', '', norm_task["title"]).strip().lower()
            clean_title_key = re.sub(r'[^a-zA-Z0-9]', '', clean_title)

            if not clean_title_key:
                clean_title_key = re.sub(r'[^a-zA-Z0-9]', '', norm_task["title"].lower())

            # Grab first 30 characters to handle feed title truncation variations
            title_prefix = clean_title_key[:30]

            if norm_task["id"] in seen_ids or title_prefix in seen_title_prefixes:
                continue

            if self.is_match(norm_task):
                seen_ids.add(norm_task["id"])
                if title_prefix:
                    seen_title_prefixes.add(title_prefix)
                matched.append(norm_task)

        return matched