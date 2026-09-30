import time
import os
import subprocess
import sys
from datetime import datetime

# Polling interval in seconds (default: 15 minutes)
POLL_INTERVAL_SECONDS = 900

def run_pipeline_cycle():
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[={timestamp}=] Starting scheduled ingestion & triage cycle...")

    try:
        # 1. Run Fetcher to pull live bounties
        print("[*] Polling external bounty sources...")
        fetch_res = subprocess.run([sys.executable, "src/fetcher.py"], capture_output=True, text=True)
        if fetch_res.returncode == 0:
            print("[+] Fetcher completed successfully.")
        else:
            print(f"[-] Fetcher encountered an issue: {fetch_res.stderr.strip()}")

        # 2. Run Triage / Router engine to process and score new tasks
        print("[*] Running triage scoring and proposal generator...")
        triage_res = subprocess.run([sys.executable, "src/router.py"], capture_output=True, text=True)
        if triage_res.returncode == 0:
            print("[+] Triage scoring completed. Active jobs updated in control center.")
        else:
            print(f"[-] Router encountered an issue: {triage_res.stderr.strip()}")

    except Exception as e:
        print(f"[-] Unexpected error during pipeline execution: {e}")

def start_daemon():
    print("==================================================")
    print("   AtlasAeon Triage Engine - Background Daemon    ")
    print(f"   Polling frequency: Every {POLL_INTERVAL_SECONDS // 60} minutes")
    print("==================================================")

    # Run immediately on startup
    run_pipeline_cycle()

    # Loop indefinitely
    while True:
        try:
            time.sleep(POLL_INTERVAL_SECONDS)
            run_pipeline_cycle()
        except KeyboardInterrupt:
            print("\n[!] Daemon gracefully stopped by user.")
            break

if __name__ == "__main__":
    start_daemon()