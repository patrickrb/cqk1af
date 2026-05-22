# CQK1AF

MCP server and web dashboard that assists a licensed amateur radio operator with voice SSB QSOs on a FlexRadio 6400. The operator stays in command at all times — TX is gated behind a software interlock, and an emergency stop is always visible on the dashboard.

## Status

All 11 plan phases complete. Phase 9 audio pipeline wired:
RX capture + faster-whisper STT works when DAX RX devices are present (SmartSDR
running). TX speak path keys PTT and plays Piper audio out DAX TX — falls back
to narration-only when Piper isn't installed.

## Setup

For RX STT on first run, faster-whisper will download the model on first call
(`distil-large-v3` ≈ 1.5 GB, or set `stt.model: base.en` in
`config/default.yaml` for a ~75 MB model).

For TX audio, install Piper and a voice model:

```powershell
# Download piper.exe from https://github.com/rhasspy/piper/releases
# and put it on PATH (or in .venv\Scripts\).
# Place a voice model in runtime\piper\:
#   https://huggingface.co/rhasspy/piper-voices
#   e.g. en_US-amy-medium.onnx + en_US-amy-medium.onnx.json
```

Without Piper installed, the assistant still works — it narrates planned
transmissions to the dashboard transcript without keying PTT. The Operate
panel's audio banner shows live capability status.

## Architecture

See `plans/` for the full plan. High-level:

- Python core (asyncio) with state-machine-driven session
- FlexRadio control via `FlexLib.dll` (pythonnet) — copied at runtime from the local SmartSDR install
- DAX RX audio via WASAPI → VAD → faster-whisper STT
- Piper TTS for phonetic CQ / QSO output
- MCP server (FastMCP, HTTP+SSE)
- React + Vite + TS + Tailwind dashboard with WebSocket live state
- Cloudlog/Wavelog logging with local SQLite durable store and ADIF export

## Safety

- TX gate defaults to **deny**. Operator must arm a session before any transmit.
- Per-TX approval modal (configurable). Auto-deny after timeout.
- Always-visible kill switch in the dashboard, redundant REST+WebSocket paths.
- Band-plan and license-class enforcement before any tune-for-TX.
- Never transmits on an occupied frequency. Voice detected mid-check aborts.

## Run

Real radio:

```powershell
$env:CQK1AF_RADIO__MOCK_MODE = "false"
$env:CQK1AF_RADIO__HOST       = "192.168.86.159"   # your radio's IP
.\.venv\Scripts\python.exe -m cqk1af serve --no-mcp
# Open http://127.0.0.1:8000/
```

The dashboard is served by FastAPI from the pre-built bundle. First run will
auto-build it (`npm install` + `npm run build`) if `web/dist` is missing.

**Do NOT use http://127.0.0.1:5173** — that's the Vite dev server, only meant
for editing the dashboard source. It has a 15–60 s cold start because it
pre-bundles every node_module. The `:8000` path serves a 63 kB gzipped bundle
in ~50 ms.

### One-time PC Audio toggle per session (SmartSDR coexistence)

On v4 firmware the first time CQK1AF opens a DAX device (RX capture at
startup, or the TX path on first transmit), SmartSDR's "PC Audio" stream
gets wedged — the button stays ON but no audio reaches the speakers.
Toggling PC Audio off then on in SmartSDR recreates the stream and it
survives every subsequent TX cycle for the rest of the session.

The workflow per session:

1. Start `cqk1af serve --no-mcp` — SmartSDR's PC Audio will go silent
   the moment CQK1AF opens DAX RX 1.
2. In SmartSDR, click the "PC Audio" button off, then on — RX audio
   resumes.
3. Operate normally. TX through the dashboard (or MCP tools when
   `--no-mcp` is omitted) will not re-wedge PC Audio.

If you spawn a *second* CQK1AF process (e.g. an ad-hoc probe script)
while the dashboard is running, that second TCP connection will re-wedge
PC Audio on its first DAX operation and you'll need to toggle again.
Single-process operation is the supported pattern.

Mock radio dev mode (no hardware required):

```powershell
.\scripts\mock_radio.ps1
```

Editing dashboard source with HMR:

```powershell
cd web; npm run dev   # http://127.0.0.1:5173 — slow first load, fast HMR
# Then `npm run build` once when done so :8000 picks up the changes.
```

## License

Personal project for K1AF. `FlexLib.dll` is not redistributed — see `runtime/flexlib/README.md`.
