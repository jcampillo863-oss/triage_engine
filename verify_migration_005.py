import db

with db.get_db() as conn:
    migration = conn.execute(
        """
        SELECT version, migration_name, applied_at
        FROM schema_meta
        WHERE version = 5
        """
    ).fetchone()

    print(
        "MIGRATION:",
        dict(migration) if migration else None,
    )

    columns = {
        row["name"]: row
        for row in conn.execute(
            """
            PRAGMA table_info(
                "external_acceptance_events"
            )
            """
        ).fetchall()
    }

    print(
        "work_delivery_id:",
        "PRESENT"
        if "work_delivery_id" in columns
        else "MISSING",
    )

    print(
        "pull_request_number:",
        "PRESENT"
        if "pull_request_number" in columns
        else "MISSING",
    )

    event_count = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM external_acceptance_events
        """
    ).fetchone()["n"]

    decision_count = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM contract_acceptance_decisions
        """
    ).fetchone()["n"]

    print(
        "EXTERNAL ACCEPTANCE EVENTS:",
        event_count,
    )

    print(
        "CONTRACT ACCEPTANCE DECISIONS:",
        decision_count,
    )

    print(
        "integrity_check:",
        conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0],
    )

    fk_errors = conn.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    print(
        "foreign_key_errors:",
        len(fk_errors),
    )
