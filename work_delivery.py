import hashlib
from datetime import datetime, timezone
from urllib.parse import urlparse

import db
from service_principals import (UNSET, PRODUCE_WORK, authenticate, audit_authority,
    PrincipalAuthenticationError, reject_credential_material)


VALID_ENVIRONMENTS = {"TEST", "SANDBOX", "PRODUCTION"}


class WorkDeliveryError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def normalize_repository(repository: str) -> str:
    value = (repository or "").strip().lower()

    parts = value.split("/")

    if (
        len(parts) != 2
        or not parts[0]
        or not parts[1]
    ):
        raise WorkDeliveryError(
            "Repository must have owner/name form."
        )

    return value


def canonical_pr_url(
    repository: str,
    pull_request_number: int,
) -> str:
    return (
        f"https://github.com/"
        f"{repository}/pull/{pull_request_number}"
    )


def validate_pr_url(
    pr_url: str,
    repository: str,
    pull_request_number: int,
) -> str:
    value = (pr_url or "").strip()

    try:
        parsed = urlparse(value)
    except Exception as exc:
        raise WorkDeliveryError(
            "Pull request URL is invalid."
        ) from exc

    if parsed.scheme != "https":
        raise WorkDeliveryError(
            "Pull request URL must use HTTPS."
        )

    if parsed.hostname != "github.com":
        raise WorkDeliveryError(
            "Pull request URL must belong to github.com."
        )

    expected_path = (
        f"/{repository}/pull/{pull_request_number}"
    )

    if parsed.path.rstrip("/") != expected_path:
        raise WorkDeliveryError(
            "Pull request URL does not match "
            "repository and pull request number."
        )

    if parsed.query or parsed.fragment:
        raise WorkDeliveryError(
            "Pull request URL must not contain "
            "query parameters or fragments."
        )

    return canonical_pr_url(
        repository,
        pull_request_number,
    )


def generate_delivery_id(
    repository: str,
    pull_request_number: int,
    environment: str,
) -> str:
    raw = (
        f"{environment}:"
        f"{repository}:"
        f"{pull_request_number}"
    )

    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:24]

    return f"wd_{digest}"


def get_delivery(delivery_id: str):
    with db.get_db() as conn:
        return conn.execute(
            """
            SELECT *
            FROM work_deliveries
            WHERE delivery_id = ?
            """,
            (delivery_id,),
        ).fetchone()


def create_delivery(
    *,
    task_id: str,
    repository: str,
    pull_request_number: int,
    pull_request_url: str,
    environment: str,
    head_branch: str = None,
    base_branch: str = None,
    dispatched_commit_hash: str = None,
    producer_id=UNSET,
    producer_credential=None,
    dispatched_at: str = None,
):
    environment = (environment or "").strip().upper()
    principal = None
    if environment == "PRODUCTION":
        principal = authenticate(producer_credential, PRODUCE_WORK, operation="create_delivery")
        if producer_id is not UNSET:
            audit_authority(principal.principal_id, PRODUCE_WORK, "create_delivery", "rejected")
            raise PrincipalAuthenticationError("Caller producer identity is forbidden")
        producer_id = principal.principal_id
        try:
            reject_credential_material(dict(task_id=task_id, repository=repository,
                pull_request_number=pull_request_number, pull_request_url=pull_request_url,
                head_branch=head_branch, base_branch=base_branch,
                dispatched_commit_hash=dispatched_commit_hash, dispatched_at=dispatched_at))
            if not dispatched_commit_hash:
                raise WorkDeliveryError("Production dispatched commit is required")
        except Exception:
            audit_authority(principal.principal_id, PRODUCE_WORK, "create_delivery", "rejected")
            raise
    elif producer_credential is not None:
        raise PrincipalAuthenticationError("Credentials cannot enter compatibility operations")
    if producer_id is UNSET:
        producer_id = None
    try:
        task_id = (task_id or "").strip()

        if not task_id:
            raise WorkDeliveryError(
                "task_id is required."
            )

        for name, value in (("producer_id", producer_id), ("dispatched_commit_hash", dispatched_commit_hash)):
            if value is not None and (not isinstance(value, str) or not value or value != value.strip()):
                raise WorkDeliveryError(f"{name} must be a nonblank canonical string or None")

        repository = normalize_repository(repository)

        if (
            not isinstance(pull_request_number, int)
            or isinstance(pull_request_number, bool)
            or pull_request_number <= 0
        ):
            raise WorkDeliveryError(
                "pull_request_number must be "
                "a positive integer."
            )

        environment = (environment or "").strip().upper()

        if environment not in VALID_ENVIRONMENTS:
            raise WorkDeliveryError(
                "Invalid environment."
            )

        pull_request_url = validate_pr_url(
            pull_request_url,
            repository,
            pull_request_number,
        )

        delivery_id = generate_delivery_id(
            repository,
            pull_request_number,
            environment,
        )

        now = utc_now()
        dispatched_at = dispatched_at or now

        with db.get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT *
                FROM work_deliveries
                WHERE delivery_id = ?
                """,
                (delivery_id,),
            ).fetchone()

            if existing:
                if existing["task_id"] != task_id:
                    raise WorkDeliveryError(
                        "Pull request is already bound "
                        "to a different task."
                    )

                if existing["repository"] != repository:
                    raise WorkDeliveryError(
                        "Existing delivery repository mismatch."
                    )

                if (
                    existing["pull_request_number"]
                    != pull_request_number
                ):
                    raise WorkDeliveryError(
                        "Existing delivery PR number mismatch."
                    )

                if (
                    existing["pull_request_url"]
                    != pull_request_url
                ):
                    raise WorkDeliveryError(
                        "Existing delivery PR URL mismatch."
                    )

                if existing["producer_id"] != producer_id:
                    raise WorkDeliveryError("Existing delivery producer identity conflicts with replay")
                if existing["dispatched_commit_hash"] != dispatched_commit_hash:
                    raise WorkDeliveryError("Existing dispatched commit conflicts with replay")

                if principal:
                    audit_authority(principal.principal_id, PRODUCE_WORK, "create_delivery", "succeeded")
                return existing

            conflict = conn.execute(
                """
                SELECT *
                FROM work_deliveries
                WHERE repository = ?
                  AND pull_request_number = ?
                  AND environment = ?
                """,
                (
                    repository,
                    pull_request_number,
                    environment,
                ),
            ).fetchone()

            if conflict:
                raise WorkDeliveryError(
                    "Pull request is already bound "
                    "to another canonical delivery."
                )

            conn.execute(
                """
                INSERT INTO work_deliveries (
                    delivery_id,
                    task_id,
                    repository,
                    pull_request_number,
                    pull_request_url,
                    head_branch,
                    base_branch,
                    dispatched_commit_hash,
                    producer_id,
                    status,
                    environment,
                    dispatched_at,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    delivery_id,
                    task_id,
                    repository,
                    pull_request_number,
                    pull_request_url,
                    head_branch,
                    base_branch,
                    dispatched_commit_hash,
                    producer_id,
                    "DISPATCHED",
                    environment,
                    dispatched_at,
                    now,
                    now,
                ),
            )

            row = conn.execute(
                """
                SELECT *
                FROM work_deliveries
                WHERE delivery_id = ?
                """,
                (delivery_id,),
            ).fetchone()

            conn.commit()

            if principal:
                audit_authority(principal.principal_id, PRODUCE_WORK, "create_delivery", "succeeded")
            return row
    except Exception:
        if principal:
            audit_authority(principal.principal_id, PRODUCE_WORK, "create_delivery", "rejected")
        raise


def resolve_delivery_for_external_event(
    event: dict,
    *,
    environment: str,
):
    """
    Resolve an authenticated external GitHub event to the
    marketplace's previously recorded canonical work delivery.

    The external event does not supply task identity.
    """
    if not isinstance(event, dict):
        raise WorkDeliveryError(
            "External event must be a dictionary."
        )

    if event.get("authenticated") is not True:
        raise WorkDeliveryError(
            "External event is not authenticated."
        )

    if event.get("source") != "github":
        raise WorkDeliveryError(
            "External event source is not GitHub."
        )

    repository = normalize_repository(
        event.get("repository")
    )

    pr_number = event.get("pull_request_number")

    if (
        not isinstance(pr_number, int)
        or isinstance(pr_number, bool)
        or pr_number <= 0
    ):
        raise WorkDeliveryError(
            "External event PR number is invalid."
        )

    environment = (environment or "").strip().upper()

    if environment not in VALID_ENVIRONMENTS:
        raise WorkDeliveryError(
            "Invalid environment."
        )

    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM work_deliveries
            WHERE repository = ?
              AND pull_request_number = ?
              AND environment = ?
            """,
            (
                repository,
                pr_number,
                environment,
            ),
        ).fetchall()

    if not rows:
        raise WorkDeliveryError(
            "No canonical work delivery matches "
            "this external event."
        )

    if len(rows) != 1:
        raise WorkDeliveryError(
            "External event resolves to an "
            "ambiguous work delivery."
        )

    delivery = rows[0]

    event_url = event.get("pull_request_url")

    if event_url:
        normalized_event_url = validate_pr_url(
            event_url,
            repository,
            pr_number,
        )

        if normalized_event_url != delivery["pull_request_url"]:
            raise WorkDeliveryError(
                "External event PR URL does not match "
                "canonical work delivery."
            )

    if delivery["status"] != "DISPATCHED":
        raise WorkDeliveryError(
            "Canonical work delivery is not active."
        )

    return delivery
