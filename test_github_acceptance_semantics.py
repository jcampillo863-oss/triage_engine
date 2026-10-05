from github_acceptance import (
    GitHubPayloadError,
    normalize_pull_request_event,
    evaluate_github_merge_acceptance,
)


REPO = "example-owner/example-repo"


def payload(action="closed", merged=True, repository=REPO):
    return {
        "action": action,
        "number": 63,
        "repository": {
            "full_name": repository,
        },
        "pull_request": {
            "number": 63,
            "html_url": "https://example.invalid/pr/63",
            "merged": merged,
            "merge_commit_sha": "synthetic-merge-sha",
        },
    }


passed = 0
failed = 0


def pass_test(name):
    global passed
    print(f"[PASS] {name}")
    passed += 1


def fail_test(name, detail):
    global failed
    print(f"[FAIL] {name}: {detail}")
    failed += 1


# 1. Legitimate merged PR.
event = normalize_pull_request_event(
    payload(),
    delivery_id="delivery-001",
    expected_repository=REPO,
)

decision = evaluate_github_merge_acceptance(event)

if decision["accepted"] is True:
    pass_test("Closed + merged PR accepted")
else:
    fail_test("Closed + merged PR accepted", decision)


# 2. Closed but not merged.
event = normalize_pull_request_event(
    payload(merged=False),
    delivery_id="delivery-002",
    expected_repository=REPO,
)

decision = evaluate_github_merge_acceptance(event)

if decision["accepted"] is False:
    pass_test("Closed but unmerged PR rejected")
else:
    fail_test("Closed but unmerged PR rejected", decision)


# 3. Reopened/open-style action.
event = normalize_pull_request_event(
    payload(action="reopened", merged=False),
    delivery_id="delivery-003",
    expected_repository=REPO,
)

decision = evaluate_github_merge_acceptance(event)

if decision["accepted"] is False:
    pass_test("Reopened PR rejected")
else:
    fail_test("Reopened PR rejected", decision)


# 4. Wrong repository must fail normalization.
try:
    normalize_pull_request_event(
        payload(repository="attacker/wrong-repo"),
        delivery_id="delivery-004",
        expected_repository=REPO,
    )
    fail_test("Wrong repository rejected", "accepted unexpectedly")
except GitHubPayloadError:
    pass_test("Wrong repository rejected")


# 5. Missing delivery identity.
try:
    normalize_pull_request_event(
        payload(),
        delivery_id="",
        expected_repository=REPO,
    )
    fail_test("Missing delivery ID rejected", "accepted unexpectedly")
except GitHubPayloadError:
    pass_test("Missing delivery ID rejected")


# 6. Missing PR object.
bad = payload()
del bad["pull_request"]

try:
    normalize_pull_request_event(
        bad,
        delivery_id="delivery-006",
        expected_repository=REPO,
    )
    fail_test("Missing PR object rejected", "accepted unexpectedly")
except GitHubPayloadError:
    pass_test("Missing PR object rejected")


# 7. Missing merged state must not silently become False/True.
bad = payload()
del bad["pull_request"]["merged"]

try:
    normalize_pull_request_event(
        bad,
        delivery_id="delivery-007",
        expected_repository=REPO,
    )
    fail_test("Missing merged state rejected", "accepted unexpectedly")
except GitHubPayloadError:
    pass_test("Missing merged state rejected")


# 8. Authentication cannot be bypassed at policy layer.
event = normalize_pull_request_event(
    payload(),
    delivery_id="delivery-008",
    expected_repository=REPO,
)

event["authenticated"] = False

decision = evaluate_github_merge_acceptance(event)

if decision["accepted"] is False:
    pass_test("Unauthenticated normalized event rejected")
else:
    fail_test("Unauthenticated normalized event rejected", decision)


print()
print("TEST RESULTS")
print("PASS:", passed)
print("FAIL:", failed)

if failed:
    raise SystemExit(1)
