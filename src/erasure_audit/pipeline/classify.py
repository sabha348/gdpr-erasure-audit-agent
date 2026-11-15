"""Does the request text authorise account deletion? (DECISIONS #15)

Rule-based backend, used until an LLM classifier is plugged in; every result is marked confidence="low".
A request is a deletion request when it asks to delete/erase/close the account or its personal data, in any of the
languages seen (en, es, pt, fr, ja, ko). It is NOT a deletion request when it only asks for something narrower:
access, a copy/export, a correction, a marketing opt-out, login help. Explicit "keep the account" wording wins.
The pipeline cross-checks the result against CLASSIFY_REQUEST activity rows for the subject's accounts.
"""
from __future__ import annotations

import re

DELETION = [
    r"\b(delete|deleting|deleted|deletion|erase|erased|erasure)\b",
    r"\b(remove|removed|removal)\b", r"\bclose (my|this|the) account\b", r"\bclosed my account\b",
    r"\bclose the account\b", r"\bno longer need this account\b", r"\b(is|was) gone\b",
    r"\bcannot identify me\b", r"\bstill (appears|be found|recognized|exist)",
    r"\beliminar\b", r"\beliminación\b", r"\bborrar\b",                  # es
    r"\bexclusão\b", r"\bexcluir\b", r"\bapagar\b",                     # pt
    r"\bsuppression\b", r"\bsupprimer\b", r"\beffacer\b",               # fr
    r"削除", r"消去",                                                    # ja
    r"삭제",                                                             # ko
    r"\blöschen\b", r"\blöschung\b",                                    # de
]
# Narrower requests; any of these without an explicit account-deletion ask -> not a deletion request.
NON_DELETION = [
    r"\bmarketing\b", r"\bpromotional\b", r"\bunsubscribe\b", r"\bnewsletter\b",
    r"\bcopy of\b", r"\bdownload my\b", r"\bportable\b", r"\bexport my\b",
    r"\bwhat personal information\b", r"\binformation (you )?hold\b",
    r"\bcorrect\b", r"\bmisspelled\b", r"\bout of date\b", r"\breplace it\b", r"\bchange the email\b",
    r"\breset(ting)? the login\b", r"\bcan't access\b", r"\binvoices?\b",
]
KEEP_ACCOUNT = [r"\bleave (the|my) (account|profile)\b", r"\bkeep the account\b", r"\bcontinue using\b",
                r"\blists only\b", r"\bleave my profile\b", r"\bstill (need|use) (it|the account)\b",
                # explicit negations of deletion
                r"\bnot (asking|requesting) (for )?(deletion|erasure)", r"\bnot a request to (erase|delete|remove)",
                r"\b(don't|do not) (delete|close|erase|remove)", r"\bwithout removing\b"]


def _hits(patterns, text):
    return [p for p in patterns if re.search(p, text, re.I)]


def classify(text: str) -> dict:
    t = text.strip()
    deletion, narrow, keep = _hits(DELETION, t), _hits(NON_DELETION, t), _hits(KEEP_ACCOUNT, t)
    if keep:
        is_deletion = False
    elif deletion:
        # "remove me from marketing lists" style requests mention removal but are scoped elsewhere.
        is_deletion = not (narrow and not re.search(r"account|profile|personal data|personal information|データ|계정|cuenta|conta|compte", t, re.I))
    else:
        is_deletion = False
    return {"is_deletion": is_deletion, "method": "rules", "confidence": "low",
            "signals": {"deletion": deletion, "narrow": narrow, "keep_account": keep}}
