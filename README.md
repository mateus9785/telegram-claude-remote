# telegram-claude-remote

![CI](https://github.com/mateus9785/telegram-claude-remote/actions/workflows/ci.yml/badge.svg)

A Telegram bot that opens a Claude Code session with Remote Control enabled,
triggered by a command from your phone. Long-polling only (no webhook, no
public HTTP endpoint to expose) — built to run under PM2 on a personal server
without a TTY.

`/claude [optional prompt]` spawns `claude --remote-control ... --bg` in a
fixed working directory and replies with the session name; `/status` lists
active sessions with an inline "close" button per session; `/reabrir <id>`
respawns a session whose Remote Control connection dropped, resuming the
conversation where it left off.

## Stack

- **Python 3.10+**, stdlib only besides one dependency: **requests**
- **hatchling** — build backend (auto-detects the `telegram_claude_remote/`
  package at the repo root, no extra config)
- **ruff** — lint + format
- **mypy** (`disallow_untyped_defs = true`) — every function is typed
- **pytest** — unit tests, `subprocess`/filesystem boundaries mocked
- GitHub Actions CI: `ruff check` → `ruff format --check` → `mypy` → `pytest`

## Architecture

```
bot.py                          <- thin entrypoint (what `pm2 start bot.py` runs)
telegram_claude_remote/
  config.py                      paths, timeouts, .env/.offset persistence, BotState
  telegram_client.py             Telegram Bot API: send message, answer callback, getUpdates
  claude_control.py              subprocess control of the `claude` CLI (launch/list/close/reopen)
  handlers.py                    per-command handlers + update dispatch
  main.py                        bootstrap: load .env, long-poll loop
```

`bot.py` stays at the repo root as a one-line shim
(`from telegram_claude_remote.main import main`) so the existing
`pm2 start bot.py` in production keeps working — the package split
underneath it is invisible to the process manager.

Authorization state (which chat is allowed to issue commands) lives in a
`BotState` instance created once in `main()` and passed explicitly into every
handler, rather than a module-level global mutated via `global` — the original
single-file version used the latter, and it made every handler's real
dependency invisible.

## Technical Decisions

- **Package-in-subfolder, not flat modules at the repo root.** Reads better
  for someone opening the repo cold, and it's the layout hatchling picks up
  with zero extra `[tool.hatch.build]` config.
- **`BotState` object instead of a module global.** The original `bot.py` read
  and wrote a bare `OWNER_CHAT_ID = None` global via the `global` keyword
  inside `handle_start`. An explicit object threaded through every handler
  signature makes the dependency visible at every call site instead of hidden
  inside a function body.
- **`dict[str, Any]` for Telegram payloads, not a modeled `TypedDict`.**
  Fully typing the Bot API's `update`/`callback_query` shape would be a
  disproportionate amount of ceremony for a small personal bot with five
  commands — same calibration used across the rest of this portfolio.
- **Tests mock at the `subprocess`/filesystem boundary, not higher.** Every
  test that exercises `claude_control` patches `subprocess.run`, `os.kill`, or
  `shutil.rmtree` directly rather than the functions that call them — so a
  test failure means the wrapper logic is wrong, not that a mock drifted from
  what it's supposed to simulate.
- **No webhook mode.** Long-polling is simpler to run under PM2 (no public
  HTTPS endpoint, no reverse proxy, no TLS cert to manage) and the bot only
  ever has one operator — the extra latency of polling doesn't matter here.

## Security

This bot is built for **single-operator personal use on a server you already
trust**, and it makes a few tradeoffs on that basis that would need
re-examining before reuse in any other context:

- **`claude --dangerously-skip-permissions`.** Every session `/claude` opens
  runs Claude Code with permission prompts disabled — full filesystem access
  within the configured working directory, no per-action sandboxing. This is
  the whole point of the bot (remote-triggering an agent that can actually do
  things without you approving every step from your phone), but it means
  anyone who can talk to the bot can have Claude Code do anything the host
  user can do.
- **Authorization model is single owner, locked on first contact.** Whichever
  chat sends `/start` first becomes `OWNER_CHAT_ID`, persisted to `.env`, and
  from then on every other chat is rejected. There's no multi-user support, no
  token rotation, and no way to change the owner short of editing `.env` by
  hand — deliberately minimal for a bot with exactly one intended user.
- **`/status` can `SIGTERM` arbitrary processes by pid.** The "close session"
  button sends `os.kill(pid, SIGTERM)` against whatever `claude agents --json`
  reports, scoped to processes discoverable that way — not a generic
  "kill any pid" primitive, but still a real capability sitting behind
  Telegram auth alone.

These choices are acceptable **for this specific bot's threat model**: one
operator, one already-isolated personal server, no other tenants. **Do not
copy this authorization pattern into a multi-user or shared-infrastructure
context** without redesigning the auth model first (per-user tokens, scoped
permissions, audit logging) — nothing here is hardened for that.

## Setup

Requires the `claude` CLI installed and on `PATH`, and a Telegram bot token
from [@BotFather](https://t.me/BotFather).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env   # fill in TELEGRAM_BOT_TOKEN; leave OWNER_CHAT_ID empty

python3 bot.py          # or: pm2 start bot.py
```

Send `/start` to the bot from the Telegram account you want as the owner —
that locks `OWNER_CHAT_ID` into `.env` on first contact.

`HOME_DIR` (`telegram_claude_remote/config.py`) is hardcoded to `/home/ubuntu`,
the working directory `claude --remote-control` launches in — this bot is
built to run on one specific server, not designed to be portable across hosts
out of the box (see [Known limitations](#known-limitations--roadmap)).

## Testing

```bash
pytest -v
```

38 unit tests, all with `subprocess`/filesystem calls mocked — no test ever
touches a real Telegram token, polls the real API, or shells out to the real
`claude` binary.

## Known limitations / Roadmap

- `HOME_DIR` is a hardcoded constant, not read from `.env` — moving this bot
  to a different server means editing `config.py`, not just `.env`.
- Single-owner authorization only; no multi-user support (see
  [Security](#security) for why that's a deliberate, not accidental, choice).
- No retry/backoff strategy beyond a flat 5-second sleep when a `getUpdates`
  request fails — fine for a personal bot's polling loop, would need real
  backoff under heavier load or a flakier network.
- Telegram payloads are typed as `dict[str, Any]` rather than modeled
  `TypedDict`s (see [Technical Decisions](#technical-decisions)).

## License

[MIT](./LICENSE)
