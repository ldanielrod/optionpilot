"""Daily markdown report: equity, open structures, trades, and every LLM
thesis of the day. Written to reports/YYYY-MM-DD.md — the raw material for
the hackathon one-pager and demo.

Run standalone (python report.py) or from the main loop after the close.
"""
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

import db
from core.occ import parse_occ

ET = ZoneInfo("America/New_York")


def _rows(sql, params=()):
    conn = db.get_connection()
    try:
        c = conn.cursor()
        c.execute(sql, params)
        return c.fetchall()
    finally:
        conn.close()


def build_report(trading=None) -> str:
    today = date.today()
    lines = [f"# OptionPilot daily report — {today}", ""]

    eq = _rows("SELECT ts, equity, cash, options_buying_power FROM equity "
               "ORDER BY ts DESC LIMIT 1")
    eq_first = _rows("SELECT equity FROM equity ORDER BY ts ASC LIMIT 1")
    if eq:
        ts, equity, cash, obp = eq[0]
        lines += [f"**Equity**: ${equity:,.2f} (cash ${cash:,.2f}, "
                  f"options BP ${obp or 0:,.2f}) as of {ts:%Y-%m-%d %H:%M} UTC"]
        if eq_first and eq_first[0][0]:
            start = eq_first[0][0]
            lines += [f"**Since start**: {(equity - start):+,.2f} "
                      f"({(equity / start - 1) * 100:+.2f}%)"]
        lines += [""]

    if trading is not None:
        try:
            positions = trading.get_all_positions()
            lines += ["## Open positions", ""]
            if not positions:
                lines += ["(none)", ""]
            for p in positions:
                occ = parse_occ(p.symbol)
                tag = (f"{occ.underlying} {occ.expiry} {occ.contract_type} "
                       f"{occ.strike:g}" if occ else p.symbol)
                lines += [f"- `{p.symbol}` ({tag}): qty {p.qty} @ "
                          f"{p.avg_entry_price}, uPnL {p.unrealized_pl}"]
            lines += [""]
        except Exception as e:
            lines += [f"(positions unavailable: {e})", ""]

    trades = _rows("SELECT ts, symbol, occ_symbol, strategy, side, qty, price, "
                   "premium, delta_at_entry, dte, reason FROM trades "
                   "WHERE ts::date = %s ORDER BY ts", (today,))
    lines += [f"## Trades today ({len(trades)})", ""]
    for t in trades:
        ts, sym, occ, strat, side, qty, price, prem, delta, dte, reason = t
        lines += [f"- {ts:%H:%M} **{strat} {sym}** {side} {qty}x `{occ}` @ "
                  f"${price} (premium ${prem:,.0f}, delta {delta}, {dte} DTE) "
                  f"— {reason}"]
    if not trades:
        lines += ["(none)"]
    lines += [""]

    llm = _rows("SELECT ts, symbol, decision->>'occ_symbol', reasoning, "
                "cost_usd, validated FROM llm_decisions "
                "WHERE ts::date = %s ORDER BY ts", (today,))
    lines += [f"## LLM decisions today ({len(llm)})", ""]
    for ts, sym, occ, reasoning, cost, validated in llm:
        badge = "✓" if validated else "✗ GUARDRAIL"
        lines += [f"### {ts:%H:%M} {sym} → `{occ}` {badge} (${cost or 0:.2f})",
                  "", f"> {(reasoning or '').strip()}", ""]

    events = _rows("SELECT ts, symbol, event, action_taken FROM guardrail_events "
                   "WHERE ts::date = %s ORDER BY ts", (today,))
    if events:
        lines += [f"## Guardrail events ({len(events)})", ""]
        for ts, sym, event, action in events:
            lines += [f"- {ts:%H:%M} {sym}: **{event}** → {action}"]
        lines += [""]

    return "\n".join(lines)


def write_report(trading=None) -> str:
    os.makedirs("reports", exist_ok=True)
    path = f"reports/{date.today()}.md"
    content = build_report(trading)
    with open(path, "w") as f:
        f.write(content)
    print(f"[report] wrote {path}")
    return path


if __name__ == "__main__":
    import os as _os
    trading = None
    if _os.getenv("ALPACA_API_KEY"):
        from alpaca.trading.client import TradingClient
        trading = TradingClient(_os.environ["ALPACA_API_KEY"],
                                _os.environ["ALPACA_API_SECRET"], paper=True)
    print(build_report(trading))
