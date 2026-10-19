"""Fit the Business & Industrial walk-away logits. Offline only.

Reads the NBER Best Offer files (Backus, Blake, Larsen, and Tadelis, 2020).
Does not run inside the Render service. Writes data/hazard_ebay_bi.json.

The decision outcome is seller-ended: the thread has no accepted offer, and
the last offer is a buyer offer that the seller declined, auto-declined, or
let expire. Thread-level no-deal, which includes threads the buyer ended, is
stored beside it and is not the decision coefficient.
"""
from __future__ import annotations

import csv
import gzip
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.heuristics import Z_95
DEFAULT_LISTS = Path("/tmp/ebay/anon_bo_lists.csv.gz")
DEFAULT_THREADS = Path("/tmp/ebay/anon_bo_threads.csv.gz")
OUT = ROOT / "data" / "hazard_ebay_bi.json"

META_BI = "12576"
BUYER_TYPES = {0, 1}
SELLER_END_STATUS = {0, 2, 6}
ACCEPT_STATUS = {1, 9}
CLIP = 20
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _when(text: str) -> tuple[int, int, int, int, int, int] | None:
    """eBay stamps look like ``03jul2012 18:03:24``. Lexical order is not time order."""
    raw = str(text or "").strip().lower()
    if len(raw) < 9:
        return None
    try:
        day = int(raw[0:2])
        month = _MONTHS[raw[2:5]]
        year = int(raw[5:9])
        hour = minute = second = 0
        if len(raw) >= 18 and raw[9] == " ":
            hour = int(raw[10:12])
            minute = int(raw[13:15])
            second = int(raw[16:18])
        return (year, month, day, hour, minute, second)
    except (KeyError, ValueError):
        return None


def _iso(stamp: tuple[int, int, int, int, int, int]) -> str:
    return f"{stamp[0]:04d}-{stamp[1]:02d}-{stamp[2]:02d}"


def _open(path: Path):
    return gzip.open(path, "rt", newline="")


def load_listings(path: Path) -> dict[int, float]:
    """Business & Industrial item id -> Buy-It-Now price."""
    found: dict[int, float] = {}
    with _open(path) as handle:
        reader = csv.reader(handle)
        header = next(reader)
        item_at = header.index("anon_item_id")
        price_at = header.index("start_price_usd")
        meta_at = header.index("meta_categ_id")
        for row in reader:
            if row[meta_at] != META_BI:
                continue
            try:
                price = float(row[price_at])
                item = int(row[item_at])
            except (TypeError, ValueError):
                continue
            if price > 0.0:
                found[item] = price
    return found


def load_threads(path: Path, listings: dict[int, float]) -> tuple[list[dict], int]:
    """One row per Business & Industrial thread with a first buyer offer at or below the BIN."""
    buckets: dict[str, list[tuple]] = defaultdict(list)
    sellers: dict[str, int] = {}
    with _open(path) as handle:
        reader = csv.reader(handle)
        header = next(reader)
        item_at = header.index("anon_item_id")
        thread_at = header.index("anon_thread_id")
        seller_at = header.index("anon_slr_id")
        type_at = header.index("offr_type_id")
        status_at = header.index("status_id")
        price_at = header.index("offr_price")
        date_at = header.index("src_cre_date")
        for index, row in enumerate(reader):
            try:
                item = int(row[item_at])
            except (TypeError, ValueError):
                continue
            if item not in listings:
                continue
            thread = row[thread_at]
            try:
                sellers[thread] = int(row[seller_at])
                offer_type = int(row[type_at])
                status = int(row[status_at])
                price = float(row[price_at])
            except (TypeError, ValueError):
                continue
            stamp = _when(row[date_at])
            if stamp is None:
                continue
            buckets[thread].append((stamp, index, offer_type, status, price, item))
    rows = []
    dropped_above_bin = 0
    for thread, offers in buckets.items():
        offers.sort()
        bin_price = listings[offers[0][5]]
        buyer = [item for item in offers if item[2] in BUYER_TYPES and item[4] > 0.0]
        if not buyer or bin_price <= 0.0:
            continue
        first = buyer[0]
        gap = 1.0 - (first[4] / bin_price)
        # An offer above the Buy-It-Now is not a discount. A few of those
        # ratios reach the thousands and flatten a logit on the raw gap.
        if gap < 0.0:
            dropped_above_bin += 1
            continue
        accepted = any(item[3] in ACCEPT_STATUS for item in offers)
        last = offers[-1]
        seller_ended = (not accepted) and last[2] in BUYER_TYPES and last[3] in SELLER_END_STATUS
        rows.append(
            {
                "seller": sellers[thread],
                "gap": float(gap),
                "date": _iso(first[0]),
                "no_deal": 0.0 if accepted else 1.0,
                "seller_ended": 1.0 if seller_ended else 0.0,
            }
        )
    return rows, dropped_above_bin


def _fit(gap: np.ndarray, outcome: np.ndarray, seller: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """Newton / IRLS logit. Standard errors are the seller-clustered sandwich."""
    count = int(gap.shape[0])
    design = np.column_stack([np.ones(count), gap])
    beta = np.zeros(2, dtype=np.float64)
    for _ in range(80):
        eta = np.clip(design @ beta, -CLIP, CLIP)
        probability = 1.0 / (1.0 + np.exp(-eta))
        weight = np.maximum(probability * (1.0 - probability), 1e-12)
        working = eta + (outcome - probability) / weight
        weighted = design * weight[:, None]
        gram = design.T @ weighted
        try:
            updated = np.linalg.solve(gram, design.T @ (weight * working))
        except np.linalg.LinAlgError:
            break
        if float(np.max(np.abs(updated - beta))) < 1e-12:
            beta = updated
            break
        beta = updated
    eta = np.clip(design @ beta, -CLIP, CLIP)
    probability = 1.0 / (1.0 + np.exp(-eta))
    weight = np.maximum(probability * (1.0 - probability), 1e-12)
    gram = design.T @ (design * weight[:, None])
    bread = np.linalg.inv(gram)
    residual = (outcome - probability)[:, None] * design
    order = np.argsort(seller, kind="mergesort")
    ordered_seller = seller[order]
    ordered = residual[order]
    cuts = np.flatnonzero(np.r_[True, ordered_seller[1:] != ordered_seller[:-1]])
    scores = np.add.reduceat(ordered, cuts)
    meat = scores.T @ scores
    covariance = bread @ meat @ bread
    se = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    return beta, se, int(scores.shape[0])


def _decile_error(probability: np.ndarray, outcome: np.ndarray) -> float | None:
    count = int(outcome.shape[0])
    if count < 10:
        return None
    order = np.argsort(probability, kind="mergesort")
    probability = probability[order]
    outcome = outcome[order]
    edges = np.linspace(0, count, 11, dtype=int)
    errors = []
    for index in range(10):
        left, right = int(edges[index]), int(edges[index + 1])
        if right <= left:
            continue
        errors.append(abs(float(outcome[left:right].mean()) - float(probability[left:right].mean())))
    return None if not errors else float(max(errors))


def _predict(beta: np.ndarray, gap: np.ndarray) -> np.ndarray:
    eta = np.clip(beta[0] + beta[1] * gap, -CLIP, CLIP)
    return 1.0 / (1.0 + np.exp(-eta))


def _block(name: str, gap: np.ndarray, outcome: np.ndarray, seller: np.ndarray, dates: np.ndarray) -> dict:
    beta, se, clusters = _fit(gap, outcome, seller)
    unique = np.unique(seller)
    unique.sort()
    held = set(unique[::5].tolist())
    train = np.array([item not in held for item in seller])
    beta_seller, _, _ = _fit(gap[train], outcome[train], seller[train])
    seller_error = _decile_error(_predict(beta_seller, gap[~train]), outcome[~train])
    early = dates < "2013-01-01"
    if int(early.sum()) > 20 and int((~early).sum()) > 20:
        beta_time, _, _ = _fit(gap[early], outcome[early], seller[early])
        time_error = _decile_error(_predict(beta_time, gap[~early]), outcome[~early])
        time_n = int((~early).sum())
    else:
        time_error = None
        time_n = int((~early).sum())
    return {
        "intercept": float(beta[0]),
        "gap": float(beta[1]),
        "se_intercept_clustered": float(se[0]),
        "se_gap_clustered": float(se[1]),
        "n": int(outcome.shape[0]),
        "clusters": clusters,
        "outcome": name,
        "ci95_intercept": [float(beta[0] - Z_95 * se[0]), float(beta[0] + Z_95 * se[0])],
        "ci95_gap": [float(beta[1] - Z_95 * se[1]), float(beta[1] + Z_95 * se[1])],
        "holdout_seller": {
            "n_test": int((~train).sum()),
            "max_abs_decile_error": seller_error,
            "split": "every fifth seller id in sorted order, fit on the rest",
        },
        "holdout_time": {
            "n_test": time_n,
            "max_abs_decile_error": time_error,
            "split": "train on first-buyer-offer dates before 2013-01-01",
        },
    }


def main() -> int:
    lists = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LISTS
    threads = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_THREADS
    print(f"listings {lists}", flush=True)
    listings = load_listings(lists)
    print(f"business and industrial listings {len(listings)}", flush=True)
    rows, dropped_above_bin = load_threads(threads, listings)
    print(f"threads {len(rows)} dropped_above_bin {dropped_above_bin}", flush=True)
    if len(rows) < 100:
        print("too few threads; refusing to write coefficients", file=sys.stderr)
        return 1
    gap = np.asarray([row["gap"] for row in rows], dtype=np.float64)
    print(
        "gap percentiles",
        {
            "p10": float(np.quantile(gap, 1 / 10)),
            "p50": float(np.quantile(gap, 1 / 2)),
            "p90": float(np.quantile(gap, 9 / 10)),
        },
        "share negative",
        float(np.mean(gap < 0)),
        flush=True,
    )
    seller = np.asarray([row["seller"] for row in rows], dtype=np.int64)
    dates = np.asarray([row["date"] for row in rows])
    no_deal = np.asarray([row["no_deal"] for row in rows], dtype=np.float64)
    seller_ended = np.asarray([row["seller_ended"] for row in rows], dtype=np.float64)
    no_deal_block = _block(
        "thread ended with no accepted offer, including threads the buyer ended",
        gap,
        no_deal,
        seller,
        dates,
    )
    seller_block = _block(
        "seller declined, auto-declined, or let the buyer's last offer expire; deals and buyer-ended threads are 0",
        gap,
        seller_ended,
        seller,
        dates,
    )
    payload = {
        "used_for_decision": "seller_ended",
        "seller_ended": seller_block,
        "thread_no_deal": no_deal_block,
        "provenance": {
            "paper": "Backus, Blake, Larsen, and Tadelis (2020), Quarterly Journal of Economics",
            "doi": "10.1093/qje/qjaa003",
            "nber": "w24306",
            "data_page": "https://www.nber.org/research/data/best-offer-sequential-bargaining",
            "threads_url": "https://data.nber.org/data-appendix/w24306/bargaining/anon_bo_threads.csv.gz",
            "lists_url": "https://data.nber.org/data-appendix/w24306/bargaining/anon_bo_lists.csv.gz",
            "dataverse_doi": "10.7910/DVN/ZOWWO7",
            "licence": "NBER files are cleared for research purposes; cite the paper. The Dataverse record is CC0, and the large files are hosted off Dataverse.",
            "fit_date": date.today().isoformat(),
            "category": "Business & Industrial",
            "meta_categ_id": 12576,
            "sample": (
                "Threads whose listing meta category is 12576, with a positive "
                "Buy-It-Now price and a positive first buyer offer at or below "
                "that price. Gap is 1 minus the offer over the Buy-It-Now price. "
                "Offers above the Buy-It-Now are dropped: a few ratios reach the "
                "thousands and a logit on the raw gap has a slope near zero even "
                "though the seller-ended rate rises with the discount."
            ),
            "dropped_above_bin": dropped_above_bin,
            "cluster": "anon_slr_id",
            "standard_errors": "sandwich, clustered by seller, bread from the IRLS Hessian",
            "decision_terms": ["quote_gap"],
            "partial": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)", flush=True)
    print(json.dumps({"seller_ended": seller_block, "thread_no_deal": no_deal_block}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
