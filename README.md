# OptionPilot

Bounded-autonomy options agent for the
[Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon)
(Aug 28 – Sep 4, 2026) — team `ldanielrod_team`.

A deterministic core decides *whether a trade is worth making* and under what
constraints. Claude, connected to Alpaca's official MCP server, decides *which
contract*. Everything the model does is re-read from the broker and audited
against the mandate it was given.

See **[ONEPAGER.md](ONEPAGER.md)** for the one-page summary and **[DESIGN.md](DESIGN.md)** for the full write-up, including what each
layer actually contributed and where the approach is weak.

## Architecture

```
daily bars ──> signals ──> volatility gate ──> mandate ──> Claude + Alpaca MCP
                                                  │                │
                                                  │                v
                                                  │           limit order
                                                  v                │
                                          rule-based baseline      │
                                          (logged, not traded)     v
                                                            verification
                                                       (alpaca-py, 11 checks)
                                                          │            │
                                                     violation      accepted
                                                          │            │
                                               cancel + flatten     ledger
                                               + kill switch
```

| Module | Role |
|---|---|
| `core/scanner.py` | Closed daily bars → signals + 20-day realized vol |
| `core/mandate.py` | Signal → bounded `OptionMandate`; all account caps resolved here |
| `agent/llm_trader.py` | One Claude session per mandate, Alpaca MCP over stdio |
| `core/guardrails.py` | Validates broker orders against the mandate |
| `core/executor_direct.py` | Rule-based selector: baseline, and kill-switch fallback |
| `core/exits.py` | Profit take, stops, expiry close, assigned-stock exit |
| `attribution.py` | Marks Claude's pick against the baseline it never saw |

## Run

```bash
cp .env.example .env      # Alpaca paper keys, Claude auth, Telegram (optional)
docker compose up -d
```

- `EXECUTE=0` runs every cycle end to end without placing orders.
- `LLM_ENABLED=0` uses the deterministic selector only.

```bash
python attribution.py     # LLM vs rule-based baseline, marked to market
python report.py          # daily report including every LLM thesis
```

## Tests

```bash
python tests/test_guardrails.py        # OCC parsing, decision schema, 11 mandate checks
python tests/test_reconcile_redteam.py # violating / foreign / already-filled orders, kill switch
python tests/test_vol_and_delta.py     # IV floor, aggregate delta cap
python tests/test_assigned_stock.py    # assignment exits, never leave a naked call
python tests/test_slots.py             # decision scheduling
```

## Provenance

The signal engine (`core/signals.py`, `core/indicators.py`), risk manager
(`core/risk.py`) and data feed (`data/feed.py`) are adapted from the author's own
production trading bot, which has run continuously in a live paper account since
June 2026 across four markets. Mandates, guardrails, verification, the LLM layer,
options handling, exits and attribution are new for this hackathon.

The inherited part contributes operational hardening, not a claim of edge — see
the Provenance and Limitations sections of the one-pager for what that means and
what it does not.

MIT licensed.
