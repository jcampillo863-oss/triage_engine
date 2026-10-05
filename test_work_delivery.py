import db

from work_delivery import (
    WorkDeliveryError,
    create_delivery,
)


passed = 0
failed = 0

TASK_ALPHA = "__test_work_delivery_alpha__"
TASK_ATTACKER = "__test_work_delivery_attacker__"
TASK_ERROR = "__test_work_delivery_error__"

REPO = "example-owner/example-repo"
ENVIRONMENT = "TEST"

TEST_TASKS = (
    TASK_ALPHA,
    TASK_ATTACKER,
    TASK_ERROR,
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
        placeholders = ",".join(
            "?" for _ in TEST_TASKS
        )

        conn.execute(
            f"""
            DELETE FROM work_deliveries
            WHERE environment = ?
              AND task_id IN ({placeholders})
            """,
            (ENVIRONMENT, *TEST_TASKS),
        )

        conn.commit()


# Start from a known synthetic-test baseline.
cleanup()

try:
    first = create_delivery(
        task_id=TASK_ALPHA,
        repository=REPO,
        pull_request_number=900063,
        pull_request_url=(
            "https://github.com/"
            "example-owner/example-repo/pull/900063"
        ),
        environment=ENVIRONMENT,
    )

    if first["status"] == "DISPATCHED":
        ok("Canonical delivery created")
    else:
        bad(
            "Canonical delivery created",
            first["status"],
        )

    replay = create_delivery(
        task_id=TASK_ALPHA,
        repository=REPO,
        pull_request_number=900063,
        pull_request_url=(
            "https://github.com/"
            "example-owner/example-repo/pull/900063"
        ),
        environment=ENVIRONMENT,
    )

    if replay["delivery_id"] == first["delivery_id"]:
        ok("Same task + same PR is idempotent")
    else:
        bad(
            "Same task + same PR is idempotent",
            "delivery IDs differ",
        )

    try:
        create_delivery(
            task_id=TASK_ATTACKER,
            repository=REPO,
            pull_request_number=900063,
            pull_request_url=(
                "https://github.com/"
                "example-owner/example-repo/pull/900063"
            ),
            environment=ENVIRONMENT,
        )

        bad(
            "Different task + same PR rejected",
            "accepted unexpectedly",
        )

    except WorkDeliveryError:
        ok("Different task + same PR rejected")

    try:
        create_delivery(
            task_id=TASK_ERROR,
            repository=REPO,
            pull_request_number=900064,
            pull_request_url=(
                "https://github.com/"
                "example-owner/example-repo/pulls "
                "(Dispatch Error: HTTP Error 422)"
            ),
            environment=ENVIRONMENT,
        )

        bad(
            "Dispatch-error string rejected",
            "accepted unexpectedly",
        )

    except WorkDeliveryError:
        ok("Dispatch-error string rejected")

    second = create_delivery(
        task_id=TASK_ALPHA,
        repository=REPO,
        pull_request_number=900064,
        pull_request_url=(
            "https://github.com/"
            "example-owner/example-repo/pull/900064"
        ),
        environment=ENVIRONMENT,
    )

    if second["delivery_id"] != first["delivery_id"]:
        ok("Same task + later different PR allowed")
    else:
        bad(
            "Same task + later different PR allowed",
            "delivery IDs identical",
        )

    try:
        create_delivery(
            task_id=TASK_ALPHA,
            repository=REPO,
            pull_request_number=900065,
            pull_request_url=(
                "https://github.com/"
                "attacker/wrong-repo/pull/900065"
            ),
            environment=ENVIRONMENT,
        )

        bad(
            "Repository/URL mismatch rejected",
            "accepted unexpectedly",
        )

    except WorkDeliveryError:
        ok("Repository/URL mismatch rejected")

    with db.get_db() as conn:
        count = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM work_deliveries
            WHERE environment = ?
              AND task_id = ?
            """,
            (
                ENVIRONMENT,
                TASK_ALPHA,
            ),
        ).fetchone()["n"]

    if count == 2:
        ok("Exactly two distinct task deliveries exist")
    else:
        bad(
            "Exactly two distinct task deliveries exist",
            f"found {count}",
        )

finally:
    cleanup()


with db.get_db() as conn:
    placeholders = ",".join(
        "?" for _ in TEST_TASKS
    )

    persisted_test_rows = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM work_deliveries
        WHERE environment = ?
          AND task_id IN ({placeholders})
        """,
        (ENVIRONMENT, *TEST_TASKS),
    ).fetchone()["n"]

    total_rows = conn.execute(
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
print(
    "PERSISTED SYNTHETIC TEST ROWS:",
    persisted_test_rows,
)
print(
    "TOTAL WORK DELIVERY ROWS:",
    total_rows,
)
print("integrity_check:", integrity)
print("foreign_key_errors:", len(fk_errors))

if failed:
    raise SystemExit(1)
