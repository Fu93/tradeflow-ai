# TradeFlow — Product Specification Baseline & Should-Cost Reference

> **Document type**: domain-expert maintained baseline (**not** auto-refreshed).  
> **Companion file**: `market_anchor.md` (auto-refreshed real customs data).  
> **Last reviewed**: 2026-10-14

## Purpose

This file tells the agent what a **correctly specified** product looks like, and what
each cost component contributes. Its job is to let the agent answer one question:

> *"When a factory says 'we can do it cheaper if we change X', is X a real cost saving
> or a specification downgrade that transfers risk to the buyer?"*

## Honest provenance statement

Every figure below is tagged with one of these labels. **Do not blur them when answering.**

| Label | Meaning |
|---|---|
| `[STANDARD]` | Publicly documented industry standard or widely published norm |
| `[EXPERT]` | Practitioner estimate for coastal-China manufacturing; order-of-magnitude only |
| `[DERIVED]` | Calculated from the figures in this table; arithmetic is shown |
| `[VERIFY]` | Must be confirmed against a supplier's written quotation before relying on it |

**This file is not a price list.** It is a structural model. Actual quotations vary by
factory, cluster, order volume, season, and payment terms.

---

## 1. Reference product: 21-inch auto open/close three-fold umbrella

Chosen because it is a genuinely common export SKU and has a well-understood cost structure.

### 1.1 Bill of materials

| # | Component | Specification | Consumption | Basis of estimate |
|---|---|---|---|---|
| 1 | Canopy fabric | 210T pongee, black coating | 1.25 yards/pc | `[EXPERT]` |
| 2 | Ribs | 8-rib fibreglass composite | 110 g/pc | `[EXPERT]` |
| 3 | Shaft | Cold-rolled hexagonal steel | 140 g/pc | `[EXPERT]` |
| 4 | Handle / top cap | Virgin ABS | 65 g/pc | `[EXPERT]` |
| 5 | Injection moulding | 160T machine, 4-cavity | 24 s/cycle | `[EXPERT]` |
| 6 | Assembly | Sewing, panelling, finishing | SMV = 12 min | `[EXPERT]` |

### 1.2 Cost build-up

| Component | Calculation | Cost (CNY/pc) | Label |
|---|---|---|---|
| Canopy fabric | 1.25 yd × ¥4.80/yd × 1.06 waste | ¥6.36 | `[EXPERT]` |
| Ribs (fibreglass) | 0.110 kg × ¥18.50/kg × 1.03 | ¥2.10 | `[EXPERT]` |
| Shaft (steel) | 0.140 kg × ¥4.60/kg × 1.04 | ¥0.67 | `[EXPERT]` |
| Plastic parts | 0.065 kg × ¥9.80/kg × 1.05 | ¥0.67 | `[EXPERT]` |
| Injection moulding | (24 s ÷ 3600) × ¥45.00/hr ÷ 4 cavities | ¥0.075 | `[EXPERT]` |
| Assembly labour | 12 min × ¥26.00/hr ÷ 60 | ¥5.20 | `[EXPERT]` |
| **Direct manufacturing cost** | sum of above | **¥15.075** | `[DERIVED]` |
| Factory overhead | × 1.08 | ¥16.28 | `[DERIVED]` |
| Fair margin | × 1.10 | **¥17.91** | `[DERIVED]` |

> **Interpretation**: ¥17.91 is a *fair should-cost* for a 210T, 8-rib fibreglass,
> virgin-ABS umbrella. A quotation materially below this should trigger the question:
> **which of the six line items was silently reduced?**

---

## 2. Specification downgrade catalogue

This is the core defensive knowledge. Each row maps a **cost-saving phrase** a factory
might use to the **engineering consequence** and the **negotiation response**.

| Downgrade | Typical phrasing | What actually changes | Risk transferred to buyer | Required response |
|---|---|---|---|---|
| Fabric substitution | 「換普通料」「聚酯布就行」 | 210T pongee → lower-density or uncoated polyester | UV protection, water repellency, print adhesion | Demand fabric spec + coating weight in writing; request a physical swatch |
| Recycled plastics | 「回料」「水口料」「二料」 | Virgin ABS → regrind/recycled regrind | Structural cracking, colour inconsistency, UV embrittlement | Require virgin-material declaration; request material certificate |
| Rib downgrade | 「傘骨換普通料」 | Fibreglass composite → plain steel or thin aluminium | Wind inversion, rust, fatigue failure | Specify rib count, material, and wind-resistance test |
| Steel grade cut | 「201 不鏽鋼就行」 | 304 → 201 stainless | Corrosion resistance (201 is markedly inferior in coastal/humid use) | Name the grade explicitly in the PO |
| Thickness reduction | 「薄一點點」「放寬兩個絲」 | Reduced wall thickness / tighter-than-agreed tolerance | Failure under load; AQL disputes later | Fix nominal + tolerance in writing |
| Coating reduction | 「塗層少一道」 | Fewer coating passes | Appearance, UV, water resistance | Specify coating layers and test method |
| Component swap | 「配件用國產的」 | Branded/qualified part → unbranded equivalent | Interchangeability, warranty | Require part number and supplier approval |

### 2.1 Why this matters commercially

A downgrade is not a price concession — it is a **risk transfer**. The unit price falls
by a few percent while the buyer absorbs warranty, return, and reputational risk
denominated in a different and much larger unit (a container of failed product).

`[EXPERT]` The asymmetry is routinely an order of magnitude: saving ¥0.50/pc across
10,000 pcs is ¥5,000, while one rejected container can exceed that many times over.

---

## 3. Non-price terms and their economic meaning

Non-price terms are where most of the real value sits. The engine normalises them into
an effective unit cost (`src/engine/tco.py`).

| Term | Buyer's exposure | Why it matters more than a small price move |
|---|---|---|
| **Lead time** | Stockout, ad-ranking loss | `[EXPERT]` Beyond a buffer of ~5 days, delay cost rises super-linearly for e-commerce sellers |
| **MOQ** | Excess inventory, markdown risk | Ties up working capital in goods that may not sell |
| **Deposit %** | Counterparty / default risk | `[EXPERT]` 30% is normal; 80%+ materially shifts risk to the buyer |
| **Balance timing** | Quality leverage | Paying before inspection removes the buyer's only real remedy |
| **Tax treatment** | Hidden cost | 「未稅」= ex-tax; the real cost is the quoted price plus the tax point |
| **Packaging** | Hidden cost | Cartons, pallets, fumigation, marking are frequently excluded from the headline price |
| **Incoterm** | Freight + risk transfer point | EXW vs FOB vs CIF vs DDP differ substantially in who pays and who bears risk |
| **Spec lock** | Everything above | Without a frozen spec, no other term is enforceable |

---

## 4. Questions the agent must always be able to answer

When reviewing a supplier message, the agent should be able to state which of these are
**known**, which are **assumed**, and which are **missing**:

1. Exact product specification and item code
2. Quoted unit price and its **basis** (tax-inclusive? packaging included? Incoterm?)
3. Currency and the FX rate used
4. MOQ and whether tiered pricing applies
5. Lead time, and the **trigger event** it is measured from
6. Deposit percentage and balance-release conditions
7. Validity period of the quotation
8. Whether the specification is **unchanged** from the previous quotation

> **Rule**: If an item is unknown, the agent must say so plainly rather than assume it.
> An assumed term presented as a known term is the single most damaging failure mode
> in this domain.

---

## 5. Maintenance

`market_anchor.md` refreshes automatically. **This file does not** — the BOM figures are
practitioner estimates that require domain review, not public data.

When updating:
- Keep quantity and price **separate** so waste factors stay visible.
- Tag every new figure with one of the labels above.
- Never replace `[EXPERT]` with an invented precise number to look more rigorous.
  A labelled estimate is more credible than an unlabelled false precision.

---

*Related: `compliance_policy.md` (negotiation red lines) · `market_anchor.md` (live trade data)*
