import json
import os
import sys

# Force UTF-8 output encoding for Windows PowerShell terminals
sys.stdout.reconfigure(encoding='utf-8')

from src.fetcher import TaskFetcher
from src.parser import TaskParser
from src.logger import TriageLogger
from src.proposal_generator import ProposalGenerator

def run():
    logger = TriageLogger()
    logger.logger.info("==========================================")
    logger.logger.info("    ATLASAEON TRIAGE ENGINE - RUN STARTED  ")
    logger.logger.info("==========================================")

    fetcher = TaskFetcher()
    parser = TaskParser()
    proposal_gen = ProposalGenerator()

    # 1. Fetch raw data across all enabled endpoints
    raw_tasks = fetcher.fetch_all()
    logger.log_ingest(len(raw_tasks), "Local/Remote Feeds")

    # 2. Filter using criteria defined in rules.json
    matched = parser.parse_feed(raw_tasks)

    # 3. Log results to console and data/triage.log
    logger.log_matches(matched)

    # 4. Save structured JSON output
    out_path = "data/matched_jobs.json"
    os.makedirs("data", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(matched, f, indent=2)

    logger.logger.info(f"Exported {len(matched)} matched jobs to {out_path}")

    # 5. Generate proposal drafts
    if matched:
        proposal_gen.process_matched_jobs()
        logger.logger.info("Generated proposal drafts in data/proposals/\n")

if __name__ == "__main__":
    run()