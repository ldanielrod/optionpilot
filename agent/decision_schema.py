"""The JSON decision log the LLM must return, and its parser.

The decision log is evidence, not authority: execution truth always comes
from the broker via core/reconcile.py. But a malformed log is itself a
mandate violation (the agent must show its work).
"""
import json
import re
from typing import Optional, Tuple

REQUIRED_FIELDS = ("action", "occ_symbol", "strategy", "qty", "limit_price", "thesis")

EXAMPLE = {
    "action": "placed",              # placed | no_trade
    "occ_symbol": "NVDA260904P00220000",
    "strategy": "CSP",
    "qty": 1,
    "limit_price": 1.84,
    "delta": -0.27,
    "iv": 0.41,
    "open_interest": 1250,
    "spread_pct": 0.04,
    "thesis": "2-3 sentences: why this strike/expiry over the alternatives.",
    "rejected_alternatives": [
        {"occ_symbol": "NVDA260904P00215000", "why": "delta 0.22 leaves premium on the table given IV skew"}
    ],
}

FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_decision(text: str) -> Tuple[Optional[dict], Optional[str]]:
    """Extract and validate the decision JSON from the LLM's final message.
    Returns (decision, error). A 'no_trade' decision only needs action+thesis."""
    if not text:
        return None, "empty response"
    m = FENCE_RE.search(text)
    raw = m.group(1) if m else None
    if raw is None:
        # maybe the whole message is bare JSON
        stripped = text.strip()
        if stripped.startswith("{"):
            raw = stripped
    if raw is None:
        return None, "no JSON block found"
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"invalid JSON: {e}"
    if not isinstance(d, dict):
        return None, "decision is not an object"
    if d.get("action") == "no_trade":
        if not d.get("thesis"):
            return None, "no_trade requires a thesis"
        return d, None
    missing = [f for f in REQUIRED_FIELDS if f not in d]
    if missing:
        return None, f"missing fields: {missing}"
    return d, None
