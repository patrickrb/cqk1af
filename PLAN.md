# CQK1AF — FlexRadio 6400 SSB QSO Assistant (MCP Tool)

An MCP server that assists a licensed amateur radio operator with voice SSB QSO
operation on a FlexRadio 6400. The operator remains in control at all times;
the assistant listens, suggests, and (when explicitly permitted) speaks and
logs contacts.

---

## 1. Goals & Non-Goals

### Goals
- Find an open frequency on the current band within the operator's privileges.
- Determine whether a candidate frequency is in use (RF + voice activity).
- Politely ask "is this frequency in use?" with proper identification.
- Call CQ with a configurable, FCC/IARU-compliant message.
- Detect responding stations, extract callsigns, names, QTH, grids, reports.
- Manage pileups one station at a time with a fair queue.
- Conduct and confirm a QSO exchange.
- Log completed contacts (ADIF + existing logbook backend).
- Provide a hard kill switch and manual override at every step.

### Non-Goals (v1)
- Contest operation, split operation, DX-pedition pileup modes.
- CW, FT8, or other digital modes.
- Remote / unattended operation. The operator must be physically present.
- Multi-radio orchestration.
- Automatic band changes without operator approval.

---

## 2. Operating Principles (non-negotiable)

1. **Operator-in-control.** Every transmission is gated by either
   `require_manual_tx_approval=true` (explicit per-TX approval) or by the
   configured operating mode rules. No surprise transmissions.
2. **Conservative by default.** When uncertain, listen. Silence is always
   a valid action.
3. **Proper identification.** The user's callsign is transmitted at the
   start of activity, at least once every 10 minutes during a QSO, and at
   the end of communication (FCC §97.119).
4. **Never transmit over another signal.** Re-check RF + VAD immediately
   before keying.
5. **Stay in band/mode privileges.** Refuse to transmit outside the
   configured operator class privileges and band plan.
6. **Auditability.** Every TX event is timestamped and logged, with a
   transcript snippet (unless privacy mode is on).

---

## 3. Configuration

Single YAML/JSON config loaded at startup; hot-reloadable for non-radio
fields.

```yaml
operator:
  user_callsign: "K1AF"           # required
  user_name: "Burns"
  user_qth: "Boston, MA"
  grid_square: "FN42"
  operator_class: "Extra"          # Technician|General|Extra
radio:
  model: "FlexRadio 6400"
  host: "192.168.1.50"             # auto-discovered if blank
  port: 4992                       # SmartSDR API
  audio_rx_device: "Flex DAX 1"
  audio_tx_device: "Flex DAX 1"
operating:
  preferred_mode: "USB"            # USB|LSB (auto by band)
  cq_message_template: >
    CQ CQ CQ, this is {phonetic}, {phonetic}, calling CQ and standing by.
  max_transmit_seconds: 15
  require_manual_tx_approval: true
  pileup_mode: false
  phonetic_alphabet: "NATO"
  cq_max_attempts: 5
  cq_listen_seconds: 7
  inter_cq_delay_seconds_range: [3, 8]
  freq_check_listen_seconds: 5
  response_listen_seconds: 3
audio:
  stt_backend: "whisper-local"     # whisper-local|whisper-api|vosk
  tts_backend: "piper-local"       # piper-local|elevenlabs|azure
  tts_voice: "en_US-amy-medium"
  vad_threshold_db: -45
logging:
  backend: "adif-file"             # adif-file|cloudlog|n1mm|custom-http
  adif_path: "./logs/cqk1af.adi"
  log_ai_assisted_flag: true
  log_transcript_snippets: true    # set false for privacy
safety:
  kill_switch_hotkey: "F8"
  require_operator_confirm_before_log: true
  band_plan_file: "./band_plans/us_extra.yaml"
```

---

## 4. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         MCP Server (Python)                       │
│                                                                   │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐ │
│  │  MCP Tools   │   │ State Machine│   │  Safety / Kill Switch│ │
│  │  (handlers)  │◄─►│   (asyncio)  │◄─►│   (priority preempt) │ │
│  └──────┬───────┘   └──────┬───────┘   └──────────────────────┘ │
│         │                  │                                      │
│  ┌──────▼─────────┐ ┌──────▼─────────┐ ┌──────────────────────┐ │
│  │  Flex Adapter  │ │ Audio Pipeline │ │   Logging Backend    │ │
│  │  (SmartSDR API)│ │  (RX VAD/STT,  │ │   (ADIF + plugins)   │ │
│  │                │ │   TX TTS/PTT)  │ │                      │ │
│  └──────┬─────────┘ └──────┬─────────┘ └──────────────────────┘ │
│         │                  │                                      │
└─────────┼──────────────────┼──────────────────────────────────────┘
          │                  │
   ┌──────▼─────┐      ┌─────▼─────┐
   │ Flex 6400  │      │ DAX audio │
   │ (TCP/UDP)  │      │ devices   │
   └────────────┘      └───────────┘
```

### Language & runtime
- **Python 3.11+** (asyncio, mature audio/STT/TTS libs, FlexLib/pyflex
  community bindings).
- **MCP SDK**: `mcp` (official Python SDK).
- **Audio**: `sounddevice` + `numpy` for I/O, `webrtcvad` for VAD,
  `faster-whisper` for STT, `piper-tts` for TTS.
- **Flex API**: thin async wrapper over the SmartSDR TCP API (port 4992)
  plus DAX virtual audio devices for RX/TX audio.
- **Persistence**: SQLite for the in-flight QSO journal; ADIF file for the
  durable log.

### Project layout
```
CQK1AF/
├── PLAN.md                        # this document
├── README.md
├── pyproject.toml
├── config/
│   └── config.example.yaml
├── band_plans/
│   ├── us_extra.yaml
│   ├── us_general.yaml
│   └── us_technician.yaml
├── src/cqk1af/
│   ├── __init__.py
│   ├── server.py                  # MCP server entry point
│   ├── config.py                  # config loader + validation
│   ├── state.py                   # state machine + transitions
│   ├── safety.py                  # kill switch, TX gates, ID timer
│   ├── radio/
│   │   ├── __init__.py
│   │   ├── flex_client.py         # SmartSDR TCP client
│   │   ├── panadapter.py          # RF level / spectrum sampling
│   │   ├── band_plan.py           # privileges & sub-band rules
│   │   └── mock.py                # simulated radio for tests
│   ├── audio/
│   │   ├── __init__.py
│   │   ├── rx.py                  # capture, VAD
│   │   ├── stt.py                 # whisper wrapper
│   │   ├── tts.py                 # piper wrapper
│   │   ├── phonetics.py           # NATO mapping
│   │   └── tx.py                  # TX gate + PTT
│   ├── qso/
│   │   ├── __init__.py
│   │   ├── extractor.py           # callsign/report/QTH NER
│   │   ├── pileup.py              # queue + selection
│   │   └── flow.py                # QSO step sequencer
│   ├── logging_backends/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── adif.py
│   │   └── http.py
│   └── tools/                     # one file per MCP tool handler
│       ├── connect.py
│       ├── scan.py
│       ├── listen.py
│       ├── ask_in_use.py
│       ├── cq.py
│       ├── pileup.py
│       ├── qso.py
│       ├── log.py
│       └── stop.py
└── tests/
    ├── test_state_machine.py
    ├── test_band_plan.py
    ├── test_extractor.py
    ├── test_pileup.py
    └── fixtures/
        └── audio/
```

---

## 5. State Machine

```
        ┌────────────────┐
        │  DISCONNECTED  │
        └──────┬─────────┘
               │ connect_radio
        ┌──────▼─────────┐
   ┌────│      IDLE      │◄────────────────────────┐
   │    └──────┬─────────┘                          │
   │           │ scan_for_open_frequency            │
   │    ┌──────▼─────────┐                          │
   │    │   SCANNING     │                          │
   │    └──────┬─────────┘                          │
   │           │ candidate found                    │
   │    ┌──────▼─────────┐                          │
   │ ┌──│   LISTENING    │  (5s passive)            │
   │ │  └──────┬─────────┘                          │
   │ │ busy    │ clear                              │
   │ └────►────┘                                    │
   │    ┌──────▼─────────┐                          │
   │    │CHECKING_FREQ   │  ask "in use?" up to 3x  │
   │    └──────┬─────────┘                          │
   │      busy │ clear                              │
   │  ┌────────┘                                    │
   │  │  ┌──────▼─────────┐                         │
   │  │  │   CALLING_CQ   │◄──┐                     │
   │  │  └──────┬─────────┘   │ no reply, retry     │
   │  │         │ reply       │                     │
   │  │  ┌──────▼─────────┐   │                     │
   │  │  │RECEIVING_REPLY │───┘                     │
   │  │  └──────┬─────────┘                         │
   │  │   multi │ single                            │
   │  │  ┌──────▼─────────┐                         │
   │  │  │HANDLING_PILEUP │                         │
   │  │  └──────┬─────────┘                         │
   │  │         │ station selected                  │
   │  │  ┌──────▼─────────┐                         │
   │  │  │     IN_QSO     │                         │
   │  │  └──────┬─────────┘                         │
   │  │         │ QSO complete                      │
   │  │  ┌──────▼─────────┐                         │
   │  │  │CONFIRMING_LOG  │                         │
   │  │  └──────┬─────────┘                         │
   │  │         │ confirmed                         │
   │  │  ┌──────▼─────────┐                         │
   │  │  │     LOGGED     │─────────────────────────┘
   │  │  └────────────────┘     return to IDLE
   │  │
   │  └─────► [any state] ──► STOPPED  (emergency_stop)
   │                          ERROR    (unrecoverable)
   └────────────────────────────────────────────────
```

### Transition rules
- Every state has an `on_kill_switch → STOPPED` edge.
- `ERROR` is recoverable to `IDLE` only after `connect_radio` succeeds.
- `IN_QSO` enforces the 10-minute ID timer; the next TX prepends the user
  callsign if the timer elapses.
- Re-entry to `LISTENING` is mandatory before any TX from `CALLING_CQ`,
  `HANDLING_PILEUP`, or `IN_QSO`.

---

## 6. Frequency Selection

1. **Read radio state**: current band, mode, VFO A frequency, filter width.
2. **Load band plan** for the operator's class. Build a list of allowed
   SSB segments, e.g. for US Extra on 20m: `14.150–14.350 MHz USB`.
3. **Exclude**: band edges (±3 kHz guard), beacon segments (e.g.
   14.099–14.101), DX windows (configurable), known nets (configurable
   YAML), digital sub-bands, satellite sub-bands, repeater outputs (VHF/UHF).
4. **Candidate scan**: step through allowed segments at 3 kHz spacing.
   For each candidate:
   - Tune (without TX).
   - Sample panadapter RF level for 500 ms; require average below noise
     floor + 6 dB.
   - Run VAD on RX audio for 1 s; require no voice detected.
5. **Score** candidates by (low RF, distance from nearest active signal,
   proximity to common operating frequencies). Pick the best.

---

## 7. "Is This Frequency in Use?" Protocol

State: `CHECKING_FREQUENCY`. Three-pass courtesy check, each pass gated
on TX approval (auto if `require_manual_tx_approval=false`).

```
Pass 1: listen 5 s
  if voice or signal: → SCANNING
  else TX: "Is this frequency in use?"        listen 3 s
Pass 2:
  if voice: → SCANNING
  else TX: "Is this frequency in use? {phonetic_callsign}"   listen 3 s
Pass 3:
  if voice: → SCANNING
  else TX: "Is this frequency in use? {phonetic_callsign}"   listen 3 s
  if voice: → SCANNING
  else: → CALLING_CQ
```

Every TX in this protocol must pass the safety gate (band check, kill
switch not engaged, no signal currently on frequency in the 100 ms
immediately before keying).

---

## 8. CQ Loop

```
attempts = 0
while attempts < cq_max_attempts and state == CALLING_CQ:
    re-verify frequency clear (1 s VAD + RF check)
    TX cq_message rendered from template
    listen cq_listen_seconds for reply
    if reply detected:
        → RECEIVING_REPLY
    sleep random(*inter_cq_delay_seconds_range)
    attempts += 1
if attempts == cq_max_attempts:
    → IDLE   (operator may choose to re-scan or change band)
```

The CQ template is rendered with `{phonetic}` expanded via the NATO map,
e.g. `K1AF` → `Kilo One Alpha Foxtrot`.

---

## 9. Response Detection & Callsign Extraction

- STT runs continuously while receiving (rolling 5 s window, overlapped).
- Each transcript chunk passed to `extractor.py`:
  - **Callsign regex**: `^[A-Z]{1,2}\d[A-Z]{1,3}$` plus phonetic
    reconstruction (e.g. "Kilo One Alpha Foxtrot" → `K1AF`).
  - **Signal report**: `\b[1-5][1-9]\b` (RST style, 59 most common on SSB).
  - **Name**: pattern `name (is|here) <X>` or `my name is <X>`.
  - **QTH**: pattern `QTH (is)? <city/state>` or `in <city>, <state>`.
  - **Grid**: `[A-R]{2}\d{2}([a-x]{2})?`.
- Each extracted field carries a confidence score (0–1). Below 0.7 →
  ask for repeat. Below 0.5 → do not log.

---

## 10. Pileup Handling

`pileup_mode=true` enables the queue.

- All callsigns detected within a single `cq_listen_seconds` window are
  appended to `PileupQueue` with timestamps and confidence scores.
- Selection policy (configurable):
  1. **highest confidence** (default)
  2. **first heard**
  3. **weakest signal** (DX courtesy)
- Tool says: `"{selected_phonetic}, this is {user_phonetic}, you are
  {report}. Please go ahead."` Other queued stations are not addressed
  yet; the tool will work them after the current QSO ends.
- Partial-callsign handling: if only a suffix is heard with high
  confidence, ask: `"Station ending in {suffix}, please come again with
  your full callsign."`
- Queue is cleared when the operator returns to `IDLE` or starts a new CQ.

---

## 11. QSO Flow

```
state = IN_QSO
1. TX: "{dx_phonetic}, this is {user_phonetic}. Thanks for the call.
        You are {report} into {user_qth}. My name is {user_name}.
        How copy? Over."
2. Listen up to 20 s. Extract: their report, name, QTH, grid, notes.
3. If any required field (their report, their callsign confirmation)
   is missing or low-confidence, TX a targeted repeat request.
4. TX: "Roger {dx_name}, thanks for the {report} from {dx_qth}.
        73, this is {user_callsign}, QRZ?"
5. → CONFIRMING_LOG
```

ID timer: if elapsed since the last user-callsign TX is ≥ 9.5 minutes,
the next TX in the QSO is prefixed with the full user callsign.

---

## 12. Logging

### Record shape
```json
{
  "qso_id": "uuid",
  "start_utc": "2026-05-22T14:32:11Z",
  "end_utc":   "2026-05-22T14:34:07Z",
  "band": "20m",
  "freq_mhz": 14.255,
  "mode": "USB",
  "contacted_call": "W1XYZ",
  "rst_sent": "59",
  "rst_rcvd": "57",
  "op_name": "John",
  "qth": "Springfield, MA",
  "grid": "FN32",
  "notes": "",
  "ai_assisted": true,
  "confidence": {
    "call": 0.96, "rst_rcvd": 0.88, "name": 0.81, "qth": 0.74
  },
  "transcript_snippet": "..." // omitted if privacy mode
}
```

### Flow
1. After `IN_QSO` close → `CONFIRMING_LOG`.
2. If `require_operator_confirm_before_log=true`, the tool surfaces the
   record via MCP and waits for `confirm_qso_details`.
3. Write to ADIF file (atomic append) and call the configured
   `logging_backend` plugin.
4. → `LOGGED` → `IDLE`.

### ADIF mapping (subset)
`<CALL>`, `<QSO_DATE>`, `<TIME_ON>`, `<TIME_OFF>`, `<BAND>`, `<FREQ>`,
`<MODE>` (= `SSB`), `<SUBMODE>` (= `USB`/`LSB`), `<RST_SENT>`,
`<RST_RCVD>`, `<NAME>`, `<QTH>`, `<GRIDSQUARE>`, `<COMMENT>`,
`<APP_CQK1AF_AI_ASSISTED>:1:Y`.

---

## 13. MCP Tool Surface

All tools return a structured result with `state`, `ok`, `detail`, and
any tool-specific payload. Tools that may transmit accept an
`approve_tx: bool` argument when `require_manual_tx_approval=true`.

| Tool                       | Purpose                                                | TX? |
|----------------------------|--------------------------------------------------------|-----|
| `connect_radio`            | Open SmartSDR session; verify model/firmware           | No  |
| `disconnect_radio`         | Close session cleanly                                  | No  |
| `get_current_band`         | Return band, segment privileges                        | No  |
| `get_current_frequency`    | Return VFO A freq + mode + filter                      | No  |
| `set_frequency`            | Tune VFO A (RX only — no TX side effects)              | No  |
| `scan_for_open_frequency`  | Run the candidate-selection algorithm                  | No  |
| `listen_for_activity`      | Sample RF + VAD for N seconds                          | No  |
| `ask_frequency_in_use`     | Execute the 3-pass courtesy check                      | Yes |
| `call_cq`                  | Run CQ loop                                            | Yes |
| `listen_for_callsigns`     | STT + extract callsigns from RX window                 | No  |
| `select_station_from_pileup` | Choose next station per policy                       | No  |
| `conduct_qso_step`         | Execute next step of the QSO flow                      | Yes |
| `confirm_qso_details`      | Operator confirms/edits before log write               | No  |
| `log_qso`                  | Persist to ADIF + backend                              | No  |
| `get_state`                | Return full state machine snapshot                     | No  |
| `stop_transmitting`        | Drop PTT immediately; stay in current state            | No  |
| `emergency_stop`           | Drop PTT, transition to STOPPED, require reconnect     | No  |
| `set_operating_mode`       | Toggle pileup mode, manual-approval, etc.              | No  |

### Tool prompts (system instructions for the agent)
Each TX-capable tool's MCP description includes:

> You are assisting a licensed amateur radio operator. You may suggest
> transmissions but you do not transmit; the radio adapter does, and only
> when the safety gate permits. Behave like a cautious, experienced
> operator: prefer listening, never step on another signal, identify
> properly, and defer to the operator's judgment. If you are uncertain
> about a callsign, ask for a repeat instead of guessing. Never log a
> contact with low confidence.

---

## 14. Safety Layer

A single `SafetyGate` object wraps every TX:

```python
def gate(tx_request) -> Allow | Deny:
    if kill_switch.engaged: return Deny("kill switch")
    if not band_plan.allows(freq, mode, op_class): return Deny("privileges")
    if rf_or_voice_present_now(): return Deny("frequency busy")
    if tx_request.estimated_seconds > config.max_transmit_seconds:
        return Deny("over max TX length")
    if require_manual_tx_approval and not tx_request.approved:
        return Deny("awaiting operator approval")
    if id_timer.expired(): tx_request.prepend_callsign()
    return Allow()
```

- Kill switch: global hotkey (configurable), MCP tool, and a physical
  GPIO option for users with a hardware foot/desk switch.
- TX watchdog: independent task that drops PTT after
  `max_transmit_seconds` regardless of caller state.
- All denials are logged with reason.

---

## 15. Audio Pipeline

### RX
```
DAX RX device ──► 16 kHz mono buffer ──► WebRTC VAD (20 ms frames)
                                     └─► faster-whisper (rolling 5 s)
                                     └─► RMS level (for "voice present")
```

### TX
```
Text ──► Piper TTS ──► 48 kHz mono ──► resample to DAX rate
      └─► safety gate
      └─► PTT on  ──► stream samples to DAX TX  ──► PTT off
```

PTT is driven via the SmartSDR API (`xmit` command), not VOX, to avoid
unintended keying.

---

## 16. Testing Strategy

- **Unit**: state transitions, band-plan validation, callsign regex,
  phonetic conversion, pileup ordering, ADIF serialization.
- **Property**: extracted callsign round-trips through the phonetic
  encoder.
- **Integration with mock radio**: `radio/mock.py` replays a recorded
  panadapter + audio stream so the full state machine can run offline.
- **Fixtures**: a small set of recorded RX audio (`tests/fixtures/audio/`)
  covering clear frequency, faint signal, single caller, three-station
  pileup, partial callsign.
- **Safety tests**:
  - TX requested while kill switch engaged → denied.
  - TX requested outside privileges → denied.
  - TX requested while RX VAD positive → denied.
  - Watchdog drops PTT at `max_transmit_seconds`.
- **No live-radio CI**. A separate manual checklist (`docs/manual_checks.md`)
  is run against the actual Flex before each release.

---

## 17. Implementation Phases

1. **Skeleton**: project scaffold, config loader, MCP server boot, mock
   radio, state machine, `get_state`/`connect_radio`/`disconnect_radio`.
2. **Band plan + scan**: `band_plan.yaml` for US Extra/General/Technician,
   `scan_for_open_frequency`, `listen_for_activity` (mock + real).
3. **Audio I/O**: DAX device wiring, VAD, STT (Whisper), TTS (Piper),
   PTT via SmartSDR.
4. **Safety gate + kill switch + TX watchdog.**
5. **"In use?" protocol + CQ loop.**
6. **Extractor + pileup queue.**
7. **QSO flow + ID timer.**
8. **Logging: ADIF writer + pluggable backend interface.**
9. **MCP tool surface complete, docs written, manual checklist.**
10. **Hardening**: privacy mode, transcript retention policy, error
    recovery from radio drop-outs.

Each phase ends with the relevant tests green and a short demo against
the mock radio.

---

## 18. Open Questions

- Which existing logbook backend should the `logging_backend=custom-http`
  plugin target first (Cloudlog, N1MM+, Log4OM, HRD, other)?
- Should the assistant ever respond to an *incoming* CQ, or only
  initiate? (v1 proposal: initiate only.)
- Hardware kill switch: GPIO on a Pi co-located with the radio, or a
  USB foot pedal mapped to the OS hotkey? (v1 proposal: OS hotkey,
  document the foot-pedal recipe.)
- Privacy retention: how long to keep transcript snippets when
  `log_transcript_snippets=true`? (v1 proposal: 30 days, then purge.)

---

## 19. Compliance Notes

- FCC Part 97 §97.119: station identification required at the end of
  each communication and at least every 10 minutes during.
- §97.113: no music, no business communications, no encrypted messages.
  The TTS voice is plain speech; no music is ever transmitted.
- §97.101(b): listen before transmitting. The "in use?" protocol exists
  to honor this.
- Operator class privileges are enforced by `band_plan.py`; the file
  selected via `safety.band_plan_file` must match the operator's
  license.
- Third-party traffic and remote control rules are out of scope for v1
  (the operator is required to be present).
