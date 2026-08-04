# Izonu

A live algorithmic trading strategy monitor built with FastAPI and Alpaca. Izonu tracks your portfolio, calculates the Sharpe ratio and related risk metrics, and automatically alerts you by email when your live strategy drifts too far from your backtest performance.

---

## What It Does

- Connects to your Alpaca paper or live trading account
- Serves a small web frontend: landing page, signup, login, and an authenticated dashboard
- Exposes REST endpoints to view your account, positions, and orders
- Calculates your annualised Sharpe ratio, Sortino ratio, max drawdown, and win rate from daily portfolio returns
- Reports execution drift: average order latency, average slippage, and rejection rate
- Compares your live Sharpe to your backtest Sharpe and triggers an email alert if drift is detected
- Runs the drift check automatically every day at 9 AM (America/New_York) via a background scheduler
- Supports multiple users, each with their own Alpaca credentials and alert settings stored in PostgreSQL
- Authenticates users with JWT bearer tokens issued at signup and login

---

## Tech Stack

- **FastAPI** - web framework
- **Alpaca Trade API** - brokerage connection
- **SQLAlchemy + PostgreSQL** - database
- **PyJWT** - token-based authentication
- **SendGrid** - email alerts
- **APScheduler** - scheduled daily monitor
- **Pydantic** - request validation
- **python-dotenv** - environment variable management
- **Static HTML/CSS/JS** - frontend served by FastAPI

---

## Project Structure

```
izonu/
  main.py          # all endpoints, auth, metrics, and scheduled monitor
  database.py      # database connection, session, and Base
  models.py        # SQLAlchemy User table definition
  static/
    landing.html   # marketing / entry page, served at /
    signup.html    # account registration
    login.html     # sign in
    dashboard.html # authenticated metrics dashboard
  requirements.txt # dependencies
  runtime.txt      # Python version for deployment
  mise.toml        # local toolchain config
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

JWT_SECRET=a_long_random_secret_string
```

The Alpaca keys above are used by the unauthenticated legacy endpoints (`/account`, `/positions`, `/orders`, `/sharpe`, `/monitor`). Per-user endpoints use the credentials each user supplies at registration, which are stored in the database.

`JWT_SECRET` is required for signup, login, and any authenticated endpoint. Requests fail if it is not set.

Backtest Sharpe, alert window, and alert threshold are **per user** and stored in the database — they are not environment variables.

**3. Run the app**

```bash
uvicorn main:app --reload
```

The app will be available at `http://127.0.0.1:8000`.

On startup it will automatically create the `users` table in PostgreSQL if it does not exist yet.

---

## Authentication

`POST /users/register` and `POST /users/login` both return a JWT:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

Tokens are signed with HS256 and expire after 7 days. Send them on protected endpoints:

```
Authorization: Bearer <access_token>
```

Passwords are stored as salted hashes; login returns the same generic error for an unknown email and a wrong password so registered emails are not leaked.

---

## Endpoints

### Pages

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Serves the landing page (`static/landing.html`) |
| GET | `/static/signup.html` | Signup page |
| GET | `/static/login.html` | Login page |
| GET | `/static/dashboard.html` | Authenticated dashboard |
| GET | `/docs` | Auto-generated interactive API docs |

### Users

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/users/register` | — | Registers a new user and returns an access token |
| POST | `/users/login` | — | Authenticates by email and password, returns an access token |

**Register request body:**

```json
{
  "email": "you@example.com",
  "password": "your_password",
  "alpaca_api_key": "your_key",
  "alpaca_secret_key": "your_secret",
  "alpaca_base_url": "https://paper-api.alpaca.markets",
  "backtest_sharpe": 1.84,
  "alert_days": 30,
  "alert_threshold": 0.3
}
```

`alpaca_base_url`, `alert_days`, and `alert_threshold` are optional and will use the defaults shown above if not provided.

**Login request body:**

```json
{
  "email": "you@example.com",
  "password": "your_password"
}
```

### Authenticated metrics

These identify the user from the JWT and query that user's own Alpaca account.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/metrics` | Sharpe ratio, Sortino ratio, max drawdown, and win rate for the last 30 days (change with `?days=N`) |
| GET | `/execution-drift` | Average order latency (seconds), average slippage, and rejection rate over the last 30 days (change with `?days=N`) |

If there is not enough trading history, both return a JSON body with an `error` field and a suggested larger `days` value rather than failing.

### Account (uses the server's `.env` Alpaca keys)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/account` | Returns account status, equity, cash, buying power, portfolio value |
| GET | `/account/raw` | Returns every field Alpaca provides on the account object |
| GET | `/positions` | Lists all open positions with P&L data |
| GET | `/orders` | Lists orders, filtered by `?status=open` (default), `closed`, or `all` |
| GET | `/sharpe` | Annualised Sharpe ratio for the last 30 days (change with `?days=N`) |
| GET | `/monitor` | Compares live Sharpe to backtest Sharpe and sends an email if drift is detected. Requires `?backtest_sharpe=X`. Optional: `?days=30&threshold=0.3` |

---

## How the Daily Monitor Works

Every day at 9 AM America/New_York, the scheduler:

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

The dashboard also reports the **Sortino ratio** (same idea but penalising only downside volatility), **max drawdown** (largest peak-to-trough decline), and **win rate** (share of days with a positive return).

---

## Notes

- Never commit your `.env` file. It is already in `.gitignore`.
- The `SENDER_EMAIL` must be verified in SendGrid before emails will send.
- The app defaults to Alpaca paper trading. To use live trading, set `ALPACA_BASE_URL=https://api.alpaca.markets`.
