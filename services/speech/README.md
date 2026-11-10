# Local speech processing

Follow the root [README](../../README.md#first-time-setup) to install Python
requirements and FFmpeg, build whisper.cpp v1.8.3, and download the multilingual
`models/ggml-base.bin` model. These model files are required but are not committed
to Git. The root README includes checksums, configuration and exact run commands.

From the repository root, with the virtual environment activated:

```bash
python -m services.speech path/to/recording.wav --session-id example-session
```

This prints transcript segments and metrics as JSON without writing the app's
database. The app uses the persistent job worker to validate and save results.
The CLI reads exported `WHISPER_*` settings or defaults; it does not read `.env`
automatically. Start the application with the root README's `--env-file .env`
command to apply file-based configuration to the API and worker.

**Language modes** (`english` → Parakeet TDT 0.6B v2; `urdu_mixed` → Urdu Sukhan with language `ur`; Whisper base as
the visible fallback), models, licences, downloads, GPU builds and smoke tests: [docs/ASR_MODELS.md](../../docs/ASR_MODELS.md).
`python -m services.speech path/to/recording.wav --mode english` selects a mode on the command line.

Input is limited to 45 minutes / 600 MB; ordinary
stereo is mixed for ASR, originals are preserved, and speaker roles start UNKNOWN.
Local model absence and decoding/inference errors are explicit; no hosted fallback
is used. Whisper/quantized-model language quality and Jetson runtime compatibility
still require separate evaluation.
