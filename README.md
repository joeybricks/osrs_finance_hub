# OSRS Finance Hub

An Old School RuneScape Grand Exchange flipping app, growing into a low-cost,
multi-cloud learning project: price and text ingestion, LLM signal extraction,
evals, scoring and backtesting, and an agent layer.

The working app lives in [`app/`](app/). Its README covers what it does, how
the maths works and how to run it.

## Layout

| Folder | What it holds |
| --- | --- |
| [`app/`](app/) | The local FastAPI flip finder, its tests and its packaging |
| [`src/`](src/) | Shared pipeline code (placeholder) |
| [`infra/`](infra/) | Terraform (placeholder) |
| [`evals/`](evals/) | LLM evaluation sets and runners (placeholder) |
| [`ops/`](ops/) | Operational scripts and checks (placeholder) |

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e "./app[dev]"

osrs_finance_hub serve --contact "you@example.com"
pytest app
```

## Secrets

This repo is public. Never commit keys, tokens or credentials. Local secrets go
in a `.env` file or a `.secrets/` folder, both ignored by git. Commit a
`.env.example` with placeholder values instead.
