"""Daily NSE price adapter and local technical-indicator calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from math import isfinite
from statistics import fmean
from typing import Any, Literal
from urllib.parse import quote, urlencode

import httpx


@dataclass(frozen=True, slots=True)
class TechnicalSnapshot:
    """Calculated indicators plus enough provenance to reproduce them."""

    ticker: str
    as_of: date
    close: float
    sma20: float
    sma50: float
    rsi14: float
    source_url: str
    price_field: Literal["adjusted_close", "close"]
    observation_count: int


def simple_moving_average(values: list[float], window: int) -> float:
    """Average the most recent `window` closes."""

    if len(values) < window:
        raise ValueError(f"Need at least {window} closing prices; got {len(values)}.")
    return fmean(values[-window:])


def wilder_rsi(values: list[float], period: int = 14) -> float:
    """Calculate Wilder's RSI, the conventional interpretation of RSI(14)."""

    if len(values) < period + 1:
        raise ValueError(f"Need at least {period + 1} closes to calculate RSI.")

    changes = [current - previous for previous, current in pairwise(values)]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]

    average_gain = fmean(gains[:period])
    average_loss = fmean(losses[:period])
    for gain, loss in zip(gains[period:], losses[period:]):
        average_gain = ((period - 1) * average_gain + gain) / period
        average_loss = ((period - 1) * average_loss + loss) / period

    # A perfectly flat series has neither gains nor losses.  Treating it as
    # overbought (RSI 100) would be misleading, so return the neutral midpoint.
    if average_gain == 0 and average_loss == 0:
        return 50.0
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


class YahooChartClient:
    """Fetch public daily candles for locally calculated technical indicators."""

    def __init__(self, *, timeout: float = 20.0, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def fetch(self, ticker: str, as_of: date | None = None) -> TechnicalSnapshot:
        """Fetch an as-of-bounded daily series and calculate technicals.

        Yahoo's ``period2`` parameter is exclusive.  We therefore request up
        to midnight immediately after ``as_of`` and independently discard any
        observation dated later than the requested cutoff.  The local filter
        is intentional defensive validation: an upstream API must never make
        a historical research run silently consume future prices.
        """

        requested_as_of = as_of or datetime.now(tz=UTC).date()
        symbol = ticker if ticker.endswith(".NS") else f"{ticker}.NS"

        # One calendar year comfortably covers the 50 trading sessions needed
        # for SMA(50), including exchange holidays and occasional missing rows.
        period1 = int(
            datetime.combine(
                requested_as_of - timedelta(days=365), time.min, tzinfo=UTC
            ).timestamp()
        )
        period2 = int(
            datetime.combine(requested_as_of + timedelta(days=1), time.min, tzinfo=UTC).timestamp()
        )
        query = urlencode(
            {
                "period1": period1,
                "period2": period2,
                "interval": "1d",
                "events": "div,splits",
                "includeAdjustedClose": "true",
            }
        )
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?{query}"
        response = self._client.get(url, headers={"User-Agent": "competitive-scoring/0.1"})
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        result = payload["chart"]["result"][0]

        timestamps = result.get("timestamp") or []
        indicators = result.get("indicators") or {}
        quote_rows = indicators.get("quote") or []
        adjusted_rows = indicators.get("adjclose") or []

        close_values = quote_rows[0].get("close", []) if quote_rows else []
        adjusted_values = adjusted_rows[0].get("adjclose", []) if adjusted_rows else []

        def usable_observations(values: list[Any]) -> list[tuple[int, float]]:
            """Keep timestamps and prices paired while removing unusable rows."""

            observations: list[tuple[int, float]] = []
            for raw_timestamp, raw_value in zip(timestamps, values, strict=False):
                if raw_timestamp is None or raw_value is None:
                    continue
                timestamp = int(raw_timestamp)
                observation_date = datetime.fromtimestamp(timestamp, tz=UTC).date()
                value = float(raw_value)
                if observation_date <= requested_as_of and isfinite(value):
                    observations.append((timestamp, value))

            # Sorting makes the calculation robust even if the API response is
            # not ordered.  A mapping also prevents duplicate timestamps from
            # counting as multiple trading sessions.
            return sorted(dict(observations).items())

        adjusted = usable_observations(adjusted_values)
        unadjusted = usable_observations(close_values)

        # Adjusted close is the right default for return-style indicators
        # because it accounts for splits and distributions.  If Yahoo does not
        # provide enough adjusted observations, fall back transparently to the
        # raw close series and label that choice in the result.
        if len(adjusted) >= 50:
            observations = adjusted
            price_field: Literal["adjusted_close", "close"] = "adjusted_close"
        else:
            observations = unadjusted
            price_field = "close"

        if len(observations) < 50:
            raise ValueError(f"Only {len(observations)} usable sessions returned for {ticker}.")

        closes = [value for _, value in observations]
        latest_timestamp = observations[-1][0]
        observation_as_of = datetime.fromtimestamp(latest_timestamp, tz=UTC).date()
        return TechnicalSnapshot(
            ticker=ticker,
            as_of=observation_as_of,
            close=round(closes[-1], 2),
            sma20=round(simple_moving_average(closes, 20), 2),
            sma50=round(simple_moving_average(closes, 50), 2),
            rsi14=round(wilder_rsi(closes, 14), 2),
            source_url=url,
            price_field=price_field,
            observation_count=len(observations),
        )
