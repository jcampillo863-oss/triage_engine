import db

from github_acceptance import (
    normalize_pull_request_event,
)
from work_delivery import (
    WorkDeliveryError,
    create_delivery,
    resolve_delivery_for_external_event,
)


passed = 0
failed = 0

TASK = "__test_acceptance_binding__"
OTHER_TASK = "__test_acceptance_binding_other__"
REPO = "example-owner/example-repo"
ENV = "TEST"
PR = 910063
PR_URL = (
    "https://github.com/"
    "example-owner/example-repo/pull/910063"
)


def ok(name):
    global passed
    print(f"[PASS] {name}")
    passed += 1


def bad(name, detail):
    global failed
    print(f"[FAIL] {name}: {detail}")
    failed += 1


def cleanup():
    with db.get_db() as conn:
        conn.execute(
            """
            DELETE FROM work_deliveries
            WHERE environment = ?
              AND task_id IN (?, ?)
            """,
            (ENV, TASK, OTHER_TASK),
        )
        conn.commit()


def github_payload(
    *,
    repository=REPO,
    pr_number=PR,
    merged=True,
):
    return {
        "action": "closed",
        "number": pr_number,
        "repository": {
            "full_name": repository,
        },
        "pull_request": {
            "number": pr_number,
            "html_url": (
                f"https://github.com/"
                f"{repository}/pull/{pr_number}"
            ),
            "merged": merged,
            "merge_commit_sha": "synthetic-sha",
        },
    }


cleanup()

try:
    delivery = create_delivery(
        task_id=TASK,
        repository=REPO,
        pull_request_number=PR,
        pull_request_url=PR_URL,
        environment=ENV,
    )

    event = normalize_pull_request_event(
        github_payload(),
        delivery_id="synthetic-delivery-001",
        expected_repository=REPO,
    )

    resolved = resolve_delivery_for_external_event(
        event,
        environment=ENV,
    )

    if (
        resolved["delivery_id"]
        == delivery["delivery_id"]
        and resolved["task_id"] == TASK
    ):
        ok("Authenticated event resolves to canonical task")
    else:
        bad(
            "Authenticated event resolves to canonical task",
            dict(resolved),
        )

    # External payload gets no opportunity to assert task identity.
    event["task_id"] = "attacker_supplied_task"

    resolved = resolve_delivery_for_external_event(
        event,
        environment=ENV,
    )

    if resolved["task_id"] == TASK:
        ok("External task_id is ignored")
    else:
        bad(
            "External task_id is ignored",
            resolved["task_id"],
        )

    # A legitimate GitHub event for an unknown PR must not bind.
    unknown_event = normalize_pull_request_event(
        github_payload(pr_number=910064),
        delivery_id="synthetic-delivery-002",
        expected_repository=REPO,
    )

    try:
        resolve_delivery_for_external_event(
            unknown_event,
            environment=ENV,
        )
        bad(
            "Unknown PR rejected",
            "resolved unexpectedly",
        )
    except WorkDeliveryError:
        ok("Unknown PR rejected")

    # Authentication flag cannot be stripped/bypassed.
    unauthenticated = dict(event)
    unauthenticated["authenticated"] = False

    try:
        resolve_delivery_for_external_event(
            unauthenticated,
            environment=ENV,
        )
        bad(
            "Unauthenticated event rejected",
            "resolved unexpectedly",
        )
    except WorkDeliveryError:
        ok("Unauthenticated event rejected")

    # Wrong source cannot use GitHub delivery binding.
    wrong_source = dict(event)
    wrong_source["source"] = "other-provider"

    try:
        resolve_delivery_for_external_event(
            wrong_source,
            environment=ENV,
        )
        bad(
            "Wrong source rejected",
            "resolved unexpectedly",
        )
    except WorkDeliveryError:
        ok("Wrong source rejected")

    # Environment isolation.
    try:
        resolve_delivery_for_external_event(
            event,
            environment="PRODUCTION",
        )
        bad(
            "Environment mismatch rejected",
            "resolved unexpectedly",
        )
    except WorkDeliveryError:
        ok("Environment mismatch rejected")

finally:
    cleanup()


with db.get_db() as conn:
    remaining = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM work_deliveries
        WHERE task_id IN (?, ?)
        """,
        (TASK, OTHER_TASK),
    ).fetchone()["n"]

    total = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM work_deliveries
        """
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
print("PERSISTED SYNTHETIC ROWS:", remaining)
print("TOTAL WORK DELIVERY ROWS:", total)
print("integrity_check:", integrity)
print("foreign_key_errors:", len(fk_errors))

if failed:
    raise SystemExit(1)
