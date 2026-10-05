import hashlib
import hmac
import json

from github_acceptance import (
    GitHubAuthenticationError,
    GitHubPayloadError,
    parse_authenticated_payload,
)


SECRET = "synthetic-test-secret-never-use-in-production"

payload = {
    "action": "closed",
    "pull_request": {
        "merged": True,
        "number": 63,
    },
}

raw = json.dumps(
    payload,
    separators=(",", ":"),
).encode("utf-8")

valid_signature = "sha256=" + hmac.new(
    SECRET.encode("utf-8"),
    raw,
    hashlib.sha256,
).hexdigest()


passed = 0
failed = 0


def expect_success(name, operation):
    global passed, failed

    try:
        operation()
        print(f"[PASS] {name}")
        passed += 1
    except Exception as exc:
        print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
        failed += 1


def expect_rejection(name, operation):
    global passed, failed

    try:
        operation()
        print(f"[FAIL] {name}: accepted unexpectedly")
        failed += 1
    except (GitHubAuthenticationError, GitHubPayloadError):
        print(f"[PASS] {name}")
        passed += 1


expect_success(
    "Valid signature",
    lambda: parse_authenticated_payload(
        raw,
        valid_signature,
        SECRET,
    ),
)

expect_rejection(
    "Missing signature",
    lambda: parse_authenticated_payload(
        raw,
        "",
        SECRET,
    ),
)

expect_rejection(
    "Wrong secret",
    lambda: parse_authenticated_payload(
        raw,
        valid_signature,
        "wrong-secret",
    ),
)

expect_rejection(
    "Malformed signature",
    lambda: parse_authenticated_payload(
        raw,
        "sha256=not-hex",
        SECRET,
    ),
)

tampered = raw.replace(b'"merged":true', b'"merged":false')

expect_rejection(
    "Payload tampering",
    lambda: parse_authenticated_payload(
        tampered,
        valid_signature,
        SECRET,
    ),
)

invalid_json = b'{"action":'

invalid_json_signature = "sha256=" + hmac.new(
    SECRET.encode("utf-8"),
    invalid_json,
    hashlib.sha256,
).hexdigest()

expect_rejection(
    "Authenticated but invalid JSON",
    lambda: parse_authenticated_payload(
        invalid_json,
        invalid_json_signature,
        SECRET,
    ),
)

print()
print("TEST RESULTS")
print("PASS:", passed)
print("FAIL:", failed)

if failed:
    raise SystemExit(1)
