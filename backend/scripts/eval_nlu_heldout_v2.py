"""Evaluates the NLU on held-out set v2 (eval/nlu_heldout_v2.py): rules baseline vs the LLM.

Reports intent accuracy with 95% Wilson intervals, by language and stratum, sensitive-topic detection, LLM latency
p50/p95, tokens and cost per message, and the variability between repeated LLM runs. Writes a JSON report to
eval/results/ when --out is given.

Usage (from backend/):
    python scripts/eval_nlu_heldout_v2.py --no-llm                    # rules only, no network
    python scripts/eval_nlu_heldout_v2.py --runs 2 --out eval/results/nlu_heldout_v2.json
    python scripts/eval_nlu_heldout_v2.py --labels eval/annotation/nlu_heldout_v2_adjudicated.csv
    python scripts/eval_nlu_heldout_v2.py --rescore eval/results/nlu_heldout_v2.json \
        --labels eval/annotation/nlu_heldout_v2_adjudicated.csv --out eval/results/nlu_heldout_v2_final.json

The LLM needs ANTHROPIC_API_KEY (or CHAT_ANTHROPIC_API_KEY) in the environment or backend/.env. Cost assumption:
--usd-per-mtok-in / --usd-per-mtok-out (defaults: Claude Haiku 4.5 list prices, 1 and 5 USD per million tokens).
Measure once: never tune prompts or rules on this set.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.nlu import MockNLU, NLUResult  # noqa: E402
from app.agent.orchestrator import validate_nlu  # noqa: E402
from eval.nlu_heldout_v2 import HELDOUT_V2  # noqa: E402


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def pct(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {100 * k / n:.0f}% [95% CI {100 * lo:.0f}-{100 * hi:.0f}]" if n else "n/a"


def load_cases(labels: str | None) -> list[tuple]:
    """Primary labels from the set, or adjudicated labels (CSV with id, intent, sensitive_topic) when given."""
    if not labels:
        return HELDOUT_V2
    with open(labels, encoding="utf-8", newline="") as f:
        adj = {int(r["id"]): r for r in csv.DictReader(f)}
    return [(t, adj[i]["intent"], lang, adj[i]["sensitive_topic"].strip().lower() == "true", pend, stratum)
            for i, (t, _, lang, _, pend, stratum) in enumerate(HELDOUT_V2, start=1)]


def predict_all(predict, cases) -> list[dict]:
    """One prediction per case, after the same post-processing as the orchestrator. Saved in the report so the
    metrics can be recomputed with other labels (--rescore) without calling the model again."""
    out = []
    for text, _, _, _, pending, _ in cases:
        error = None
        try:
            r: NLUResult = predict(text, pending)
        except Exception as e:  # noqa: BLE001 - an invalid model output counts as an error, as in production
            r, error = NLUResult(intent="unknown"), type(e).__name__
        r = validate_nlu(r, text)          # explicit handoff guard, as in production
        out.append({"intent": r.intent, "sensitive_topic": bool(r.sensitive_topic), "error": error})
    return out


def score(name: str, preds: list[dict], cases) -> dict:
    hits, failures = [], 0
    by_lang, by_stratum, by_intent = defaultdict(list), defaultdict(list), defaultdict(list)
    sens = {"tp": 0, "fn": 0, "fp": 0}
    misses = []
    for (text, intent, lang, sensitive, _, stratum), pr in zip(cases, preds, strict=True):
        failures += pr["error"] is not None
        hit = pr["intent"] == intent
        hits.append(hit)
        by_lang[lang].append(hit)
        by_stratum[stratum].append(hit)
        by_intent[intent].append(hit)
        if sensitive:
            sens["tp" if pr["sensitive_topic"] else "fn"] += 1
        elif pr["sensitive_topic"]:
            sens["fp"] += 1
        if not hit:
            misses.append((text, intent, f"ERROR {pr['error']}" if pr["error"] else pr["intent"]))
    n, k = len(hits), sum(hits)
    print(f"\n### {name}: intent accuracy {pct(k, n)}")
    print("   by language: " + " | ".join(f"{g} {pct(sum(v), len(v))}" for g, v in sorted(by_lang.items())))
    print("   by stratum:  " + " | ".join(f"{g} {sum(v)}/{len(v)}" for g, v in sorted(by_stratum.items())))
    print("   by intent:   " + ", ".join(f"{g} {sum(v)}/{len(v)}" for g, v in sorted(by_intent.items())))
    print(f"   sensitive topic: detected {sens['tp']}/{sens['tp'] + sens['fn']}, false positives {sens['fp']}"
          f" | invalid outputs: {failures}")
    for t, exp, got in misses:
        print(f"   x '{t}' -> {got} (expected {exp})")
    return {"name": name, "n": n, "correct": k, "accuracy": k / n, "wilson_95": wilson(k, n),
            "by_language": {g: [sum(v), len(v)] for g, v in by_lang.items()},
            "by_stratum": {g: [sum(v), len(v)] for g, v in by_stratum.items()},
            "by_intent": {g: [sum(v), len(v)] for g, v in by_intent.items()},
            "sensitive": sens, "invalid_outputs": failures, "predictions": preds,
            "misses": [{"text": t, "expected": e, "got": g} for t, e, g in misses]}


class MeteredLLM:
    """Wraps AnthropicLLM to record latency and tokens per call without changing its behavior."""

    def __init__(self, llm):
        self.llm, self.calls = llm, []
        create = llm._client.messages.create

        def metered(**kw):
            t0 = time.perf_counter()
            resp = create(**kw)
            self.calls.append({"ms": (time.perf_counter() - t0) * 1000,
                               "in": resp.usage.input_tokens, "out": resp.usage.output_tokens})
            return resp
        llm._client.messages.create = metered

    def extract(self, text: str, pending: bool) -> NLUResult:
        return self.llm.extract(text, None, pending)


def efficiency(calls: list[dict], usd_in: float, usd_out: float) -> dict:
    ms = sorted(c["ms"] for c in calls)
    tin, tout = sum(c["in"] for c in calls), sum(c["out"] for c in calls)
    cost = tin / 1e6 * usd_in + tout / 1e6 * usd_out
    q = statistics.quantiles(ms, n=20) if len(ms) >= 2 else ms * 19
    out = {"calls": len(calls), "latency_ms_p50": statistics.median(ms), "latency_ms_p95": q[18],
           "input_tokens": tin, "output_tokens": tout, "usd_total": cost, "usd_per_message": cost / len(calls)}
    print(f"   LLM calls {out['calls']}: latency p50 {out['latency_ms_p50']:.0f} ms, p95 {out['latency_ms_p95']:.0f} ms;"
          f" tokens in {tin:,} out {tout:,}; cost {cost:.4f} USD ({1000 * out['usd_per_message']:.3f} USD per 1,000 messages)")
    return out


def main(a):
    cases = load_cases(a.labels)
    print(f"HELD-OUT v2: {len(cases)} phrases ({sum(c[2] == 'es' for c in cases)} es, {sum(c[2] == 'pt' for c in cases)} pt)."
          f" Labels: {'adjudicated ' + a.labels if a.labels else 'primary (single annotator until adjudicated)'}")
    report = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "n": len(cases),
              "labels": a.labels or "primary", "results": []}
    if a.rescore:
        saved = json.loads(Path(a.rescore).read_text(encoding="utf-8"))
        report.update({k: saved[k] for k in ("model", "prompt_chars", "llm_run_disagreements") if k in saved})
        report["rescored_from"] = {"file": a.rescore, "run_at": saved["run_at"], "labels": saved["labels"]}
        for res in saved["results"]:
            new = score(res["name"], res["predictions"], cases)
            if "efficiency" in res:
                new["efficiency"] = res["efficiency"]
            report["results"].append(new)
        return write(report, a.out)
    rules = MockNLU()
    report["results"].append(score("Rules baseline (MockNLU)",
                                   predict_all(lambda t, p: rules.extract(t, None, p), cases), cases))
    if not a.no_llm:
        from app.agent.llm_anthropic import SYSTEM_NLU, AnthropicLLM
        from app.config import Settings
        s = Settings()
        llm = MeteredLLM(AnthropicLLM(s.anthropic_api_key, s.anthropic_model))
        report["model"] = s.anthropic_model
        report["prompt_chars"] = len(SYSTEM_NLU)
        runs = []
        for run in range(a.runs):
            llm.calls = []
            res = score(f"LLM {s.anthropic_model} (run {run + 1})", predict_all(llm.extract, cases), cases)
            res["efficiency"] = efficiency(llm.calls, a.usd_per_mtok_in, a.usd_per_mtok_out)
            runs.append(res)
            report["results"].append(res)
        if len(runs) > 1:
            changed = sum(len({r["predictions"][i]["intent"] for r in runs}) > 1 for i in range(len(cases)))
            report["llm_run_disagreements"] = changed
            print(f"\nLLM repeated runs: {changed}/{len(cases)} phrases got a different intent between runs")
    write(report, a.out)


def write(report: dict, out: str | None) -> None:
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nReport written to {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--labels", help="adjudicated labels CSV (id, intent, sensitive_topic)")
    ap.add_argument("--rescore", help="saved JSON report: recompute its metrics with --labels, without calling the model")
    ap.add_argument("--out", help="JSON report path, e.g. eval/results/nlu_heldout_v2.json")
    ap.add_argument("--usd-per-mtok-in", type=float, default=1.0)
    ap.add_argument("--usd-per-mtok-out", type=float, default=5.0)
    main(ap.parse_args())
