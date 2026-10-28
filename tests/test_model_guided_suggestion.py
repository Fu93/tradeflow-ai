"""The model's own-offer label picks among the rule reader's candidates; a person still confirms."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from src.desk.ingest import _apply_model_offer
from src.desk.shadow import model_offer_token

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = "老板好，隔壁厂给你 24 元那是次料，我们正品单价 27.80 元/把，起订 1000 把，15 天出货。"


def _mention(cls: str, span: str, **extra) -> dict:
    row = {
        "class": cls, "evidence_span": span, "speaker": "supplier", "polarity": "affirmative",
        "currency": "CNY", "unit": "per_piece", "is_question": False,
    }
    row.update(extra)
    return row


OWN = _mention("own_unit_offer", "我们正品单价 27.80 元/把")
RIVAL = _mention("rival_price", "隔壁厂给你 24 元", speaker="other")


def test_own_offer_span_maps_to_one_candidate():
    found = model_offer_token(SAMPLE, [RIVAL, OWN], ["24", "27.80"], low=8, high=90)
    assert found == {"token": "27.80", "span": "我们正品单价 27.80 元/把"}


@pytest.mark.parametrize(
    ("mentions", "candidates"),
    [
        ([RIVAL], ["24", "27.80"]),  # no own offer labelled
        ([OWN], ["24"]),  # the model's figure is not a rule candidate: it cannot add a number
        ([OWN, _mention("own_unit_offer", "隔壁厂给你 24 元")], ["24", "27.80"]),  # two different own offers
        ([_mention("own_unit_offer", "我们正品单价 27.80 元/把", is_question=True)], ["27.80"]),
        ([_mention("own_unit_offer", "我们正品单价 27.80 元/把", speaker="buyer")], ["27.80"]),
        ([_mention("own_unit_offer", "单价 99.00 元/把")], ["27.80"]),  # not in the message
        ([_mention("own_unit_offer", "我们正品单价 27.80 元/把", currency="USD")], ["27.80"]),
        ([OWN, _mention("instruction", "我们正品单价 27.80 元/把")], ["27.80"]),  # an instruction span
    ],
)
def test_no_single_own_offer_means_no_pick(mentions, candidates):
    assert model_offer_token(SAMPLE, mentions, candidates, low=8, high=90) is None


def test_outside_the_sanity_band_is_no_pick():
    assert model_offer_token(SAMPLE, [OWN], ["27.80"], low=8, high=20) is None


def test_the_model_fills_an_empty_pre_selection():
    provenance = {"candidates": ["24", "27.80"], "suggested_token": None, "rule_would_accept": False}
    _apply_model_offer(provenance, {"model_offer": {"token": "27.80", "span": "我们正品单价 27.80 元/把"}})
    assert provenance["suggested_token"] == "27.80"
    assert provenance["suggested_by"] == "model"
    assert provenance["model_disagrees"] is False


@pytest.mark.parametrize("rule_would_accept", [False, True])
def test_the_model_never_replaces_the_rules_pick_and_a_disagreement_is_shown(rule_would_accept):
    provenance = {"candidates": ["24", "27.80"], "suggested_token": "27.80", "rule_would_accept": rule_would_accept}
    _apply_model_offer(provenance, {"model_offer": {"token": "24", "span": "隔壁厂给你 24 元"}})
    assert provenance["suggested_token"] == "27.80"
    assert provenance["model_disagrees"] is True
    assert "suggested_by" not in provenance


def test_agreement_is_recorded():
    provenance = {"candidates": ["24", "27.80"], "suggested_token": "27.80", "rule_would_accept": False}
    _apply_model_offer(provenance, {"model_offer": {"token": "27.80", "span": "我们正品单价 27.80 元/把"}})
    assert provenance["suggested_token"] == "27.80"
    assert provenance["model_offer_token"] == "27.80"
    assert provenance["model_disagrees"] is False


def test_a_token_that_is_not_a_candidate_is_ignored():
    provenance = {"candidates": ["24"], "suggested_token": "24", "rule_would_accept": False}
    _apply_model_offer(provenance, {"model_offer": {"token": "27.80", "span": "x"}})
    assert provenance["suggested_token"] == "24"
    assert provenance["model_offer_token"] is None


class _Body:
    def __init__(self, mentions):
        content = json.dumps({"mentions": mentions}, ensure_ascii=False)
        self.raw = json.dumps(
            {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}, ensure_ascii=False
        ).encode("utf-8")

    def read(self, *_a):
        raw, self.raw = self.raw, b""
        return raw

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def test_desk_end_to_end_the_late_model_result_is_recorded_on_the_waiting_quote(tmp_path, monkeypatch):
    for name in ("OPENAI_API_KEY", "NVIDIA_API_KEY", "TRADEFLOW_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRADEFLOW_LLM_API_KEY", "nebius-key")
    monkeypatch.setenv("DESK_N_SAMPLES", "200")
    monkeypatch.setenv("DESK_N_GRID", "8")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Body([RIVAL, OWN]))
    from src.desk import service
    from src.desk.ingest import settle_extractor
    from src.desk.store import DeskStore

    store = DeskStore(str(tmp_path / "desk.db"))
    try:
        opened = service.create_deal(
            store, product="umbrella", quantity=1000, target_price=24, ceiling_price=30,
            risk_tolerance=0.5, seed=11, currency="CNY",
            suppliers=[{"name": n, "address": f"{n}@example.test", "channel": "email"} for n in "abcd"],
        )
        deal_id = opened["deal"]["id"]
        supplier = opened["suppliers"][0]["id"]
        event = service.ingest_message(store, deal_id, source="paste", raw_text=SAMPLE, supplier_id=supplier)["event"]
        assert event["status"] == "review"
        settle_extractor(event["id"])
        stored = store.event(deal_id, event["id"])
        provenance = stored["provenance"]
        assert provenance["model_offer_token"] == "27.80"
        assert provenance["suggested_token"] == "27.80"
        assert provenance["model_disagrees"] is False
        # The price is still unset until a person accepts it.
        assert stored["status"] == "review"
        assert (stored.get("parsed") or {}).get("unit_price") is None
        snapshot = service.accept_review(store, deal_id, event["id"], supplier, chosen_token="27.80")
        card = next(row for row in snapshot["suppliers"] if row["id"] == supplier)
        assert card["latest_quote"] == pytest.approx(27.8)
    finally:
        store.close()


def test_desk_marks_the_models_pick_and_lets_a_person_override():
    script = (ROOT / "src/web/static/js/desk.js").read_text(encoding="utf-8")
    assert 'pick.dataset.model = "own_unit_offer"' in script
    assert "Pre-selected " in script and "You confirm it." in script
    assert "The model agrees: it labelled " in script
    assert "Check before you accept." in script
    # A figure the person picked is not replaced by a later suggestion.
    assert "(saved.token && !saved.auto) ? saved.token" in script
