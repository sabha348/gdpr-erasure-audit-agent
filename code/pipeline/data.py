"""Loads every participant-facing input once and indexes it for per-case queries.

Observations come from the extraction cache (extraction/). Two additions happen here:
  * LINK_RESOURCE_IDENTITY activity rows ("Data catalog linked X (alias) to email and user_N") become identity
    links for X. An alias that is not a user id (e.g. statement NST-2026-0718-1842) attaches X to any observation
    whose text contains it.
  * Screenshots that have no model transcription yet get a rule-based fallback: the screen type is read from the
    file name, the subject and resource from the catalog LINK row written just after the capture. These observations
    are marked confidence=low.
"""
from __future__ import annotations

import collections
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE_DIR))
from extraction import load_evidence  # noqa: E402
from extraction.common import scan_identifiers  # noqa: E402

LINK_RE = re.compile(r"linked (\S+) \(([^)]*)\) to (\S+) and (user_\d+)|confirmed (\S+) \(([^)]*)\) belongs only to (\S+) and (user_\d+)")

# Screen type (file-name keyword) -> (assumed state, category hint). Used only without a transcription.
SCREEN_RULES = [
    ("account_admin", "PRESENT", "USER_PROFILE"),
    ("search_index", "PRESENT", "SEARCH_INDEX_ENTRY"),
    ("connector", "ACTIVE", "DATA_FLOW"),
    ("analytics_profile", "EXPOSED", "ANALYTICS_PROFILE"),
    ("export_console", "PRESENT", "USER_EXPORT"),
    ("support_ticket", "EXPOSED", "SUPPORT_MESSAGE"),
    ("backup_restore", "RESTORABLE", "BACKUP_ARCHIVE"),
    ("marketing_profile", "PRESENT", "MARKETING_PROFILE"),
    ("object_storage", "PRESENT", "SUPPORT_ATTACHMENT"),
    ("deleted_account_reference", "CLEAN", "ACCOUNT_TOMBSTONE"),
    ("security_audit", "PRESENT", "SECURITY_AUDIT_LOG"),
]
LINK_WINDOW = timedelta(hours=1)


def ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@dataclass
class Obs:
    """One observation plus the file it came from."""
    file_id: str
    file_captured_at: str
    raw: dict
    resource_id: str | None
    tokens: set = field(default_factory=set)  # lower-cased emails, user ids, hashes
    confidence: str = "high"

    @property
    def observed_at(self) -> str:
        return self.raw.get("observed_at") or self.file_captured_at


class Dataset:
    def __init__(self, dataset_dir: Path, image_fallback: str = "rules"):
        self.dir = dataset_dir
        self.identities = {r["user_id"]: r for r in read_csv(dataset_dir / "user_identities.csv")}
        self.audit_log = read_csv(dataset_dir / "user_audit_log.csv")
        self.rules = read_csv(dataset_dir / "deletion_rules.csv")
        self.manifest = {r["file_id"]: r for r in read_csv(dataset_dir / "evidence_files.csv")}
        self.activity = sorted(read_csv(dataset_dir / "system_activity.csv"), key=lambda a: (a["event_time"], a["activity_id"]))
        self.activity_by_resource = collections.defaultdict(list)
        for a in self.activity:
            self.activity_by_resource[a["resource_id"]].append(a)
        self.docs = load_evidence(dataset_dir, allow_llm=False)
        self.links = self._catalog_links()
        self.observations: list[Obs] = []
        for doc in self.docs.values():
            for o in doc["observations"]:
                self._add(doc, o)
            if doc["format"] == "pdf" and doc["observations"]:
                self._pdf_document(doc)
        if image_fallback == "rules":
            self._image_fallback()
        self._attach_aliases()
        self.by_resource = collections.defaultdict(list)
        self.by_token = collections.defaultdict(list)
        for o in self.observations:
            if o.resource_id:
                self.by_resource[o.resource_id].append(o)
            for t in o.tokens:
                self.by_token[t].append(o)

    # ---------------------------------------------------------------- observations
    def _add(self, doc: dict, o: dict, confidence: str = "high") -> Obs:
        ids = o["identifiers"]
        tokens = {e.lower() for e in ids["emails"]} | set(ids["user_ids"]) | set(ids["hashes"])
        obs = Obs(doc["file_id"], doc["captured_at"], o, o.get("resource_id"), tokens, confidence)
        self.observations.append(obs)
        return obs

    def _pdf_document(self, doc: dict) -> None:
        """A PDF is one business artifact: identifiers on different lines (reference, name, email) belong together."""
        text = "\n".join(o["fields"]["text"] for o in doc["observations"] if o["source"] == "pdf_line")
        fields_ = {"text": text, "origin": doc["origin"]}
        ids = scan_identifiers(fields_)
        resource = ids["resource_ids"][0] if len(ids["resource_ids"]) == 1 else None
        raw = {"obs_id": f"{doc['file_id']}#document", "source": "pdf_document", "observed_at": doc["captured_at"],
               "system": doc["origin"], "collection": None, "copy": None, "resource_id": resource, "event": None,
               "fields": fields_, "identifiers": ids, "location": {"document": True}}
        self._add(doc, raw)

    def _catalog_links(self) -> list[dict]:
        links = []
        for a in self.activity:
            if a["event_type"] != "LINK_RESOURCE_IDENTITY":
                continue
            m = LINK_RE.search(a["details"])
            if not m:
                continue
            g = [x for x in m.groups() if x is not None]
            resource, alias, email, user = g
            links.append({"activity": a, "resource_id": resource, "alias": alias, "email": email.lower().rstrip("."),
                          "user_id": user, "system": a["target_system"],
                          "exclusive": "belongs only to" in a["details"]})
        return links

    def _image_fallback(self) -> None:
        """One state observation per screenshot: screen type (file name) + the catalog LINK row written just after
        the capture. With a model transcription that shows the linked resource id or subject on screen, the
        observation is confirmed (confidence=medium); without one it is an assumption (confidence=low).
        Transcribed rows and panels add identifiers for linking but carry no state of their own."""
        for doc in self.docs.values():
            if doc["format"] != "image":
                continue
            stem = Path(doc["file_path"]).stem
            rule = next((r for r in SCREEN_RULES if r[0] in stem), None)
            cap = ts(doc["captured_at"])
            transcript = json.dumps(doc["meta"].get("transcription") or {}, ensure_ascii=False).lower()
            for link in self.links:
                t = ts(link["activity"]["event_time"])
                if not (cap <= t <= cap + LINK_WINDOW):
                    continue
                seen = bool(transcript) and (link["resource_id"].lower() in transcript or link["email"] in transcript
                                             or link["user_id"] in transcript)
                confidence = "medium" if seen else "low"
                fields_ = {"screen": stem, "assumed_state": rule[1] if rule else None,
                           "category_hint": rule[2] if rule else None, "confidence": confidence,
                           "transcription_confirms_link": seen,
                           "basis": f"file name + {link['activity']['activity_id']}",
                           "email": link["email"], "user_id": link["user_id"]}
                raw = {"obs_id": f"{doc['file_id']}#screen", "source": "image_fallback",
                       "observed_at": doc["captured_at"], "system": link["system"], "collection": fields_["category_hint"],
                       "copy": None, "resource_id": link["resource_id"], "event": None, "fields": fields_,
                       "identifiers": scan_identifiers(fields_), "location": {"fallback": True}}
                self._add(doc, raw, confidence=confidence)

    def _attach_aliases(self) -> None:
        aliases = {l["alias"]: l["resource_id"] for l in self.links
                   if l["alias"] != l["resource_id"] and not l["alias"].startswith("user_")}
        if not aliases:
            return
        for o in self.observations:
            if o.resource_id:
                continue
            text = json.dumps(o.raw["fields"], ensure_ascii=False)
            for alias, rid in aliases.items():
                if alias in text:
                    o.resource_id = rid
                    break

    # ---------------------------------------------------------------- helpers
    def visible_docs(self, audit_at: str) -> list[str]:
        return [fid for fid, d in self.docs.items() if d["captured_at"] <= audit_at]

    def activity_until(self, audit_at: str):
        return [a for a in self.activity if a["event_time"] <= audit_at]
