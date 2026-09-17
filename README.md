# Hermes Companion (Omarchy plugin)

Always-on Hermes agent: watches the focused monitor, speaks up when it judges it useful,
answers voice requests on demand (right-click the bar icon, the Listen button, or Super+Alt+H). Models: a **vision** model (must accept images; sees the screen) and an optional separate **reasoning** model
(default = same as vision). Lists are built from every provider Hermes has credentials for, grouped by provider,
with vision capability from models.dev / the Nous catalog. When the two differ, the vision model describes each
frame in a stateless one-shot and the reasoning model runs the persistent conversation on that text. Read-only tools only.

## Requirements
- Omarchy 4.x (`omarchy`, `omarchy-shell`, `grim`, `hyprctl`, `notify-send`, PipeWire tools) — `install.sh` offers `omarchy pkg add` for missing packages.
- Hermes Agent ≥ 0.21 at `~/.hermes/hermes-agent` (or `HERMES_AGENT_DIR`) — `install.sh` offers `omarchy install ai hermes` / `omarchy install hermes cli` if absent.
- At least one model provider credential (`hermes auth add …` or a Claude Code login). Not required to install; pick a vision model in the widget afterwards.
- Local speech-to-text (`faster-whisper`, installed by `install.sh`) for voice requests.

Install / update: `~/.config/omarchy/plugins/hermes.companion/install.sh`

## Layout
- `daemon/companion.py`  main loop · `--ctl <cmd>` talks to the running daemon
- `daemon/perception.py` grim + hyprctl, dHash change detection, privacy filter (RAM only)
- `daemon/brain.py`      persistent Hermes AIAgent (web/file read-only), JSON tick protocol; native vs split vision
- `daemon/catalog.py`    provider/model catalog with vision flags (Hermes credentials + models.dev + Nous catalog)
- `daemon/voice.py`      on-demand Hermes VAD capture → local faster-whisper → edge TTS
- `daemon/policy.py`     cooldowns / fullscreen / call / idle gating for unprompted speech
- `daemon/state.py`      `~/.local/state/hermes-companion/state.json` + `$XDG_RUNTIME_DIR/hermes-companion.sock`
- `BarWidget.qml`        bar eye icon + popup (toggles, last remarks)   `Service.qml` starts the unit
- `companion.json`       tunables (tick_seconds, cooldowns) + `vision`/`reasoning` {model, effort, thinking}
- `hermes-companion.service.in` template → `~/.config/systemd/user/hermes-companion.service` (install.sh fills in the Hermes path)

## Commands
```
CTL="$HOME/.hermes/hermes-agent/venv/bin/python $HOME/.config/omarchy/plugins/hermes.companion/daemon/companion.py --ctl"
$CTL status | toggle-eyes | listen | toggle-mute | toggle-toasts | hush | tick | models | set-vision <provider:model> | set-reasoning <provider:model|same> | set-{vision,reasoning}-effort <low|medium|high> | toggle-{vision,reasoning}-thinking | say <text> | ask <text> | toast <text> | quit
journalctl --user -fu hermes-companion
```
Keys: Super+Alt+H listen · Super+Alt+E eyes · Super+Alt+S hush. Bar icon: left = panel, right = listen, middle = hush.

