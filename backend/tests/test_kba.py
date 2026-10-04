from __future__ import annotations

import random

from app.auth import kba
from app.config import Settings
from app.data.repository import SnapshotRepository

SETTINGS = Settings(_env_file=None)
REPO = SnapshotRepository(SETTINGS.data_dir)
AS_OF = SETTINGS.as_of_date


def _customers():
    return [REPO.find_by_document(d) for d in REPO.sample_documents(10_000)]


def test_snapshot_customers_have_enough_data_for_a_challenge():
    cs = _customers()
    built = [kba.build_challenge(REPO, c, n=3, lang="es", as_of=AS_OF) for c in cs]
    missing = sum(b is None for b in built)
    assert missing / len(cs) < 0.05, f"{missing} de {len(cs)} clientes sin datos para 3 preguntas"


def test_questions_are_well_formed():
    rng = random.Random(1)
    for c in _customers()[:60]:
        for lang in ("es", "pt"):
            ch = kba.build_challenge(REPO, c, n=3, lang=lang, as_of=AS_OF, rng=rng)
            if ch is None:
                continue
            assert len({q.kind for q in ch.questions}) == 3
            for q in ch.questions:
                labels = [o.label for o in q.options]
                assert len(labels) == 4 and len(set(labels)) == 4, (q.kind, labels)
                assert sum(o.id == q.correct_option_id for o in q.options) == 1


def test_correct_answers_verify_and_wrong_ones_do_not():
    c = _customers()[0]
    ch = kba.build_challenge(REPO, c, n=3, lang="es", as_of=AS_OF)
    good = {q.id: q.correct_option_id for q in ch.questions}
    assert kba.verify(ch, good)
    bad = dict(good)
    q0 = ch.questions[0]
    bad[q0.id] = next(o.id for o in q0.options if o.id != q0.correct_option_id)
    assert not kba.verify(ch, bad)
    assert not kba.verify(ch, {})                                  # sin respuestas
    assert not kba.verify(ch, {**good, "extra": "x"})              # preguntas ajenas


def test_decoy_challenge_never_verifies_and_looks_real():
    d = kba.decoy_challenge(REPO, n=3, lang="es", as_of=AS_OF)
    assert len(d.questions) == 3
    for q in d.questions:
        assert len(q.options) == 4 and q.correct_option_id is None
    for q in d.questions:  # ninguna combinacion posible se acepta
        assert not kba.verify(d, {x.id: x.options[0].id for x in d.questions})


def test_public_view_hides_correct_answers():
    c = _customers()[0]
    ch = kba.build_challenge(REPO, c, n=3, lang="pt", as_of=AS_OF)
    pub = str(ch.public())
    assert "correct" not in pub
