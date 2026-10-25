"""Per-case deletion audit: subject -> linked resources -> state at audit_at -> policy -> ordered, cited steps.

Only evidence files with captured_at <= audit_at and activity rows with event_time <= audit_at are used or cited
(DECISIONS #2). Reported statuses and activity results are claims; state comes from observations (#4, #9).
"""
from __future__ import annotations

import collections
import re
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from contract import natural_key, step_sort_key  # noqa: E402

from .classify import classify
from .config import Options
from .data import Dataset, ts
from .evidence import IGNORED_PREFIXES, PREFIX_CATEGORY, PREFIX_SYSTEM, Assertion, interpret, prefix_of, snapshot_units, system_of

KIND_PRIORITY = ["EXPOSED", "ACTIVE", "RESTORABLE", "PRESENT", "CLEAN", "DISABLED", "BLOCKED", "ABSENT"]
VIOLATING = {"EXPOSED", "PRESENT", "ACTIVE", "RESTORABLE"}
SUPPORTS = {  # final kind -> evidence kinds that show it
    "ABSENT": {"ABSENT"}, "PRESENT": {"PRESENT", "EXPOSED"}, "EXPOSED": {"EXPOSED", "PRESENT"},
    "CLEAN": {"CLEAN"}, "ACTIVE": {"ACTIVE"}, "DISABLED": {"DISABLED"}, "RESTORABLE": {"RESTORABLE"}, "BLOCKED": {"BLOCKED"},
}
HINT_ACTIONS = {"DELETE_RECORD": "DELETE", "REDACT_RECORD": "REDACT", "ANONYMIZE_RECORD": "ANONYMIZE"}
RECREATE_RE = re.compile(r"repopulated downstream resource (\S+?)\.?$")
BATCH_GAP = {"policy": timedelta(minutes=45), "other": timedelta(minutes=105)}  # tie-break only
JOIN_BEFORE, JOIN_AFTER = timedelta(minutes=90), timedelta(minutes=10)
STATUS_RESULT = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}


def _file_no(fid: str) -> int:
    m = re.search(r"(\d+)$", fid)
    return int(m.group(1)) if m else 0


class Pipeline:
    def __init__(self, dataset_dir: Path, options: Options = Options(), context_requests: list[dict] | None = None):
        self.opt = options
        self.ds = Dataset(dataset_dir, image_fallback=options.image_fallback)
        self.actions = {r["data_category"]: r["required_action"] for r in self.ds.rules}
        ids = self.ds.identities
        self.by_email = {r["email"].lower(): uid for uid, r in ids.items()}
        self.hist_email = {a["old_value"].lower(): a["user_id"] for a in self.ds.audit_log if a["field_name"] == "email"}
        self.units = {fid: list(snapshot_units(doc)) for fid, doc in self.ds.docs.items()}
        self.recreated = collections.defaultdict(list)
        for a in self.ds.activity:
            m = RECREATE_RE.search(a["details"])
            if m and a["event_type"] in ("SYNC_RECORD", "RESTORE_BACKUP"):
                self.recreated[m.group(1)].append(a)
        self.joined_accounts: dict[str, tuple[str, str]] = {}
        if options.account_linking == "evidence_and_workflow_join" and context_requests:
            self.joined_accounts = self._assign_accounts(context_requests)

    # ------------------------------------------------------------------ identity and linking
    def subject(self, email: str):
        e = email.strip().lower()
        uid = self.by_email.get(e) or self.hist_email.get(e)
        if uid is None:
            return None, set()
        u = self.ds.identities[uid]
        tokens = {uid, u["email"].lower(), u["email_hash"]}
        for a in self.ds.audit_log:
            if a["user_id"] == uid and a["field_name"] in ("email", "email_hash"):
                tokens.add(a["old_value"].lower())
        return uid, tokens

    def linked_resources(self, tokens: set, audit_at: str) -> dict[str, set]:
        linked = collections.defaultdict(set)
        for t in tokens:
            for o in self.ds.by_token.get(t, []):
                rid = o.resource_id
                if rid and o.file_captured_at <= audit_at and prefix_of(rid) in PREFIX_SYSTEM:
                    linked[rid].add(o.file_id)
        for link in self.ds.links:
            if link["activity"]["event_time"] <= audit_at and (link["user_id"] in tokens or link["email"] in tokens):
                if prefix_of(link["resource_id"]) in PREFIX_SYSTEM:
                    linked[link["resource_id"]].add(link["activity"]["activity_id"])
        return linked

    def _assign_accounts(self, requests: list[dict]) -> dict[str, tuple[str, str]]:
        """Accounts that no evidence ties to the person, joined through the deletion workflow's own rows
        (docs/DECISIONS.md, "Account linking"). Returns {case_id: (account_id, "exact" | "tie_break")}.

        Exact key: the workflow's account DELETE_RECORD row falls in [reported_at - 90 min, reported_at + 10 min]
        and its reported_result is the case's reported_status (COMPLETE=SUCCESS, PARTIALLY_COMPLETE=PARTIAL,
        NOT_STARTED=FAILED; window alone if no row matches the status). Each row belongs to one request and accounts
        linked to anyone by evidence are excluded, so single candidates are fixed and removed from the other cases
        until nothing changes. Only a group that stays tied falls back to the timing gap, and only among its own
        exact-key candidates."""
        evidence_accounts, need, anchors = set(), {}, {}
        for q in requests:
            if not classify(q["request_text"])["is_deletion"]:
                continue
            uid, tokens = self.subject(q["email"])
            if not uid:
                continue
            linked = self.linked_resources(tokens, q["audit_at"])
            accts = {r for r in linked if prefix_of(r) == "account"}
            evidence_accounts |= accts
            if accts:
                continue
            need[q["case_id"]] = q
            rows = [a for r in linked if prefix_of(r) != "account" for a in self.ds.activity_by_resource.get(r, [])
                    if a["source_system"] == "deletion_worker" and q["requested_at"] <= a["event_time"] <= q["audit_at"]]
            if rows:
                first = min(rows, key=lambda a: a["event_time"])
                kind = "policy" if first["details"].startswith("Policy action completed") else "other"
                anchors[q["case_id"]] = (ts(first["event_time"]), BATCH_GAP[kind])
        pool = [a for a in self.ds.activity if a["source_system"] == "deletion_worker" and a["target_system"] == "account_service"
                and a["event_type"] == "DELETE_RECORD" and a["resource_id"] not in evidence_accounts]
        row_time = {a["resource_id"]: ts(a["event_time"]) for a in pool}

        cands = {}
        for cid, q in need.items():
            rep = ts(q["reported_at"])
            window = [a for a in pool if a["event_time"] <= q["audit_at"]
                      and -JOIN_BEFORE <= row_time[a["resource_id"]] - rep <= JOIN_AFTER]
            same = [a for a in window if a["reported_result"] == STATUS_RESULT.get(q["reported_status"])]
            cands[cid] = {a["resource_id"] for a in (same or window)}

        assigned: dict[str, tuple[str, str]] = {}

        def propagate(label: str):
            changed = True
            while changed:
                changed = False
                taken = {acct for acct, _ in assigned.values()}
                owners = collections.defaultdict(list)
                for cid, c in cands.items():
                    if cid in assigned:
                        continue
                    c -= taken
                    if len(c) == 1:
                        assigned[cid] = (next(iter(c)), label)
                        changed = True
                        break
                    for acct in c:
                        owners[acct].append(cid)
                else:
                    for acct, claimants in owners.items():
                        if len(claimants) == 1 and claimants[0] not in assigned:
                            assigned[claimants[0]] = (acct, label)
                            changed = True
                            break

        propagate("exact")
        # Tied groups: closest timing gap, restricted to each case's exact-key candidates; then propagate again.
        pairs = sorted((abs((anchor - row_time[acct]) - gap), cid, acct)
                       for cid, (anchor, gap) in anchors.items() if cid not in assigned for acct in cands[cid])
        taken = {acct for acct, _ in assigned.values()}
        for _, cid, acct in pairs:
            if cid not in assigned and acct not in taken:
                assigned[cid] = (acct, "tie_break")
                taken.add(acct)
        propagate("tie_break")  # anything that follows from a guess is itself a guess
        return assigned

    # ------------------------------------------------------------------ state reconstruction
    def assertions(self, rid: str, linked: dict, q: dict) -> list[Assertion]:
        audit_at, out = q["audit_at"], []
        for o in self.ds.by_resource.get(rid, []):
            if o.file_captured_at > audit_at:
                continue
            for r, kind, hint, ret in interpret(o):
                if r == rid and kind:
                    out.append(Assertion(rid, kind, o.observed_at, o.file_id, o.file_captured_at, hint=hint,
                                         confidence=o.confidence, retention_until=ret))
        # absence by omission from snapshots that list the store's contents
        system = system_of(rid)
        subject_files = {f for fs in linked.values() for f in fs}
        for fid, units in self.units.items():
            doc = self.ds.docs[fid]
            if doc["captured_at"] > audit_at:
                continue
            for unit_system, at, present, full in units:
                if unit_system != system or rid in present or at > audit_at:
                    continue
                if full or fid in subject_files:
                    out.append(Assertion(rid, "ABSENT", at, fid, doc["captured_at"]))
        # activity: checks are observations, policy results are claims, sync/restore recreate records
        flow_mode = self.opt.flow_precedence
        for a in self.ds.activity_by_resource.get(rid, []):
            if a["event_time"] > audit_at:
                continue
            et, ok = a["event_type"], a["reported_result"] == "SUCCESS"
            kind, claim = None, False
            if et == "CHECK_DATA_FLOW" and "remains enabled" in a["details"]:
                kind = "ACTIVE"
            elif et == "CHECK_RESTORE_ACCESS" and "can still be restored" in a["details"]:
                kind = "RESTORABLE"
            elif et == "RESTORE_BACKUP":
                kind = "RESTORABLE"
            elif et == "DISABLE_DATA_FLOW" and ok:
                kind, claim = "DISABLED", True
            elif et == "BLOCK_RESTORE" and ok:
                kind, claim = "BLOCKED", True
            if kind is None or flow_mode == "evidence_only" or (claim and flow_mode != "claims_as_observations"):
                continue
            out.append(Assertion(rid, kind, a["event_time"], None, None, activity_id=a["activity_id"],
                                 claim=claim and flow_mode != "claims_as_observations"))
        for a in self.recreated.get(rid, []):
            if a["event_time"] <= audit_at:
                out.append(Assertion(rid, "PRESENT", a["event_time"], None, None, activity_id=a["activity_id"]))
        if self.opt.stale_evidence == "drop_before_request":
            out = [a for a in out if a.at >= q["requested_at"]]
        return out

    def state(self, asserts: list[Assertion], requested_at: str) -> str | None:
        obs = [a for a in asserts if not a.claim]
        if not obs:
            return None
        groups = collections.defaultdict(list)
        for a in obs:
            groups[(a.file_id or a.activity_id, a.at)].append(a)
        merged = []
        for (_src, at), items in groups.items():
            kind = min((i.kind for i in items), key=KIND_PRIORITY.index)
            merged.append((at, items[0].file_at or at, kind))
        mode = self.opt.latest_state
        if mode == "present_since_request":
            hits = [m for m in merged if m[2] in VIOLATING and m[0] >= requested_at]
            if hits:
                return max(hits)[2]
            mode = "latest_observation"
        key = (lambda m: (m[0], m[1])) if mode == "latest_observation" else (lambda m: (m[1], m[0]))
        latest = max(key(m) for m in merged)
        tied = [m[2] for m in merged if key(m) == latest]
        return min(tied, key=KIND_PRIORITY.index)

    # ------------------------------------------------------------------ policy
    def category(self, rid: str, asserts: list[Assertion]) -> str:
        hints = {a.hint for a in asserts if a.hint}
        pre = prefix_of(rid)
        if pre == "support":
            for h in ("SUPPORT_ATTACHMENT", "SECONDARY_IDENTIFIER", "SUPPORT_MESSAGE"):
                if h in hints:
                    return h
        return PREFIX_CATEGORY[pre]

    def action(self, rid: str, category: str, asserts: list[Assertion], audit_at: str) -> str:
        if category in ("DATA_FLOW", "BACKUP_ARCHIVE"):
            return category
        action = self.actions.get(category, "DELETE")
        if action == "RETAIN":
            until = max((a.retention_until for a in asserts if a.retention_until), default=None)
            if until and until < audit_at:  # DECISIONS #8: retention expired -> ordinary deletion
                return "DELETE"
            return "RETAIN"
        if self.opt.treatment == "policy_with_activity_hint" and category not in ("USER_PROFILE", "ACCOUNT_TOMBSTONE"):
            hinted = [HINT_ACTIONS[a["event_type"]] for a in self.ds.activity_by_resource.get(rid, [])
                      if a["source_system"] == "deletion_worker" and a["event_type"] in HINT_ACTIONS and a["event_time"] <= audit_at]
            if hinted:
                return hinted[-1]
        return action

    @staticmethod
    def plan(rid: str, action: str, kind: str | None) -> tuple[list, list, list]:
        """(findings, remediations, verifications) as (operation, state) pairs for one resource."""
        if action == "DATA_FLOW":
            if kind == "ACTIVE":
                return [("REPORT_ACTIVE_DATA_FLOW", "ACTIVE")], [("DISABLE_DATA_FLOW", "DISABLED")], [("VERIFY_DATA_FLOW_DISABLED", "DISABLED")]
            return [], [], [("VERIFY_DATA_FLOW_DISABLED", "DISABLED")] if kind == "DISABLED" else []
        if action == "BACKUP_ARCHIVE":
            if kind == "RESTORABLE":
                return [("REPORT_RESTORE_RISK", "ACTIVE")], [("BLOCK_RESTORE", "BLOCKED")], [("VERIFY_RESTORE_BLOCKED", "BLOCKED")]
            return [], [], [("VERIFY_RESTORE_BLOCKED", "BLOCKED")] if kind == "BLOCKED" else []
        if action == "RETAIN":
            return [], [], [("VERIFY_RECORD_PRESENT", "PRESENT")] if kind in ("PRESENT", "EXPOSED", "CLEAN") else []
        if kind is None:
            return [], [], []
        if kind == "ABSENT":
            return [], [], [("VERIFY_RECORD_ABSENT", "ABSENT")]
        if action == "DELETE":
            return [("REPORT_REMAINING_RECORD", "PRESENT")], [("DELETE_RECORD", "ABSENT")], [("VERIFY_RECORD_ABSENT", "ABSENT")]
        verify = {"REDACT": ("VERIFY_RECORD_REDACTED", "REDACTED"), "ANONYMIZE": ("VERIFY_RECORD_ANONYMIZED", "ANONYMIZED")}[action]
        if kind == "CLEAN":
            return [], [], [verify]
        fix = {"REDACT": ("REDACT_RECORD", "REDACTED"), "ANONYMIZE": ("ANONYMIZE_RECORD", "ANONYMIZED")}[action]
        return [("REPORT_EXPOSED_PII", "PRESENT")], [fix], [verify]

    # ------------------------------------------------------------------ citations
    def supporting(self, kind: str, asserts: list[Assertion]) -> tuple[dict[str, str], list[str]]:
        files, acts = {}, []
        for a in asserts:
            if a.claim or a.kind not in SUPPORTS.get(kind, set()):
                continue
            if a.file_id:
                files[a.file_id] = max(files.get(a.file_id, ""), a.file_at or "")
            elif a.activity_id:
                acts.append(a.activity_id)
        return files, acts

    def cite(self, needs: dict[str, tuple[dict, list]]) -> dict[str, str]:
        coverage = collections.Counter(f for files, _ in needs.values() for f in files)

        def latest(files):
            return max(files, key=lambda f: (files[f], coverage[f], -_file_no(f)))

        def covered(files):
            return max(files, key=lambda f: (coverage[f], files[f], -_file_no(f)))

        chosen = {}
        dominant = None
        if self.opt.citation == "case_dominant" and coverage:
            caps = {}
            for files, _ in needs.values():
                caps.update(files)
            dominant = max(coverage, key=lambda f: (coverage[f], caps[f], -_file_no(f)))
        for key, (files, acts) in needs.items():
            if files:
                if dominant and dominant in files:
                    chosen[key] = dominant
                elif self.opt.citation == "per_resource_coverage":
                    chosen[key] = covered(files)
                else:
                    chosen[key] = latest(files)
            elif acts:
                chosen[key] = acts[-1]
        return chosen

    # ------------------------------------------------------------------ one case
    def assess(self, q: dict) -> tuple[str, list[dict], dict]:
        diag = {"case_id": q["case_id"], "low_confidence": []}
        cls = classify(q["request_text"])
        diag["classification"] = cls
        if cls["confidence"] == "low":
            diag["low_confidence"].append("classification:rules")
        uid, tokens = self.subject(q["email"])
        diag["subject"] = uid
        if not cls["is_deletion"]:
            return "NOT_APPLICABLE", [], diag
        if uid is None:
            diag["error"] = "requester email not found in identities or audit log"
            return "COMPLETE", [], diag

        linked = self.linked_resources(tokens, q["audit_at"])
        accounts = [r for r in linked if prefix_of(r) == "account"]
        account_source = "evidence"
        if not accounts and q["case_id"] in self.joined_accounts:
            acct, how = self.joined_accounts[q["case_id"]]
            accounts, account_source = [acct], f"workflow_join:{how}"
            linked[acct] = set()
        resources = {}
        for rid in sorted(linked):
            if prefix_of(rid) in IGNORED_PREFIXES:
                continue
            resources[rid] = self.assertions(rid, linked, q)
        if len(accounts) > 1:  # keep the account observed most recently (sample case_17)
            def last_seen(r):
                return max((a.at for a in resources[r]), default="")
            keep = max(accounts, key=last_seen)
            for r in accounts:
                if r != keep:
                    resources.pop(r, None)
            accounts = [keep]
        diag["account"] = {"ids": accounts, "source": account_source if accounts else None}
        if account_source == "workflow_join:tie_break" and accounts:
            diag["low_confidence"].append(f"account:{accounts[0]}:tie_break")

        planned, needs = [], {}
        for rid, asserts in resources.items():
            category = self.category(rid, asserts)
            kind = self.state(asserts, q["requested_at"])
            if prefix_of(rid) == "account" and kind not in ("PRESENT", "EXPOSED"):
                kind = kind or "ABSENT"  # DECISIONS #7: the account is always verified absent
            action = "DELETE" if prefix_of(rid) == "account" else self.action(rid, category, asserts, q["audit_at"])
            f, r, v = self.plan(rid, action, kind)
            system = PREFIX_SYSTEM[prefix_of(rid)]
            for phase, ops in (("FINDING", f), ("REMEDIATION", r), ("VERIFICATION", v)):
                for op, st in ops:
                    planned.append({"phase": phase, "system_id": system, "resource_id": rid, "operation": op, "state": st})
            for level in ("low", "medium"):
                if any(a.confidence == level for a in asserts):
                    diag["low_confidence"].append(f"state:{rid}:screenshot_{level}")
                    break
            needs[rid] = self.supporting(kind, asserts) if kind else ({}, [])
            diag.setdefault("resources", {})[rid] = {"category": category, "action": action, "state": kind,
                                                     "evidence": sorted(needs[rid][0])}

        incomplete = any(p["phase"] == "FINDING" for p in planned)
        status = "INCOMPLETE" if incomplete else "COMPLETE"
        cited_phase = "FINDING" if incomplete else "VERIFICATION"
        cite_for = {p["resource_id"] for p in planned if p["phase"] == cited_phase}
        chosen = self.cite({rid: needs[rid] for rid in cite_for})
        fallback = None
        for p in planned:
            if p["phase"] == cited_phase:
                ev = chosen.get(p["resource_id"])
                if ev is None:  # no visible proof of this state: cite the case's best file, flagged
                    fallback = fallback or self._fallback_citation(chosen, linked, q["audit_at"])
                    ev = fallback or ""
                    diag["low_confidence"].append(f"citation:{p['resource_id']}:fallback")
                p["evidence_id"] = ev
            else:
                p["evidence_id"] = ""
        if not incomplete:
            planned = [p for p in planned if p["phase"] == "VERIFICATION"]

        if self.opt.resource_order == "numeric":
            planned.sort(key=lambda s: (*step_sort_key(s)[:3], natural_key(s["resource_id"])))
        else:
            planned.sort(key=step_sort_key)
        steps = [{"step_no": i, **p} for i, p in enumerate(planned, 1)]
        if not steps:
            diag["warning"] = "deletion request but no linked resources found"
        return status, steps, diag

    def _fallback_citation(self, chosen: dict, linked: dict, audit_at: str) -> str | None:
        if chosen:
            return collections.Counter(chosen.values()).most_common(1)[0][0]
        files = {f for fs in linked.values() for f in fs if f in self.ds.docs and self.ds.docs[f]["captured_at"] <= audit_at}
        return max(files, key=lambda f: (self.ds.docs[f]["captured_at"], -_file_no(f))) if files else None
