"""Supplier name identity shared by the auction and the desk."""
from __future__ import annotations

import unicodedata


def supplier_name_key(name: str) -> str:
    """NFKC, casefold, one space between words, combining marks removed.

    Full-width letters match ASCII. A repeated space does not make a second
    supplier. Dotted capital I matches i.
    """
    folded = unicodedata.normalize("NFKC", str(name)).casefold()
    without_marks = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(without_marks.split())
