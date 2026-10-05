# Exact Git review commands — not executed by the agent

Run in PowerShell from `C:\Users\User\triage_engine`. No commit, push, branch or PR command is proposed here. Do not use `git add .`, `git add -A`, reset, clean, checkout or stash.

Read-only review:

```powershell
$env:GIT_OPTIONAL_LOCKS = '0'
git -c safe.directory=C:/Users/User/triage_engine status --short --untracked-files=all
git -c safe.directory=C:/Users/User/triage_engine diff --stat
git -c safe.directory=C:/Users/User/triage_engine diff --cached --name-status
Get-Content -LiteralPath source_review/STAGING_MANIFEST.txt
Get-Content -LiteralPath source_review/TRACKED_RUNTIME_REMOVAL_MANIFEST.txt
```

Artifact-generating maintenance (NOT read-only):

```powershell
.\venv\Scripts\python.exe source_review/prepare_snapshot_review.py
```

This regenerates and overwrites review manifests/classification/scan reports. It does not modify the Git index. Do not run it during a review-only gate; obtain source-hygiene/artifact-maintenance authorization first.

Inspect the redacted tracked-change summaries and source files locally. Raw diffs against HEAD may display removed compromised credential literals; do not paste or publish them. `SECRET_SCAN_REPORT.json` checks proposed current file contents, not the deleted secrets in history.

Only after the human approves BOTH explicit manifests, prepare a source-only index while retaining excluded local files:

```powershell
git -c safe.directory=C:/Users/User/triage_engine rm --cached --ignore-unmatch --pathspec-from-file=source_review/TRACKED_RUNTIME_REMOVAL_MANIFEST.txt
git -c safe.directory=C:/Users/User/triage_engine add --pathspec-from-file=source_review/STAGING_MANIFEST.txt
git -c safe.directory=C:/Users/User/triage_engine diff --cached --stat
git -c safe.directory=C:/Users/User/triage_engine diff --cached --name-status
git -c safe.directory=C:/Users/User/triage_engine diff --cached --check
git -c safe.directory=C:/Users/User/triage_engine ls-files -ci --exclude-standard
```

The index-only removals are required because inherited tracked runtime/local files would otherwise remain in the next commit tree. They do not delete working files or remove old history. If Git refuses an index removal, stop and investigate; do not add `--force` automatically. Verify there are no unexpected staged paths or ignored tracked artifacts before any future commit. Secret rotation and history cleanup require separate decisions. Re-run the scan and approved tests if source changes after review.
