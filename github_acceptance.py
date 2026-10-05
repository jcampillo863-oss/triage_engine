import hashlib
import hmac
import json


class GitHubAuthenticationError(ValueError):
    pass


class GitHubPayloadError(ValueError):
    pass


def verify_signature(raw_body: bytes, signature_header: str, secret: str) -> bool:
    """
    Verify a GitHub-style HMAC-SHA256 webhook signature.

    Authentication is performed against the exact raw HTTP body bytes.
    Parsed/re-serialized JSON must never be substituted for raw_body.
    """
    if not isinstance(raw_body, bytes):
        raise TypeError("raw_body must be bytes.")

    if not secret:
        raise GitHubAuthenticationError(
            "Webhook secret is not configured."
        )

    if not signature_header:
        raise GitHubAuthenticationError(
            "Webhook signature is missing."
        )

    if not signature_header.startswith("sha256="):
        raise GitHubAuthenticationError(
            "Webhook signature format is invalid."
        )

    supplied_digest = signature_header[len("sha256="):].strip()

    if len(supplied_digest) != 64:
        raise GitHubAuthenticationError(
            "Webhook signature digest length is invalid."
        )

    try:
        bytes.fromhex(supplied_digest)
    except ValueError as exc:
        raise GitHubAuthenticationError(
            "Webhook signature digest is not valid hexadecimal."
        ) from exc

    expected_digest = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_digest, supplied_digest):
        raise GitHubAuthenticationError(
            "Webhook signature verification failed."
        )

    return True


def parse_authenticated_payload(
    raw_body: bytes,
    signature_header: str,
    secret: str,
) -> dict:
    """
    Authentication happens before the payload is trusted or interpreted.
    """
    verify_signature(raw_body, signature_header, secret)

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GitHubPayloadError(
            "Authenticated webhook body is not valid JSON."
        ) from exc

    if not isinstance(payload, dict):
        raise GitHubPayloadError(
            "Webhook payload must be a JSON object."
        )

    return payload


def normalize_pull_request_event(
    payload: dict,
    *,
    delivery_id: str,
    expected_repository: str,
) -> dict:
    """
    Convert an already-authenticated GitHub payload into normalized
    external-event evidence.

    This function does NOT decide contractual acceptance.
    """
    if not delivery_id or not delivery_id.strip():
        raise GitHubPayloadError(
            "GitHub delivery ID is required."
        )

    repository = payload.get("repository") or {}
    repository_full_name = repository.get("full_name")

    if not repository_full_name:
        raise GitHubPayloadError(
            "Repository identity is missing."
        )

    if repository_full_name != expected_repository:
        raise GitHubPayloadError(
            "Webhook repository does not match configured repository."
        )

    action = payload.get("action")

    if not action:
        raise GitHubPayloadError(
            "Webhook action is missing."
        )

    pull_request = payload.get("pull_request")

    if not isinstance(pull_request, dict):
        raise GitHubPayloadError(
            "Pull request object is missing."
        )

    pr_number = pull_request.get("number")

    if pr_number is None:
        # GitHub commonly supplies PR number at the top level.
        pr_number = payload.get("number")

    if not isinstance(pr_number, int) or isinstance(pr_number, bool):
        raise GitHubPayloadError(
            "Pull request number is missing or invalid."
        )

    pr_url = pull_request.get("html_url")

    if not pr_url:
        raise GitHubPayloadError(
            "Pull request URL is missing."
        )

    merged = pull_request.get("merged")

    if not isinstance(merged, bool):
        raise GitHubPayloadError(
            "Pull request merged state is missing or invalid."
        )

    merge_commit_sha = pull_request.get("merge_commit_sha")

    return {
        "source": "github",
        "external_event_id": delivery_id.strip(),
        "event_type": "pull_request",
        "action": action,
        "repository": repository_full_name,
        "pull_request_number": pr_number,
        "pull_request_url": pr_url,
        "merged": merged,
        "merge_commit_sha": merge_commit_sha,
        "authenticated": True,
    }


def evaluate_github_merge_acceptance(event: dict) -> dict:
    """
    Contract-policy candidate:
    a merged PR event is acceptable only when GitHub reports
    action=closed and merged=True.

    Authentication and repository checks must already have occurred.
    """
    reasons = []

    if event.get("authenticated") is not True:
        reasons.append("Event is not authenticated.")

    if event.get("event_type") != "pull_request":
        reasons.append("Event is not a pull request event.")

    if event.get("action") != "closed":
        reasons.append("Pull request action is not closed.")

    if event.get("merged") is not True:
        reasons.append("Pull request was not merged.")

    return {
        "accepted": len(reasons) == 0,
        "policy_name": "github_merge_acceptance",
        "policy_version": "1",
        "rejection_reasons": reasons,
    }
