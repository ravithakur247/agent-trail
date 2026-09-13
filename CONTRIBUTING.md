# Contributing to AgentTrail

Thanks for your interest in contributing! This project is Phase 1 of a two-phase build — check the README's Roadmap section before proposing new features, since some ideas are intentionally deferred to Phase 2.

## Dev environment setup

```bash
git clone https://github.com/agenttrail/agenttrail.git
cd agenttrail
python -m venv .venv
source .venv/bin/activate  # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

## Running tests

```bash
pytest
```

## Coding style

- Follow PEP 8.
- Run `ruff check .` before submitting a PR; fix any lint errors it reports.
- Keep functions small and modules focused — `detector.py`, `metrics.py`, `fs_watcher.py`, `db.py`, and `api.py` each own one concern.
- Add or update tests under `tests/` for any behavioral change.

## Submitting a PR

1. Fork the repo and create a branch from `main`.
2. Make your change, with tests.
3. Ensure `pytest` and `ruff check .` both pass.
4. Open a PR describing what changed and why. Link any related issue.

## Reporting issues

Please use the issue templates under `.github/ISSUE_TEMPLATE/` and include:

- Your OS and Python version
- Steps to reproduce
- What you expected vs. what happened

Since AgentTrail introspects other processes on your machine, please double-check that bug reports don't include sensitive command lines or file paths before posting them publicly.
