import json
import sqlite3

from tg_signer.sign_record_store import SignRecordStore


def test_sign_record_store_upsert_and_list_groups(tmp_path):
    workdir = tmp_path / ".signer"
    store = SignRecordStore(workdir)

    store.upsert_record(
        "linuxdo",
        "123456",
        "2026-03-17",
        "2026-03-17T06:00:00+08:00",
        account="acct",
        status="success",
    )
    store.upsert_record(
        "linuxdo",
        "123456",
        "2026-03-18",
        "2026-03-18T06:00:00+08:00",
        account="acct",
        status="failed",
    )

    assert store.db_path.is_file()
    assert store.load_records("linuxdo", "123456") == {
        "2026-03-17": "2026-03-17T06:00:00+08:00",
        "2026-03-18": "2026-03-18T06:00:00+08:00",
    }

    groups = store.list_record_groups()
    assert len(groups) == 1
    assert groups[0].task_name == "linuxdo"
    assert groups[0].user_id == "123456"
    assert groups[0].records == [
        ("2026-03-18", "2026-03-18T06:00:00+08:00"),
        ("2026-03-17", "2026-03-17T06:00:00+08:00"),
    ]
    assert groups[0].statuses == {
        "2026-03-18": "failed",
        "2026-03-17": "success",
    }


def test_sign_record_store_runs_explicit_schema_migrations(tmp_path):
    workdir = tmp_path / ".signer"
    workdir.mkdir(parents=True, exist_ok=True)
    db_path = workdir / "data.sqlite3"

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version = 0")
        conn.commit()

    store = SignRecordStore(workdir)
    store.upsert_record(
        "linuxdo",
        "123456",
        "2026-03-17",
        "2026-03-17T06:00:00+08:00",
    )

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        count = conn.execute("SELECT COUNT(*) FROM sign_records").fetchone()[0]
        status = conn.execute("SELECT status FROM sign_records").fetchone()[0]

    assert version == store.SCHEMA_VERSION
    assert count == 1
    assert status == "unknown"


def test_sign_record_store_migrates_v1_database_to_status_column(tmp_path):
    workdir = tmp_path / ".signer"
    workdir.mkdir(parents=True, exist_ok=True)
    db_path = workdir / "data.sqlite3"

    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE sign_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_name TEXT NOT NULL,
                user_id TEXT NOT NULL,
                sign_date TEXT NOT NULL,
                signed_at TEXT NOT NULL,
                account TEXT,
                source TEXT NOT NULL DEFAULT 'runtime',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(task_name, user_id, sign_date)
            );
            PRAGMA user_version = 1;
            INSERT INTO sign_records (
                task_name, user_id, sign_date, signed_at, account, source
            ) VALUES ('linuxdo', '123456', '2026-03-17',
                      '2026-03-17T06:00:00+08:00', 'acct', 'runtime');
            """
        )
        conn.commit()

    store = SignRecordStore(workdir)
    store.upsert_record(
        "linuxdo",
        "123456",
        "2026-03-18",
        "2026-03-18T06:00:00+08:00",
        account="acct",
        status="success",
    )

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        rows = conn.execute(
            "SELECT sign_date, status FROM sign_records ORDER BY sign_date"
        ).fetchall()

    assert version == store.SCHEMA_VERSION
    assert rows == [
        ("2026-03-17", "unknown"),
        ("2026-03-18", "success"),
    ]


def test_sign_record_store_lists_recent_records_with_filters(tmp_path):
    workdir = tmp_path / ".signer"
    store = SignRecordStore(workdir)
    store.upsert_record(
        "linuxdo",
        "1001",
        "2026-03-17",
        "2026-03-17T06:00:00+08:00",
    )
    store.upsert_record(
        "linuxdo",
        "1001",
        "2026-03-18",
        "2026-03-18T06:00:00+08:00",
    )
    store.upsert_record(
        "v2ex",
        "2002",
        "2026-03-16",
        "2026-03-16T06:00:00+08:00",
    )

    recent = store.list_recent_records(limit=2)
    assert [(record.task_name, record.sign_date) for record in recent] == [
        ("linuxdo", "2026-03-18"),
        ("linuxdo", "2026-03-17"),
    ]

    filtered = store.list_recent_records(limit=10, task_name="v2ex", user_id="2002")
    assert [
        (record.task_name, record.user_id, record.sign_date) for record in filtered
    ] == [("v2ex", "2002", "2026-03-16")]


def test_sign_record_store_migrates_legacy_json_with_explicit_user_id(tmp_path):
    workdir = tmp_path / ".signer"
    record_file = workdir / "signs" / "linuxdo" / "sign_record.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    with open(record_file, "w", encoding="utf-8") as fp:
        json.dump(
            {
                "2026-03-17": "2026-03-17T06:00:00+08:00",
                "2026-03-18": "2026-03-18T06:00:00+08:00",
            },
            fp,
        )

    store = SignRecordStore(workdir)
    summary = store.migrate_all_json_records(legacy_user_id="123456")

    assert summary.migrated_files == 1
    assert summary.migrated_records == 2
    assert summary.skipped_files == []
    assert store.load_records("linuxdo", "123456") == {
        "2026-03-17": "2026-03-17T06:00:00+08:00",
        "2026-03-18": "2026-03-18T06:00:00+08:00",
    }


def test_sign_record_store_skips_ambiguous_legacy_json(tmp_path):
    workdir = tmp_path / ".signer"
    (workdir / "users" / "1001").mkdir(parents=True, exist_ok=True)
    (workdir / "users" / "1002").mkdir(parents=True, exist_ok=True)
    record_file = workdir / "signs" / "linuxdo" / "sign_record.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    with open(record_file, "w", encoding="utf-8") as fp:
        json.dump({"2026-03-17": "2026-03-17T06:00:00+08:00"}, fp)

    store = SignRecordStore(workdir)
    summary = store.migrate_all_json_records()

    assert summary.migrated_files == 0
    assert summary.migrated_records == 0
    assert summary.skipped_files == [record_file]
