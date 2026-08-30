"""Did the LLM add value over the rule-based selector?

For every mandate the agent handled, two contracts exist: the one Claude chose
and executed, and the one the deterministic delta-nearest rule would have
chosen (computed at the same moment, never traded). Both are short puts opened
at a known credit, so both can be marked to the current market and compared on
equal terms.

P&L convention for a short option: credit collected at entry minus the cost to
close now. Positive is good.

Run: python attribution.py
"""
import os
from collections import Counter

from dotenv import load_dotenv

load_dotenv()

import db
from data.options import OptionsData


def _short_pnl(entry_credit, mark):
    """Per-contract P&L of a short option, in dollars (x100 multiplier)."""
    if entry_credit is None or mark is None:
        return None
    return (float(entry_credit) - float(mark)) * 100


def load_pairs():
    conn = db.get_connection()
    try:
        c = conn.cursor()
        c.execute("""
        SELECT ts, symbol,
               decision->>'occ_symbol'          AS llm_symbol,
               (decision->>'limit_price')::float AS llm_limit,
               deterministic_pick->>'occ_symbol' AS det_symbol,
               (deterministic_pick->>'limit')::float AS det_limit,
               agreed, reasoning, cost_usd
        FROM llm_decisions
        WHERE deterministic_pick IS NOT NULL
          AND decision->>'occ_symbol' IS NOT NULL
        ORDER BY ts
        """)
        return c.fetchall()
    finally:
        conn.close()


def actual_fill(occ_symbol):
    """The real fill price if this contract was actually traded."""
    conn = db.get_connection()
    try:
        c = conn.cursor()
        c.execute("SELECT price FROM trades WHERE occ_symbol=%s "
                  "AND side='sell_to_open' ORDER BY ts LIMIT 1", (occ_symbol,))
        row = c.fetchone()
        return float(row[0]) if row else None
    finally:
        conn.close()


def main():
    rows = load_pairs()
    if not rows:
        print("No paired decisions yet — the agent logs one per LLM mandate.")
        return

    key = os.environ["ALPACA_API_KEY"]
    secret = os.environ["ALPACA_API_SECRET"]
    options = OptionsData(key, secret)

    symbols = sorted({r[2] for r in rows} | {r[4] for r in rows if r[4]})
    marks = {}
    for i in range(0, len(symbols), 100):
        marks.update(options.get_quotes(symbols[i:i + 100]))

    print(f"# LLM vs rule-based selector — {len(rows)} mandates\n")
    agree = Counter()
    llm_total = det_total = 0.0
    scored = 0

    for (ts, sym, llm_s, llm_lim, det_s, det_lim, agreed, reasoning,
         cost) in rows:
        agree[bool(agreed)] += 1
        # closing a short costs the ask
        llm_mark = (marks.get(llm_s) or {}).get("ask")
        det_mark = (marks.get(det_s) or {}).get("ask") if det_s else None
        llm_entry = actual_fill(llm_s) or llm_lim
        llm_pnl = _short_pnl(llm_entry, llm_mark)
        det_pnl = _short_pnl(det_lim, det_mark)

        flag = "=" if agreed else "≠"
        print(f"## {ts:%m-%d %H:%M} {sym} {flag}")
        print(f"- LLM   : {llm_s} entry {llm_entry} mark {llm_mark} "
              f"→ {'n/a' if llm_pnl is None else f'${llm_pnl:+,.0f}'}")
        print(f"- Rules : {det_s} entry {det_lim} mark {det_mark} "
              f"→ {'n/a' if det_pnl is None else f'${det_pnl:+,.0f}'}")
        if llm_pnl is not None and det_pnl is not None:
            delta = llm_pnl - det_pnl
            print(f"- Edge  : ${delta:+,.0f} to the "
                  f"{'LLM' if delta > 0 else 'rules'}")
            llm_total += llm_pnl
            det_total += det_pnl
            scored += 1
        print()

    n = len(rows)
    disagreed = agree[False]
    print("## Summary\n")
    print(f"- Mandates: {n} · same contract: {agree[True]} · "
          f"different: {disagreed} ({disagreed / n * 100:.0f}% divergence)")
    if scored:
        print(f"- Marked-to-market on {scored} comparable pairs: "
              f"LLM ${llm_total:+,.0f} vs rules ${det_total:+,.0f} "
              f"→ **${llm_total - det_total:+,.0f}** attributable to model choice")
    total_cost = sum(r[8] or 0 for r in rows)
    print(f"- LLM cost: ${total_cost:.2f}")


if __name__ == "__main__":
    main()
