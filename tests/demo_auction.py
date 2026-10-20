"""Explicit labelled-demo arguments for tests of the scripted auction."""
from src.app.demo_data import self_play_bundle


def demo_suppliers() -> list[dict]:
    return self_play_bundle()["names"]


def demo_prices() -> dict:
    bundle = self_play_bundle()
    return {
        "suppliers": bundle["names"],
        "target_price": bundle["target"],
        "walk_away_ceiling": bundle["ceiling"],
        "moq": bundle["moq"],
        "self_play": bundle,
    }
