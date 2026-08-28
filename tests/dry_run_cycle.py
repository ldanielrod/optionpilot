"""End-to-end dry run of one decision cycle: real feed + real chain, no orders,
no DB. Verifies scanner -> mandate -> contract selection against the TEST
paper account."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from alpaca.trading.client import TradingClient

from config import CONFIG
from core.scanner import Scanner
from core.mandate import MandateBuilder
from core.executor_direct import DirectExecutor
from data.feed import AlpacaFeed
from data.options import OptionsData
from main import build_open_state

key, secret = os.environ["ALPACA_API_KEY"], os.environ["ALPACA_API_SECRET"]

feed = AlpacaFeed(key, secret)
scanner = Scanner(feed, CONFIG)
print("== scan ==")
events = scanner.scan()
for sym, ev in events.items():
    print(f"  {sym}: {ev.signal:4s} conf={ev.info.get('confidence')} "
          f"net={ev.info.get('net_score')} adx={ev.info.get('adx')} "
          f"reason={ev.info.get('reason')}")

trading = TradingClient(key, secret, paper=True)
acct = trading.get_account()
equity = float(acct.equity)
open_state = build_open_state(trading)
open_state["structures_opened_today"] = 0  # no DB in this dry run
print(f"\n== open state == equity=${equity:,.2f}")
print(f"  short_puts={open_state['short_puts']}")
print(f"  stock_positions={list(open_state['stock_positions'].items())[:10]}")
print(f"  covered_calls={open_state['covered_calls']}")

builder = MandateBuilder(CONFIG)
mandates = builder.build(events, equity, open_state)
print(f"\n== mandates ({len(mandates)}) ==")
for m in mandates:
    print(f"  {m.strategy} {m.underlying} qty={m.qty} delta={m.delta_band} "
          f"dte={m.dte_min}-{m.dte_max} max_strike={m.max_strike} "
          f"id={m.client_order_id} ctx={m.signal_context.get('reason')}")

if mandates:
    executor = DirectExecutor(key, secret, OptionsData(key, secret))
    m = mandates[0]
    print(f"\n== dry-run executor on {m.strategy} {m.underlying} ==")
    r = executor.execute(m, execute=False)
    print(f"  ok={r.ok} reason={r.reason} contract={r.occ_symbol} "
          f"limit={r.fill_price} delta={r.delta} dte={r.dte}")
else:
    print("\n(no mandates this cycle — nothing to dry-run)")
