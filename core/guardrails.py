"""Post-hoc validation of what an executor (LLM or human) actually did.

Everything here works from broker truth (orders fetched via alpaca-py), never
from the LLM transcript. Violations are facts about orders, not opinions
about reasoning.
"""
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from core.mandate import OptionMandate
from core.occ import parse_occ


@dataclass
class Violation:
    code: str
    detail: str


def validate_order_against_mandate(order, mandate: OptionMandate,
                                   quote: Optional[dict] = None) -> List[Violation]:
    """Validate one broker order object against its mandate.
    quote: optional fresh {bid, ask, delta} for the contract, from the core's
    own data path (data/options.py) — used for delta and NBBO checks."""
    v: List[Violation] = []

    cid = getattr(order, "client_order_id", "") or ""
    if not cid.startswith(mandate.client_order_id):
        v.append(Violation("foreign_order_id",
                           f"order id {cid!r} not derived from mandate id"))

    occ = parse_occ(getattr(order, "symbol", "") or "")
    if occ is None:
        v.append(Violation("not_an_option", f"symbol {order.symbol!r}"))
        return v  # nothing else is checkable

    if occ.underlying != mandate.underlying:
        v.append(Violation("wrong_underlying",
                           f"{occ.underlying} != {mandate.underlying}"))

    expected_type = "put" if mandate.strategy == "CSP" else "call"
    if occ.contract_type != expected_type:
        v.append(Violation("wrong_contract_type",
                           f"{occ.contract_type} != {expected_type}"))

    qty = float(getattr(order, "qty", 0) or 0)
    if qty > mandate.qty:
        v.append(Violation("oversize", f"qty {qty} > mandated {mandate.qty}"))

    expected_side = "buy" if mandate.strategy == "LONG_CALL" else "sell"
    side = str(getattr(order, "side", "")).lower()
    if expected_side not in side:
        v.append(Violation("wrong_side", f"{side} != {expected_side}"))

    otype = str(getattr(order, "type", getattr(order, "order_type", ""))).lower()
    if "limit" not in otype:
        v.append(Violation("not_limit", f"order type {otype}"))

    dte = (occ.expiry - date.today()).days
    if not (mandate.dte_min <= dte <= mandate.dte_max):
        v.append(Violation("dte_out_of_band",
                           f"dte {dte} not in [{mandate.dte_min}, {mandate.dte_max}]"))

    if mandate.max_strike is not None and occ.strike > mandate.max_strike:
        v.append(Violation("strike_over_cap",
                           f"strike {occ.strike} > cap {mandate.max_strike}"))

    if quote is not None:
        d = quote.get("delta")
        if d is not None:
            lo, hi = mandate.delta_band
            # small tolerance: delta drifts between LLM read and our re-read
            if not (lo - 0.05 <= abs(d) <= hi + 0.05):
                v.append(Violation("delta_out_of_band",
                                   f"|delta| {abs(d):.3f} not in [{lo}, {hi}] (±0.05)"))
        limit = getattr(order, "limit_price", None)
        bid, ask = quote.get("bid", 0), quote.get("ask", 0)
        if limit is not None and bid > 0 and ask > 0:
            lp = float(limit)
            # generous NBBO band (quotes move): reject only clearly-off prices
            if not (bid * 0.5 <= lp <= ask * 1.5):
                v.append(Violation("price_off_market",
                                   f"limit {lp} vs NBBO [{bid}, {ask}]"))
    return v


def find_foreign_orders(orders, mandate: OptionMandate, session_start) -> list:
    """Orders submitted during the session that do NOT carry the mandated id —
    the LLM went off-script. These get cancelled unconditionally."""
    foreign = []
    for o in orders:
        cid = getattr(o, "client_order_id", "") or ""
        sub = getattr(o, "submitted_at", None)
        if sub is not None and sub < session_start:
            continue
        if not cid.startswith(mandate.client_order_id):
            foreign.append(o)
    return foreign
