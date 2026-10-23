"""Desk errors that callers are expected to catch.

These live in their own module because both the send gate (``desk/verify.py``)
and the service layer raise them, and neither may import the other. They were
defined in ``service.py``, which meant any module that wanted to catch
``SendRefused`` had to import the whole service layer — including the ingest
path, the extractor pool and the mailbox poller.

``service.py`` re-exports both names, so ``from src.desk.service import
SendRefused`` keeps working.
"""
from __future__ import annotations


class SendRefused(Exception):
    """A send was blocked. The reason is safe to show a person."""


class DraftStale(Exception):
    """The draft changed under the approver, or was already used."""
