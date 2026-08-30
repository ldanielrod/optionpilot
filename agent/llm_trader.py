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

from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus

import db
from agent.decision_schema import parse_decision
from agent.prompts import SYSTEM_PROMPT, render_mandate
from core.executor_direct import ExecutionResult, TERMINAL
from core.guardrails import validate_order_against_mandate, Violation
from core.mandate import OptionMandate
from core.occ import parse_occ

FILL_WAIT_S = 120
ALLOWED_TOOLS_READONLY = [
    "mcp__alpaca__get_option_chain",
    "mcp__alpaca__get_option_snapshot",
    "mcp__alpaca__get_stock_snapshot",
    "mcp__alpaca__get_stock_latest_quote",
    "mcp__alpaca__get_account_info",
    "mcp__alpaca__get_orders",
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
            text, cost, turns = asyncio.run(
                self._run_session(prompt, execute=execute))
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
            still_enabled = db.record_llm_violation(
                self.market, self.config.llm_violation_limit)
            if not still_enabled:
                print("[llm] KILL SWITCH: violation limit reached — "
                      "LLM execution disabled permanently")
                self.ledger.log_guardrail_event(
                    session_id, mandate.underlying, "kill_switch",
                    {"limit": self.config.llm_violation_limit}, "llm_disabled")
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

        final = self._wait_fill(order.id)
        occ = parse_occ(order.symbol)
        dte = (occ.expiry - session_start.date()).days if occ else None
        if final is not None and str(final.status).lower().endswith("filled"):
            return ExecutionResult(
                ok=True, reason="filled", occ_symbol=order.symbol,
                order_id=str(order.id), client_order_id=order.client_order_id,
                filled_qty=float(final.filled_qty or 0),
                fill_price=float(final.filled_avg_price or 0),
                delta=(quote or {}).get("delta"), dte=dte,
                status=str(final.status)), violations

        self._cancel_quiet(order)
        return ExecutionResult(ok=False, reason="unfilled_after_wait",
                               occ_symbol=order.symbol,
                               status=str(final.status) if final else None), violations

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
