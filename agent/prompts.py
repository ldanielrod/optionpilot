"""Prompt construction for the LLM trader session."""
import json

from core.mandate import OptionMandate
from agent.decision_schema import EXAMPLE

SYSTEM_PROMPT = """You are the execution trader for OptionPilot, a hybrid \
options trading agent. A deterministic strategy core has already decided WHAT \
to do; your job is to pick the single best contract WITHIN the mandate below \
and execute it well. You are judged on selection quality: strike/expiry choice \
given the live chain, IV, greeks and liquidity.

Hard rules — the core independently audits every order you place and will \
cancel violations and revoke your execution privileges after repeated ones:
- Stay strictly inside the mandate: underlying, strategy, qty, delta band, \
DTE window, strike cap, liquidity floor.
- LIMIT orders only, priced inside the current NBBO.
- Use EXACTLY the client_order_id given in the mandate.
- Place at most ONE order (one contract; no spreads unless mandated).
- If nothing in the chain satisfies the mandate, do NOT stretch it: return a \
no_trade decision and say why.

Process: fetch the option chain for the mandated window, compare the 3-5 \
plausible candidates on premium-per-unit-risk, IV, spread and OI, then act. \
End your reply with the decision JSON in a ```json fence."""


def render_mandate(mandate: OptionMandate, execute: bool) -> str:
    strategy_desc = {
        "CSP": "SELL to open 1 cash-secured put",
        "CC": "SELL to open covered call(s) against held stock",
        "LONG_CALL": "BUY to open 1 call",
    }[mandate.strategy]
    lo, hi = mandate.delta_band
    action_line = (
        "Place the order now via place_option_order, confirm its status via "
        "get_orders, then output the decision JSON."
        if execute else
        "SHADOW MODE: do NOT place any order. Analyze the chain, pick the "
        "contract you would trade, and output the decision JSON with "
        '"action": "placed" replaced by "action": "shadow_pick".'
    )
    iv_line = (
        f"{mandate.min_iv:.1%} — the underlying's 20-day realized vol plus the "
        "required premium. A contract below this floor is not paying for the "
        "risk being taken; reject it however attractive it looks otherwise."
        if mandate.min_iv else "n/a"
    )
    return f"""## Mandate
- Strategy: {mandate.strategy} — {strategy_desc}
- Underlying: {mandate.underlying}
- Quantity: exactly {mandate.qty}
- |delta| band: {lo} to {hi}
- DTE window: {mandate.dte_min} to {mandate.dte_max} days
- Min open interest: {mandate.min_open_interest}
- Max bid-ask spread: {mandate.max_spread_pct_of_mid:.0%} of mid
- Max strike: {mandate.max_strike if mandate.max_strike is not None else "n/a"}
- Min implied vol: {iv_line}
- client_order_id (use verbatim): {mandate.client_order_id}

## Signal context (from the deterministic core)
{json.dumps(mandate.signal_context, indent=2)}

## Task
{action_line}

## Decision JSON format
```json
{json.dumps(EXAMPLE, indent=2)}
```"""
