<h1 align="center">agentmon</h1>

<p align="center">
  <a href="README.md">English</a>
  &nbsp;&nbsp;|&nbsp;&nbsp;
  <a href="README.ko.md">한국어</a>
</p>

<p align="center">
  Show the session status and usage limits of your coding agents<br>
  (Claude Code, Codex, Antigravity, Cursor) on an Ulanzi D200 deck.
</p>

<p align="center">
  <a href="https://dont-do-it-now.github.io/Agentmon/">Website</a>
  &nbsp;&nbsp;|&nbsp;&nbsp;
  <a href="https://github.com/Dont-Do-it-Now/Agentmon/releases">Releases</a>
</p>

<p align="center">
  <img src="docs/preview.webp" width="640" alt="agentmon on an Ulanzi D200: session tiles, limit tiles and the wide panel">
</p>

## What's new in v0.2.0

- **Cursor agent** — reads Cursor transcript files; session tiles and focus work the same way as other agents
- **Herdr integration** — when your agents are running inside [Herdr](https://herdr.io), pressing a session tile switches Herdr to that terminal's tab instead of opening a separate app window; falls back to the app's deep link when Herdr is not running
- **Dynamic bottom tile (button 12)** — cycles through Antigravity, Cursor and any future agents; auto-selects by most-recent activity, or press to cycle manually
- **Agent icons on limit tiles** — each bottom tile now shows the agent's logo in the top-left corner
- **Status icons** — quota-loading and login-required states are shown with dedicated icons on tiles and the wide panel

## Features

- Session tiles (9 per page) filled with the agent's color, with model, branch and a context bar
- Finished or unread sessions blink until you press them; pressing opens that session (or switches Herdr to its tab)
- Wide 458x196 panel with one row per agent and two limit bars (5-hour and weekly)
- Bottom tiles: first two slots are fixed (Claude, Codex); the third cycles through the rest automatically
- Agents are independent modules — adding one never touches dashboard code
- Reads only local files and local apps; limits come from each agent's own service

## Requirements

- Windows
- Ulanzi D200 (connected over USB)
- Python 3 (`hidapi`, `Pillow`, `certifi`, `psutil`; see `requirements.txt`)
- At least one supported agent installed: Claude Code, Codex, Antigravity, or Cursor

## Install and run

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python.exe dashboard.py
```

Or run `deploy.bat`, which creates `.venv`, installs the requirements and starts the dashboard.

To run without a window, use `run_silent.vbs` (`run_log.bat` writes `logs/dashboard.log`).
To start it at every login, run `install_startup.bat` as administrator.

To restart: end the python process whose command line contains `dashboard.py`, then run `wscript.exe run_silent.vbs`.

## Deck controls

| Button               | Action                                                                                  |
| -------------------- | --------------------------------------------------------------------------------------- |
| Session tiles (0-8)  | Mark as seen and open that session (switches Herdr tab when Herdr is running)           |
| Next (9)             | Next session page; from the last page back to page 1                                    |
| Bottom tiles (10-11) | Fixed agent limits (5H and weekly bars). Press to switch remaining / used % and refresh |
| Bottom tile (12)     | Cycles through remaining agents (Antigravity, Cursor…) with the same toggle             |

## Adding an agent

1. Create `agents/<name>.py` with the constants `KEY, LABEL, SHORT, COLOR, BG, APP_TITLE`.
2. Implement `sessions() -> list[AgentSession]` (`state` is `"running"` or `"done"`; `focus_url` is what opens on press).
3. Implement `usage()` returning an object with `.limits -> list[Limit]`, or `None` when the app is off.
4. Optionally add `focus(session)` when a deep link alone cannot open the session.
5. Add the module to `MODULES` in `agents/__init__.py`. Its order is the display order.

`dashboard.py` and `panel.py` stay untouched. The first two modules occupy the fixed bottom tiles; modules from index 2 onward share the third tile and cycle on press.
See [AGENTS.md](AGENTS.md) for details.

## Notes

- The Claude limit API returns 429 if called too often, so `agents/claude.py` caches it for 120 seconds.
- `agents/claude.py` refreshes an expired Claude Code token and writes it back to `~/.claude/.credentials.json`.
- Tokens and CSRF values are never logged.

## License

MIT License. See [LICENSE](LICENSE); a Korean reference translation is in [LICENSE.ko.md](LICENSE.ko.md).

---

Ulanzi, Claude, Codex, Antigravity, Cursor and Herdr are trademarks of their respective owners. agentmon is not affiliated with or endorsed by them.
