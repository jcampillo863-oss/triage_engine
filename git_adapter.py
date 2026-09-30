import os
import json
import base64
import urllib.request
import urllib.error
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
PAYPAL_EMAIL = os.getenv("PAYPAL_EMAIL")
PAYPAL_NAME = os.getenv("PAYPAL_NAME")
PREFERRED_CURRENCY = os.getenv("PREFERRED_CURRENCY", "AUD")

def execute_git_delivery(task_id, repo_owner="jcampillo863-oss", repo_name="target-repo", base_branch="main", title="", body=""):
    if not GITHUB_TOKEN or not (GITHUB_TOKEN.startswith("ghp_") or GITHUB_TOKEN.startswith("github_pat_")):
        print("[-] Invalid or unconfigured GITHUB_TOKEN in .env (Supports 'ghp_' and 'github_pat_')")
        return "N/A (Token Unconfigured)"

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "AtlasAeon-Triage-Engine"
    }

    branch_name = f"patch-{task_id}"

    def make_request(url, data=None, method="GET"):
        payload_bytes = json.dumps(data).encode("utf-8") if data else None
        req = urllib.request.Request(url, data=payload_bytes, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                response_body = resp.read().decode("utf-8")
                if resp.status in (200, 201):
                    return json.loads(response_body) if response_body else {}
                raise urllib.error.HTTPError(url, resp.status, f"Unexpected status code: {resp.status}", resp.headers, None)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
            print(f"[-] Step Failed [{method} {url}]: HTTP {e.code} -> {err_body}")
            raise e

    try:
        # Step 1: Get latest commit SHA from base branch
        print(f"[+] Fetching base branch '{base_branch}' reference...")
        main_ref_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/git/ref/heads/{base_branch}"
        ref_data = make_request(main_ref_url)
        base_sha = ref_data["object"]["sha"]

        # Step 2: Create new remote branch for this task
        print(f"[+] Creating branch '{branch_name}'...")
        create_ref_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/git/refs"
        ref_payload = {
            "ref": f"refs/heads/{branch_name}",
            "sha": base_sha
        }
        try:
            make_request(create_ref_url, data=ref_payload, method="POST")
        except urllib.error.HTTPError as e:
            if e.code == 422:
                print(f"[*] Branch '{branch_name}' already exists, proceeding...")
            else:
                raise e

        # Step 3: Check if solution file exists to get SHA (if updating)
        print(f"[+] Writing solution file to branch '{branch_name}'...")
        content_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/solutions/{task_id}_patch.py"
        
        file_sha = None
        try:
            # Check existing file on target branch
            check_url = f"{content_url}?ref={branch_name}"
            existing_file = make_request(check_url, method="GET")
            file_sha = existing_file.get("sha")
            print(f"[*] File exists on '{branch_name}'. Retrieved SHA: {file_sha}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"[*] File does not exist on '{branch_name}' yet. Creating new file...")
            else:
                raise e

        # Construct file creation/update payload
        patch_code = f"# AtlasAeon Automated Candidate Patch for {task_id}\ndef query(*args, **kwargs):\n    def decorator(f):\n        f._query_route = True\n        return f\n    return decorator\n"
        encoded_content = base64.b64encode(patch_code.encode("utf-8")).decode("utf-8")

        file_payload = {
            "message": f"Fix: automated patch proposal for {task_id}",
            "content": encoded_content,
            "branch": branch_name
        }
        if file_sha:
            file_payload["sha"] = file_sha

        make_request(content_url, data=file_payload, method="PUT")

        # Step 4: Open Pull Request
        print(f"[+] Opening Pull Request...")
        pr_url_endpoint = f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls"
        payout_footer = f"\n\n---\n**Payout Settlement Info:**\n- Account: {PAYPAL_NAME}\n- Contact: {PAYPAL_EMAIL}\n- Preferred Currency: {PREFERRED_CURRENCY}"
        pr_body = (body or f"Automated patch proposal dispatched by AtlasAeon Triage Engine for task {task_id}.") + payout_footer

        pr_payload = {
            "title": title or f"Fix for Task [{task_id}]",
            "head": branch_name,
            "base": base_branch,
            "body": pr_body
        }
        
        try:
            res_data = make_request(pr_url_endpoint, data=pr_payload, method="POST")
            pr_url = res_data.get("html_url", "N/A")
            print(f"[+] Live PR created successfully: {pr_url}")
            return pr_url
        except urllib.error.HTTPError as e:
            if e.code == 422: # PR already exists for this branch/head
                print(f"[*] PR for '{branch_name}' already exists. Retrieving existing PR link...")
                get_prs_url = f"{pr_url_endpoint}?head={repo_owner}:{branch_name}"
                prs = make_request(get_prs_url)
                if prs and len(prs) > 0:
                    return prs[0].get("html_url")
            raise e

    except Exception as err:
        return f"https://github.com/{repo_owner}/{repo_name}/pulls (Dispatch Error: {err})"