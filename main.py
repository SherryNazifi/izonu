from typing import Dict
from fastapi import FastAPI
from dotenv import load_dotenv
import alpaca_trade_api as tradeapi
import os
import math
import statistics

load_dotenv()

app = FastAPI(title="Izonu", description="Live algo trading strategy monitor for Alpaca")

api = tradeapi.REST(
    key_id=os.getenv("ALPACA_API_KEY"),
    secret_key=os.getenv("ALPACA_SECRET_KEY"),
    base_url=os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
)


@app.get("/")
def root():
    return {"message": "Izonu is running"}


@app.get("/account")
def get_account():
    """Return current Alpaca account details."""
    account = api.get_account()
    return {
        "status": account.status,
        "equity": account.equity,
        "cash": account.cash,
        "buying_power": account.buying_power,
        "portfolio_value": account.portfolio_value,
    }

@app.get("/account/raw")
def get_account_raw():
    """Return current Alpaca account details in raw format."""
    account = api.get_account()
    return dict(account._raw)

@app.get("/positions")
def get_positions():
    """Return all open positions."""
    positions = api.list_positions()
    return [
        {
            "symbol": p.symbol,
            "qty": p.qty,
            "side": p.side,
            "market_value": p.market_value,
            "unrealized_pl": p.unrealized_pl,
            "unrealized_plpc": p.unrealized_plpc,
            "avg_entry_price": p.avg_entry_price,
            "current_price": p.current_price,
        }
        for p in positions
    ]


def _compute_live_sharpe(days: int):
    """
    Shared helper used by /sharpe and /monitor.
    Returns (sharpe, trading_days) on success, or raises a ValueError with a message.
    """
    history = api.get_portfolio_history(period=f"{days}D", timeframe="1D")
    returns = [r for r in history.profit_loss_pct if r is not None]

    if len(returns) < 2:
        raise ValueError(
            f"not_enough_data|{len(returns)}"
        )

    mean_r = statistics.mean(returns)
    std_r = statistics.stdev(returns)

    if std_r == 0:
        raise ValueError("no_variance")

    sharpe = round((mean_r / std_r) * math.sqrt(252), 4)
    return sharpe, len(returns)


@app.get("/sharpe")
def get_sharpe(days: int = 30):
    """Calculate annualised Sharpe ratio from daily portfolio returns over the last N days."""
    try:
        sharpe, trading_days = _compute_live_sharpe(days)
    except ValueError as e:
        msg = str(e)
        if msg.startswith("not_enough_data"):
            found = msg.split("|")[1]
            return {
                "error": "Not enough trading data to calculate Sharpe ratio.",
                "trading_days_found": int(found),
                "days_requested": days,
                "suggestion": f"Try increasing the days parameter beyond {days}.",
            }
        return {
            "error": "No variance in daily returns — cannot calculate Sharpe ratio.",
            "reason": "This usually means no trades were executed in this period.",
        }

    return {
        "sharpe_ratio": sharpe,
        "trading_days_analyzed": trading_days,
        "period_days_requested": days,
    }


@app.get("/monitor")
def monitor(backtest_sharpe: float, days: int = 30, threshold: float = 0.3):
    """
    Compare live Sharpe to backtest Sharpe.
    Triggers an alert if live Sharpe has dropped more than threshold% below backtest.
    """

    # Calculate live Sharpe — reuse the same helper as /sharpe
    try:
        live_sharpe, trading_days = _compute_live_sharpe(days)
    except ValueError as e:
        msg = str(e)
        if msg.startswith("not_enough_data"):
            found = msg.split("|")[1]
            return {
                "alert": False,
                "error": "Not enough trading data to calculate live Sharpe ratio.",
                "trading_days_found": int(found),
                "days_requested": days,
                "suggestion": f"Try increasing the days parameter beyond {days}.",
            }
        return {
            "alert": False,
            "error": "No variance in daily returns — cannot calculate live Sharpe ratio.",
            "reason": "This usually means no trades were executed in this period.",
        }

    # The lowest acceptable live Sharpe before the alarm triggers
    # e.g. backtest=1.84, threshold=0.3 → minimum = 1.84 * 0.7 = 1.288
    minimum_acceptable = backtest_sharpe * (1 - threshold)
    alert = live_sharpe < minimum_acceptable

    # How far below the backtest the live Sharpe is, as a percentage
    pct_below = round((backtest_sharpe - live_sharpe) / backtest_sharpe * 100, 2)

    return {
        "alert": alert,
        "message": (
            f"ALERT: Live Sharpe ({live_sharpe}) is {pct_below}% below backtest ({backtest_sharpe}), "
            f"exceeding the {int(threshold * 100)}% threshold."
            if alert else
            f"OK: Live Sharpe ({live_sharpe}) is within acceptable range of backtest ({backtest_sharpe})."
        ),
        "backtest_sharpe": backtest_sharpe,
        "live_sharpe": live_sharpe,
        "minimum_acceptable_sharpe": round(minimum_acceptable, 4),
        "pct_below_backtest": pct_below,
        "threshold_pct": int(threshold * 100),
        "trading_days_analyzed": trading_days,
    }


@app.get("/sharpe/test")
def get_sharpe_test():
    """Verify Sharpe math using hardcoded fake daily returns."""

    # Fake daily returns as decimals (e.g. 0.01 = 1%)
    returns = [0.01, -0.005, 0.02, 0.003, -0.01, 0.015, 0.008, -0.003, 0.012, 0.007]

    mean_r = statistics.mean(returns)
    std_r = statistics.stdev(returns)
    sharpe = (mean_r / std_r) * math.sqrt(252)

    return {
        "note": "These are hardcoded fake returns to verify the math",
        "fake_returns": returns,
        "mean_daily_return_pct": round(mean_r * 100, 4),
        "std_daily_return_pct": round(std_r * 100, 4),
        "sharpe_ratio": round(sharpe, 4),
    }


@app.get("/orders")
def get_orders(status: str = "open"):
    """Return orders filtered by status (open, closed, all)."""
    orders = api.list_orders(status=status)
    return [
        {
            "id": o.id,
            "symbol": o.symbol,
            "qty": o.qty,
            "side": o.side,
            "type": o.type,
            "status": o.status,
            "submitted_at": o.submitted_at,
            "filled_at": o.filled_at,
            "filled_avg_price": o.filled_avg_price,
        }
        for o in orders
    ]
