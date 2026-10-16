# rackathon-2026

AI Trial Matching. Matches pseudonymized oncology patients (FN Motol) with clinical trials
from ClinicalTrials.gov and explains which criteria are met, not met or unknown.

## Architecture

```
GitHub (this repo) ──push to main──► Azure App Service  team49-trials.azurewebsites.net
                                       ├─ /        frontend/ (static files)
                                       └─ /api/... backend/ (FastAPI)
                                             ├──► Azure Postgres  team49-pg
                                             └──► LLM endpoint (organizers' Azure models)
```

Azure resource group: `rg-team49` (Sweden Central).

## Local setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env              # fill in DB password and LLM key (ask the team)
.venv/bin/python -m backend.scripts.init_db
.venv/bin/uvicorn backend.app.main:app --reload
# open http://localhost:8000
```

The Azure Postgres firewall only allows listed IPs. Add yours:

```bash
az postgres flexible-server firewall-rule create -g rg-team49 --name team49-pg \
  --rule-name <your-name> --start-ip-address <ip> --end-ip-address <ip>
```

## Data

Patient data **must never be committed** (see `.gitignore`). Keep the dataset outside the repo.
