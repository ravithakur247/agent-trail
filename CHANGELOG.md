# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-13

### Added

- Phase 1 MVP: local daemon that detects AI coding agent processes (Claude Code,
  Aider, Cursor agent, Copilot, Windsurf, Codex CLI, and others) via process
  name/command-line signature matching, including their full descendant trees.
- Command auditing: full command line, cwd, executable path, start/end time,
  and exit tracking per agent-related process.
- Resource metrics: CPU%, RSS memory, and disk read/write bytes sampled every
  5 seconds per tracked process.
- Filesystem watching of each active session's working directory, logging
  created/modified/deleted file paths (never file contents).
- SQLite-backed local history (`~/.agenttrail/agenttrail.db`) with sessions,
  commands, metrics, and filesystem events tables.
- FastAPI dashboard with a live view (running sessions, live CPU/RAM, kill
  switch) and a history view (past sessions, merged command + filesystem
  event timeline, CPU/RAM chart).
- `agenttrail` CLI with `start`, `dashboard`, and `stop` subcommands.
