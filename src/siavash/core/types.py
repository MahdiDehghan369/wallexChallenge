"""Shared enums (section 8.3 contract — do not change without a CONTRACT CHANGE entry)."""

from enum import Enum


class Mode(str, Enum):
    BACKTEST = "backtest"; PAPER = "paper"; LIVE = "live"; REPLAY = "replay"

class Side(str, Enum):
    BUY = "buy"; SELL = "sell"

class OrderType(str, Enum):
    LIMIT = "limit"; MARKET = "market"

class OrderStatus(str, Enum):
    NEW = "new"; PARTIALLY_FILLED = "partially_filled"; FILLED = "filled"
    CANCELED = "canceled"; REJECTED = "rejected"; EXPIRED = "expired"
    @property
    def is_terminal(self) -> bool:
        return self in {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}

class Timeframe(str, Enum):
    M1 = "1m"; M15 = "15m"; H1 = "1h"; H4 = "4h"
    @property
    def seconds(self) -> int:
        return {"1m": 60, "15m": 900, "1h": 3600, "4h": 14400}[self.value]

class Regime(str, Enum):
    TREND_UP = "TREND_UP"; RANGE = "RANGE"; HIGH_VOL = "HIGH_VOL"; PANIC = "PANIC"

class StrategyId(str, Enum):
    S1_TREND_BREAKOUT = "S1_TREND_BREAKOUT"
    S2_MEAN_REVERSION = "S2_MEAN_REVERSION"
    S3_IMPLIED_DEV = "S3_IMPLIED_DEV"

class PositionStatus(str, Enum):
    PENDING_ENTRY = "PENDING_ENTRY"; OPEN = "OPEN"; PENDING_EXIT = "PENDING_EXIT"
    CLOSED = "CLOSED"; CANCELLED = "CANCELLED"

class ExitReason(str, Enum):
    STOP_LOSS = "STOP_LOSS"; TAKE_PROFIT = "TAKE_PROFIT"; TP1_PARTIAL = "TP1_PARTIAL"
    TRAILING_STOP = "TRAILING_STOP"; TIME_STOP = "TIME_STOP"; SIGNAL = "SIGNAL"
    BREAKER = "BREAKER"; ENDGAME = "ENDGAME"; OPERATOR = "OPERATOR"

class DecisionType(str, Enum):
    ENTRY_CANDIDATE = "ENTRY_CANDIDATE"; EXIT = "EXIT"; REGIME_UPDATE = "REGIME_UPDATE"
    UNIVERSE_UPDATE = "UNIVERSE_UPDATE"; BREAKER = "BREAKER"
    OPERATOR_COMMAND = "OPERATOR_COMMAND"; ENDGAME = "ENDGAME"

class RejectStage(str, Enum):
    FEE_FILTER = "FEE_FILTER"; ML = "ML"; EV = "EV"; LLM = "LLM"; RISK = "RISK"; EXECUTION = "EXECUTION"

class BreakerState(str, Enum):
    NORMAL = "NORMAL"; NO_NEW_ENTRIES = "NO_NEW_ENTRIES"; FLATTEN_AND_HALT = "FLATTEN_AND_HALT"

class ComponentStatus(str, Enum):
    OK = "ok"; SKIPPED = "skipped"; FALLBACK = "fallback"; ERROR = "error"
