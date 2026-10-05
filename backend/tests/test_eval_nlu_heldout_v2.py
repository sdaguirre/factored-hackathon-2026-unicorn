"""Smoke test of scripts/eval_nlu_heldout_v2.py in LLM mode with a fake Anthropic client (no network, no API key).

The fake answers are test data built from the reference labels, with planted errors, to check the script's
bookkeeping: accuracy, invalid outputs, latency/token/cost metering, run-to-run disagreements, the JSON report and
--rescore. They are never results of the model.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent import llm_anthropic
from eval.nlu_heldout_v2 import HELDOUT_V2

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval_nlu_heldout_v2.py"
LABEL = {text: intent for text, intent, *_ in HELDOUT_V2}
FIRST, SECOND = HELDOUT_V2[0][0], HELDOUT_V2[1][0]


def load_script():
    spec = importlib.util.spec_from_file_location("eval_nlu_heldout_v2", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeAnthropicLLM(llm_anthropic.AnthropicLLM):
    """Answers with the reference label, except: run 1 returns no JSON for FIRST; run 2 mislabels SECOND."""
    runs = 0

    def __init__(self, api_key, model):
        FakeAnthropicLLM.runs += 1
        self._model, self._calls = model, 0
        self._client = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, *, model, max_tokens, system, messages):
        text = re.search(r"<user_message>(.*)</user_message>", messages[0]["content"], re.S).group(1)
        self._calls += 1
        if self._calls > len(HELDOUT_V2):                       # second run of the same client
            intent = "unknown" if text == SECOND else LABEL[text]
            out = json.dumps({"intent": intent})
        else:
            out = "sin json" if text == FIRST else json.dumps({"intent": LABEL[text]})
        return SimpleNamespace(content=[SimpleNamespace(text=out)],
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=50))


@pytest.fixture()
def script(monkeypatch):
    monkeypatch.setattr(llm_anthropic, "AnthropicLLM", FakeAnthropicLLM)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    return load_script()


def args(**kw):
    base = dict(runs=2, no_llm=False, labels=None, rescore=None, out=None, usd_per_mtok_in=1.0, usd_per_mtok_out=5.0)
    return argparse.Namespace(**{**base, **kw})


def test_llm_mode_end_to_end_with_a_fake_client(script, tmp_path):
    out = tmp_path / "report.json"
    script.main(args(out=str(out)))
    report = json.loads(out.read_text(encoding="utf-8"))
    rules, run1, run2 = report["results"]
    n = len(HELDOUT_V2)
    assert rules["name"].startswith("Rules") and run1["n"] == run2["n"] == n
    assert run1["invalid_outputs"] == 1 and run2["invalid_outputs"] == 0
    assert run1["correct"] <= n - 1 and run2["correct"] <= n - 1          # one planted error each
    assert report["llm_run_disagreements"] >= 1
    eff = run1["efficiency"]
    assert eff["calls"] == n and eff["input_tokens"] == 1000 * n and eff["output_tokens"] == 50 * n
    assert eff["usd_total"] == pytest.approx((1000 * n * 1.0 + 50 * n * 5.0) / 1e6)
    assert eff["latency_ms_p50"] <= eff["latency_ms_p95"]
    assert len(run1["predictions"]) == n and set(run1["predictions"][0]) == {"intent", "sensitive_topic", "language", "error"}


def test_rescore_reuses_the_saved_predictions_without_calling_the_model(script, tmp_path):
    saved = tmp_path / "report.json"
    script.main(args(out=str(saved)))
    runs_before = FakeAnthropicLLM.runs
    labels = tmp_path / "adjudicated.csv"
    rows = ["id,text,intent,sensitive_topic"]
    for i, (text, intent, _, sensitive, _, _) in enumerate(HELDOUT_V2, start=1):
        rows.append(f'{i},"{text}",{"unknown" if text == SECOND else intent},{str(sensitive).lower()}')
    labels.write_text("\n".join(rows), encoding="utf-8")
    final = tmp_path / "final.json"
    script.main(args(rescore=str(saved), labels=str(labels), out=str(final)))
    assert FakeAnthropicLLM.runs == runs_before                            # no new client, no API call
    report = json.loads(final.read_text(encoding="utf-8"))
    original = json.loads(saved.read_text(encoding="utf-8"))
    run2_before, run2_after = original["results"][2], report["results"][2]
    assert run2_after["correct"] == run2_before["correct"] + 1             # the relabeled phrase now counts as a hit
    assert run2_after["efficiency"] == run2_before["efficiency"]
    assert report["rescored_from"]["file"] == str(saved)


def test_sheets_saved_by_excel_are_read(script, tmp_path):
    """Excel with Latin American settings saves ';' as separator and a UTF-8 BOM."""
    from scripts.nlu_agreement import read_sheet

    sheet = tmp_path / "second.csv"
    sheet.write_text("id;text;yes_no_pending;intent;sensitive_topic\n1;hola;false;greeting;false\n", encoding="utf-8-sig")
    rows = read_sheet(str(sheet))
    assert rows == [{"id": "1", "text": "hola", "yes_no_pending": "false", "intent": "greeting", "sensitive_topic": "false"}]
    comma = tmp_path / "comma.csv"
    comma.write_text('id,text,intent\n1,"hola, buenas",greeting\n', encoding="utf-8")
    assert read_sheet(str(comma))[0]["text"] == "hola, buenas"


def test_report_has_language_and_unseen_in_prompt_figures(script, tmp_path):
    out = tmp_path / "report.json"
    script.main(args(out=str(out), no_llm=True))
    rules = json.loads(out.read_text(encoding="utf-8"))["results"][0]
    assert rules["language_correct"][1] == len(HELDOUT_V2)
    assert rules["unseen_in_prompt"][1] == len(HELDOUT_V2) - 2
    assert "indicative" in rules["breakdowns_note"]
