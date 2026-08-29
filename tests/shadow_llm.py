"""Shadow-mode LLM session: real Claude session + real Alpaca MCP server,
read-only tools, no orders. Compares the LLM's pick with the deterministic
executor's pick for the same mandate.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from alpaca.trading.client import TradingClient

import db
from config import CONFIG
from core.mandate import OptionMandate, make_order_id
from core.executor_direct import DirectExecutor
from data.options import OptionsData
from ledger import Ledger
from agent.llm_trader import LLMTrader

key, secret = os.environ["ALPACA_API_KEY"], os.environ["ALPACA_API_SECRET"]
db.init_db()

mandate = OptionMandate(
    strategy="CSP", underlying="NVDA", qty=1,
    delta_band=CONFIG.csp_delta_band,
    dte_min=CONFIG.dte_min, dte_max=CONFIG.dte_max,
    min_open_interest=CONFIG.min_open_interest,
    max_spread_pct_of_mid=CONFIG.max_spread_pct_of_mid,
    max_strike=376.0,
    client_order_id=make_order_id(CONFIG.client_order_prefix, "NVDA"),
    signal_context={"reason": "buy_signal", "signal": "BUY", "confidence": 0.45,
                    "rsi": 62.1, "adx": 32.2, "net_score": 0.42},
)

options_data = OptionsData(key, secret)
trading = TradingClient(key, secret, paper=True)

print("== deterministic pick ==")
det = DirectExecutor(key, secret, options_data).execute(mandate, execute=False)
print(f"  {det.occ_symbol} limit={det.fill_price} delta={det.delta} dte={det.dte}")

print("\n== LLM shadow session (this takes a minute) ==")
trader = LLMTrader(CONFIG, Ledger(), options_data, trading)
res = trader.execute(mandate, execute=False)
print(f"\n  ok={res.ok} reason={res.reason}")
print(f"  LLM pick: {res.occ_symbol} @ {res.fill_price}")

conn = db.get_connection()
c = conn.cursor()
c.execute("SELECT reasoning, num_turns, cost_usd, validated FROM llm_decisions "
          "ORDER BY id DESC LIMIT 1")
row = c.fetchone()
conn.close()
if row:
    print(f"\n== thesis ==\n{row[0][:600]}")
    print(f"\nturns={row[1]} cost=${row[2]} validated={row[3]}")
