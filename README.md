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
uv sync                      # creates .venv from uv.lock (app + dev tools)
uv run pre-commit install    # git hooks: gitleaks, ruff, whitespace fixers

uv run osrs_finance_hub serve --contact "you@example.com"
uv run pytest app
uv run ruff check
```

Python packages are managed with [uv](https://docs.astral.sh/uv/). The repo is
a uv workspace: the root `pyproject.toml` lists the member packages (`app/` for
now) and holds the repo-wide dev tools; `uv.lock` pins every dependency.
Add a dependency with `uv add --package osrs_finance_hub <name>`.

## Secrets

This repo is public. Never commit keys, tokens or credentials. Local secrets go
in a `.env` file or a `.secrets/` folder, both ignored by git. Commit a
`.env.example` with placeholder values instead.
