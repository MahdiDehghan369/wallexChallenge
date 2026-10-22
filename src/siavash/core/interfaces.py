"""Module interfaces (section 8.3 contract — do not change without a CONTRACT CHANGE entry)."""

from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Protocol, Mapping
from .models import *
from .types import *

FeatureRow = Mapping[str, float]        # ویژگی‌های یک (symbol, ts)

class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime: ...                  # UTC
    @abstractmethod
    async def sleep(self, seconds: float) -> None: ...

class MarketData(ABC):
    @abstractmethod
    async def list_markets(self) -> list[MarketInfo]: ...
    @abstractmethod
    async def get_candles(self, symbol: str, tf: Timeframe, start: datetime, end: datetime) -> list[Candle]: ...
    @abstractmethod
    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook: ...
    @abstractmethod
    async def get_ticker(self, symbol: str) -> Ticker: ...
    @abstractmethod
    async def get_recent_trades(self, symbol: str, limit: int = 100) -> list[PublicTrade]: ...

class Broker(ABC):
    """Live، Paper و Backtest همه این را پیاده می‌کنند. موتور فقط این را می‌شناسد."""
    @abstractmethod
    async def place_order(self, req: OrderRequest) -> Order: ...
    @abstractmethod
    async def cancel_order(self, symbol: str, client_order_id: str) -> Order: ...
    @abstractmethod
    async def get_order(self, symbol: str, client_order_id: str) -> Order | None: ...
    @abstractmethod
    async def get_open_orders(self, symbol: str | None = None) -> list[Order]: ...
    @abstractmethod
    async def get_balances(self) -> dict[str, Balance]: ...
    @abstractmethod
    async def get_fills(self, since: datetime) -> list[Fill]: ...

class SignalGenerator(ABC):
    strategy_id: StrategyId
    @abstractmethod
    def generate(self, symbol: str, ts: datetime, features: FeatureRow,
                 regime: RegimeAssessment) -> list[Candidate]: ...

class Scorer(Protocol):
    def score(self, c: Candidate, features: FeatureRow) -> MLScore: ...

class RegimeProvider(Protocol):
    async def assess(self, market_features: FeatureRow, ts: datetime) -> RegimeAssessment: ...

class Reviewer(Protocol):
    async def review(self, c: Candidate, features: FeatureRow, ml: MLScore,
                     regime: RegimeAssessment, portfolio: PortfolioSnapshot) -> TradeReview: ...

class RiskManager(Protocol):
    def evaluate(self, c: Candidate, ml: MLScore, review: TradeReview | None,
                 regime: RegimeAssessment, portfolio: PortfolioSnapshot,
                 market: MarketInfo, book: OrderBook) -> RiskDecision: ...

class DecisionLogger(Protocol):
    def write(self, record: DecisionRecord) -> None: ...
