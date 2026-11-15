import sqlite3

import pytest

from erasure_audit.extraction.common import scan_identifiers
from erasure_audit.extraction.log_parser import parse_line
from erasure_audit.extraction.sql_parser import parse_sql
from paths import DATASET


def test_sql_dump_cannot_attach_other_databases(tmp_path):
    dump = tmp_path / "evil.sql"
    secret = tmp_path / "secret.db"
    dump.write_text(f"ATTACH DATABASE '{secret}' AS s;\nCREATE TABLE t(resource_id TEXT);\n", encoding="utf-8")
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        parse_sql(dump, "evidence_x")  # the runner records this as status=error for the file
    assert not secret.exists()


def test_every_real_sql_snapshot_parses():
    for path in sorted((DATASET / "evidence" / "databases").glob("*.sql")):
        body = parse_sql(path, path.stem)
        assert body["status"] == "ok", (path.name, body["warnings"])
        assert body["meta"]["tables"], path.name


def test_log_lines_parse_across_files():
    seen = 0
    for path in sorted((DATASET / "evidence" / "logs").glob("*.log")):
        first = next(line for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        parsed = parse_line(first)
        assert parsed is not None, path.name
        assert parsed["ts"] and parsed["event"], path.name
        seen += 1
    assert seen >= 20


def test_scan_identifiers_skips_integrity_seals():
    h = "ab" * 32
    ids = scan_identifiers({"a": f"Mail.Box@Example.com user_12 {h}", "seal": "sha256:" + "0" * 64})
    assert ids["emails"] == ["mail.box@example.com"]
    assert ids["user_ids"] == ["user_12"]
    assert ids["hashes"] == [h]
