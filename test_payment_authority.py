import db

from github_acceptance import normalize_pull_request_event
from work_delivery import create_delivery
from external_acceptance import process_github_acceptance_event

from payment_authority import (
    PaymentAuthorityError,
    establish_payment_authorization,
)


passed = 0
failed = 0

TASK = "__test_payment_authority__"
REPO = "example-owner/example-repo"
PR = 930063
ENV = "TEST"

PROVIDER = "synthetic-provider"
AMOUNT = 5000
CURRENCY = "USD"


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
        auth_ids = [
            row["authorization_id"]
            for row in conn.execute(
                """
                SELECT authorization_id
                FROM payment_authorizations
                WHERE task_id = ?
                """,
                (TASK,),
            ).fetchall()
        ]

        if auth_ids:
            placeholders = ",".join("?" for _ in auth_ids)

            conn.execute(
                f"""
                DELETE FROM canonical_settlements
                WHERE payment_authorization_id
                    IN ({placeholders})
                """,
                auth_ids,
            )

        conn.execute(
            """
            DELETE FROM payment_authorizations
            WHERE task_id = ?
            """,
            (TASK,),
        )

        verification_events = [
            row["authorization_event_id"]
            for row in conn.execute(
                """
                SELECT authorization_event_id
                FROM payment_authorization_verifications
                WHERE task_id = ?
                """,
                (TASK,),
            ).fetchall()
        ]

        conn.execute(
            """
            DELETE FROM payment_authorization_verifications
            WHERE task_id = ?
            """,
            (TASK,),
        )

        if verification_events:
            placeholders = ",".join(
                "?" for _ in verification_events
            )

            conn.execute(
                f"""
                DELETE FROM payment_authorization_events
                WHERE event_id IN ({placeholders})
                """,
                verification_events,
            )

        event_ids = [
            row["event_id"]
            for row in conn.execute(
                """
                SELECT event_id
                FROM external_acceptance_events
                WHERE task_id = ?
                """,
                (TASK,),
            ).fetchall()
        ]

        conn.execute(
            """
            DELETE FROM contract_acceptance_decisions
            WHERE task_id = ?
            """,
            (TASK,),
        )

        if event_ids:
            placeholders = ",".join(
                "?" for _ in event_ids
            )

            conn.execute(
                f"""
                DELETE FROM external_acceptance_events
                WHERE event_id IN ({placeholders})
                """,
                event_ids,
            )

        conn.execute(
            """
            DELETE FROM work_deliveries
            WHERE task_id = ?
            """,
            (TASK,),
        )

        conn.commit()


def provider_event(
    *,
    event_id,
    authorization_id,
    amount=AMOUNT,
    currency=CURRENCY,
    provider=PROVIDER,
    authenticated=True,
):
    return {
        "provider": provider,
        "provider_event_id": event_id,
        "provider_authorization_id": authorization_id,
        "event_type": "authorization.established",
        "provider_status": "AUTHORIZED",
        "amount_cents": amount,
        "currency": currency,
        "authenticated": authenticated,
    }


cleanup()

try:
    create_delivery(
        task_id=TASK,
        repository=REPO,
        pull_request_number=PR,
        pull_request_url=(
            f"https://github.com/{REPO}/pull/{PR}"
        ),
        environment=ENV,
    )

    github_event = normalize_pull_request_event(
        {
            "action": "closed",
            "number": PR,
            "repository": {
                "full_name": REPO,
            },
            "pull_request": {
                "number": PR,
                "html_url": (
                    f"https://github.com/{REPO}/pull/{PR}"
                ),
                "merged": True,
                "merge_commit_sha": "synthetic-payment-sha",
            },
        },
        delivery_id="synthetic-gh-payment-930063",
        expected_repository=REPO,
    )

    acceptance = process_github_acceptance_event(
        github_event,
        environment=ENV,
    )

    decision_id = acceptance[
        "decision"
    ]["decision_id"]

    result = establish_payment_authorization(
        provider_event=provider_event(
            event_id="provider-event-001",
            authorization_id="provider-auth-001",
        ),
        task_id=TASK,
        contract_acceptance_decision_id=decision_id,
        expected_amount_cents=AMOUNT,
        expected_currency=CURRENCY,
        expected_provider=PROVIDER,
        environment=ENV,
    )

    if (
        result["verification"]["verified"] == 1
        and result["authorization"] is not None
        and result["authorization"]["verified"] == 1
    ):
        ok("Matching authenticated evidence creates authorization")
    else:
        bad(
            "Matching authenticated evidence creates authorization",
            "unexpected result",
        )

    replay = establish_payment_authorization(
        provider_event=provider_event(
            event_id="provider-event-001",
            authorization_id="provider-auth-001",
        ),
        task_id=TASK,
        contract_acceptance_decision_id=decision_id,
        expected_amount_cents=AMOUNT,
        expected_currency=CURRENCY,
        expected_provider=PROVIDER,
        environment=ENV,
    )

    if (
        replay["event"]["event_id"]
        == result["event"]["event_id"]
        and
        replay["authorization"]["authorization_id"]
        == result["authorization"]["authorization_id"]
    ):
        ok("Exact provider replay is idempotent")
    else:
        bad(
            "Exact provider replay is idempotent",
            "identity changed",
        )

    mismatch = establish_payment_authorization(
        provider_event=provider_event(
            event_id="provider-event-002",
            authorization_id="provider-auth-002",
            amount=2500,
        ),
        task_id=TASK,
        contract_acceptance_decision_id=decision_id,
        expected_amount_cents=AMOUNT,
        expected_currency=CURRENCY,
        expected_provider=PROVIDER,
        environment=ENV,
    )

    if (
        mismatch["verification"]["verified"] == 0
        and mismatch["authorization"] is None
    ):
        ok("Amount mismatch retained but not authorized")
    else:
        bad(
            "Amount mismatch retained but not authorized",
            "unexpected authorization",
        )

    try:
        establish_payment_authorization(
            provider_event=provider_event(
                event_id="provider-event-003",
                authorization_id="provider-auth-003",
                authenticated=False,
            ),
            task_id=TASK,
            contract_acceptance_decision_id=decision_id,
            expected_amount_cents=AMOUNT,
            expected_currency=CURRENCY,
            expected_provider=PROVIDER,
            environment=ENV,
        )

        bad(
            "Unauthenticated provider event rejected",
            "accepted unexpectedly",
        )

    except PaymentAuthorityError:
        ok("Unauthenticated provider event rejected")

    try:
        establish_payment_authorization(
            provider_event=provider_event(
                event_id="provider-event-001",
                authorization_id="provider-auth-CONFLICT",
            ),
            task_id=TASK,
            contract_acceptance_decision_id=decision_id,
            expected_amount_cents=AMOUNT,
            expected_currency=CURRENCY,
            expected_provider=PROVIDER,
            environment=ENV,
        )

        bad(
            "Conflicting replay rejected",
            "accepted unexpectedly",
        )

    except PaymentAuthorityError:
        ok("Conflicting replay rejected")

    with db.get_db() as conn:
        auth_count = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM payment_authorizations
            WHERE task_id = ?
            """,
            (TASK,),
        ).fetchone()["n"]

    if auth_count == 1:
        ok("Exactly one usable authorization exists")
    else:
        bad(
            "Exactly one usable authorization exists",
            f"found {auth_count}",
        )

finally:
    cleanup()


with db.get_db() as conn:
    counts = {}

    for table in (
        "work_deliveries",
        "external_acceptance_events",
        "contract_acceptance_decisions",
        "payment_authorization_events",
        "payment_authorization_verifications",
        "payment_authorizations",
    ):
        counts[table] = conn.execute(
            f"SELECT COUNT(*) AS n FROM {table}"
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

for table, count in counts.items():
    print(f"{table}: {count}")

print("integrity_check:", integrity)
print("foreign_key_errors:", len(fk_errors))

if failed:
    raise SystemExit(1)
