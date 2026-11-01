# Project: AI Investment and Portfolio Management Agent

Act as a Senior Software Architect and Senior Full-Stack Engineer. You are responsible for building a professional, modular AI-powered investment and portfolio-management application for the Indian stock market (NSE).

I am developing this project on a MacBook M1. You have access to the local project directory.

## 1. Product vision

Build an intelligent investment system that will eventually support:

1. **Intraday trading:** Analyse market movements and identify short-term trading opportunities.
2. **Swing trading:** Identify opportunities over several days or weeks.
3. **Long-term investing:** Evaluate companies using financial fundamentals, valuation and business quality.
4. **Stock ranking and rotation:** Compare stocks and identify when reallocating capital from a weaker holding to a stronger opportunity may be justified.
5. **Portfolio management:** Track capital, holdings, realised/unrealised P&L, allocation and investment performance.
6. **Risk management:** Protect capital through independent risk controls.
7. **Profit Vault:** Track realised profits separately from the original investment/trading capital.

The eventual application may integrate with Groww for market data and order execution, subject to API availability, permissions and applicable regulatory requirements.

## 2. Technology stack

Use the following initial technology choices:

**Frontend**

* React
* TypeScript
* Vite
* TradingView Lightweight Charts (when charting is introduced)

**Backend**

* Python 3.12
* FastAPI
* Pydantic
* SQLAlchemy

**Database**

* SQLite initially
* No paid database or cloud infrastructure.

**Data science and machine learning (future phases)**

* Pandas
* NumPy
* Scikit-learn
* LightGBM
* XGBoost

**Testing and development**

* Pytest
* Git and GitHub
* Appropriate frontend testing tools.

## 3. Architecture requirements

Design a modular architecture with clear separation between:

* Market data collection
* Historical data storage
* Technical analysis
* Fundamental analysis
* Intraday forecasting
* Swing forecasting
* Long-term forecasting
* Stock ranking and rotation
* Portfolio management
* Risk management
* Backtesting
* Paper trading
* Broker integration
* AI-powered financial research

The risk-management module must independently validate every proposed trade. Neither an AI model nor a forecasting engine can bypass risk controls.

Machine-learning models must handle numerical forecasting. An LLM may later assist with news, financial announcements, company results and market context. It must not independently decide or execute trades.

Never promise guaranteed returns.

## 4. Current development environment

* MacBook M1
* macOS
* Python 3.12 installed
* Project virtual environment `.venv` already created
* Node.js and npm installed
* Git configured
* GitHub SSH authentication working
* Private GitHub repository: https://github.com/Mayank244Singh/investment-agent
* Local project directory: `/Users/mayanksingh/Investment/investment-agent`

Preserve the existing Git repository and `.venv`. Do not overwrite or delete existing configuration.

## 5. FIRST MILESTONE: Project foundation

Start ONLY with the project foundation.

Your responsibilities:

1. Inspect the existing project directory.
2. Inspect the installed Python, Node.js, npm and Git versions.
3. Verify that the existing Python virtual environment works.
4. Design a clean, modular directory structure.
5. Set up a FastAPI backend.
6. Set up a React + TypeScript + Vite frontend.
7. Configure SQLite and SQLAlchemy.
8. Add environment-based configuration.
9. Add structured application logging.
10. Configure backend and frontend testing.
11. Add a `.gitignore` that excludes `.venv`, environment files, secrets, generated data and temporary files.
12. Create a useful README.md explaining installation, architecture and how to run the application.
13. Create `docs/PROJECT_STATUS.md` to track progress, decisions, known issues and the next development task.
14. Add basic health-check functionality to the backend.
15. Create a minimal frontend dashboard shell that can communicate with the backend.

## 6. Initial dashboard

Create a clean, professional financial application interface.

The initial dashboard can contain:

* Application name: Investment Agent
* Market overview section
* Portfolio summary section
* Intraday engine status
* Swing engine status
* Long-term engine status
* Risk manager status
* Recent activity section

Important: These are initial UI placeholders. Do not fabricate live market prices, portfolio balances, trading signals or performance numbers. Clearly label unavailable data as unavailable.

## 7. Engineering standards

* Follow clean architecture and SOLID principles where appropriate.
* Use type hints.
* Keep modules small and maintainable.
* Avoid unnecessary abstractions.
* Write meaningful tests.
* Handle exceptions properly.
* Never hardcode passwords, API keys or credentials.
* Never use unverified financial data.
* Do not scrape websites without authorization.
* Keep business logic separate from API routes and UI components.
* Document important architectural decisions.
* Make the application easy to extend.

## 8. Strict development boundaries

For this milestone:

* Do not implement stock prediction.
* Do not implement trading strategies.
* Do not connect to Groww.
* Do not place real or simulated broker orders.
* Do not integrate paid APIs.
* Do not deploy to cloud infrastructure.
* Do not introduce unnecessary ML or AI dependencies.
* Do not install global packages.
* Do not overwrite existing files without inspecting them first.
* Do not commit or push to GitHub without my approval.

Use the existing `.venv` for Python dependencies.

## 9. Working procedure

Work in small, understandable steps.

First, inspect the repository and present:

1. Current environment assessment.
2. Proposed directory structure.
3. Architecture overview.
4. Dependencies required and why.
5. Implementation plan for this milestone.
6. Any compatibility issues specific to Apple Silicon/MacBook M1.

After presenting the plan, proceed with Phase 1 implementation unless a destructive operation, significant cost, credential requirement or unresolved architectural decision requires my approval.

After implementation:

* Run backend tests.
* Run frontend tests.
* Verify the backend starts.
* Verify the frontend starts.
* Verify frontend-to-backend communication.
* Report all completed files and commands.
* Update `docs/PROJECT_STATUS.md`.
* Explain how I can run the application myself.

Do not start Phase 2 until I explicitly approve it.

**Your priority is correctness, maintainability, security and a reliable foundation — not rushing to build a trading bot.**