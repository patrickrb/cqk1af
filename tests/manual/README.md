# Manual smoke tests — real FlexRadio 6400

These are hardware-required smoke checks that run against the live radio. They
are NOT part of CI. Run them before release / after any change to the
FlexLib adapter, audio pipeline, or interlock.

Setup:

1. SmartSDR for Windows running and connected to the 6400.
2. `scripts\package_flexlib.ps1` has copied FlexLib.dll into `runtime\flexlib\`.
3. `pip install -e ".[radio,audio,stt]"` has installed pythonnet + sounddevice + faster-whisper.
4. DAX RX 1 and DAX TX devices enabled in SmartSDR.
5. `runtime\piper\` contains an `en_US-amy-medium.onnx` (or similar) Piper voice.
6. K1AF operating privileges confirmed; antennas configured; into a dummy load
   for the first run.

## 1. Radio connect

```powershell
.\.venv\Scripts\python.exe -m cqk1af serve --no-dashboard --transport stdio
```

Verify the log shows `flexlib.dll_loaded`, `RadioConnected`, and at least one slice.

## 2. Dashboard live state

```powershell
.\.venv\Scripts\python.exe -m cqk1af serve
# Separately:
cd web; npm run dev
```

Open `http://127.0.0.1:5173`.

- [ ] Header shows `CQK1AF`.
- [ ] Radio panel shows "connected" and the current slice freq/mode.
- [ ] Tune to **14.250 MHz USB** via the panel; SmartSDR moves to match.
- [ ] S-meter bar reflects RF activity (sweep antenna near a known carrier).
- [ ] Emergency-stop button is visible on every page.

## 3. Frequency-in-use check (DUMMY LOAD)

- [ ] Antenna disconnected; dummy load in.
- [ ] Tune to a known-clear segment (e.g. 14.270).
- [ ] Click "Arm session" → state badge turns red, "ARMED".
- [ ] Approve TX modal appears for each of three attempts.
- [ ] Approve all three → state advances to CALLING_CQ.
- [ ] At any point, click the kill switch → PTT releases in well under 1 s,
      state → STOPPED. Confirm the radio's MOX indicator goes off.

## 4. CQ transmission (REAL ANTENNA, known-clear frequency)

- [ ] Antenna connected, dummy load out.
- [ ] On 14.270 USB or similar, run the freq-in-use check; approve.
- [ ] CALLING_CQ → Approve the CQ TX → audio plays through DAX TX.
- [ ] Verify your monitor receiver hears the CQ correctly with phonetic callsign.
- [ ] ID timer fires after 10 min if a long QSO; confirm phonetic ID is appended.

## 5. STT reception

- [ ] After CQ, listen on the freq. Have a second station (or playback through
      DAX RX from another receiver) call you with a known callsign.
- [ ] Dashboard transcript shows the received audio text.
- [ ] Dashboard pileup queue lists the heard callsign with confidence > 0.5.
- [ ] Click "Work" → QSO state opens with the callsign populated.

## 6. QSO logging

- [ ] Complete a QSO (signal report exchange, name, QTH).
- [ ] Click "Confirm & log" in CONFIRMING_LOG.
- [ ] `data\qsos.sqlite` has the new row.
- [ ] If Cloudlog configured: row reaches Cloudlog within a minute. Inspect
      the station's recent QSOs in Cloudlog UI.
- [ ] Visit `/log` and click "Export ADIF". Open the file in Log4OM or N1MM —
      the QSO imports cleanly.

## 7. Kill switch latency

- [ ] During an active TX (CQ or signal report), click the kill switch.
- [ ] Stopwatch (or audio recording) shows TX terminates in < 200 ms.
- [ ] Tokens for that session are invalidated; even retrying the action with
      the same token fails with "TxNotPermitted".

## 8. Disarm-on-disconnect

- [ ] Pull the network cable to the radio mid-session.
- [ ] State transitions through ERROR → STOPPED.
- [ ] PTT is forced off (verify via the radio's MOX indicator).

## Notes

- Don't skip the dummy-load step on first run after any FlexLib adapter change.
- File a follow-up issue if the WS reconnect after a transient backend restart
  shows stale state — the hello payload should refresh.
