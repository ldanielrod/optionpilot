"""LLM execution layer: one Claude session per mandate, with Alpaca's MCP
server attached over stdio.

Trust model: the session may only call the whitelisted Alpaca MCP tools (no
shell, no files, no stock orders). Whatever it reports, the core re-reads
orders from the broker via alpaca-py, validates them against the mandate
(core/guardrails.py), cancels violations, and counts strikes toward the
persistent kill switch (db.record_llm_violation).
"""
import asyncio
import shutil
import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest
from alpaca.trading.enums import QueryOrderStatus, OrderSide, TimeInForce

import db
import notify
from agent.decision_schema import parse_decision
from agent.prompts import SYSTEM_PROMPT, render_mandate
from core.executor_direct import ExecutionResult, TERMINAL
from core.guardrails import validate_order_against_mandate, Violation
from core.mandate import OptionMandate
from core.occ import parse_occ

# 60s, not 120: the cycle must have room to cancel and reprice before the
# next mandate needs the collateral this order is holding.
FILL_WAIT_S = 60
TRANSIENT_RETRIES = 3      # total attempts, not extra ones
TRANSIENT_BACKOFF_S = 12
ALLOWED_TOOLS_READONLY = [
    "mcp__alpaca__get_option_chain",
    "mcp__alpaca__get_option_snapshot",
    "mcp__alpaca__get_stock_snapshot",
    "mcp__alpaca__get_stock_latest_quote",
    "mcp__alpaca__get_account_info",
    "mcp__alpaca__get_orders",
    # Qualitative context the deterministic core cannot represent. This is the
    # one input where a language model has an advantage over the price series,
    # so it is the one worth adding — more information, not more models.
    "mcp__alpaca__get_news",
]
ALLOWED_TOOLS_EXECUTE = ALLOWED_TOOLS_READONLY + ["mcp__alpaca__place_option_order"]
DISALLOWED_TOOLS = ["Bash", "Write", "Edit", "Read", "Glob", "Grep",
                    "WebFetch", "WebSearch", "Task", "NotebookEdit"]


class LLMTrader:
    def __init__(self, config, ledger, options_data, trading,
                 api_key: str = None, api_secret: str = None,
                 market: str = "OPTIONPILOT"):
        import os
        self.config = config
        self.ledger = ledger
        self.options = options_data      # core's own data path (validation)
        self.trading = trading           # core's own trading client (truth)
        self.market = market
        self.api_key = api_key or os.environ["ALPACA_API_KEY"]
        self.api_secret = api_secret or os.environ["ALPACA_API_SECRET"]
        self.mcp_command = shutil.which("alpaca-mcp-server")
        if not self.mcp_command:
            raise RuntimeError("alpaca-mcp-server not found on PATH")

    # ── session ──────────────────────────────────────────────────────

    def _session_options(self, execute: bool):
        from claude_agent_sdk import ClaudeAgentOptions
        return ClaudeAgentOptions(
            model=self.config.llm_model,
            system_prompt=SYSTEM_PROMPT,
            max_turns=self.config.llm_max_turns,
            mcp_servers={
                "alpaca": {
                    "type": "stdio",
                    "command": self.mcp_command,
                    "args": [],
                    "env": {
                        "ALPACA_API_KEY": self.api_key,
                        "ALPACA_SECRET_KEY": self.api_secret,
                        "ALPACA_PAPER_TRADE": "true",
                    },
                }
            },
            allowed_tools=ALLOWED_TOOLS_EXECUTE if execute else ALLOWED_TOOLS_READONLY,
            disallowed_tools=DISALLOWED_TOOLS,
            strict_mcp_config=True,
        )

    async def _run_session(self, prompt: str, execute: bool):
        from claude_agent_sdk import query, AssistantMessage, TextBlock, ResultMessage
        text_parts: List[str] = []
        cost = None
        turns = 0
        async for message in query(prompt=prompt,
                                   options=self._session_options(execute)):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
            elif isinstance(message, ResultMessage):
                cost = getattr(message, "total_cost_usd", None)
                turns = getattr(message, "num_turns", 0)
        return "\n".join(text_parts), cost, turns

    def _session_with_retry(self, prompt: str, execute: bool):
        """Retry a session that failed for a reason the server itself calls
        temporary. A 529 during the judged window costs a decision, and the
        API says outright to try again in a moment.

        Only transient signatures are retried: a schema or mandate problem
        would fail again identically and is handled downstream.
        """
        attempts = 0
        while True:
            attempts += 1
            try:
                return asyncio.run(self._run_session(prompt, execute=execute))
            except Exception as e:
                msg = str(e).lower()
                transient = any(s in msg for s in (
                    "529", "overloaded", "503", "502", "504", "timeout",
                    "timed out", "connection", "rate limit", "429"))
                if not transient or attempts >= TRANSIENT_RETRIES:
                    raise
                wait = TRANSIENT_BACKOFF_S * attempts
                print(f"[llm] transient failure ({e}) — retry {attempts} "
                      f"in {wait}s")
                time.sleep(wait)

    # ── main entry ───────────────────────────────────────────────────

    def execute(self, mandate: OptionMandate, execute: bool = True,
                deterministic_pick: Optional[dict] = None) -> ExecutionResult:
        """deterministic_pick: what the rule-based selector chose for this same
        mandate — computed, never executed, and never shown to the LLM. Logged
        beside the LLM's choice so "did the model add value?" is measurable."""
        session_id = f"llm-{uuid.uuid4().hex[:10]}"
        session_start = datetime.now(timezone.utc)
        prompt = render_mandate(mandate, execute=execute)

        try:
            text, cost, turns = self._session_with_retry(prompt, execute)
        except Exception as e:
            print(f"[llm] session failed: {e}")
            return ExecutionResult(ok=False, reason=f"llm_session_failed: {e}")

        decision, parse_err = parse_decision(text)
        if decision is None:
            # one retry with the parse error appended
            try:
                text, cost2, turns2 = asyncio.run(self._run_session(
                    prompt + f"\n\nYour previous reply was rejected: {parse_err}. "
                             "Reply again ending with the decision JSON.",
                    execute=execute))
                cost = (cost or 0) + (cost2 or 0)
                turns += turns2
                decision, parse_err = parse_decision(text)
            except Exception as e:
                print(f"[llm] retry failed: {e}")
        reasoning = (decision or {}).get("thesis", "") or text[-2000:]

        # ── broker truth: what did the session actually do? ──────────
        violations = []
        result = ExecutionResult(ok=False, reason="llm_no_order")
        if execute:
            result, violations = self._reconcile_session(
                mandate, session_start, decision)
        elif decision is not None and decision.get("action") in ("shadow_pick", "no_trade"):
            result = ExecutionResult(
                ok=True, reason=decision["action"],
                occ_symbol=decision.get("occ_symbol"),
                fill_price=decision.get("limit_price"),
                status="shadow")

        llm_symbol = result.occ_symbol or (decision or {}).get("occ_symbol")
        det_symbol = (deterministic_pick or {}).get("occ_symbol")
        agreed = (llm_symbol == det_symbol) if (llm_symbol and det_symbol) else None

        self.ledger.log_llm_decision(
            session_id=session_id, symbol=mandate.underlying,
            mandate=mandate.to_dict(), decision=decision,
            reasoning=reasoning, num_turns=turns, cost_usd=cost,
            validated=not violations,
            validation_errors=[f"{v.code}: {v.detail}" for v in violations] or None,
            deterministic_pick=deterministic_pick, agreed=agreed)
        if det_symbol and llm_symbol and not agreed:
            print(f"[llm] DIVERGENCE {mandate.underlying}: "
                  f"rules={det_symbol} llm={llm_symbol}")

        if violations:
            for v in violations:
                self.ledger.log_guardrail_event(
                    session_id, mandate.underlying, v.code,
                    {"detail": v.detail, "mandate_id": mandate.client_order_id},
                    "cancelled")
                notify.on_guardrail(mandate.underlying, v.code, v.detail,
                                    "cancelled")
            still_enabled = db.record_llm_violation(
                self.market, self.config.llm_violation_limit)
            if not still_enabled:
                print("[llm] KILL SWITCH: violation limit reached — "
                      "LLM execution disabled permanently")
                self.ledger.log_guardrail_event(
                    session_id, mandate.underlying, "kill_switch",
                    {"limit": self.config.llm_violation_limit}, "llm_disabled")
                notify.on_kill_switch(self.config.llm_violation_limit)
        return result

    # ── verification ─────────────────────────────────────────────────

    def _session_orders(self, mandate: OptionMandate, session_start):
        req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=100)
        orders = self.trading.get_orders(req)
        mine, foreign = [], []
        for o in orders:
            sub = getattr(o, "submitted_at", None)
            if sub is None or sub < session_start:
                continue
            occ = parse_occ(getattr(o, "symbol", "") or "")
            if occ is None:
                continue  # stock orders can't come from this session's tools
            cid = getattr(o, "client_order_id", "") or ""
            (mine if cid.startswith(mandate.client_order_id) else foreign).append(o)
        return mine, foreign

    def _reconcile_session(self, mandate: OptionMandate, session_start,
                           decision) -> Tuple[ExecutionResult, list]:
        violations = []
        try:
            mine, foreign = self._session_orders(mandate, session_start)
        except Exception as e:
            return ExecutionResult(ok=False, reason=f"reconcile_failed: {e}"), []

        for o in foreign:
            violations.append(Violation(
                "foreign_order_id", f"{o.symbol} cid={o.client_order_id}"))
            self._cancel_quiet(o)

        if not mine:
            if decision is not None and decision.get("action") == "no_trade":
                return ExecutionResult(ok=True, reason="no_trade",
                                       status="no_trade"), violations
            return ExecutionResult(ok=False, reason="llm_no_order"), violations

        if len(mine) > 1:
            violations.append(Violation("multiple_orders", f"{len(mine)} orders"))
            for o in mine[1:]:
                self._cancel_quiet(o)

        order = mine[0]
        quote = None
        try:
            quote = self.options.get_quotes([order.symbol]).get(order.symbol)
        except Exception as e:
            print(f"[llm] quote for validation failed: {e}")
        violations.extend(validate_order_against_mandate(order, mandate, quote))

        if violations:
            self._cancel_quiet(order)
            self._flatten_if_filled(order)
            return ExecutionResult(ok=False, reason="guardrail_violation",
                                   occ_symbol=order.symbol), violations

        occ = parse_occ(order.symbol)
        dte = (occ.expiry - session_start.date()).days if occ else None

        def filled(o):
            return o is not None and str(o.status).lower().endswith("filled")

        def result_from(o, oid, cid):
            return ExecutionResult(
                ok=True, reason="filled", occ_symbol=order.symbol,
                order_id=str(oid), client_order_id=cid,
                filled_qty=float(o.filled_qty or 0),
                fill_price=float(o.filled_avg_price or 0),
                delta=(quote or {}).get("delta"), dte=dte,
                status=str(o.status)), violations

        final = self._wait_fill(order.id)
        if filled(final):
            return result_from(final, order.id, order.client_order_id)

        # The model chose the contract; getting filled is the core's job. A
        # limit at mid needs someone to cross it, which is how day one produced
        # three cancelled orders and no position — and a resting order holds
        # collateral, which starved the next mandate of buying power.
        self._cancel_quiet(order)
        reprice = self._reprice(order, mandate)
        if reprice is None:
            return ExecutionResult(ok=False, reason="unfilled_no_reprice",
                                   occ_symbol=order.symbol), violations
        final2 = self._wait_fill(reprice.id)
        if filled(final2):
            return result_from(final2, reprice.id, reprice.client_order_id)

        self._cancel_quiet(reprice)
        return ExecutionResult(ok=False, reason="unfilled_after_reprice",
                               occ_symbol=order.symbol,
                               status=str(final2.status) if final2 else None), violations

    def _reprice(self, order, mandate: OptionMandate):
        """Resubmit the model's contract at the marketable side of the book:
        the bid when we are selling, the ask when buying. Keeps the mandate's
        client_order_id prefix so verification still recognises it as ours."""
        try:
            time.sleep(1.5)
            quote = self.options.get_quotes([order.symbol]).get(order.symbol)
            if not quote:
                return None
            selling = mandate.strategy != "LONG_CALL"
            price = quote["bid"] if selling else quote["ask"]
            if not price or price <= 0:
                print(f"[llm] no reprice: dead quote on {order.symbol}")
                return None
            print(f"[llm] repricing {order.symbol} to {price} "
                  f"({'bid' if selling else 'ask'})")
            return self.trading.submit_order(LimitOrderRequest(
                symbol=order.symbol, qty=mandate.qty,
                side=OrderSide.SELL if selling else OrderSide.BUY,
                time_in_force=TimeInForce.DAY, limit_price=round(price, 2),
                client_order_id=f"{mandate.client_order_id}-r1"))
        except Exception as e:
            print(f"[llm] reprice failed: {e}")
            return None

    def _wait_fill(self, order_id):
        deadline = time.time() + FILL_WAIT_S
        last = None
        while time.time() < deadline:
            try:
                last = self.trading.get_order_by_id(order_id)
                s = str(last.status).lower()
                if any(s.endswith(t) for t in TERMINAL):
                    return last
            except Exception as e:
                print(f"[llm] poll error: {e}")
            time.sleep(4)
        return last

    def _cancel_quiet(self, order) -> None:
        try:
            self.trading.cancel_order_by_id(order.id)
        except Exception:
            pass

    def _flatten_if_filled(self, order) -> None:
        """If a violating order already filled, close the resulting position."""
        try:
            o = self.trading.get_order_by_id(order.id)
            if float(o.filled_qty or 0) > 0:
                print(f"[llm] flattening filled violating position {o.symbol}")
                self.trading.close_position(o.symbol)
        except Exception as e:
            print(f"[llm] flatten check failed: {e}")
