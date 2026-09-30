import json
import os

class ProposalGenerator:
    def __init__(self, templates_dir="templates"):
        self.templates_dir = templates_dir
        os.makedirs(self.templates_dir, exist_ok=True)

    def generate_proposal(self, task):
        """Drafts a targeted proposal based on task attributes and keywords."""
        title = task.get("title", "")
        bounty = task.get("bounty_usd", 0.0)
        task_id = task.get("id", "unknown")
        
        # Select focus areas based on task content
        text_lower = f"{title} {task.get('description', '')}".lower()
        
        if "pytest" in text_lower or "test" in text_lower or "qa" in text_lower:
            focus = "automated test suite coverage, edge-case validation, and CI pipeline integration"
        elif "schema" in text_lower or "json" in text_lower or "api" in text_lower:
            focus = "strict JSON schema validation, payload normalization, and API error-handling"
        else:
            focus = "root-cause analysis, isolated bug reproduction, and clean code implementation"

        proposal = f"""================================================================================
PROPOSAL DRAFT | Task ID: {task_id}
Target: {title}
Est. Value: ${bounty:.2f} USD
================================================================================

Hello,

I reviewed the requirements for "{title}" and can execute this efficiently. 

Key Focus & Deliverables:
- Implementation focused on {focus}.
- Modular, well-documented code with comprehensive test coverage.
- Quick turnaround with clean pull-request integration.

I am ready to begin immediately upon assignment.

Best regards,
AtlasAeon Automated Triage Pipeline
"""
        return proposal

    def process_matched_jobs(self, matched_jobs_path="data/matched_jobs.json", output_dir="data/proposals"):
        if not os.path.exists(matched_jobs_path):
            print(f"[!] File not found: {matched_jobs_path}")
            return

        with open(matched_jobs_path, "r", encoding="utf-8") as f:
            jobs = json.load(f)

        os.makedirs(output_dir, exist_ok=True)
        generated_count = 0

        for job in jobs:
            proposal_text = self.generate_proposal(job)
            file_name = f"proposal_{job['id']}.txt"
            file_path = os.path.join(output_dir, file_name)
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(proposal_text)
            generated_count += 1

        print(f"[+] Successfully generated {generated_count} proposal draft(s) in '{output_dir}/'.")

if __name__ == "__main__":
    generator = ProposalGenerator()
    generator.process_matched_jobs()