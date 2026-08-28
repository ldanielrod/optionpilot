"""Smoke test: verify alpaca-py options support end-to-end on a PAPER account.

1. Fetch AAPL option contracts (puts, next weekly window)
2. Fetch option chain snapshot with Greeks for a few contracts
3. Submit an UNMARKETABLE sell-to-open limit order (CSP) and cancel it

Run:  ALPACA_API_KEY=... ALPACA_API_SECRET=... python tests/smoke_alpaca_options.py
Safe: the order limit is set far above the ask so it can never fill; it is
cancelled immediately after status check.
"""
import os
import sys
import time
from datetime import date, timedelta

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, AssetStatus, ContractType
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionSnapshotRequest

KEY = os.environ["ALPACA_API_KEY"]
SECRET = os.environ["ALPACA_API_SECRET"]

tc = TradingClient(KEY, SECRET, paper=True)
odc = OptionHistoricalDataClient(KEY, SECRET)

acct = tc.get_account()
print(f"[1] Account OK: equity=${float(acct.equity):,.2f} "
      f"options_level={getattr(acct, 'options_approved_level', 'N/A')} "
      f"options_buying_power={getattr(acct, 'options_buying_power', 'N/A')}")

today = date.today()
req = GetOptionContractsRequest(
    underlying_symbols=["AAPL"],
    status=AssetStatus.ACTIVE,
    type=ContractType.PUT,
    expiration_date_gte=today + timedelta(days=2),
    expiration_date_lte=today + timedelta(days=12),
    limit=100,
)
contracts = tc.get_option_contracts(req).option_contracts
print(f"[2] Chain OK: {len(contracts)} AAPL puts expiring {today + timedelta(days=2)}..{today + timedelta(days=12)}")
if not contracts:
    sys.exit("No contracts returned — check options availability on this account")

# pick a deep OTM put (lowest strike) to guarantee harmlessness
otm = sorted(contracts, key=lambda c: float(c.strike_price))[0]
print(f"    picked deep-OTM: {otm.symbol} strike={otm.strike_price} exp={otm.expiration_date} OI={otm.open_interest}")

snap = odc.get_option_snapshot(OptionSnapshotRequest(symbol_or_symbols=[otm.symbol]))
s = snap[otm.symbol]
greeks = s.greeks
quote = s.latest_quote
print(f"[3] Snapshot OK: bid={quote.bid_price} ask={quote.ask_price} "
      f"delta={getattr(greeks, 'delta', None) if greeks else None} "
      f"iv={s.implied_volatility}")

ask = float(quote.ask_price) if quote.ask_price else 0.05
unmarketable = round(max(ask * 5, 1.0), 2)  # sell limit way above ask: cannot fill
order = tc.submit_order(LimitOrderRequest(
    symbol=otm.symbol,
    qty=1,
    side=OrderSide.SELL,
    time_in_force=TimeInForce.DAY,
    limit_price=unmarketable,
    client_order_id=f"smoke-{int(time.time())}",
))
print(f"[4] Order submitted: id={order.id} status={order.status} limit={unmarketable}")

time.sleep(2)
o2 = tc.get_order_by_id(order.id)
print(f"[5] Order status after 2s: {o2.status}")

tc.cancel_order_by_id(order.id)
time.sleep(1)
o3 = tc.get_order_by_id(order.id)
print(f"[6] Cancelled: status={o3.status}")
print("SMOKE TEST PASSED" if str(o3.status).lower().find("cancel") >= 0 else "WARNING: unexpected final status")
