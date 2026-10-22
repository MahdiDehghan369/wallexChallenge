"""Shared data models (section 8.3 contract — do not change without a CONTRACT CHANGE entry)."""

from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict
from .types import *

class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

class MarketInfo(_M):
    symbol: str                 # "BTC/TMN"
    exchange_symbol: str        # فرمت والکس، مثلاً "BTCTMN"
    base: str
    quote: str
    price_tick: Decimal         # کوچک‌ترین گام قیمت
    qty_step: Decimal           # کوچک‌ترین گام مقدار
    min_qty: Decimal
    min_notional: Decimal       # بر حسب quote
    is_active: bool
    is_spot: bool = True

class Candle(_M):
    symbol: str
    timeframe: Timeframe
    ts_open: datetime
    open: float; high: float; low: float; close: float
    volume: float               # بر حسب base
    is_filled: bool = False     # True اگر با forward-fill ساخته شده

class OrderBookLevel(_M):
    price: Decimal
    qty: Decimal

class OrderBook(_M):
    symbol: str
    ts: datetime
    bids: list[OrderBookLevel]  # نزولی بر اساس قیمت
    asks: list[OrderBookLevel]  # صعودی بر اساس قیمت
    @property
    def best_bid(self) -> Decimal | None: return self.bids[0].price if self.bids else None
    @property
    def best_ask(self) -> Decimal | None: return self.asks[0].price if self.asks else None
    @property
    def mid(self) -> Decimal | None:
        return (self.best_bid + self.best_ask) / 2 if self.bids and self.asks else None  # type: ignore[operator]
    @property
    def spread_pct(self) -> float | None:
        if not (self.bids and self.asks): return None
        return float((self.best_ask - self.best_bid) / self.mid * 100)  # type: ignore[operator]

class Ticker(_M):
    symbol: str
    ts: datetime
    last: Decimal
    bid: Decimal | None
    ask: Decimal | None
    volume_24h_quote: Decimal   # حجم ۲۴ساعته بر حسب quote

class PublicTrade(_M):
    symbol: str
    ts: datetime
    price: Decimal
    qty: Decimal
    side: Side | None           # سمت taker اگر API بدهد

class Balance(_M):
    asset: str
    free: Decimal
    locked: Decimal
    @property
    def total(self) -> Decimal: return self.free + self.locked

class OrderRequest(_M):
    symbol: str
    side: Side
    type: OrderType
    qty: Decimal
    price: Decimal | None       # برای LIMIT الزامی
    client_order_id: str
    decision_id: str
    purpose: Literal["entry", "exit", "tp1", "endgame", "operator", "smoke_test"]

class Order(_M):
    order_id: str | None        # شناسه صرافی
    client_order_id: str
    symbol: str
    side: Side
    type: OrderType
    price: Decimal | None
    qty: Decimal
    filled_qty: Decimal = Decimal("0")
    avg_fill_price: Decimal | None = None
    status: OrderStatus
    created_at: datetime
    updated_at: datetime

class Fill(_M):
    fill_id: str
    order_id: str | None
    client_order_id: str
    symbol: str
    side: Side
    price: Decimal
    qty: Decimal
    fee: Decimal
    fee_asset: str
    ts: datetime
    is_maker: bool | None = None

class Candidate(_M):
    candidate_id: str           # ULID
    ts: datetime                # زمان بسته شدن کندل سیگنال
    symbol: str
    strategy: StrategyId
    entry_ref_price: float
    stop_loss: float
    take_profit: float
    tp1_price: float | None = None
    time_stop_minutes: int
    expected_move_pct: float    # (take_profit/entry - 1) * 100
    conditions: dict[str, float | str | bool]  # شرط‌ها و مقادیرشان (برای لاگ)

class MLScore(_M):
    status: ComponentStatus
    model_version: str | None = None
    p_win: float | None = None
    threshold: float | None = None
    ev_pct: float | None = None
    top_features: list[tuple[str, float]] = Field(default_factory=list)

class RegimeAssessment(_M):
    ts: datetime
    regime: Regime
    risk_multiplier: float = Field(ge=0.0, le=1.0)
    enabled_strategies: list[StrategyId]
    reasons: list[str]
    source: Literal["llm", "rule", "fallback"]
    rule_regime: Regime
    status: ComponentStatus = ComponentStatus.OK

class TradeReview(_M):
    status: ComponentStatus
    verdict: Literal["APPROVE", "REJECT"]
    size_multiplier: float = Field(ge=0.25, le=1.0)
    reasons: list[str]
    risk_flags: list[str] = Field(default_factory=list)
    confidence: float | None = None
    model: str | None = None
    prompt_version: str | None = None
    cache_key: str | None = None

class PortfolioSnapshot(_M):
    ts: datetime
    equity_tmn: Decimal
    cash_tmn: Decimal
    peak_equity_tmn: Decimal
    exposure_tmn: Decimal
    open_positions: list["Position"]
    trades_last_hour: int
    trades_today: int
    daily_pnl_pct: float
    drawdown_pct: float         # از اوج، مثبت = افت
    breaker_state: BreakerState

class RiskDecision(_M):
    approved: bool
    size_tmn: Decimal = Decimal("0")
    qty: Decimal = Decimal("0")
    checks_failed: list[str] = Field(default_factory=list)   # کدهای RISK_*
    equity_tmn: Decimal

class Position(BaseModel):     # قابل تغییر (state)
    model_config = ConfigDict(extra="forbid")
    position_id: str
    symbol: str
    strategy: StrategyId
    status: PositionStatus
    entry_decision_id: str
    qty: Decimal = Decimal("0")
    avg_entry: Decimal | None = None
    stop_loss: Decimal
    take_profit: Decimal
    tp1_price: Decimal | None = None
    tp1_done: bool = False
    trailing_stop: Decimal | None = None
    highest_price: Decimal | None = None
    time_stop_at: datetime
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    exit_reason: ExitReason | None = None
    realized_pnl_tmn: Decimal = Decimal("0")
    fees_tmn: Decimal = Decimal("0")
    order_client_ids: list[str] = Field(default_factory=list)

class DecisionRecord(BaseModel):
    """اسکیمای هر خط logs/decisions.jsonl — جزئیات در ضمیمه C"""
    model_config = ConfigDict(extra="forbid")
    decision_id: str
    ts: datetime
    mode: Mode
    git_sha: str
    version_tag: str | None
    config_hash: str
    type: DecisionType
    symbol: str | None = None
    strategy: StrategyId | None = None
    regime: RegimeAssessment | None = None
    signal: Candidate | None = None
    ml: MLScore | None = None
    llm_review: TradeReview | None = None
    risk: RiskDecision | None = None
    position_id: str | None = None
    exit_reason: ExitReason | None = None
    outcome_action: Literal["ORDER_PLACED", "REJECTED", "EXITED", "INFO"]
    rejected_at: RejectStage | None = None
    reject_code: str | None = None
    client_order_ids: list[str] = Field(default_factory=list)
    final_reason: str           # یک جمله خوانا برای داور
    extra: dict = Field(default_factory=dict)

PortfolioSnapshot.model_rebuild()
