# hhbot

**hhbot** is a Python bot deployable with Docker.  
The project combines backend and client modules, database migrations (alembic), infrastructure configs, and a set of helper scripts. It is designed to automate tasks and integrate with external services (such as Telegram).

> Русская версия: см. [README.md](README.md)

---

## Key Features
- 🚀 Quick startup with **Docker Compose**  
- ⚙️ Flexible configuration via `.env` 
- 🗄 Database migrations with **alembic**  
- 🛠 Maintenance and diagnostic scripts  
- 📦 Clean structure for production and development  

---

## Quickstart

### Requirements
- Python **3.10+**  
- Docker and Docker Compose  

### Installation & Run
1. Copy the environment template:
   ```bash
   cp .env.example .env
and fill in your values.
Start services:
docker-compose up -d
Verify that the bot is running (e.g., via Telegram).
Local Development

# Create virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the app (example)
python main.py
Project Structure
backend/          # backend logic
front_bot/        # bot client logic
infra/            # infrastructure configs
scripts/          # helper scripts
alembic.ini       # DB migration settings
docker-compose*   # container orchestration
Dockerfile        # application image
Caddyfile         # web/proxy configuration
requirements.txt  # dependencies

### Configuration

All settings are managed via .env. Real tokens and keys must never be committed.
See .env.example for documented variable names.

