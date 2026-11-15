"""Scoring of predicted verdicts and step plans against labelled cases. Pure functions, no I/O.

Per case:
  * verdict: predicted deletion_status equals the label;
  * steps: multiset match on (phase, system_id, resource_id, operation, state, evidence_id), so precision /
    recall / F1 are computed overall and per phase (REMEDIATION is "remediation-step correctness");
  * fields: every gold step is aligned to the best predicted step on the same system_id + resource_id, then each
    field is compared (unaligned gold steps count as wrong for every field);
  * exact: status and the full ordered step list are identical, step_no running 1..n;
  * plan validity (label-free): flows are disabled and restores blocked before records are changed, and every
    remediated resource has the verification its remediation requires.
Aggregates are micro-averaged over steps and macro-averaged over cases where noted.
"""
from __future__ import annotations

import collections
from collections.abc import Iterable

from ..contract import PHASES, REMEDIATION_TO_VERIFICATION

FIELDS = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")
FLOW_OPS = {"DISABLE_DATA_FLOW", "BLOCK_RESTORE"}
RECORD_OPS = {"DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"}


def step_tuple(s: dict, fields: tuple[str, ...] = FIELDS) -> tuple:
    return tuple(s.get(f) for f in fields)


def multiset_tp(gold: Iterable[tuple], pred: Iterable[tuple]) -> int:
    return sum((collections.Counter(gold) & collections.Counter(pred)).values())


def prf(tp: int, n_gold: int, n_pred: int) -> dict[str, float]:
    p = tp / n_pred if n_pred else (1.0 if not n_gold else 0.0)
    r = tp / n_gold if n_gold else (1.0 if not n_pred else 0.0)
    return {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0}


def align(gold: list[dict], pred: list[dict]):
    """Greedy alignment: each gold step takes the unused predicted step with the same system_id + resource_id that
    agrees on the most remaining fields (ties -> earliest). Returns (pairs, unmatched_gold, unmatched_pred)."""
    used: set[int] = set()
    pairs, missing = [], []
    for g in gold:
        best, best_score = None, -1
        for j, p in enumerate(pred):
            if j in used or p.get("system_id") != g["system_id"] or p.get("resource_id") != g["resource_id"]:
                continue
            score = sum(p.get(f) == g[f] for f in ("phase", "operation", "state", "evidence_id"))
            if score > best_score:
                best, best_score = j, score
        if best is None:
            missing.append(g)
        else:
            used.add(best)
            pairs.append((g, pred[best]))
    extra = [p for j, p in enumerate(pred) if j not in used]
    return pairs, missing, extra


def plan_violations(steps: list[dict]) -> list[str]:
    """Label-free safety checks on a predicted plan."""
    out = []
    rem = [s for s in steps if s.get("phase") == "REMEDIATION"]
    last_flow = max((i for i, s in enumerate(rem) if s.get("operation") in FLOW_OPS), default=-1)
    first_record = min((i for i, s in enumerate(rem) if s.get("operation") in RECORD_OPS), default=len(rem))
    if last_flow > first_record:
        out.append("a record is changed before every data flow is disabled / restore blocked")
    verifications = {(s.get("operation"), s.get("system_id"), s.get("resource_id"))
                     for s in steps if s.get("phase") == "VERIFICATION"}
    for s in rem:
        need = (REMEDIATION_TO_VERIFICATION.get(s.get("operation")), s.get("system_id"), s.get("resource_id"))
        if need not in verifications:
            out.append(f"{s.get('operation')} {s.get('resource_id')} has no {need[0]}")
    phases = [s.get("phase") for s in steps]
    if phases != sorted(phases, key=lambda p: PHASES.index(p) if p in PHASES else 99):
        out.append("phases out of order")
    return out


def fmt_step(s: dict) -> str:
    return (f"{str(s.get('phase', '?'))[:5]:5} {s.get('system_id')}/{s.get('resource_id')} {s.get('operation')} "
            f"{s.get('state')} ev={s.get('evidence_id')!r}")


def score_case(gold: dict, pred_status: str | None, pred_steps: list[dict]) -> dict:
    """gold = {case_id, deletion_status, steps: [step dicts]}."""
    g_status, g_steps = gold["deletion_status"], gold["steps"]
    p_steps = [s for s in pred_steps if isinstance(s, dict)]
    g_t, p_t = [step_tuple(s) for s in g_steps], [step_tuple(s) for s in p_steps]
    per_phase = {}
    for ph in PHASES:
        gp, pp = [t for t in g_t if t[0] == ph], [t for t in p_t if t[0] == ph]
        per_phase[ph] = {"tp": multiset_tp(gp, pp), "gold": len(gp), "pred": len(pp)}
    pairs, missing, extra = align(g_steps, p_steps)
    field_ok = {f: sum(g[f] == p.get(f) for g, p in pairs) for f in FIELDS}
    same_set = collections.Counter(g_t) == collections.Counter(p_t)
    numbered = [s.get("step_no") for s in p_steps] == list(range(1, len(p_steps) + 1))
    exact = g_status == pred_status and g_t == p_t and numbered

    diff = []
    if not exact:
        diff.append(f"{gold['case_id']}: status gold={g_status} pred={pred_status}")
        for g, p in pairs:
            changed = [f"{f}: {g[f]!r} -> {p.get(f)!r}" for f in FIELDS if g[f] != p.get(f)]
            if changed:
                diff.append(f"  ~ gold#{g['step_no']} {fmt_step(g)}  |  " + "; ".join(changed))
        diff += [f"  - missing gold#{g['step_no']} {fmt_step(g)}" for g in missing]
        diff += [f"  + extra   pred#{p.get('step_no')} {fmt_step(p)}" for p in extra]
        if not numbered:
            diff.append(f"  ! step_no not consecutive from 1: {[s.get('step_no') for s in p_steps]}")
        if same_set and g_t != p_t:
            diff.append("  order differs")

    tp = multiset_tp(g_t, p_t)
    return {
        "case_id": gold["case_id"], "gold_status": g_status, "pred_status": pred_status,
        "status_ok": g_status == pred_status, "tp": tp, "gold": len(g_t), "pred": len(p_t),
        "tp_ignoring_evidence": multiset_tp([t[:5] for t in g_t], [t[:5] for t in p_t]),
        "per_phase": per_phase, "field_ok": field_ok, "unaligned_pred": len(extra),
        "same_set": same_set, "exact": exact, "plan_violations": plan_violations(p_steps),
        **{f"step_{k}": v for k, v in prf(tp, len(g_t), len(p_t)).items()},
        "diff": diff,
    }


def aggregate(scores: list[dict]) -> dict:
    n = len(scores)
    if not n:
        return {"cases": 0}
    tp, n_gold, n_pred = (sum(s[k] for s in scores) for k in ("tp", "gold", "pred"))
    overall = prf(tp, n_gold, n_pred)
    phases = {}
    for ph in PHASES:
        t, g, p = (sum(s["per_phase"][ph][k] for s in scores) for k in ("tp", "gold", "pred"))
        phases[ph] = {**prf(t, g, p), "gold": g, "pred": p}
    confusion = collections.Counter(f"{s['gold_status']}->{s['pred_status']}" for s in scores)
    same_set = sum(s["same_set"] for s in scores)
    return {
        "cases": n,
        "status_accuracy": sum(s["status_ok"] for s in scores) / n,
        "status_confusion": dict(sorted(confusion.items())),
        "steps_gold": n_gold, "steps_pred": n_pred, "steps_matched": tp,
        "step_precision": overall["precision"], "step_recall": overall["recall"], "step_f1": overall["f1"],
        "step_recall_ignoring_evidence_id": sum(s["tp_ignoring_evidence"] for s in scores) / n_gold if n_gold else 1.0,
        "per_phase": phases,
        "remediation_f1": phases["REMEDIATION"]["f1"],
        "field_accuracy": {f: sum(s["field_ok"][f] for s in scores) / n_gold if n_gold else 1.0 for f in FIELDS},
        "unaligned_pred_steps": sum(s["unaligned_pred"] for s in scores),
        "step_set_accuracy": same_set / n,
        "order_accuracy_given_correct_set": sum(s["exact"] for s in scores if s["same_set"]) / same_set if same_set else 0.0,
        "sequence_accuracy": sum(s["exact"] for s in scores) / n,
        "exact_cases": sum(s["exact"] for s in scores),
        "plan_validity": sum(not s["plan_violations"] for s in scores) / n,
        "macro_case_step_f1": sum(s["step_f1"] for s in scores) / n,
    }
