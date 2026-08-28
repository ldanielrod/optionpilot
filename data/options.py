"""Option chain / quote access via alpaca-py.

Used by the deterministic executor and by the guardrail validator. The LLM
agent reaches the same data through Alpaca's MCP server instead — this module
is the core's independent view, never shared with the LLM.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.trading.enums import AssetStatus, ContractType
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionSnapshotRequest


@dataclass
class ContractQuote:
    occ_symbol: str
    underlying: str
    contract_type: str      # "put" | "call"
    strike: float
    expiry: date
    dte: int
    open_interest: int
    bid: float
    ask: float
    mid: float
    spread_abs: float
    spread_pct_of_mid: float
    delta: Optional[float]  # None when the feed has no greeks (illiquid strikes)
    iv: Optional[float]


class OptionsData:
    def __init__(self, api_key: str, api_secret: str, paper: bool = True):
        self.trading = TradingClient(api_key, api_secret, paper=paper)
        self.data = OptionHistoricalDataClient(api_key, api_secret)

    def get_contracts(self, underlying: str, contract_type: str,
                      dte_min: int, dte_max: int, limit: int = 300) -> list:
        """Raw contracts (strike/expiry/OI) inside the DTE window."""
        today = date.today()
        req = GetOptionContractsRequest(
            underlying_symbols=[underlying],
            status=AssetStatus.ACTIVE,
            type=ContractType.PUT if contract_type == "put" else ContractType.CALL,
            expiration_date_gte=today + timedelta(days=dte_min),
            expiration_date_lte=today + timedelta(days=dte_max),
            limit=limit,
        )
        return self.trading.get_option_contracts(req).option_contracts or []

    def get_quotes(self, occ_symbols: List[str]) -> Dict[str, dict]:
        """Snapshot (latest quote + greeks + IV) per OCC symbol."""
        if not occ_symbols:
            return {}
        snaps = self.data.get_option_snapshot(
            OptionSnapshotRequest(symbol_or_symbols=occ_symbols))
        out = {}
        for sym, s in snaps.items():
            q = s.latest_quote
            g = s.greeks
            out[sym] = {
                "bid": float(q.bid_price) if q and q.bid_price else 0.0,
                "ask": float(q.ask_price) if q and q.ask_price else 0.0,
                "delta": float(g.delta) if g and g.delta is not None else None,
                "iv": float(s.implied_volatility) if s.implied_volatility else None,
            }
        return out

    def get_candidates(self, underlying: str, contract_type: str,
                       dte_min: int, dte_max: int,
                       min_open_interest: int) -> List[ContractQuote]:
        """Contracts in the DTE window that pass the OI filter, enriched with
        quotes/greeks and spread metrics. Batches snapshot calls at 100 symbols."""
        contracts = self.get_contracts(underlying, contract_type, dte_min, dte_max)
        liquid = [c for c in contracts
                  if (c.open_interest or 0) and int(c.open_interest) >= min_open_interest]
        today = date.today()
        quotes: Dict[str, dict] = {}
        symbols = [c.symbol for c in liquid]
        for i in range(0, len(symbols), 100):
            quotes.update(self.get_quotes(symbols[i:i + 100]))

        out = []
        for c in liquid:
            q = quotes.get(c.symbol)
            if not q or q["bid"] <= 0 or q["ask"] <= 0:
                continue  # dead quote: not tradeable right now
            mid = (q["bid"] + q["ask"]) / 2
            spread = q["ask"] - q["bid"]
            expiry = c.expiration_date
            out.append(ContractQuote(
                occ_symbol=c.symbol,
                underlying=underlying,
                contract_type=contract_type,
                strike=float(c.strike_price),
                expiry=expiry,
                dte=(expiry - today).days,
                open_interest=int(c.open_interest),
                bid=q["bid"], ask=q["ask"], mid=mid,
                spread_abs=spread,
                spread_pct_of_mid=(spread / mid) if mid > 0 else 999.0,
                delta=q["delta"], iv=q["iv"],
            ))
        return out
