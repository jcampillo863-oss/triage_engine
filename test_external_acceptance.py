import db

from github_acceptance import (
    normalize_pull_request_event,
)
from work_delivery import create_delivery
from external_acceptance import (
    ExternalAcceptanceError,
    process_github_acceptance_event,
)


passed = 0
failed = 0

TASK_MERGED = "__test_external_accept_merged__"
TASK_REJECTED = "__test_external_accept_rejected__"

REPO = "example-owner/example-repo"
ENV = "TEST"

MERGED_PR = 920063
REJECTED_PR = 920064


def ok(name):
    global passed
    passed += 1
    print(f"[PASS] {name}")


def bad(name, detail):
    global failed
    failed += 1
    print(f"[FAIL] {name}: {detail}")


def cleanup():
    with db.get_db() as conn:
        task_ids = (
            TASK_MERGED,
            TASK_REJECTED,
        )

        event_rows = conn.execute(
            """
            SELECT event_id
            FROM external_acceptance_events
            WHERE task_id IN (?, ?)
              AND environment = ?
            """,
            (*task_ids, ENV),
        ).fetchall()

        event_ids = [
            row["event_id"]
            for row in event_rows
        ]

        if event_ids:
            placeholders = ",".join(
                "?" for _ in event_ids
            )

            conn.execute(
                f"""
                DELETE FROM contract_acceptance_decisions
                WHERE acceptance_event_id
                    IN ({placeholders})
                """,
                event_ids,
            )

            conn.execute(
                f"""
                DELETE FROM external_acceptance_events
                WHERE event_id
                    IN ({placeholders})
                """,
                event_ids,
            )

        conn.execute(
            """
            DELETE FROM work_deliveries
            WHERE task_id IN (?, ?)
              AND environment = ?
            """,
            (*task_ids, ENV),
        )

        conn.commit()


def payload(pr_number, *, merged):
    return {
        "action": "closed",
        "number": pr_number,
        "repository": {
            "full_name": REPO,
        },
        "pull_request": {
            "number": pr_number,
            "html_url": (
                f"https://github.com/"
                f"{REPO}/pull/{pr_number}"
            ),
            "merged": merged,
            "merge_commit_sha": (
                f"synthetic-sha-{pr_number}"
            ),
        },
    }


cleanup()

try:
    # -------------------------------------------------
    # 1. Canonical delivery for merged PR.
    # -------------------------------------------------

    merged_delivery = create_delivery(
        task_id=TASK_MERGED,
        repository=REPO,
        pull_request_number=MERGED_PR,
        pull_request_url=(
            f"https://github.com/"
            f"{REPO}/pull/{MERGED_PR}"
        ),
        environment=ENV,
    )

    merged_event = normalize_pull_request_event(
        payload(
            MERGED_PR,
            merged=True,
        ),
        delivery_id="synthetic-gh-delivery-920063",
        expected_repository=REPO,
    )

    result = process_github_acceptance_event(
        merged_event,
        environment=ENV,
    )

    if (
        result["decision"]["accepted"] == 1
        and
        result["event"]["work_delivery_id"]
        == merged_delivery["delivery_id"]
        and
        result["event"]["task_id"]
        == TASK_MERGED
    ):
        ok(
            "Merged PR persisted as accepted "
            "contractual decision"
        )
    else:
        bad(
            "Merged PR persisted as accepted "
            "contractual decision",
            "unexpected persisted state",
        )

    # -------------------------------------------------
    # 2. Replay exact same GitHub delivery.
    # -------------------------------------------------

    replay = process_github_acceptance_event(
        merged_event,
        environment=ENV,
    )

    if (
        replay["event"]["event_id"]
        == result["event"]["event_id"]
        and
        replay["decision"]["decision_id"]
        == result["decision"]["decision_id"]
    ):
        ok("Exact GitHub delivery replay is idempotent")
    else:
        bad(
            "Exact GitHub delivery replay is idempotent",
            "identities changed",
        )

    with db.get_db() as conn:
        event_count = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM external_acceptance_events
            WHERE external_event_id = ?
              AND source = 'github'
            """,
            ("synthetic-gh-delivery-920063",),
        ).fetchone()["n"]

        decision_count = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM contract_acceptance_decisions
            WHERE acceptance_event_id = ?
            """,
            (result["event"]["event_id"],),
        ).fetchone()["n"]

    if event_count == 1 and decision_count == 1:
        ok("Replay created no duplicate canonical rows")
    else:
        bad(
            "Replay created no duplicate canonical rows",
            (
                f"events={event_count}, "
                f"decisions={decision_count}"
            ),
        )

    # -------------------------------------------------
    # 3. Authenticated closed-but-unmerged PR.
    # -------------------------------------------------

    rejected_delivery = create_delivery(
        task_id=TASK_REJECTED,
        repository=REPO,
        pull_request_number=REJECTED_PR,
        pull_request_url=(
            f"https://github.com/"
            f"{REPO}/pull/{REJECTED_PR}"
        ),
        environment=ENV,
    )

    rejected_event = normalize_pull_request_event(
        payload(
            REJECTED_PR,
            merged=False,
        ),
        delivery_id="synthetic-gh-delivery-920064",
        expected_repository=REPO,
    )

    rejected = process_github_acceptance_event(
        rejected_event,
        environment=ENV,
    )

    if (
        rejected["decision"]["accepted"] == 0
        and
        rejected["event"]["work_delivery_id"]
        == rejected_delivery["delivery_id"]
    ):
        ok(
            "Unmerged PR retained as evidence "
            "with rejected decision"
        )
    else:
        bad(
            "Unmerged PR retained as evidence "
            "with rejected decision",
            "unexpected persisted state",
        )

    # -------------------------------------------------
    # 4. Unknown PR must never create evidence.
    # -------------------------------------------------

    unknown_event = normalize_pull_request_event(
        payload(
            920099,
            merged=True,
        ),
        delivery_id="synthetic-gh-delivery-920099",
        expected_repository=REPO,
    )

    try:
        process_github_acceptance_event(
            unknown_event,
            environment=ENV,
        )

        bad(
            "Unknown PR cannot create acceptance evidence",
            "accepted unexpectedly",
        )

    except ExternalAcceptanceError:
        ok(
            "Unknown PR cannot create acceptance evidence"
        )

    with db.get_db() as conn:
        unknown_count = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM external_acceptance_events
            WHERE external_event_id = ?
            """,
            ("synthetic-gh-delivery-920099",),
        ).fetchone()["n"]

    if unknown_count == 0:
        ok("Unknown PR left no database evidence")
    else:
        bad(
            "Unknown PR left no database evidence",
            f"found {unknown_count}",
        )

    # -------------------------------------------------
    # 5. Same delivery ID with conflicting PR.
    # -------------------------------------------------

    conflict_event = dict(merged_event)
    conflict_event["pull_request_number"] = REJECTED_PR
    conflict_event["pull_request_url"] = (
        f"https://github.com/"
        f"{REPO}/pull/{REJECTED_PR}"
    )

    try:
        process_github_acceptance_event(
            conflict_event,
            environment=ENV,
        )

        bad(
            "Reused external delivery identity "
            "cannot bind to another PR",
            "accepted unexpectedly",
        )

    except ExternalAcceptanceError:
        ok(
            "Reused external delivery identity "
            "cannot bind to another PR"
        )

finally:
    cleanup()


with db.get_db() as conn:
    synthetic_events = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM external_acceptance_events
        WHERE task_id IN (?, ?)
        """,
        (
            TASK_MERGED,
            TASK_REJECTED,
        ),
    ).fetchone()["n"]

    synthetic_decisions = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM contract_acceptance_decisions
        WHERE task_id IN (?, ?)
        """,
        (
            TASK_MERGED,
            TASK_REJECTED,
        ),
    ).fetchone()["n"]

    synthetic_deliveries = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM work_deliveries
        WHERE task_id IN (?, ?)
        """,
        (
            TASK_MERGED,
            TASK_REJECTED,
        ),
    ).fetchone()["n"]

    integrity = conn.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    fk_errors = conn.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()


print()
print("TEST RESULTS")
print("PASS:", passed)
print("FAIL:", failed)
print("PERSISTED SYNTHETIC EVENTS:", synthetic_events)
print(
    "PERSISTED SYNTHETIC DECISIONS:",
    synthetic_decisions,
)
print(
    "PERSISTED SYNTHETIC DELIVERIES:",
    synthetic_deliveries,
)
print("integrity_check:", integrity)
print("foreign_key_errors:", len(fk_errors))

if failed:
    raise SystemExit(1)
