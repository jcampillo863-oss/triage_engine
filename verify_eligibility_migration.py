"""Read-only comparison of migration 008 against its verified pre-migration backup."""
from contextlib import closing
from pathlib import Path
import sqlite3
import db

source=Path(db.DB_PATH).resolve()
backup=max((source.parent/'backups').glob('pre-eligibility-008-*.db'),key=lambda p:p.name)
with closing(sqlite3.connect(backup.as_uri()+'?mode=ro',uri=True)) as before, closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as after:
    tables=[r[0] for r in before.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT GLOB 'sqlite_*'")]
    for table in tables:
        if table=='schema_meta':
            old=list(before.execute('SELECT * FROM schema_meta ORDER BY version'))
            new=list(after.execute('SELECT * FROM schema_meta WHERE version<>8 ORDER BY version'))
            assert old==new
            continue
        columns=[r[1] for r in before.execute('PRAGMA table_info("'+table+'")')]
        quoted=','.join('"'+c+'"' for c in columns)
        sql='SELECT '+quoted+' FROM "'+table+'" ORDER BY rowid'
        assert list(before.execute(sql))==list(after.execute(sql)), 'Original rows changed in '+table
    assert after.execute('SELECT count(*) FROM settlement_eligibility_decisions').fetchone()[0]==0
    assert after.execute('SELECT count(*) FROM work_deliveries WHERE producer_id IS NOT NULL').fetchone()[0]==0
    assert after.execute('SELECT count(*) FROM canonical_settlements WHERE eligibility_decision_id IS NOT NULL').fetchone()[0]==0
    assert after.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert not after.execute('PRAGMA foreign_key_check').fetchall()
    print('All pre-existing table rows preserved: PASS')
    print('Historical producer/eligibility fabrication: NONE')
    print('Original database integrity and foreign keys: PASS')
    print('Historical Sandbox revenue-recorded settlements:',after.execute("SELECT count(*) FROM canonical_settlements WHERE environment='SANDBOX' AND state='REVENUE_RECORDED'").fetchone()[0])
    print('Verified pre-migration backup:',backup)
