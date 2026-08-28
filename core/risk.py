# Adapted from trading_bot position_manager.py (proven in production since Jun 2026).
import time
from typing import Dict, Tuple
from config import Config


class PositionManager:
    """
    Gestiona riesgos de posiciones:
    - Cooldowns entre trades
    - Limites de exposicion por activo
    - Control de drawdown
    - Validación de invariantes críticos (v3.2)
    """

    def __init__(self, config: Config, initial_equity: float = 0.0,
                 market: str = None):
        self.config = config
        self.cooldowns: Dict[str, float] = {}  # {symbol: last_trade_timestamp}
        self.peak_equity = initial_equity
        self.initial_equity = initial_equity  # Para validación de supervivencia
        self.trading_halted = False
        self.halt_reason = ""
        # Emergency halt (v3.2) - requiere reinicio manual
        self.emergency_halt = False
        self.emergency_reason = ""

        # Persistencia del estado de drawdown (opcional).
        # Sin ella, peak_equity se reinicializaba al equity actual en cada
        # arranque, borrando cualquier drawdown acumulado: la tabla `equity`
        # registró una caída del -79.5% en CRYPTO que el halt nunca vio porque
        # el proceso se había reiniciado entremedias.
        self.market = market
        if market:
            self._load_persisted_state()

    # ──────────────────────────────────────────────────────────────
    # Persistencia
    # ──────────────────────────────────────────────────────────────

    def _load_persisted_state(self) -> None:
        try:
            from db import load_risk_state
            state = load_risk_state(self.market)
        except Exception as e:
            print(f"[PositionManager] {self.market}: no se pudo leer risk_state ({e}) "
                  f"— arrancando con peak={self.peak_equity:.2f}")
            return

        if not state:
            self._save_persisted_state()
            return

        # El peak es el máximo histórico: nunca lo bajamos al arrancar.
        self.peak_equity    = max(self.peak_equity, state["peak_equity"])
        self.trading_halted = state["trading_halted"]
        self.halt_reason    = state["halt_reason"]
        if self.trading_halted:
            print(f"[PositionManager] {self.market}: HALT restaurado desde DB "
                  f"— {self.halt_reason} (peak=${self.peak_equity:,.2f})")
        else:
            print(f"[PositionManager] {self.market}: peak restaurado "
                  f"${self.peak_equity:,.2f}")

    def _save_persisted_state(self) -> None:
        if not self.market:
            return
        try:
            from db import save_risk_state
            save_risk_state(self.market, self.peak_equity,
                            self.trading_halted, self.halt_reason)
        except Exception as e:
            print(f"[PositionManager] {self.market}: no se pudo guardar risk_state ({e})")

    def can_trade(self, symbol: str) -> Tuple[bool, str]:
        """
        Verifica si se puede operar en el simbolo (cooldown).
        Returns: (allowed, reason)
        """
        # Emergency halt tiene prioridad absoluta
        if self.emergency_halt:
            return False, f"emergency_halt: {self.emergency_reason}"

        if self.trading_halted:
            return False, f"trading_halted: {self.halt_reason}"

        last_trade = self.cooldowns.get(symbol, 0)
        elapsed = time.time() - last_trade

        if elapsed < self.config.cooldown_seconds:
            remaining = int(self.config.cooldown_seconds - elapsed)
            return False, f"cooldown: {remaining}s remaining"

        return True, "ok"

    def adjust_size(
        self,
        symbol: str,
        requested_size: float,
        equity: float,
        current_exposure: float
    ) -> float:
        """
        Ajusta el tamano del trade segun limites de exposicion.

        Args:
            symbol: Simbolo a operar
            requested_size: Tamano solicitado en USD
            equity: Equity total actual
            current_exposure: Exposicion actual total en USD

        Returns:
            Tamano ajustado (puede ser 0 si no se permite)
        """
        if equity <= 0:
            return 0.0

        # Limite por activo individual
        max_per_asset = equity * self.config.max_exposure_pct
        adjusted_size = min(requested_size, max_per_asset)

        # Limite de exposicion total
        max_total = equity * self.config.max_total_exposure_pct
        available_exposure = max_total - current_exposure

        if available_exposure <= 0:
            return 0.0

        adjusted_size = min(adjusted_size, available_exposure)

        # No permitir trades muy pequenos
        if adjusted_size < self.config.min_trade_usd:
            return 0.0

        return adjusted_size

    def check_drawdown(self, equity: float) -> bool:
        """
        Verifica si hay que detener trading por drawdown, con histéresis.

            halt   cuando drawdown >= max_drawdown_pct      (default 20%)
            resume cuando drawdown <= resume_drawdown_pct   (default 10%)

        El desarme por recuperación parcial es necesario, no cosmético. Antes
        el halt sólo se limpiaba al hacer un nuevo máximo de equity: detenido
        en -20%, hacía falta +25% para reanudar... estando detenido, o sea sin
        capacidad de generarlo. Era una trampa absorbente, y lo único que la
        rescataba era que un reinicio borrase el peak — justo el accidente que
        la persistencia elimina. Persistir sin histéresis dejaría el halt
        permanente.

        Returns:
            True si el trading debe estar detenido
        """
        # Un equity no positivo es un error de dato, no un movimiento de
        # mercado: con posiciones abiertas es imposible, y el caso real de
        # ruina lo cubre validate_state_integrity (invariante de supervivencia,
        # que además reconfirma con una segunda lectura). Tratarlo como
        # drawdown produjo "drawdown 100.0% >= 20%" sobre un glitch puntual del
        # broker que devolvió equity=$0.00.
        if equity is None or equity <= 0:
            print(f"[PositionManager] {self.market or ''} equity={equity} no positivo "
                  f"— ignorado para drawdown (lo cubre el invariante de supervivencia)")
            return self.trading_halted

        state_changed = False

        # Nuevo máximo: actualizar peak (NO desarma el halt por sí solo; de eso
        # se encarga el umbral de reanudación, que se cumple trivialmente aquí).
        if equity > self.peak_equity:
            self.peak_equity = equity
            state_changed = True

        if self.peak_equity <= 0:
            return self.trading_halted

        drawdown = (self.peak_equity - equity) / self.peak_equity
        resume_threshold = getattr(self.config, "resume_drawdown_pct", 0.10)

        if not self.trading_halted and drawdown >= self.config.max_drawdown_pct:
            self.trading_halted = True
            self.halt_reason = (
                f"drawdown {drawdown*100:.1f}% >= {self.config.max_drawdown_pct*100:.0f}%"
            )
            print(f"[PositionManager] {self.market or ''} HALT: {self.halt_reason} "
                  f"(peak=${self.peak_equity:,.2f} equity=${equity:,.2f})")
            state_changed = True

        elif self.trading_halted and drawdown <= resume_threshold:
            print(f"[PositionManager] {self.market or ''} RESUME: drawdown "
                  f"{drawdown*100:.1f}% <= {resume_threshold*100:.0f}% "
                  f"(era: {self.halt_reason})")
            self.trading_halted = False
            self.halt_reason = ""
            state_changed = True

        if state_changed:
            self._save_persisted_state()

        return self.trading_halted

    def record_trade(self, symbol: str):
        """Registra timestamp del trade para cooldown."""
        self.cooldowns[symbol] = time.time()

    def reset_halt(self):
        """Reinicia el estado de halt (manual). Persiste, o el próximo arranque
        vuelve a leer el halt desde la DB y lo deshace."""
        self.trading_halted = False
        self.halt_reason = ""
        self._save_persisted_state()

    def get_status(self) -> dict:
        """Retorna estado actual del manager."""
        return {
            "trading_halted": self.trading_halted,
            "halt_reason": self.halt_reason,
            "emergency_halt": self.emergency_halt,
            "emergency_reason": self.emergency_reason,
            "peak_equity": self.peak_equity,
            "initial_equity": self.initial_equity,
            "active_cooldowns": {
                sym: int(self.config.cooldown_seconds - (time.time() - ts))
                for sym, ts in self.cooldowns.items()
                if time.time() - ts < self.config.cooldown_seconds
            }
        }

    def validate_state_integrity(
        self, equity: float, cash_balance: float
    ) -> Tuple[bool, str]:
        """
        Valida invariantes críticos del sistema.
        Si retorna False, el bot debe detenerse inmediatamente (EMERGENCY HALT).

        Invariantes verificados:
        1. Equity nunca negativo
        2. Cash balance nunca negativo
        3. Equity mínimo de supervivencia (10% del peak o initial)

        Args:
            equity: Equity total actual (cash + positions)
            cash_balance: Balance en efectivo disponible

        Returns:
            (is_valid, error_message)
        """
        # INVARIANTE 1: Equity nunca puede ser negativo
        if equity < 0:
            return False, f"CRITICAL: equity={equity:.2f} < 0 (impossible state)"

        # INVARIANTE 2: Cash balance nunca puede ser negativo
        if cash_balance < 0:
            return False, f"CRITICAL: cash_balance={cash_balance:.2f} < 0"

        # INVARIANTE 3: Equity mínimo de supervivencia
        # Usar el mayor entre peak_equity e initial_equity como referencia
        reference_equity = max(self.peak_equity, self.initial_equity)
        if reference_equity > 0:
            min_survival_equity = reference_equity * 0.10  # 10% mínimo
            if equity < min_survival_equity:
                return False, (
                    f"CRITICAL: equity={equity:.2f} < 10% of reference "
                    f"({min_survival_equity:.2f}). Possible catastrophic loss."
                )

        return True, "ok"

    def trigger_emergency_halt(self, reason: str) -> None:
        """
        Activa emergency halt. Requiere reinicio manual del bot.
        """
        self.emergency_halt = True
        self.emergency_reason = reason
        self.trading_halted = True
        self.halt_reason = f"EMERGENCY: {reason}"
        self._save_persisted_state()
