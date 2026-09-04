from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from competitive_scoring.tools.market_data import (
    YahooChartClient,
    simple_moving_average,
    wilder_rsi,
)


def test_simple_moving_average_uses_latest_values() -> None:
    assert simple_moving_average([1, 2, 3, 4, 5], 3) == 4


def test_rsi_is_bounded() -> None:
    value = wilder_rsi([100, 101, 100, 102, 101, 103, 104, 103, 105, 106, 105, 107, 108, 109, 110])
    assert 0 <= value <= 100


def test_flat_series_has_neutral_rsi() -> None:
    assert wilder_rsi([100.0] * 20) == 50.0


def test_indicator_rejects_too_little_history() -> None:
    with pytest.raises(ValueError):
        simple_moving_average([1, 2], 20)


def _unix(day: date) -> int:
    return int(datetime.combine(day, datetime.min.time(), tzinfo=UTC).timestamp())


def test_fetch_is_as_of_bounded_and_prefers_adjusted_close() -> None:
    cutoff = date(2026, 8, 31)
    first_day = cutoff - timedelta(days=60)
    days = [first_day + timedelta(days=offset) for offset in range(62)]
    timestamps = [_unix(day) for day in days]

    # One missing price checks that timestamp and value filtering stays paired.
    # The final row is deliberately later than the cutoff and must be discarded.
    adjusted = [float(100 + offset) for offset in range(62)]
    adjusted[10] = None
    raw_close = [float(1000 + offset) for offset in range(62)]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["period1"] == str(_unix(cutoff - timedelta(days=365)))
        assert request.url.params["period2"] == str(_unix(cutoff + timedelta(days=1)))
        assert request.url.params["includeAdjustedClose"] == "true"
        return httpx.Response(
            200,
            json={
                "chart": {
                    "result": [
                        {
                            "timestamp": timestamps,
                            "indicators": {
                                "quote": [{"close": raw_close}],
                                "adjclose": [{"adjclose": adjusted}],
                            },
                        }
                    ]
                }
            },
        )

    snapshot = YahooChartClient(transport=httpx.MockTransport(handler)).fetch("PNGJL", as_of=cutoff)

    # 61 rows through the cutoff, less the missing adjusted-close row.
    assert snapshot.observation_count == 60
    assert snapshot.as_of == cutoff
    assert snapshot.close == 160.0
    assert snapshot.price_field == "adjusted_close"
    assert "period1=" in snapshot.source_url
    assert "period2=" in snapshot.source_url


def test_fetch_falls_back_to_close_and_uses_its_matching_timestamp() -> None:
    cutoff = date(2026, 8, 31)
    first_day = cutoff - timedelta(days=55)
    days = [first_day + timedelta(days=offset) for offset in range(56)]
    timestamps = [_unix(day) for day in days]
    raw_close = [float(200 + offset) for offset in range(56)]
    raw_close[-1] = None

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "chart": {
                    "result": [
                        {
                            "timestamp": timestamps,
                            "indicators": {
                                "quote": [{"close": raw_close}],
                                # Fewer than 50 adjusted rows triggers fallback.
                                "adjclose": [{"adjclose": [100.0] * 40}],
                            },
                        }
                    ]
                }
            },
        )

    snapshot = YahooChartClient(transport=httpx.MockTransport(handler)).fetch("PNGJL", as_of=cutoff)

    assert snapshot.price_field == "close"
    assert snapshot.observation_count == 55
    assert snapshot.as_of == days[-2]
    assert snapshot.close == raw_close[-2]
