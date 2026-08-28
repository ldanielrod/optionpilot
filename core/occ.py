"""OCC option symbol parsing (e.g. AAPL260904P00220000)."""
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

OCC_RE = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")


@dataclass
class OccParts:
    underlying: str
    expiry: date
    contract_type: str  # "call" | "put"
    strike: float


def parse_occ(symbol: str) -> Optional[OccParts]:
    m = OCC_RE.match(symbol.strip().upper())
    if not m:
        return None
    root, ymd, cp, strike = m.groups()
    try:
        expiry = date(2000 + int(ymd[0:2]), int(ymd[2:4]), int(ymd[4:6]))
    except ValueError:
        return None
    return OccParts(
        underlying=root,
        expiry=expiry,
        contract_type="call" if cp == "C" else "put",
        strike=int(strike) / 1000.0,
    )
