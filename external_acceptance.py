import hashlib
import json
from datetime import datetime, timezone

import db

from github_acceptance import (
    evaluate_github_merge_acceptance,
)
from work_delivery import (
    WorkDeliveryError,
    resolve_delivery_for_external_event,
)


VALID_ENVIRONMENTS = {"TEST", "SANDBOX", "PRODUCTION"}


class ExternalAcceptanceError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def generate_event_id(
    source: str,
    external_event_id: str,
) -> str:
    raw = (
        f"{source.strip().lower()}:"
        f"{external_event_id.strip()}"
    )

    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:24]

    return f"evt_{digest}"


def generate_decision_id(
    event_id: str,
    policy_name: str,
    policy_version: str,
) -> str:
    raw = (
        f"{event_id}:"
        f"{policy_name}:"
        f"{policy_version}"
    )

    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:24]

    return f"cad_{digest}"


def process_github_acceptance_event(
    event: dict,
    *,
    environment: str,
):
    if not isinstance(event, dict):
        raise ExternalAcceptanceError(
            "Normalized event must be a dictionary."
        )

    environment = (environment or "").strip().upper()

    if environment not in VALID_ENVIRONMENTS:
        raise ExternalAcceptanceError(
            "Invalid environment."
        )

    try:
        delivery = resolve_delivery_for_external_event(
            event,
            environment=environment,
        )
    except WorkDeliveryError as exc:
        raise ExternalAcceptanceError(
            str(exc)
        ) from exc

    source = event.get("source")
    external_event_id = (
        event.get("external_event_id") or ""
    ).strip()

    if not external_event_id:
        raise ExternalAcceptanceError(
            "External event identity is required."
        )

    event_id = generate_event_id(
        source,
        external_event_id,
    )

    policy = evaluate_github_merge_acceptance(event)

    policy_name = policy["policy_name"]
    policy_version = str(policy["policy_version"])
    accepted = bool(policy["accepted"])
    rejection_reasons = policy.get(
        "rejection_reasons",
        [],
    )

    decision_id = generate_decision_id(
        event_id,
        policy_name,
        policy_version,
    )

    raw_payload = json.dumps(
        event,
        sort_keys=True,
        separators=(",", ":"),
    )

    now = utc_now()

    with db.get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing_event = conn.execute(
            """
            SELECT *
            FROM external_acceptance_events
            WHERE source = ?
              AND external_event_id = ?
            """,
            (
                source,
                external_event_id,
            ),
        ).fetchone()

        if existing_event:
            if (
                existing_event["event_id"] != event_id
                or existing_event["environment"] != environment
                or existing_event["raw_payload"] != raw_payload
                or
                existing_event["task_id"]
                != delivery["task_id"]
                or
                existing_event["work_delivery_id"]
                != delivery["delivery_id"]
                or
                existing_event["repository"]
                != event["repository"]
                or
                existing_event["pull_request_number"]
                != event["pull_request_number"]
            ):
                raise ExternalAcceptanceError(
                    "External event identity conflicts "
                    "with existing canonical evidence."
                )
        else:
            conn.execute(
                """
                INSERT INTO external_acceptance_events (
                    event_id,
                    task_id,
                    source,
                    external_event_id,
                    event_type,
                    repository,
                    pull_request_url,
                    git_commit_hash,
                    authenticated,
                    raw_payload,
                    environment,
                    observed_at,
                    created_at,
                    work_delivery_id,
                    pull_request_number
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    event_id,
                    delivery["task_id"],
                    source,
                    external_event_id,
                    event.get("event_type"),
                    event.get("repository"),
                    event.get("pull_request_url"),
                    event.get("merge_commit_sha"),
                    1,
                    raw_payload,
                    environment,
                    now,
                    now,
                    delivery["delivery_id"],
                    event.get("pull_request_number"),
                ),
            )

        existing_decision = conn.execute(
            """
            SELECT *
            FROM contract_acceptance_decisions
            WHERE acceptance_event_id = ?
              AND policy_name = ?
              AND policy_version = ?
            """,
            (
                event_id,
                policy_name,
                policy_version,
            ),
        ).fetchone()

        if existing_decision:
            if (
                existing_decision["decision_id"]
                != decision_id
                or
                existing_decision["task_id"]
                != delivery["task_id"]
                or
                bool(existing_decision["accepted"])
                != accepted
            ):
                raise ExternalAcceptanceError(
                    "Existing contractual decision "
                    "conflicts with replayed event."
                )
        else:
            conn.execute(
                """
                INSERT INTO contract_acceptance_decisions (
                    decision_id,
                    task_id,
                    acceptance_event_id,
                    policy_name,
                    policy_version,
                    accepted,
                    rejection_reasons,
                    environment,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    delivery["task_id"],
                    event_id,
                    policy_name,
                    policy_version,
                    1 if accepted else 0,
                    json.dumps(rejection_reasons),
                    environment,
                    now,
                ),
            )

        conn.commit()

        stored_event = conn.execute(
            """
            SELECT *
            FROM external_acceptance_events
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()

        stored_decision = conn.execute(
            """
            SELECT *
            FROM contract_acceptance_decisions
            WHERE decision_id = ?
            """,
            (decision_id,),
        ).fetchone()

    return {
        "delivery": delivery,
        "event": stored_event,
        "decision": stored_decision,
    }
