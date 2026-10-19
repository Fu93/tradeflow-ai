"""Authenticated desk routes. Registered on the existing /api/v1 router."""
from __future__ import annotations

import asyncio
import json
import queue
import re
import ssl
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from src.app.security import require_desk_mutation
from src.desk.mailbox import MailboxError
from src.config.heuristics import (
    BREAKDOWN_TRIP_THRESHOLD,
    DEAL_CURRENCY,
    FIELD_MAX_CHARS,
    RISK_TOLERANCE_FLOOR,
    RISK_TOLERANCE_MAX,
)
from src.desk.names import supplier_name_key
from src.desk.service import (
    BUS,
    DeskRejected,
    DraftStale,
    SendRefused,
    accept_review,
    create_deal,
    ingest_message,
    record_outcome,
    set_assumptions,
    poll_gmail_once,
    poll_imap_once,
    publish,
    send_draft,
)
from src.desk.store import get_store

_ID = re.compile(r"^[a-f0-9]{32}$")


class SupplierIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    address: str = Field(min_length=1, max_length=320)
    channel: str = Field(default="email", max_length=40)


class DealIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product: str = Field(min_length=1, max_length=200)
    quantity: int = Field(ge=1, le=10_000_000)
    target_price: float = Field(gt=0, allow_inf_nan=False)
    ceiling_price: float = Field(gt=0, allow_inf_nan=False)
    risk_tolerance: float | None = Field(
        default=None,
        ge=RISK_TOLERANCE_FLOOR,
        le=RISK_TOLERANCE_MAX,
        allow_inf_nan=False,
    )
    seed: int = Field(default=11, ge=0, le=2_000_000_000)
    currency: Literal["CNY", "USD", "EUR"] = DEAL_CURRENCY
    suppliers: list[SupplierIn] = Field(min_length=4, max_length=5)

    @model_validator(mode="after")
    def _bounds(self):
        if self.target_price > self.ceiling_price:
            raise ValueError("The target must be at or below the ceiling.")
        names = [supplier_name_key(item.name) for item in self.suppliers]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate supplier name. Each supplier needs its own name.")
        addresses = [item.address.strip().casefold() for item in self.suppliers]
        if len(addresses) != len(set(addresses)):
            raise ValueError("Duplicate supplier address. Each supplier needs its own address.")
        return self


class LlmFieldsIn(BaseModel):
    """Caller notes only. A price key is rejected by extra=forbid."""

    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=FIELD_MAX_CHARS)


class QuoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=40)
    raw_text: str = Field(min_length=1, max_length=20000)
    sender: str | None = Field(default=None, max_length=320)
    supplier_id: str | None = None
    llm_fields: LlmFieldsIn | None = None
    freight: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    duty_rate: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    fees: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    external_id: str | None = Field(default=None, max_length=500)
    simulated: bool = False
    page_url: str | None = Field(default=None, max_length=2000)
    page_title: str | None = Field(default=None, max_length=500)


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_id: str | None = None
    action: str = "accept"
    chosen_token: str | None = Field(default=None, max_length=80)


class SendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    human_approved: StrictBool
    body_sha256: str = Field(min_length=64, max_length=64)


class OutcomeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str
    quote: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    offer: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    agreed_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class AssumptionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hazard_intercept: float | None = Field(default=None, allow_inf_nan=False)
    hazard_tau: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    hazard_quote_gap: float | None = Field(default=None, allow_inf_nan=False)
    hazard_reserve_gap: float | None = Field(default=None, allow_inf_nan=False)
    bayes_obs_noise: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    unit_sanity_multiple: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    reserve_prior_mean: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    reserve_prior_std: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    expected_markup: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    search_cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    include_unverified: bool | None = None


class PollIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: str = "imap"
    deal_id: str | None = None


def _ident(value: str) -> str:
    if not _ID.fullmatch(value or ""):
        raise HTTPException(status_code=422, detail="Invalid id")
    return value


def _rejected(exc: DeskRejected) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def register_desk_routes(api_v1: APIRouter) -> None:
    @api_v1.post("/desk/deals")
    def post_deal(body: DealIn, _: str = Depends(require_desk_mutation)):
        try:
            return create_deal(
                get_store(),
                product=body.product,
                quantity=body.quantity,
                target_price=body.target_price,
                ceiling_price=body.ceiling_price,
                risk_tolerance=body.risk_tolerance if body.risk_tolerance is not None else BREAKDOWN_TRIP_THRESHOLD,
                seed=body.seed,
                currency=body.currency,
                suppliers=[item.model_dump() for item in body.suppliers],
            )
        except DeskRejected as exc:
            raise _rejected(exc) from exc

    @api_v1.get("/desk/deals/{deal_id}")
    def get_deal(deal_id: str, _: str = Depends(require_desk_mutation)):
        _ident(deal_id)
        store = get_store()
        if store.get_deal(deal_id) is None:
            raise HTTPException(status_code=404, detail="No such deal")
        snapshot = store.get_snapshot(deal_id)
        if snapshot is None:
            snapshot = publish(store, deal_id)
        return snapshot

    @api_v1.post("/desk/deals/{deal_id}/quotes")
    def post_quote(deal_id: str, body: QuoteIn, _: str = Depends(require_desk_mutation)):
        _ident(deal_id)
        if body.supplier_id is not None:
            _ident(body.supplier_id)
        try:
            result = ingest_message(
                get_store(),
                deal_id,
                source=body.source,
                raw_text=body.raw_text,
                sender=body.sender,
                supplier_id=body.supplier_id,
                llm_fields=None if body.llm_fields is None else body.llm_fields.model_dump(),
                freight=body.freight,
                duty_rate=body.duty_rate,
                fees=body.fees,
                external_id=body.external_id,
                provenance_extra={
                    "page_url": body.page_url,
                    "page_title": body.page_title,
                    "simulated": body.simulated,
                },
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="No such deal") from exc
        except DeskRejected as exc:
            raise _rejected(exc) from exc
        return result

    @api_v1.post("/desk/deals/{deal_id}/review/{event_id}")
    def post_review(deal_id: str, event_id: str, body: ReviewIn, _: str = Depends(require_desk_mutation)):
        _ident(deal_id)
        _ident(event_id)
        if body.action not in {"accept", "reject"}:
            raise HTTPException(status_code=422, detail="Review action must be accept or reject.")
        if body.supplier_id is not None:
            _ident(body.supplier_id)
        try:
            return accept_review(
                get_store(),
                deal_id,
                event_id,
                body.supplier_id,
                chosen_token=body.chosen_token,
                reject=body.action == "reject",
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="No such review item") from exc
        except DeskRejected as exc:
            raise _rejected(exc) from exc

    @api_v1.post("/desk/deals/{deal_id}/suppliers/{supplier_id}/outcome")
    def post_outcome(
        deal_id: str,
        supplier_id: str,
        body: OutcomeIn,
        _: str = Depends(require_desk_mutation),
    ):
        _ident(deal_id)
        _ident(supplier_id)
        try:
            return record_outcome(
                get_store(),
                deal_id,
                supplier_id,
                outcome=body.outcome,
                quote=body.quote,
                offer=body.offer,
                agreed_price=body.agreed_price,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="No such deal") from exc
        except DeskRejected as exc:
            raise _rejected(exc) from exc

    @api_v1.post("/desk/deals/{deal_id}/assumptions")
    def post_assumptions(deal_id: str, body: AssumptionsIn, _: str = Depends(require_desk_mutation)):
        _ident(deal_id)
        fields = body.model_dump(exclude_unset=True)
        if "include_unverified" in fields and fields["include_unverified"] is not None:
            fields["include_unverified"] = 1 if fields["include_unverified"] else 0
        try:
            return set_assumptions(get_store(), deal_id, fields)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="No such deal") from exc

    @api_v1.post("/desk/drafts/{draft_id}/send")
    async def post_send(draft_id: str, body: SendIn, _: str = Depends(require_desk_mutation)):
        _ident(draft_id)
        try:
            return await send_draft(
                get_store(),
                draft_id,
                human_approved=body.human_approved,
                body_sha256=body.body_sha256,
            )
        except SendRefused as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except DraftStale as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="No such draft") from exc

    @api_v1.post("/desk/poll")
    def post_poll(body: PollIn, _: str = Depends(require_desk_mutation)):
        if body.deal_id is not None:
            _ident(body.deal_id)
        try:
            if body.channel == "gmail":
                return poll_gmail_once(get_store(), deal_id=body.deal_id)
            if body.channel != "imap":
                raise HTTPException(status_code=422, detail="Poll channel must be imap or gmail.")
            return poll_imap_once(get_store(), deal_id=body.deal_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="No such deal") from exc
        except MailboxError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ssl.SSLError as exc:
            raise HTTPException(
                status_code=502,
                detail="The mail server certificate could not be verified.",
            ) from exc

    @api_v1.get("/desk/deals/{deal_id}/audit/verify")
    def get_audit_verify(deal_id: str, _: str = Depends(require_desk_mutation)):
        _ident(deal_id)
        store = get_store()
        if store.get_deal(deal_id) is None:
            raise HTTPException(status_code=404, detail="No such deal")
        return store.verify_chain(deal_id)

    @api_v1.get("/desk/deals/{deal_id}/evidence.json")
    def get_evidence(deal_id: str, _: str = Depends(require_desk_mutation)):
        _ident(deal_id)
        store = get_store()
        if store.get_deal(deal_id) is None:
            raise HTTPException(status_code=404, detail="No such deal")
        chain = store.verify_chain(deal_id)
        return {
            "deal_id": deal_id,
            "ok": chain["ok"],
            "head": chain["head"],
            "entries": chain["entries"],
        }

    @api_v1.get("/desk/deals/{deal_id}/stream")
    async def stream_deal(deal_id: str, _: str = Depends(require_desk_mutation)):
        _ident(deal_id)
        store = get_store()
        if store.get_deal(deal_id) is None:
            raise HTTPException(status_code=404, detail="No such deal")
        snapshot = store.get_snapshot(deal_id) or {}
        mailbox_queue = BUS.subscribe(deal_id)

        async def generate():
            try:
                yield _frame(snapshot)
                while True:
                    try:
                        item = await asyncio.to_thread(mailbox_queue.get, True, 5)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    yield _frame(item)
            finally:
                BUS.unsubscribe(deal_id, mailbox_queue)

        return StreamingResponse(generate(), media_type="text/event-stream")


def _frame(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False, default=str) + "\n\n"
