# NSE Options Trader 🚀

> **Production-grade, multi-agent quantitative options trading system for NIFTY & BANKNIFTY on NSE.**  
> Powered by **Dhan broker API**, **Google Gemini LLM**, **LangGraph**, and a **React dashboard**.

---

## ⚠️ Risk Warning

Options trading involves substantial risk of loss, including **unlimited risk on the sell side**. This system is provided as-is for educational and research purposes. **Always start in PAPER mode** and thoroughly backtest before connecting real capital.

---

## Features

| Feature | Details |
|---|---|
| **Broker** | Dhan (`dhanhq` SDK + WebSocket live feed) |
| **Underlyings** | NIFTY & BANKNIFTY options (weekly expiry) |
| **Strategies** | Iron Condor, Bull/Bear Spreads, Short Straddle/Strangle, VWAP Momentum |
| **AI Engine** | LangGraph + Google Gemini 1.5 Pro for strategy selection & validation |
| **Greeks** | Real-time Δ Γ Θ V ρ + IV Rank/Percentile via Black-Scholes |
| **Risk Mgmt** | Hard-gate agent with circuit breaker, delta/vega limits, margin checks |
| **Dashboard** | React + Recharts live P&L, positions, Greeks, signals, alerts |
| **DB** | TimescaleDB for time-series OHLCV + option chain history |
| **Cache** | Redis Streams (event bus) + Redis key-value (hot cache) |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          ORCHESTRATOR AGENT                             │
│              (LangGraph coordinator — manages all agents)               │
└────────────────────────────┬────────────────────────────────────────────┘
                             │ Redis Streams Event Bus
     ┌───────────────────────┼────────────────────────────┐
     ▼                       ▼                            ▼
┌──────────┐         ┌──────────────┐             ┌──────────────┐
│  Market  │         │   Strategy   │             │    Risk      │
│  Data    │────────▶│   Agent      │────────────▶│  Management  │
│  Agent   │         │ LangGraph+LLM│             │   Agent      │
└──────────┘         └──────────────┘             └──────┬───────┘
     │                                                   │
     ▼                                                   ▼
┌──────────┐         ┌──────────────┐             ┌──────────────┐
│  Greeks  │         │  Execution   │◀────────────│   Order      │
│  Engine  │         │   Agent      │             │  Manager     │
│  Agent   │         │ (Dhan API)   │             │   Agent      │
└──────────┘         └──────────────┘             └──────────────┘
                             │
                             ▼
                     ┌──────────────┐
                     │  Analytics   │
                     │   Agent      │
                     └──────────────┘
```

---

## Prerequisites

- Python 3.11+
- Docker & Docker Compose
- Node.js 20+ (for dashboard)
- A **Dhan** broker account with API access
- A **Google AI API key** (Gemini)

---

## Quick Start

### 1. Clone & configure

```bash
git clone <repo>
cd nse-options-trader
cp .env.example .env
# Edit .env with your API keys
```

### 2. Start infrastructure (Redis + TimescaleDB)

```bash
make up
```

Wait ~30 seconds for the DB to initialise, then:

```bash
make db-init
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Run in paper mode

```bash
python -m agents.orchestrator.agent
```

Or with the FastAPI server:

```bash
make dev
```

### 5. Start the dashboard

```bash
cd dashboard
npm install
npm run dev
# Open http://localhost:5173
```

---

## Configuration

All configuration is in `.env` (secrets) and `config/` (strategy/risk params).

### Key `.env` variables

| Variable | Description |
|---|---|
| `DHAN_CLIENT_ID` | Your Dhan client ID |
| `DHAN_ACCESS_TOKEN` | Dhan API access token |
| `GOOGLE_API_KEY` | Gemini API key |
| `TRADING_MODE` | `paper` (default) or `live` |
| `TOTAL_CAPITAL` | Starting capital in INR |
| `MAX_DAILY_LOSS_PCT` | Circuit breaker threshold (e.g. 5.0 = 5%) |

### Strategy configuration (`config/strategies.yaml`)

Enable/disable strategies and tune parameters:

```yaml
strategies:
  iron_condor:
    enabled: true
    min_iv_rank: 50       # Only trade when IV Rank > 50
    short_leg_offset: 100 # Sell 100 pts OTM
```

---

## Project Structure

```
nse-options-trader/
├── agents/
│   ├── base_agent.py           # Abstract base class for all agents
│   ├── market_data/agent.py    # Dhan WebSocket + option chain fetcher
│   ├── greeks_engine/          # Black-Scholes Greeks + IV solver
│   ├── strategy/               # LangGraph + Gemini strategy selection
│   │   └── strategies/         # Iron Condor, Spreads, Straddle, VWAP
│   ├── risk_management/        # Hard-gate pre-trade + real-time checks
│   ├── execution/brokers/      # Dhan broker wrapper
│   ├── order_manager/          # Order lifecycle + P&L tracking
│   ├── analytics/              # Metrics, EOD report, Telegram alerts
│   └── orchestrator/           # System coordinator
├── core/
│   ├── config.py               # Pydantic settings
│   ├── models.py               # Shared domain models
│   ├── enums.py                # All enumerations
│   ├── event_bus.py            # Redis Streams pub/sub
│   └── exceptions.py           # Custom exception hierarchy
├── api/
│   ├── main.py                 # FastAPI app + WebSocket
│   └── routes/                 # REST endpoints
├── dashboard/                  # Vite + React live dashboard
├── db/schema.sql               # TimescaleDB schema
├── config/
│   ├── strategies.yaml         # Strategy parameters
│   └── risk.yaml               # Risk limits
├── docker-compose.yml
├── Dockerfile
├── Makefile
└── requirements.txt
```

---

## Going Live

> ⚠️ **Read this section carefully before enabling live trading.**

1. **Paper trade for at least 2 weeks** with real market data.
2. Verify Greeks match Sensibull / Opstra reference values.
3. Review the risk config in `config/risk.yaml` carefully.
4. Set `TRADING_MODE=live` in `.env`.
5. Run:
   ```bash
   make live
   # You must type "I UNDERSTAND" to confirm
   ```

---

## Testing

```bash
make test
```

Runs:
- Unit tests for Black-Scholes Greeks (validated against known solutions)
- Unit tests for each strategy's signal logic
- Risk agent tests asserting hard blocks on limit breaches

---

## License

MIT — for educational and research purposes only. Use at your own risk.
