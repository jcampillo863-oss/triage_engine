import db

with db.get_db() as conn:
    tables = [
        row["name"]
        for row in conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table'
            ORDER BY name
            """
        )
    ]

    for table in tables:
        columns = [
            row["name"]
            for row in conn.execute(
                f'PRAGMA table_info("{table}")'
            )
        ]

        interesting = [
            name for name in columns
            if any(
                token in name.lower()
                for token in (
                    "task",
                    "job",
                    "pr_",
                    "pull",
                    "repo",
                    "branch",
                    "dispatch",
                )
            )
        ]

        if interesting:
            print(f"{table}: {interesting}")
