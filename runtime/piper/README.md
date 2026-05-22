# runtime/piper

This directory should contain a Piper TTS install plus a voice model.
**Neither is redistributed in this repo** — install them yourself:

1. Download a Windows Piper release from
   <https://github.com/rhasspy/piper/releases>
   (e.g. `piper_windows_amd64.zip`) and unzip its contents directly into
   this folder. After unzipping you should have, alongside this README:

   ```
   piper.exe
   espeak-ng.dll
   onnxruntime.dll
   piper_phonemize.dll
   espeak-ng-data/...
   ```

2. Download a voice model from
   <https://huggingface.co/rhasspy/piper-voices> — the project default is
   `en_US-amy-medium`. Drop both files here:

   ```
   en_US-amy-medium.onnx
   en_US-amy-medium.onnx.json
   ```

3. Verify the path matches `tts.voice_path` in `config/default.yaml`
   (or your local override). The default points at
   `runtime/piper/en_US-amy-medium.onnx`.

The audio pipeline finds `piper.exe` by looking next to the voice model
first, then falling back to `PATH`. If neither is present, TTS degrades
to narration-only (no PTT, no audio).
