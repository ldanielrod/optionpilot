"""OptionPilot — hybrid AI options agent for the Alpaca hackathon.

Deterministic core: scans daily-bar signals, builds bounded OptionMandates,
manages exits and risk. Execution goes through the LLM trader (Claude +
Alpaca MCP server) when LLM_ENABLED, with the direct executor as fallback
and as the kill-switch degradation path.
"""
import os
import time
from datetime import datetime, date
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from alpaca.trading.client import TradingClient

import db
from config import CONFIG
from core.scanner import Scanner
from core.mandate import MandateBuilder
from core.executor_direct import DirectExecutor
from core.exits import ExitManager
from core.risk import PositionManager
from core.occ import parse_occ
from data.feed import AlpacaFeed
from data.corporate_actions import CorporateActions
from data.options import OptionsData
import notify
from ledger import Ledger
from report import write_report

ET = ZoneInfo("America/New_York")
MARKET = "OPTIONPILOT"


def build_open_state(trading: TradingClient, options_data=None) -> dict:
    short_puts, stock_positions, covered_calls = {}, {}, set()
    short_put_symbols = []
    for pos in trading.get_all_positions():
        qty = float(pos.qty)
        occ = parse_occ(pos.symbol)
        if occ is None:
            stock_positions[pos.symbol] = stock_positions.get(pos.symbol, 0) + qty
            continue
        if occ.contract_type == "put" and qty < 0:
            short_puts[occ.underlying] = (
                short_puts.get(occ.underlying, 0) + occ.strike * 100 * abs(qty))
            short_put_symbols.append((pos.symbol, occ.strike, abs(qty)))
        elif occ.contract_type == "call" and qty < 0:
            covered_calls.add(occ.underlying)

    # Long-delta equivalent carried by the open short puts. Strike stands in
    # for spot (near-the-money at these deltas, ~3% error) to keep this to one
    # batched quote call.
    delta_notional = 0.0
    if short_put_symbols and options_data is not None:
        try:
            quotes = options_data.get_quotes([s for s, _, _ in short_put_symbols])
            for sym, strike, qty in short_put_symbols:
                d = (quotes.get(sym) or {}).get("delta")
                if d is not None:
                    delta_notional += abs(d) * 100 * strike * qty
        except Exception as e:
            print(f"[main] delta notional unavailable ({e}) — using band midpoint")
            mid = sum(CONFIG.csp_delta_band) / 2
            delta_notional = sum(mid * 100 * k * q for _, k, q in short_put_symbols)

    obp = None
    try:
        obp = float(getattr(trading.get_account(), "options_buying_power", 0) or 0)
    except Exception as e:
        print(f"[main] options buying power unavailable ({e})")

    return {
        "short_puts": short_puts,
        "options_buying_power": obp,
        "short_put_delta_notional": delta_notional,
        "stock_positions": stock_positions,
        "covered_calls": covered_calls,
        "structures_opened_today": count_structures_today(),
    }


def count_structures_today() -> int:
    try:
        conn = db.get_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM trades WHERE ts::date = CURRENT_DATE "
                  "AND side IN ('sell_to_open','buy_to_open')")
        n = c.fetchone()[0]
        conn.close()
        return int(n)
    except Exception as e:
        print(f"[main] count_structures_today failed: {e} — assuming cap reached")
        return 999  # fail closed: no DB means no new entries


def decision_cycle(trading, scanner, builder, executor, llm_trader, pm,
                   ledger, equity: float, exits=None, slot: str = "") -> dict:
    events = scanner.scan()
    for sym, ev in events.items():
        rv = f"{ev.realized_vol:.1%}" if ev.realized_vol else "n/a"
        print(f"  {sym}: {ev.signal} conf={ev.info.get('confidence')} "
              f"rv20={rv} new_bar={ev.is_new_bar}")
    # Assigned shares need a signal to be judged against, so this runs here
    # rather than in the 10-minute exit loop.
    if exits is not None:
        exits.manage_assigned_stock(events, execute=CONFIG.execute)

    open_state = build_open_state(trading, executor.options)
    mandates = builder.build(events, equity, open_state)
    notify.on_cycle(slot, equity,
                    " ".join(f"{s}:{e.signal[0]}" for s, e in events.items()),
                    len(mandates))
    for m in mandates:
        allowed, why = pm.can_trade(m.underlying)
        if not allowed:
            ledger.log_decision(m.underlying, m.strategy, "SKIP", why)
            continue
        use_llm = llm_trader is not None and db.llm_is_enabled(MARKET)
        if use_llm:
            # Compute (never execute) the rule-based answer to the same
            # mandate first, so the LLM's choice can be scored against a
            # baseline. The LLM never sees it.
            baseline = executor.execute(m, execute=False)
            det_pick = {
                "occ_symbol": baseline.occ_symbol, "limit": baseline.fill_price,
                "delta": baseline.delta, "dte": baseline.dte,
                "reason": baseline.reason,
            } if baseline.ok else {"occ_symbol": None, "reason": baseline.reason}
            result = llm_trader.execute(m, execute=CONFIG.execute,
                                        deterministic_pick=det_pick)
        else:
            result = executor.execute(m, execute=CONFIG.execute)
        print(f"[main] {m.strategy} {m.underlying}: "
              f"{result.reason} {result.occ_symbol or ''}")
        if result.ok and result.reason == "filled":
            pm.record_trade(m.underlying)
            premium = (result.fill_price or 0) * 100 * m.qty
            ledger.log_trade(
                symbol=m.underlying, occ_symbol=result.occ_symbol,
                strategy=m.strategy,
                side="buy_to_open" if m.strategy == "LONG_CALL" else "sell_to_open",
                qty=m.qty, price=result.fill_price or 0,
                premium=premium, delta_at_entry=result.delta,
                dte=result.dte,
                reason=m.signal_context.get("reason", ""),
                client_order_id=result.client_order_id or m.client_order_id,
                broker_order_id=result.order_id,
                equity_before=equity)
            notify.on_fill(m.strategy, m.underlying, result.occ_symbol, m.qty,
                           result.fill_price or 0, premium, result.delta,
                           result.dte)
        else:
            ledger.log_decision(
                m.underlying, m.strategy, "NO_FILL", result.reason,
                details=m.to_dict())
            notify.on_no_trade(m.underlying, result.reason)


def due_decision_slot(done: set, grace_minutes: int = 45) -> str | None:
    """The slot we are currently inside, if it hasn't run yet.

    Slots more than `grace_minutes` old are marked done rather than fired:
    a restart at 15:00 must not replay 09:45 and 12:30 back to back and burn
    the whole day's structure budget in one minute.
    """
    now = datetime.now(ET)
    minutes_now = now.hour * 60 + now.minute
    for slot in CONFIG.decision_times_et:
        key = f"{date.today()}-{slot}"
        if key in done:
            continue
        h, m = (int(x) for x in slot.split(":"))
        minutes_slot = h * 60 + m
        if minutes_now < minutes_slot:
            continue
        if minutes_now - minutes_slot > grace_minutes:
            done.add(key)  # missed it; don't replay
            continue
        return key
    return None


def main() -> None:
    api_key = os.environ["ALPACA_API_KEY"]
    api_secret = os.environ["ALPACA_API_SECRET"]

    db.init_db()
    trading = TradingClient(api_key, api_secret, paper=True)
    feed = AlpacaFeed(api_key, api_secret)
    options_data = OptionsData(api_key, api_secret)
    ledger = Ledger()
    scanner = Scanner(feed, CONFIG)
    corp_actions = CorporateActions(api_key, api_secret)
    builder = MandateBuilder(CONFIG, ledger, corp_actions)
    executor = DirectExecutor(api_key, api_secret, options_data)
    exits = ExitManager(trading, options_data, CONFIG, ledger)

    acct = trading.get_account()
    equity0 = float(acct.equity)
    pm = PositionManager(CONFIG, initial_equity=equity0, market=MARKET)
    print(f"[main] start: equity=${equity0:,.2f} "
          f"options_level={getattr(acct, 'options_trading_level', '?')} "
          f"EXECUTE={CONFIG.execute} LLM={CONFIG.llm_enabled}")
    notify.on_start(equity0, CONFIG.execute, CONFIG.llm_enabled)

    llm_trader = None
    if CONFIG.llm_enabled:
        from agent.llm_trader import LLMTrader
        llm_trader = LLMTrader(CONFIG, ledger, options_data, trading)

    slots_done: set = set()
    while True:
        try:
            # liveness marker for the container healthcheck: a loop that is
            # running but wedged looks identical to a healthy one from outside
            try:
                open("/tmp/heartbeat", "w").close()
            except OSError:
                pass

            clock = trading.get_clock()
            if not clock.is_open:
                time.sleep(60)
                continue

            acct = trading.get_account()
            equity = float(acct.equity)
            cash = float(acct.cash)
            ledger.log_equity(equity, cash,
                              float(getattr(acct, "options_buying_power", 0) or 0))

            ok, msg = pm.validate_state_integrity(equity, max(cash, 0.0))
            if not ok:
                time.sleep(5)
                acct2 = trading.get_account()
                ok2, msg2 = pm.validate_state_integrity(
                    float(acct2.equity), max(float(acct2.cash), 0.0))
                if not ok2:
                    pm.trigger_emergency_halt(msg2)
                    ledger.log_decision("*", "HALT", "EMERGENCY_HALT", msg2)
                    notify.on_halt(msg2)
                    print(f"[main] EMERGENCY HALT: {msg2}")
                    break

            halted = pm.check_drawdown(equity)

            # exits always run, even during a drawdown halt
            exits.check_and_execute(execute=CONFIG.execute)

            slot = None if halted else due_decision_slot(slots_done)
            if slot:
                slots_done.add(slot)
                print(f"[main] decision cycle {slot} (equity=${equity:,.2f})")
                decision_cycle(trading, scanner, builder, executor, llm_trader,
                               pm, ledger, equity, exits=exits, slot=slot)
                write_report(trading)

            time.sleep(CONFIG.reconcile_seconds)
        except KeyboardInterrupt:
            print("[main] stopped by user")
            break
        except Exception as e:
            print(f"[main] cycle error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
