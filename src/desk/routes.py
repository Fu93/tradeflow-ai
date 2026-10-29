"""Authenticated desk routes. Registered on the existing /api/v1 router."""
from __future__ import annotations

import asyncio
import json
import queue
import re
import ssl
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from src.app.security import require_desk_mutation
from src.desk.mailbox import MailboxError
from src.desk.ingest import deal_lock
from src.config.heuristics import (
    ADDRESS_CHANGE_MAX_BYTES,
    AUDIT_VERIFY_DEFAULT_LIMIT,
    AUDIT_VERIFY_MAX_LIMIT,
    BREAKDOWN_TRIP_THRESHOLD,
    DEAL_CURRENCY,
    DEAL_MAX_SUPPLIERS,
    DEAL_MIN_SUPPLIERS,
    FIELD_MAX_CHARS,
    HTTP_UNAVAILABLE,
    RISK_TOLERANCE_FLOOR,
    RISK_TOLERANCE_MAX,
)
from src.desk.audit_chain import ChainKeyMissing
from src.desk.input_checks import require_address, require_contact, require_plain
from src.desk.names import supplier_name_key
from src.desk.service import (
    BUS,
    DeskRejected,
    DraftStale,
    SendRefused,
    accept_review,
    confirm_keep_supplier,
    confirm_policy,
    confirm_supplier_address,
    create_deal,
    ingest_message,
    migrate_deal,
    record_outcome,
    reanchor_deal,
    recover_anchor_history,
    reopen_supplier,
    set_assumptions,
    set_offer_mode,
    poll_gmail_once,
    poll_imap_once,
    publish,
    send_draft,
)
from src.desk.store import DeskUnavailable
from src.desk.store import get_store

_ID = re.compile(r"^[a-f0-9]{32}$")


class SupplierIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    address: str = Field(min_length=1, max_length=320)
    channel: str = Field(default="email", max_length=40)

    @model_validator(mode="after")
    def _contact(self):
        # Email suppliers need one addr-spec; chat suppliers (WeChat, WhatsApp)
        # need a one-line handle, and their drafts are sent by hand.
        self.channel = (self.channel or "email").strip().lower() or "email"
        self.address = require_contact(self.address, self.channel)
        return self


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
    currency: Literal["CNY", "USD", "EUR"] = cast(Literal["CNY", "USD", "EUR"], DEAL_CURRENCY)
    suppliers: list[SupplierIn]

    @field_validator("product")
    @classmethod
    def _product(cls, value: str) -> str:
        return require_plain(value, "Product")

    @field_validator("suppliers")
    @classmethod
    def _supplier_count(cls, value: list[SupplierIn]) -> list[SupplierIn]:
        # The decision compares each supplier with the others, so a deal needs
        # four or five. Say so in words rather than as a list-length error.
        if len(value) < DEAL_MIN_SUPPLIERS or len(value) > DEAL_MAX_SUPPLIERS:
            raise ValueError(
                f"A deal needs {DEAL_MIN_SUPPLIERS} or {DEAL_MAX_SUPPLIERS} suppliers, one per line; "
                f"this has {len(value)}."
            )
        return value

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
    # A price the person typed (28塊5 -> 28.5) and the words it came from.
    typed_amount: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    typed_words: str | None = Field(default=None, max_length=80)


class ApprovalSignatureIn(BaseModel):
    """Ed25519 signature over {deal_id, draft_id, body_sha256}.

    Produced client-side by `python -m src.desk.approval_signing sign`.
    The private key never reaches the server.
    """

    model_config = ConfigDict(extra="forbid")
    alg: Literal["ed25519"]
    pubkey: str = Field(min_length=64, max_length=64)
    sig: str = Field(min_length=128, max_length=128)


class SendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    human_approved: StrictBool
    body_sha256: str = Field(min_length=64, max_length=64)
    approval: ApprovalSignatureIn | None = None


class AddressIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str = Field(min_length=1, max_length=320)
    name: str | None = Field(default=None, max_length=200)
    human_confirmed: StrictBool
    # [SECURITY 2026-10-23] Optional field, required by policy. A supplier
    # address change redirects the next counter, so when the desk has a pinned
    # approval key (or runs in production) it must carry a signature over
    # {deal_id, supplier_id, address} from that key.
    address_change: dict | None = Field(default=None, max_length=ADDRESS_CHANGE_MAX_BYTES)

    @field_validator("address")
    @classmethod
    def _address(cls, value: str) -> str:
        return require_address(value)


class MigrateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    human_confirmed: StrictBool


class PolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    human_confirmed: StrictBool
    product: str = Field(min_length=1, max_length=200)
    quantity: int = Field(ge=1, le=10_000_000)
    target_price: float = Field(gt=0, allow_inf_nan=False)
    ceiling_price: float = Field(gt=0, allow_inf_nan=False)
    risk_tolerance: float = Field(ge=RISK_TOLERANCE_FLOOR, le=RISK_TOLERANCE_MAX, allow_inf_nan=False)
    currency: Literal["CNY", "USD", "EUR"] = cast(Literal["CNY", "USD", "EUR"], DEAL_CURRENCY)

    @field_validator("product")
    @classmethod
    def _product(cls, value: str) -> str:
        return require_plain(value, "Product")


class ReanchorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    human_confirmed: StrictBool


class ReopenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    human_confirmed: StrictBool


class OfferModeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["default", "push"]
    human_confirmed: StrictBool


class KeepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str = Field(min_length=1, max_length=320)
    name: str | None = Field(default=None, max_length=200)
    human_confirmed: StrictBool

    @field_validator("address")
    @classmethod
    def _address(cls, value: str) -> str:
        return require_address(value)


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
    # D is a ratio of the first ask, strictly between 0 and 1; 1 breaks the Beta prior.
    reserve_prior_mean: float | None = Field(default=None, gt=0, lt=1, allow_inf_nan=False)
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
        except DeskUnavailable as exc:
            raise HTTPException(status_code=int(HTTP_UNAVAILABLE), detail=str(exc)) from exc
        except ChainKeyMissing as exc:
            raise HTTPException(
                status_code=int(HTTP_UNAVAILABLE),
                detail="desk sends disabled: chain key missing",
            ) from exc
        except DeskRejected as exc:
            raise _rejected(exc) from exc

    @api_v1.get("/desk/deals/{deal_id}")
    def get_deal(deal_id: str, _: str = Depends(require_desk_mutation)):
        _ident(deal_id)
        store = get_store()
        if store.get_deal(deal_id) is None:
            raise HTTPException(status_code=404, detail="No such deal")
        snapshot = store.get_snapshot(deal_id)
        if snapshot is None or any("conversation" not in row for row in snapshot.get("suppliers", [])):
            with deal_lock(deal_id):
                snapshot = store.get_snapshot(deal_id)
                if snapshot is None or any("conversation" not in row for row in snapshot.get("suppliers", [])):
                    snapshot = publish(store, deal_id)
        from src.desk.transmission import send_policy

        return {**snapshot, "send_policy": send_policy()}

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
                typed_amount=body.typed_amount,
                typed_words=body.typed_words,
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

    @api_v1.post("/desk/deals/{deal_id}/migrate")
    def post_migrate(deal_id: str, body: MigrateIn, _: str = Depends(require_desk_mutation)):
        _ident(deal_id)
        try:
            return migrate_deal(get_store(), deal_id, human_confirmed=body.human_confirmed)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="No such deal") from exc
        except DeskRejected as exc:
            raise _rejected(exc) from exc

    @api_v1.post("/desk/deals/{deal_id}/policy")
    def post_policy(deal_id: str, body: PolicyIn, _: str = Depends(require_desk_mutation)):
        _ident(deal_id)
        try:
            return confirm_policy(
                get_store(),
                deal_id,
                human_confirmed=body.human_confirmed,
                product=body.product,
                quantity=body.quantity,
                target_price=body.target_price,
                ceiling_price=body.ceiling_price,
                risk_tolerance=body.risk_tolerance,
                currency=body.currency,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="No such deal") from exc
        except DeskRejected as exc:
            raise _rejected(exc) from exc

    @api_v1.post("/desk/deals/{deal_id}/reanchor")
    def post_reanchor(deal_id: str, body: ReanchorIn, _: str = Depends(require_desk_mutation)):
        _ident(deal_id)
        try:
            return reanchor_deal(get_store(), deal_id, human_confirmed=body.human_confirmed)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="No such deal") from exc
        except DeskRejected as exc:
            raise _rejected(exc) from exc

    @api_v1.post("/desk/deals/{deal_id}/anchor-history/recover")
    def post_anchor_history_recover(deal_id: str, body: ReanchorIn, _: str = Depends(require_desk_mutation)):
        _ident(deal_id)
        try:
            return recover_anchor_history(get_store(), deal_id, human_confirmed=body.human_confirmed)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="No such deal") from exc
        except DeskRejected as exc:
            raise _rejected(exc) from exc

    @api_v1.post("/desk/deals/{deal_id}/suppliers/{supplier_id}/address")
    def post_address(
        deal_id: str,
        supplier_id: str,
        body: AddressIn,
        _: str = Depends(require_desk_mutation),
    ):
        _ident(deal_id)
        _ident(supplier_id)
        try:
            return confirm_supplier_address(
                get_store(),
                deal_id,
                supplier_id,
                address=body.address,
                name=body.name,
                human_confirmed=body.human_confirmed,
                address_change=body.address_change,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="No such deal") from exc
        except DeskRejected as exc:
            raise _rejected(exc) from exc

    @api_v1.post("/desk/deals/{deal_id}/suppliers/{supplier_id}/keep")
    def post_keep(
        deal_id: str,
        supplier_id: str,
        body: KeepIn,
        _: str = Depends(require_desk_mutation),
    ):
        _ident(deal_id)
        _ident(supplier_id)
        try:
            return confirm_keep_supplier(
                get_store(),
                deal_id,
                supplier_id,
                address=body.address,
                name=body.name,
                human_confirmed=body.human_confirmed,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="No such deal") from exc
        except DeskRejected as exc:
            raise _rejected(exc) from exc

    @api_v1.post("/desk/deals/{deal_id}/suppliers/{supplier_id}/reopen")
    def post_reopen(
        deal_id: str,
        supplier_id: str,
        body: ReopenIn,
        _: str = Depends(require_desk_mutation),
    ):
        _ident(deal_id)
        _ident(supplier_id)
        try:
            return reopen_supplier(
                get_store(),
                deal_id,
                supplier_id,
                human_confirmed=body.human_confirmed,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="No such deal") from exc
        except DeskRejected as exc:
            raise _rejected(exc) from exc

    @api_v1.post("/desk/deals/{deal_id}/suppliers/{supplier_id}/offer-mode")
    def post_offer_mode(
        deal_id: str,
        supplier_id: str,
        body: OfferModeIn,
        _: str = Depends(require_desk_mutation),
    ):
        _ident(deal_id)
        _ident(supplier_id)
        try:
            return set_offer_mode(
                get_store(),
                deal_id,
                supplier_id,
                body.mode,
                human_confirmed=body.human_confirmed,
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
        except DeskRejected as exc:
            raise _rejected(exc) from exc

    @api_v1.post("/desk/drafts/{draft_id}/send")
    async def post_send(draft_id: str, body: SendIn, _: str = Depends(require_desk_mutation)):
        _ident(draft_id)
        try:
            return await send_draft(
                get_store(),
                draft_id,
                human_approved=body.human_approved,
                body_sha256=body.body_sha256,
                approval=body.approval.model_dump() if body.approval is not None else None,
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
    def get_audit_verify(
        deal_id: str,
        include_entries: bool = False,
        offset: int = 0,
        limit: int = AUDIT_VERIFY_DEFAULT_LIMIT,
        _: str = Depends(require_desk_mutation),
    ):
        """Verify the chain and return a summary, not the whole chain.

        [PERF 2026-10-23] This endpoint returned every entry with its payload.
        At 100,000 entries that is a 36.6 MB response built under the store lock,
        and verification is O(n) — so the audit surface got slower and heavier
        the more trustworthy the chain became, which is backwards. The default
        answer is now the verdict plus a head and a count; entries are opt-in and
        paginated. ``/evidence.json`` remains the full export.
        """
        _ident(deal_id)
        store = get_store()
        if store.get_deal(deal_id) is None:
            raise HTTPException(status_code=404, detail="No such deal")
        result = store.verify_chain(deal_id)
        entries = list(result.get("entries") or [])
        summary = {
            "ok": result.get("ok"),
            "broken_seq": result.get("broken_seq"),
            "head": result.get("head"),
            "count": result.get("count", len(entries)),
            "anchor": result.get("anchor"),
            "reason": result.get("reason"),
        }
        if not include_entries:
            return summary
        window = max(0, min(int(limit), AUDIT_VERIFY_MAX_LIMIT))
        start = max(0, int(offset))
        summary["entries"] = entries[start:start + window]
        summary["offset"] = start
        summary["limit"] = window
        summary["truncated"] = start + window < len(entries)
        return summary

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
