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

ENV = "TEST"
REPO = "example-owner/example-repo"
PROVIDER = "synthetic-provider"
AMOUNT = 5000
CURRENCY = "USD"

TASK_A = "__test_payinv_a__"
TASK_B = "__test_payinv_b__"

PR_A = 940063
PR_B = 940064


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
        tasks = (TASK_A, TASK_B)

        auth_ids = [
            row["authorization_id"]
            for row in conn.execute(
                """
                SELECT authorization_id
                FROM payment_authorizations
                WHERE task_id IN (?, ?)
                """,
                tasks,
            ).fetchall()
        ]

        if auth_ids:
            marks = ",".join("?" for _ in auth_ids)

            conn.execute(
                f"""
                DELETE FROM canonical_settlements
                WHERE payment_authorization_id
                    IN ({marks})
                """,
                auth_ids,
            )

        conn.execute(
            """
            DELETE FROM payment_authorizations
            WHERE task_id IN (?, ?)
            """,
            tasks,
        )

        auth_event_ids = [
            row["authorization_event_id"]
            for row in conn.execute(
                """
                SELECT authorization_event_id
                FROM payment_authorization_verifications
                WHERE task_id IN (?, ?)
                """,
                tasks,
            ).fetchall()
        ]

        conn.execute(
            """
            DELETE FROM payment_authorization_verifications
            WHERE task_id IN (?, ?)
            """,
            tasks,
        )

        if auth_event_ids:
            marks = ",".join(
                "?" for _ in auth_event_ids
            )

            conn.execute(
                f"""
                DELETE FROM payment_authorization_events
                WHERE event_id IN ({marks})
                """,
                auth_event_ids,
            )

        acceptance_event_ids = [
            row["event_id"]
            for row in conn.execute(
                """
                SELECT event_id
                FROM external_acceptance_events
                WHERE task_id IN (?, ?)
                """,
                tasks,
            ).fetchall()
        ]

        conn.execute(
            """
            DELETE FROM contract_acceptance_decisions
            WHERE task_id IN (?, ?)
            """,
            tasks,
        )

        if acceptance_event_ids:
            marks = ",".join(
                "?" for _ in acceptance_event_ids
            )

            conn.execute(
                f"""
                DELETE FROM external_acceptance_events
                WHERE event_id IN ({marks})
                """,
                acceptance_event_ids,
            )

        conn.execute(
            """
            DELETE FROM work_deliveries
            WHERE task_id IN (?, ?)
            """,
            tasks,
        )

        conn.commit()


def make_acceptance(task, pr, delivery_id):
    create_delivery(
        task_id=task,
        repository=REPO,
        pull_request_number=pr,
        pull_request_url=(
            f"https://github.com/{REPO}/pull/{pr}"
        ),
        environment=ENV,
    )

    event = normalize_pull_request_event(
        {
            "action": "closed",
            "number": pr,
            "repository": {
                "full_name": REPO,
            },
            "pull_request": {
                "number": pr,
                "html_url": (
                    f"https://github.com/{REPO}/pull/{pr}"
                ),
                "merged": True,
                "merge_commit_sha": f"sha-{pr}",
            },
        },
        delivery_id=delivery_id,
        expected_repository=REPO,
    )

    result = process_github_acceptance_event(
        event,
        environment=ENV,
    )

    return result["decision"]["decision_id"]


def provider_event(
    event_id,
    authorization_id,
    *,
    provider=PROVIDER,
    amount=AMOUNT,
    currency=CURRENCY,
):
    return {
        "provider": provider,
        "provider_event_id": event_id,
        "provider_authorization_id": authorization_id,
        "event_type": "authorization.established",
        "provider_status": "AUTHORIZED",
        "amount_cents": amount,
        "currency": currency,
        "authenticated": True,
    }


cleanup()

try:
    decision_a = make_acceptance(
        TASK_A,
        PR_A,
        "synthetic-payinv-gh-a",
    )

    decision_b = make_acceptance(
        TASK_B,
        PR_B,
        "synthetic-payinv-gh-b",
    )

    # -------------------------------------------------
    # 1. Currency mismatch
    # -------------------------------------------------

    result = establish_payment_authorization(
        provider_event=provider_event(
            "payinv-event-currency",
            "payinv-auth-currency",
            currency="EUR",
        ),
        task_id=TASK_A,
        contract_acceptance_decision_id=decision_a,
        expected_amount_cents=AMOUNT,
        expected_currency=CURRENCY,
        expected_provider=PROVIDER,
        environment=ENV,
    )

    if (
        result["verification"]["verified"] == 0
        and result["authorization"] is None
    ):
        ok("Currency mismatch cannot authorize")
    else:
        bad(
            "Currency mismatch cannot authorize",
            "authorization created",
        )

    # -------------------------------------------------
    # 2. Provider mismatch
    # -------------------------------------------------

    result = establish_payment_authorization(
        provider_event=provider_event(
            "payinv-event-provider",
            "payinv-auth-provider",
            provider="wrong-provider",
        ),
        task_id=TASK_A,
        contract_acceptance_decision_id=decision_a,
        expected_amount_cents=AMOUNT,
        expected_currency=CURRENCY,
        expected_provider=PROVIDER,
        environment=ENV,
    )

    if (
        result["verification"]["verified"] == 0
        and result["authorization"] is None
    ):
        ok("Provider mismatch cannot authorize")
    else:
        bad(
            "Provider mismatch cannot authorize",
            "authorization created",
        )

    # -------------------------------------------------
    # 3. Wrong task for acceptance decision
    # -------------------------------------------------

    try:
        establish_payment_authorization(
            provider_event=provider_event(
                "payinv-event-task",
                "payinv-auth-task",
            ),
            task_id=TASK_B,
            contract_acceptance_decision_id=decision_a,
            expected_amount_cents=AMOUNT,
            expected_currency=CURRENCY,
            expected_provider=PROVIDER,
            environment=ENV,
        )

        bad(
            "Wrong task/acceptance binding rejected",
            "accepted unexpectedly",
        )

    except PaymentAuthorityError:
        ok("Wrong task/acceptance binding rejected")

    # -------------------------------------------------
    # 4. Environment mismatch
    # -------------------------------------------------

    try:
        establish_payment_authorization(
            provider_event=provider_event(
                "payinv-event-env",
                "payinv-auth-env",
            ),
            task_id=TASK_A,
            contract_acceptance_decision_id=decision_a,
            expected_amount_cents=AMOUNT,
            expected_currency=CURRENCY,
            expected_provider=PROVIDER,
            environment="PRODUCTION",
        )

        bad(
            "Environment mismatch rejected",
            "accepted unexpectedly",
        )

    except PaymentAuthorityError:
        ok("Environment mismatch rejected")

    # -------------------------------------------------
    # 5. Unknown acceptance decision
    # -------------------------------------------------

    try:
        establish_payment_authorization(
            provider_event=provider_event(
                "payinv-event-unknown",
                "payinv-auth-unknown",
            ),
            task_id=TASK_A,
            contract_acceptance_decision_id=(
                "cad_does_not_exist"
            ),
            expected_amount_cents=AMOUNT,
            expected_currency=CURRENCY,
            expected_provider=PROVIDER,
            environment=ENV,
        )

        bad(
            "Unknown acceptance decision rejected",
            "accepted unexpectedly",
        )

    except PaymentAuthorityError:
        ok("Unknown acceptance decision rejected")

    # -------------------------------------------------
    # 6. Establish one legitimate authorization
    # -------------------------------------------------

    legitimate = establish_payment_authorization(
        provider_event=provider_event(
            "payinv-event-legitimate",
            "payinv-auth-shared",
        ),
        task_id=TASK_A,
        contract_acceptance_decision_id=decision_a,
        expected_amount_cents=AMOUNT,
        expected_currency=CURRENCY,
        expected_provider=PROVIDER,
        environment=ENV,
    )

    if (
        legitimate["authorization"] is not None
        and
        legitimate["authorization"]["verified"] == 1
    ):
        ok("Legitimate authorization established")
    else:
        bad(
            "Legitimate authorization established",
            "authorization missing",
        )

    # -------------------------------------------------
    # 7. Same provider authorization cannot bind
    #    to another marketplace obligation.
    # -------------------------------------------------

    try:
        establish_payment_authorization(
            provider_event=provider_event(
                "payinv-event-reuse",
                "payinv-auth-shared",
            ),
            task_id=TASK_B,
            contract_acceptance_decision_id=decision_b,
            expected_amount_cents=AMOUNT,
            expected_currency=CURRENCY,
            expected_provider=PROVIDER,
            environment=ENV,
        )

        bad(
            "Provider authorization reuse rejected",
            "rebound to another task",
        )

    except PaymentAuthorityError:
        ok("Provider authorization reuse rejected")

    # -------------------------------------------------
    # 8. Failed verification never creates usable auth
    # -------------------------------------------------

    with db.get_db() as conn:
        bad_auth_count = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM payment_authorizations
            WHERE provider_authorization_id IN (
                'payinv-auth-currency',
                'payinv-auth-provider'
            )
            """
        ).fetchone()["n"]

    if bad_auth_count == 0:
        ok("Rejected verification creates no usable auth")
    else:
        bad(
            "Rejected verification creates no usable auth",
            f"found {bad_auth_count}",
        )

finally:
    cleanup()


with db.get_db() as conn:
    synthetic_auths = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM payment_authorizations
        WHERE task_id IN (?, ?)
        """,
        (TASK_A, TASK_B),
    ).fetchone()["n"]

    synthetic_verifications = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM payment_authorization_verifications
        WHERE task_id IN (?, ?)
        """,
        (TASK_A, TASK_B),
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
    "PERSISTED SYNTHETIC AUTHS:",
    synthetic_auths,
)
print(
    "PERSISTED SYNTHETIC VERIFICATIONS:",
    synthetic_verifications,
)
print("integrity_check:", integrity)
print("foreign_key_errors:", len(fk_errors))

if failed:
    raise SystemExit(1)
