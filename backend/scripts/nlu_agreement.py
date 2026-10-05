"""Inter-annotator agreement for NLU held-out set v2, and the adjudication sheet.

1. `python scripts/nlu_agreement.py blind` writes eval/annotation/nlu_heldout_v2_blind.csv: id, text,
   yes_no_pending and empty intent / sensitive_topic columns. A team member fills it WITHOUT looking at
   eval/nlu_heldout_v2.py, using the guide at the top of the sheet (eval/annotation/README.md).
2. `python scripts/nlu_agreement.py compare <filled.csv>` reports Cohen's kappa for intent and sensitive_topic
   against the primary labels, lists the disagreements and writes
   eval/annotation/nlu_heldout_v2_adjudicated.csv with both labels and a `final` column to settle each disagreement
   (default: primary label). Settle them by discussion, never by looking at classifier outputs.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.nlu_heldout_v2 import HELDOUT_V2  # noqa: E402

OUT = ROOT / "eval" / "annotation"


def kappa(a: list, b: list) -> float:
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(a) | set(b)) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def blind():
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "nlu_heldout_v2_blind.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "text", "yes_no_pending", "intent", "sensitive_topic"])
        for i, (text, _, _, _, pending, _) in enumerate(HELDOUT_V2, start=1):
            w.writerow([i, text, str(pending).lower(), "", ""])
    print(f"Blind sheet with {len(HELDOUT_V2)} phrases: {path}")


def compare(filled: str):
    with open(filled, encoding="utf-8-sig", newline="") as f:
        second = {int(r["id"]): r for r in csv.DictReader(f)}
    missing = [i for i in range(1, len(HELDOUT_V2) + 1) if not second.get(i, {}).get("intent", "").strip()]
    if missing:
        sys.exit(f"{len(missing)} phrases without an intent, e.g. ids {missing[:10]}")
    prim_i = [c[1] for c in HELDOUT_V2]
    sec_i = [second[i]["intent"].strip() for i in range(1, len(HELDOUT_V2) + 1)]
    prim_s = [c[3] for c in HELDOUT_V2]
    sec_s = [second[i]["sensitive_topic"].strip().lower() in ("true", "1", "yes", "si", "sí")
             for i in range(1, len(HELDOUT_V2) + 1)]
    n = len(HELDOUT_V2)
    agree = sum(x == y for x, y in zip(prim_i, sec_i))
    print(f"Intent: agreement {agree}/{n} = {100 * agree / n:.0f}%, Cohen's kappa {kappa(prim_i, sec_i):.2f}")
    agree_s = sum(x == y for x, y in zip(prim_s, sec_s))
    print(f"Sensitive topic: agreement {agree_s}/{n}, Cohen's kappa {kappa(prim_s, sec_s):.2f}")
    path = OUT / "nlu_heldout_v2_adjudicated.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "text", "primary_intent", "second_intent", "primary_sensitive", "second_sensitive",
                    "intent", "sensitive_topic", "note"])
        for i, (text, intent, _, sens, _, _) in enumerate(HELDOUT_V2, start=1):
            if intent != sec_i[i - 1] or sens != sec_s[i - 1]:
                print(f"  {i:>3} '{text}': primary {intent}/{sens} vs second {sec_i[i - 1]}/{sec_s[i - 1]}")
            w.writerow([i, text, intent, sec_i[i - 1], str(sens).lower(), str(sec_s[i - 1]).lower(),
                        intent, str(sens).lower(), ""])
    print(f"Adjudication sheet: {path} (edit intent / sensitive_topic / note for each disagreement)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("blind")
    c = sub.add_parser("compare")
    c.add_argument("filled")
    a = ap.parse_args()
    blind() if a.cmd == "blind" else compare(a.filled)
