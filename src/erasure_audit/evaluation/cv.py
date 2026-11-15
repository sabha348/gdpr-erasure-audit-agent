"""Leave-one-out cross-validation of the option selection.

The named options (pipeline/config.py) were chosen by a one-factor-at-a-time sweep on the same 32 public samples
the scores are reported on, so the in-sample score is optimistic. Here, for every sample case, the sweep is re-run
on the other 31 cases only, and the held-out case is predicted with the options that sweep picks. The held-out
predictions together give an estimate that is not biased by option tuning.

What it does not remove: design choices made by reading the samples (for example the account-join rule in
docs/DECISIONS.md D3) are code, not options, so they are still in-sample. Only owner-written custom cases are free
of that bias; they are reported separately.
"""
from __future__ import annotations

import collections
from dataclasses import asdict, fields
from pathlib import Path

from ..pipeline import CHOICES, Options, build
from .cases import LabelledCase
from .metrics import aggregate, score_case


def rank(m: dict) -> tuple:
    """Same objective as the original sweep: exact sequences, then step F1, mean field accuracy, status."""
    field_mean = sum(m["field_accuracy"].values()) / len(m["field_accuracy"])
    return tuple(round(x, 6) for x in (m["sequence_accuracy"], m["step_f1"], field_mean, m["status_accuracy"]))


class _Predictions:
    """Pipeline predictions per option set, computed once and shared by every fold."""

    def __init__(self, cases: list[LabelledCase], dataset_dir: Path):
        self.cases = {c.case_id: c for c in cases}
        self.dataset_dir = dataset_dir
        self.scores: dict[Options, dict[str, dict]] = {}

    def __call__(self, opt: Options) -> dict[str, dict]:
        if opt not in self.scores:
            pipe = build(self.dataset_dir, opt)
            out = {}
            for cid, case in self.cases.items():
                status, steps, _ = pipe.assess(case.request.as_row())
                out[cid] = score_case(case.gold_row(), status, steps)
            self.scores[opt] = out
        return self.scores[opt]


def select_options(preds: _Predictions, train_ids: list[str], start: Options = Options()) -> Options:
    base = start
    for f in fields(Options):
        name, current = f.name, getattr(base, f.name)
        if len(CHOICES[name]) < 2:
            continue
        ranked = {v: rank(aggregate([preds(base.with_(**{name: v}))[c] for c in train_ids])) for v in CHOICES[name]}
        best = max(ranked, key=lambda v: ranked[v])
        if ranked[best] == ranked[current]:
            best = current
        base = base.with_(**{name: best})
    return base


def neutral_options() -> Options:
    """The first listed value of every option: the sweep's starting point when nothing has been tuned."""
    return Options(**{name: values[0] for name, values in CHOICES.items()})


def leave_one_out(cases: list[LabelledCase], dataset_dir: Path, start: Options | None = None) -> dict:
    """Each fold re-runs the sweep from `start` (default: neutral, untuned options) on the other cases."""
    start = start or neutral_options()
    public = [c for c in cases if c.slice == "public_sample"]
    preds = _Predictions(public, dataset_dir)
    ids = [c.case_id for c in public]
    held_out, picks = [], collections.Counter()
    deviations: dict[str, dict] = {}
    for cid in ids:
        opt = select_options(preds, [i for i in ids if i != cid], start)
        held_out.append(preds(opt)[cid])
        changed = {k: v for k, v in asdict(opt).items() if getattr(Options(), k) != v}
        picks[tuple(sorted(changed.items()))] += 1
        if changed:
            deviations[cid] = changed
    return {
        "folds": len(ids),
        "held_out": aggregate(held_out),
        "in_sample": aggregate([preds(Options())[c] for c in ids]),
        "start_options": asdict(start),
        "option_sets_evaluated": len(preds.scores),
        "folds_matching_shipped_defaults": picks.get((), 0),
        "fold_deviations": deviations,
    }
