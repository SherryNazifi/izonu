# Izonu

A live algorithmic trading strategy monitor built with FastAPI and Alpaca. Izonu tracks your portfolio, calculates the Sharpe ratio, and automatically alerts you by email when your live strategy drifts too far from your backtest performance.

---

## What It Does

- Connects to your Alpaca paper or live trading account
- Exposes REST endpoints to view your account, positions, and orders
- Calculates your annualised Sharpe ratio from daily portfolio returns
- Compares your live Sharpe to your backtest Sharpe and triggers an email alert if drift is detected
- Runs the drift check automatically every day at 9 AM via a background scheduler
- Supports multiple users, each with their own Alpaca credentials and alert settings stored in PostgreSQL

---

## Tech Stack

- **FastAPI** - web framework
- **Alpaca Trade API** - brokerage connection
- **SQLAlchemy + PostgreSQL** - database
- **SendGrid** - email alerts
- **APScheduler** - scheduled daily monitor
- **Pydantic** - request validation
- **python-dotenv** - environment variable management

---

## Project Structure

```
izonu/
  main.py          # all endpoints and scheduled monitor
  database.py      # database connection, session, and Base
  models.py        # SQLAlchemy User table definition
  requirements.txt # dependencies
  .env             # secrets and config (never commit this)
  README.md
```

---

## Setup

**1. Clone the repo and install dependencies**

```bash
pip install -r requirements.txt
```

**2. Fill in your `.env` file**

```
ALPACA_API_KEY=your_alpaca_api_key
ALPACA_SECRET_KEY=your_alpaca_secret_key
ALPACA_BASE_URL=https://paper-api.alpaca.markets

SENDGRID_API_KEY=your_sendgrid_api_key
SENDER_EMAIL=your_sending_email
ALERT_EMAIL=your_receiving_email

DATABASE_URL=postgresql://postgres:password@localhost:5432/izonu

BACKTEST_SHARPE=1.84
ALERT_DAYS=30
ALERT_THRESHOLD=0.3
TIMEZONE=America/New_York
```

**3. Run the app**

```bash
uvicorn main:app --reload
```

The app will be available at `http://127.0.0.1:8000`.

On startup it will automatically create the `users` table in PostgreSQL if it does not exist yet.

---

## Endpoints

### General

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Health check |
| GET | `/docs` | Auto-generated interactive API docs |

### Account

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/account` | Returns account status, equity, cash, buying power, portfolio value |
| GET | `/account/raw` | Returns every field Alpaca provides on the account object |

### Positions and Orders

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/positions` | Lists all open positions with P&L data |
| GET | `/orders` | Lists orders, filtered by `?status=open` (default), `closed`, or `all` |

### Sharpe Ratio

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/sharpe` | Calculates annualised Sharpe ratio for the last 30 days (change with `?days=N`) |
| GET | `/sharpe/test` | Runs the Sharpe calculation on hardcoded fake returns to verify the math |

### Monitor

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/monitor` | Compares live Sharpe to backtest Sharpe and sends an email if drift is detected. Requires `?backtest_sharpe=X`. Optional: `?days=30&threshold=0.3` |
| GET | `/monitor/test` | Triggers a fake alert with hardcoded values to test the email integration |

### Users

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/users/register` | Registers a new user with their Alpaca credentials and alert settings |

**Register request body:**

```json
{
  "email": "you@example.com",
  "alpaca_api_key": "your_key",
  "alpaca_secret_key": "your_secret",
  "alpaca_base_url": "https://paper-api.alpaca.markets",
  "backtest_sharpe": 1.84,
  "alert_days": 30,
  "alert_threshold": 0.3
}
```

`alpaca_base_url`, `alert_days`, and `alert_threshold` are optional and will use the defaults shown above if not provided.

---

## How the Daily Monitor Works

Every day at 9 AM in the configured timezone, the scheduler:

1. Fetches every user from the database
2. Creates a separate Alpaca connection for each user using their own credentials
3. Calculates their live Sharpe ratio over their configured number of days
4. Compares it to their backtest Sharpe
5. Sends them an email alert if the live Sharpe has dropped more than their threshold percentage below the backtest

If a user does not have enough trading data yet, they are skipped silently and checked again the next day.

---

## Sharpe Ratio

The Sharpe ratio is calculated from daily portfolio returns using portfolio history from Alpaca:

```
Sharpe = (mean daily return / std daily return) * sqrt(252)
```

Multiplying by `sqrt(252)` annualises the result since there are 252 trading days in a year. This makes the number comparable to industry benchmarks.

| Sharpe | Interpretation |
|--------|----------------|
| Below 1 | Not great |
| 1 to 2 | Good |
| 2 to 3 | Very good |
| Above 3 | Exceptional |

---

## Notes

- Never commit your `.env` file. It is already in `.gitignore`.
- The `SENDER_EMAIL` must be verified in SendGrid before emails will send.
- The app defaults to Alpaca paper trading. To use live trading, set `ALPACA_BASE_URL=https://api.alpaca.markets`.
