from typing import Dict
from collections import defaultdict
from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import alpaca_trade_api as tradeapi
import os
import math
import statistics
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
from database import engine, Base, get_db, SessionLocal
import models

load_dotenv()

app = FastAPI(title="Izonu", description="Live algo trading strategy monitor for Alpaca")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

api = tradeapi.REST(
    key_id=os.getenv("ALPACA_API_KEY"),
    secret_key=os.getenv("ALPACA_SECRET_KEY"),
    base_url=os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
)


def _scheduled_monitor():
    """
    Runs every day at 9 AM automatically.
    Loops through every user in the database, checks for strategy drift
    using their own Alpaca credentials and settings, and emails them if alert triggers.
    """
    db = SessionLocal()
    try:
        users = db.query(models.User).all()
        for user in users:
            # Create a per-user Alpaca client using their own credentials
            user_api = tradeapi.REST(
                key_id=user.alpaca_api_key,
                secret_key=user.alpaca_secret_key,
                base_url=user.alpaca_base_url,
            )

            try:
                live_sharpe, *_ = _compute_live_metrics(user.alert_days, api_client=user_api)
            except ValueError:
                # Not enough data or no variance for this user — skip and move to next
                continue

            minimum_acceptable = user.backtest_sharpe * (1 - user.alert_threshold)
            alert = live_sharpe < minimum_acceptable

            if alert:
                pct_below = round((user.backtest_sharpe - live_sharpe) / user.backtest_sharpe * 100, 2)
                _send_alert_email(live_sharpe, user.backtest_sharpe, pct_below, user.alert_threshold, recipient=user.email)
    finally:
        db.close()


# Create the scheduler but don't start it yet — startup event handles that
scheduler = BackgroundScheduler()
scheduler.add_job(_scheduled_monitor, CronTrigger(hour=9, minute=0, timezone="America/New_York"))


@app.on_event("startup")
def start_scheduler():
    """Start the background scheduler when the app starts."""
    scheduler.start()
    # Create all tables in the database if they don't exist yet
    Base.metadata.create_all(bind=engine)


@app.on_event("shutdown")
def stop_scheduler():
    """Stop the background scheduler cleanly when the app stops."""
    scheduler.shutdown()


class UserRegister(BaseModel):
    email: str
    alpaca_api_key: str
    alpaca_secret_key: str
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    backtest_sharpe: float
    alert_days: int = 30
    alert_threshold: float = 0.3


@app.delete("/admin/users")
def delete_all_users(db: Session = Depends(get_db)):
    """Delete all users from the database. For development use only."""
    count = db.query(models.User).delete()
    db.commit()
    return {"message": f"Deleted {count} user(s)."}


@app.post("/users/register")
def register_user(user: UserRegister, db: Session = Depends(get_db)):
    """Register a new user and save their settings to the database."""

    # Check if email already exists
    existing = db.query(models.User).filter(models.User.email == user.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered.")

    new_user = models.User(
        email=user.email,
        alpaca_api_key=user.alpaca_api_key,
        alpaca_secret_key=user.alpaca_secret_key,
        alpaca_base_url=user.alpaca_base_url,
        backtest_sharpe=user.backtest_sharpe,
        alert_days=user.alert_days,
        alert_threshold=user.alert_threshold,
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {
        "message": "User registered successfully.",
        "id": new_user.id,
        "email": new_user.email,
        "created_at": new_user.created_at,
    }


@app.get("/")
def root():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


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


def _compute_live_metrics(days: int, api_client=None):
    """
    Shared helper used by /metrics, /sharpe, and _scheduled_monitor.
    Returns (sharpe, sortino, max_drawdown, win_rate, trading_days) on success,
    or raises a ValueError with a message.
    api_client defaults to the global api if not provided.
    """
    if api_client is None:
        api_client = api

    # Portfolio history — used for sharpe, sortino, max drawdown
    history = api_client.get_portfolio_history(period=f"{days}D", timeframe="1D")
    returns = [r for r in history.profit_loss_pct if r is not None]

    if len(returns) < 2:
        raise ValueError(f"not_enough_data|{len(returns)}")

    mean_r = statistics.mean(returns)
    std_r = statistics.stdev(returns)

    if std_r == 0:
        raise ValueError("no_variance")

    sharpe = round((mean_r / std_r) * math.sqrt(252), 4)

    # Sortino: same annualisation but uses only downside deviation
    downside = [r for r in returns if r < 0]
    if len(downside) >= 2:
        downside_std = statistics.stdev(downside)
        sortino = round((mean_r / downside_std) * math.sqrt(252), 4) if downside_std > 0 else None
    else:
        sortino = None  # too few losing days to measure downside risk

    # Max drawdown from the actual equity curve
    equity = [e for e in history.equity if e is not None]
    peak = equity[0]
    max_dd = 0.0
    for val in equity:
        if val > peak:
            peak = val
        dd = (peak - val) / peak
        if dd > max_dd:
            max_dd = dd
    max_drawdown = round(max_dd, 4)

    # Win rate from list_orders — FIFO-matched buy/sell pairs per symbol
    orders = api_client.list_orders(status="closed")
    filled = [o for o in orders if o.status == "filled" and o.filled_avg_price is not None]

    by_symbol = defaultdict(list)
    for o in filled:
        by_symbol[o.symbol].append(o)

    wins = 0
    total_matched = 0
    for symbol_orders in by_symbol.values():
        symbol_orders.sort(key=lambda o: o.filled_at)
        buy_queue = []   # open longs: [price, remaining_qty]
        sell_queue = []  # open shorts: [price, remaining_qty]
        for o in symbol_orders:
            price = float(o.filled_avg_price)
            qty = float(o.qty)
            if o.side == "buy":
                # First try to close any open shorts
                remaining = qty
                while remaining > 0 and sell_queue:
                    short_price, short_qty = sell_queue[0]
                    matched = min(remaining, short_qty)
                    total_matched += 1
                    if price < short_price:  # bought back lower than shorted — win
                        wins += 1
                    remaining -= matched
                    sell_queue[0][1] -= matched
                    if sell_queue[0][1] <= 0:
                        sell_queue.pop(0)
                # Any leftover qty is a new long entry
                if remaining > 0:
                    buy_queue.append([price, remaining])
            elif o.side == "sell":
                # First try to close any open longs (long exit takes priority)
                remaining = qty
                while remaining > 0 and buy_queue:
                    buy_price, buy_qty = buy_queue[0]
                    matched = min(remaining, buy_qty)
                    total_matched += 1
                    if price > buy_price:  # sold higher than bought — win
                        wins += 1
                    remaining -= matched
                    buy_queue[0][1] -= matched
                    if buy_queue[0][1] <= 0:
                        buy_queue.pop(0)
                # Any leftover qty is a new short entry
                if remaining > 0:
                    sell_queue.append([price, remaining])

    win_rate = round(wins / total_matched, 4) if total_matched > 0 else None

    return sharpe, sortino, max_drawdown, win_rate, len(returns)


@app.get("/sharpe")
def get_sharpe(days: int = 30):
    """Calculate annualised Sharpe ratio from daily portfolio returns over the last N days."""
    try:
        sharpe, _sortino, _max_dd, _win_rate, trading_days = _compute_live_metrics(days)
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


@app.get("/metrics")
def get_metrics(days: int = 30):
    """Return Sharpe ratio, Sortino ratio, max drawdown, and win rate for the last N days."""
    try:
        sharpe, sortino, max_drawdown, win_rate, trading_days = _compute_live_metrics(days)
    except ValueError as e:
        msg = str(e)
        if msg.startswith("not_enough_data"):
            found = msg.split("|")[1]
            return {
                "error": "Not enough trading data to calculate metrics.",
                "trading_days_found": int(found),
                "days_requested": days,
                "suggestion": f"Try increasing the days parameter beyond {days}.",
            }
        return {
            "error": "No variance in daily returns — cannot calculate metrics.",
            "reason": "This usually means no trades were executed in this period.",
        }

    return {
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown": max_drawdown,
        "win_rate": win_rate,
        "trading_days_analyzed": trading_days,
        "period_days_requested": days,
    }


def _execution_drift(days: int, api_client=None):
    """
    Returns (avg_latency_seconds, avg_slippage, rejection_rate) for orders over the last N days.
    Raises ValueError("no_orders") if no orders exist in the period.
    api_client defaults to the global api if not provided.
    """
    if api_client is None:
        api_client = api

    orders = api_client.list_orders(status="all", limit=500, after=(
        (lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ"))
        (__import__("datetime").datetime.utcnow() - __import__("datetime").timedelta(days=days))
    ))

    if not orders:
        raise ValueError("no_orders")

    # Latency: filled_at - submitted_at in seconds, for filled orders only
    latency_samples = []
    for o in orders:
        if o.status == "filled" and o.filled_at and o.submitted_at:
            delta = (o.filled_at - o.submitted_at).total_seconds()
            latency_samples.append(delta)
    avg_latency = round(statistics.mean(latency_samples), 4) if latency_samples else None

    # Slippage: limit orders only
    # Buy: filled_avg_price - limit_price (positive = paid more than limit)
    # Sell: limit_price - filled_avg_price (positive = received less than limit)
    slippage_samples = []
    for o in orders:
        if o.type == "limit" and o.status == "filled" and o.limit_price and o.filled_avg_price:
            fill = float(o.filled_avg_price)
            limit = float(o.limit_price)
            if o.side == "buy":
                slippage_samples.append(fill - limit)
            elif o.side == "sell":
                slippage_samples.append(limit - fill)
    avg_slippage = round(statistics.mean(slippage_samples), 4) if slippage_samples else None

    # Rejection rate: orders that were not filled / total orders
    not_filled = [o for o in orders if o.status not in ("filled", "partially_filled")]
    rejection_rate = round(len(not_filled) / len(orders), 4)

    return avg_latency, avg_slippage, rejection_rate


@app.get("/execution-drift")
def get_execution_drift(days: int = 30):
    """Return avg order latency, avg slippage, and rejection rate over the last N days."""
    try:
        avg_latency, avg_slippage, rejection_rate = _execution_drift(days)
    except ValueError:
        return {
            "error": "No orders found in the requested period.",
            "days_requested": days,
            "suggestion": f"Try increasing the days parameter beyond {days}.",
        }

    return {
        "avg_latency_seconds": avg_latency,
        "avg_slippage": avg_slippage,
        "rejection_rate": rejection_rate,
        "period_days_requested": days,
    }


def _send_alert_email(live_sharpe: float, backtest_sharpe: float, pct_below: float, threshold: float, recipient: str = None):
    """Send a SendGrid alert email when strategy drift is detected."""
    sg = SendGridAPIClient(os.getenv("SENDGRID_API_KEY"))
    sender = os.getenv("SENDER_EMAIL")
    # Fall back to ALERT_EMAIL from .env if no recipient provided
    if recipient is None:
        recipient = os.getenv("ALERT_EMAIL")

    body = f"""
Izonu has detected strategy drift in your live trading account.

Live Sharpe Ratio:     {live_sharpe}
Backtest Sharpe Ratio: {backtest_sharpe}
Drop:                  {pct_below}% below backtest
Threshold:             {int(threshold * 100)}%

Your live Sharpe has fallen more than {int(threshold * 100)}% below your backtest Sharpe.
This may indicate the strategy is no longer performing as expected.

— Izonu Monitor
    """.strip()

    message = Mail(
        from_email=sender,
        to_emails=recipient,
        subject="Izonu Alert: Strategy Drift Detected",
        plain_text_content=body,
    )

    sg.send(message)


@app.get("/monitor")
def monitor(backtest_sharpe: float, days: int = 30, threshold: float = 0.3):
    """
    Compare live Sharpe to backtest Sharpe.
    Triggers an alert if live Sharpe has dropped more than threshold% below backtest.
    Sends a SendGrid email when an alert is triggered.
    """

    # Calculate live Sharpe — reuse the same helper as /sharpe
    try:
        live_sharpe, _sortino, _max_dd, _win_rate, trading_days = _compute_live_metrics(days)
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

    # Send email if alert triggered
    email_status = None
    if alert:
        try:
            _send_alert_email(live_sharpe, backtest_sharpe, pct_below, threshold)
            email_status = f"Alert email sent to {os.getenv('ALERT_EMAIL')}"
        except Exception as e:
            email_status = f"Email failed to send: {str(e)}"

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
        "email_status": email_status,
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
