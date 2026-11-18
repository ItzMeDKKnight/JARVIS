# Privacy

This document says exactly what data goes where. It was fully re-audited in Phase 4
(2026-10-18) and again in Phase 5 (2026-10-19, adding the HUD): every network call in the
code and every installed package is listed below.

## In one paragraph

Your voice never leaves your laptop: wake word, speech recognition and speech synthesis
all run locally. The only thing Jarvis sends anywhere is **text** (what you said or typed,
plus context), and it goes to **one place: the Anthropic API**. Everything Jarvis keeps
about you (memory, logs) sits in one folder on your machine,
`%LOCALAPPDATA%\Jarvis`. You can export it or delete it with one command each.

## Every network call Jarvis makes

Found by searching the code for the Anthropic client, `urllib`, `http`, `socket`,
`subprocess`, `webbrowser` and `os.startfile`. There are exactly four places that open a
connection to another machine. (The HUD link is a fifth socket, but it never leaves the
laptop: see [The HUD](#the-hud-electron).) All of them go to `api.anthropic.com` over HTTPS, except the one-time
model downloader.

| # | Where in the code | Destination | When | What is sent |
| --- | --- | --- | --- | --- |
| 1 | `jarvis/llm/client.py` (conversation) | `api.anthropic.com` | every request you make | the system prompt (`config/persona.md` with your first name), tool definitions, the conversation so far (up to `llm.history_max_turns` turns), **memory notes relevant to this request**, **your local date and time with its UTC offset** (`llm.time_note`, so Claude can answer "what's the date"; turn it off in config.yaml), tool results |
| 2 | `jarvis/search/anthropic_search.py` (web search, and finding a YouTube video for `play_youtube`) | `api.anthropic.com` | when Jarvis searches, or when you ask it to play something on YouTube | the search query only. Anthropic runs the search with a third-party search provider; no search key or SDK is used locally. YouTube lookups are one search restricted to youtube.com; Jarvis never contacts YouTube itself. `search.user_location` only if you set it (off by default). |
| 3 | `jarvis/memory/summary.py` (memory consolidation) | `api.anthropic.com` | in the background every `memory.consolidate_every_turns` requests (default 6), when the conversation pauses, and on exit | the text of recent turns **with sensitive sentences already removed**, the current rolling summary, and the list of stored facts, so Claude can update the summary and spot new or outdated facts |
| 4 | `scripts/download_models.py` (setup only) | `huggingface.co`, `github.com` (pinned URLs in `config/models.yaml`) | only when you run it | an ordinary HTTPS download request; nothing about you |

The official Anthropic SDK also sends request headers with your OS name, CPU architecture,
Python and SDK version (`x-stainless-*`). Anthropic's data-handling terms apply to calls 1–3.

`scripts/check_caching.py` is an optional diagnostic that makes two tiny conversation
calls (type 1) with fixed test sentences.

Things that look like network activity but are not Jarvis's:
- `open_url` hands an http/https address to your **default browser** (`webbrowser`). The
  browser then loads the page, as if you had clicked a link. Sites opened by name come from
  the `websites:` list in `config/apps.yaml`. `play_youtube` does the same with a
  `https://www.youtube.com/watch?v=<id>` address rebuilt from the video ID alone.
- `create_pdf` writes files into the project's `Outputs` folder; fpdf2 can load remote
  images, but Jarvis only ever places text, so no request is made.
- `open_app` starts local programs with `os.startfile` (Windows ShellExecute). It never runs
  shell commands.
- Installing the Python packages with pip downloads them from PyPI once. That's pip, at setup.

## What never leaves your machine

- **Audio.** Your voice and Jarvis's voice are processed in memory, locally.
- **Your API key**, except as the authentication header to Anthropic. It is never logged,
  printed or shown in any UI.
- **Logs**, the **memory database file** itself, and the names and paths of apps on your PC.
  Start Menu searches run locally; only the outcome line (e.g. "Opened Spotify.") goes to
  Claude.
- **Facts not relevant to the current request.** Recall runs locally: only the few facts
  whose words match what you just said are added to that request (call 1), plus the notes
  Jarvis looks up when you ask what it remembers. The full fact list goes only to the
  background consolidation call (call 3).

## What is stored locally, and where

Everything is under `%LOCALAPPDATA%\Jarvis` (or `paths.data_dir`). Jarvis refuses to start if
`paths.data_dir` points inside the repository.

| Data | Location | Contents |
| --- | --- | --- |
| **Memory database** | `%LOCALAPPDATA%\Jarvis\memory.db` (SQLite) | facts about you (one short sentence each, with English/Hindi keywords, a category, whether you asked for it or it was extracted, and timestamps) and one rolling summary of past conversations (up to `memory.max_summary_chars`, default 1,500 characters) |
| Logs (rotating, 5 × 1 MB by default) | `%LOCALAPPDATA%\Jarvis\logs\` | timings, token counts, cache hits, estimated cost, model used, tool names and success, how many facts were recalled or saved. **No conversation text and no fact text** unless `logging.log_transcripts: true`. API keys are masked. |
| Debug recordings (only if `audio.debug_save_audio: true`) | `%LOCALAPPDATA%\Jarvis\debug-audio\` | WAV files of what you said |
| HUD data | `%LOCALAPPDATA%\Jarvis\hud\` | window settings and Chromium cache; no conversation text |
| API key | `.env` in the repo folder (git-ignored), or an environment variable | your key |
| Speech models | `models\` in the repo folder (git-ignored) | Whisper, Kokoro, Piper and wake-word model files; no personal data |
| PDFs you asked for | `Outputs\` (or `Outputs_Jarvis\`) in the repo folder (git-ignored) | the documents themselves; delete them whenever you like |

The current conversation (the last `llm.history_max_turns` turns) is held **in memory only**
and is gone when Jarvis exits. What carries over between sessions is only what is in
`memory.db`.

## Memory: what gets remembered, and your controls

**How facts get in:**
1. You ask: "remember that my sister is called Meera" (the `remember` tool).
2. Extraction: the background consolidation call (call 3 above) reads recent turns and
   returns lasting facts you stated or confirmed (preferences, people you mention, your
   projects, routines, dates) as structured JSON. It also rewrites the rolling summary.

**What is never stored, even if you say it.** Passwords, PINs, OTPs, CVVs, government ID
numbers (Aadhaar, PAN, passport, voter ID, driving licence, SSN), payment card numbers and API
keys. This is enforced in code (`jarvis/memory/sensitive.py`), not left to the model:

- every fact and every summary is checked by the database layer before it is written;
  a fact that trips the check is refused, and a summary loses the offending sentences;
- transcript text is cleaned *before* it is sent to the consolidation call, so Claude never
  sees those sentences there;
- when you ask Jarvis to remember one of these, it refuses politely and does not repeat the
  value back.

The check is deliberately strict: any mention of a password or PIN is refused, even "I use
a password manager". Card numbers must pass the Luhn checksum. 12-digit numbers are treated
as Aadhaar numbers. Devanagari digits count too.

Note that what you *say* in a request still goes to Claude as part of that conversation
(call 1), like everything else you say. The guard is about what Jarvis **keeps**.

**Your controls:**

| You want to | Do this |
| --- | --- |
| See what Jarvis remembers | ask "what do you remember about me?", or `python -m jarvis --export-memory memory.json` (works without an API key) |
| Forget one thing | "forget that my sister is Meera" / "भूल जाओ ...". If several notes match, Jarvis asks first. If the rolling summary mentions the topic, the whole summary is deleted too. |
| Forget everything | "forget everything about me" (asks "Are you sure?") |
| Delete all local data | quit Jarvis, then `python scripts\wipe_data.py`. It deletes `memory.db`, `logs\`, `debug-audio\` and `hud\` by name, after you type `wipe`. |
| See or delete memory in the HUD | the Memory tab lists every fact and the summary; × forgets one fact, "Forget everything" (click twice) erases all |
| Turn memory off | `memory.enabled: false` in `config/config.yaml`. The memory tools and the consolidation call disappear. |

Deleted rows are overwritten with zeros (`PRAGMA secure_delete`), so a forgotten fact is gone
from the file, not just hidden. After a forget, conversation turns not yet saved are
discarded too, so the forgotten fact cannot be re-extracted from the conversation that
mentioned it. **One limitation:** during the *current* session, Claude can still see the
forgotten text in the recent conversation history it receives. It is told not to use it
again, and it is gone after a restart.

## The HUD (Electron)

The HUD (`ui/`) is a desktop window built with Electron. It shows Jarvis's state and talks to
the Python backend over a WebSocket. What it does and doesn't do:

- **No network access beyond the laptop.** The HUD's main process cancels every request
  from the page except the backend WebSocket on `127.0.0.1:<this launch's port>`. A strict
  Content-Security-Policy says the same (`connect-src ws://127.0.0.1:*`, scripts, styles
  and fonts only from the app itself). There are no CDNs, remote fonts, analytics, update
  checks or crash reporting. Fonts and libraries are bundled at build time.
- **The backend link is local and authenticated.** The backend listens on 127.0.0.1 only.
  For every launch Electron creates a random 256-bit token, hands it over in an environment
  variable (never on the command line), and the HUD must present it first. Connections from
  any other web origin (such as a page in your browser) are refused during the handshake.
  The token is masked in logs.
- **No microphone or camera in the window.** All permission requests from the page are
  denied; audio is handled by the Python backend as described below.
- **Links** (web search sources) open in your default browser, only if they are http/https.
- **Stored data:** window settings (compact mode) and Chromium's own cache live in
  `%LOCALAPPDATA%\Jarvis\hud\`. Transcripts are not stored by the HUD.
- **Start on login** is off unless you tick it in the tray menu. It's stored by Windows
  (the usual Run registry entry) and can be unticked the same way.

## The microphone

### Hands-free (wake word)

With `wake_word` in `voice.triggers` (the default), the microphone stays **open while
Jarvis runs**, so it can hear "Hey Jarvis". The Windows mic indicator stays on. What
happens to that audio:

- Every 32 ms block goes into memory. The wake-word detector (openWakeWord models via
  ONNX Runtime, one CPU thread) turns it into a score, and the audio is then dropped. A
  ring buffer keeps at most the last 300 ms, used as push-to-talk pre-roll.
- Only after "Hey Jarvis" (or push-to-talk) is audio **recorded**, and only until the
  voice-activity detector hears you stop, the follow-up window ends, or 30 s pass.
- That recording is transcribed locally by Whisper. Only the **text** goes to Claude.
- While Jarvis is speaking, the mic feeds only the wake-word detector, so you can
  interrupt. Nothing heard then is transcribed.
- Logs record when the wake word fired and its score, never audio.

For a mic that is open only while you hold a key, set `voice.triggers: [push_to_talk]`.

### Push-to-talk and your voice

- In push-to-talk-only mode, the microphone is open **only while you hold the key**.
- Recorded audio stays **in memory**. It is transcribed locally by Whisper and then dropped.
- Jarvis's replies are synthesised locally by Kokoro (or Piper) and played straight to your
  speakers.
- Nothing audio-related is written to disk unless you turn on `audio.debug_save_audio`
  (off by default).
- Push-to-talk reads the state of one key on request (`GetAsyncKeyState`). It installs no
  keyboard hook and records no keystrokes.

## Models

Speech models are downloaded **once** by `scripts/download_models.py` from pinned URLs:
fixed Hugging Face commits and fixed GitHub releases, never "latest". Every file must match
the SHA-256 in `config/models.yaml`. The script uses only Python's standard library. After
that, models are loaded from the local `models/` folder only:

- faster-whisper is called with a local folder and `local_files_only=True`.
- Jarvis sets `HF_HUB_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1`,
  `HF_HUB_DISABLE_IMPLICIT_TOKEN=1` and `DO_NOT_TRACK=1` at startup, before any of those
  libraries are imported.
- ONNX Runtime's Windows telemetry events are switched off with
  `onnxruntime.disable_telemetry_events()`.
- Piper is given local voice files. Its own downloader is never used.

## Every dependency and its network behaviour

Checked against the packages actually installed in `.venv` (`pip list`) and `ui/node_modules`
(2026-10-19). Phase 4 added no dependency (memory uses Python's built-in `sqlite3`); Phase 5
added `websockets` and the HUD's npm packages.

### Runtime

| Package | License | Network behaviour |
| --- | --- | --- |
| `anthropic` 1.15.0 | MIT | Calls the Anthropic API only (calls 1–3). It bundles OpenTelemetry tracing hooks, which export nothing without an OpenTelemetry SDK. Jarvis also disables tracing and trace-header propagation explicitly. |
| `opentelemetry-api` 1.45.1 (via anthropic) | Apache-2.0 | none on its own (no SDK or exporter installed) |
| `httpx2`, `httpcore2`, `h11`, `anyio`, `sniffio`, `idna` (via anthropic) | BSD-3-Clause / MIT | HTTP client stack, used only for the Anthropic API |
| `truststore` (via anthropic) | MIT | reads the Windows certificate store; no network of its own |
| `jiter`, `docstring_parser`, `typing_extensions`, `annotated-types`, `typing-inspection` | MIT / Apache-2.0 / PSF-2.0 | none |
| `pydantic` 2.14.1, `pydantic_core` | MIT | none |
| `pyyaml` 6.0.3 | MIT | none |
| `python-dotenv` 1.2.4 | BSD-3-Clause | none |
| `sqlite3` (Python standard library) | PSF / public domain (SQLite) | none; a local file |
| `websockets` 17.2 | BSD-3-Clause | a **server** bound to 127.0.0.1 for the HUD; makes no outbound connections |
| `fpdf2` 2.9.0 (added 2026-11-18, for `create_pdf`) | **LGPL-3.0-only** | can fetch remote images when asked (`image()` with a URL, `write_html` `<img>`); Jarvis never calls either, it only places text. No other network code. |
| `uharfbuzz` 0.57.0 (HarfBuzz text shaping for Devanagari) | Apache-2.0 (HarfBuzz: MIT) | none |
| `fonttools` 4.68.0, `Pillow` 13.0.0 (via fpdf2) | MIT / MIT-CMU | none |
| `faster-whisper` 1.2.1 | MIT | none at runtime with a local model folder. Its `download_model` helper (Hugging Face Hub) is never called. |
| `ctranslate2` (Whisper inference) | MIT | none |
| `huggingface_hub`, `hf-xet`, `fsspec`, `filelock`, `tqdm`, `colorama`, `click`, `packaging` (via faster-whisper / onnxruntime) | Apache-2.0 / BSD / MIT | **network only if asked to download**. Jarvis never asks, and forces offline mode. |
| `tokenizers` (via faster-whisper) | Apache-2.0 | none; reads the local `tokenizer.json` |
| `av` (PyAV, via faster-whisper) | BSD-3-Clause; the bundled FFmpeg includes x264/x265, so **the binary is GPL** | none; only used for decoding audio *files*, which Jarvis never does |
| `onnxruntime` 1.31.0 | MIT | none over the network. Windows telemetry events are disabled at startup. |
| `kokoro-onnx` 0.6.1 | MIT | none (model files come from our downloader) |
| `phonemizer` (via kokoro-onnx) | **GPL-3.0-or-later** | none |
| `espeakng-loader` (bundles espeak-ng) | loader MIT, espeak-ng **GPL-3.0** | none |
| `joblib`, `attrs`, `dlinfo`, `cloudpickle`, `pathvalidate` (via phonemizer) | BSD / MIT | none |
| `sounddevice` 0.5.6 (bundles PortAudio) | MIT | none |
| `numpy`, `protobuf`, `flatbuffers`, `cffi`, `pycparser` | BSD / Apache-2.0 / MIT | none |
| openWakeWord models (downloaded files; the inference code is ported, see THIRD_PARTY_NOTICES.md) | models **CC BY-NC-SA 4.0** (non-commercial) | none; run locally with onnxruntime |
| Silero VAD v6 (shipped inside faster-whisper) | MIT | none |
| optional `piper-tts` 1.8.0 (`pip install -e ".[piper]"`, currently installed) | **GPL-3.0-or-later** | none as used. It contains download helpers (voice list, Chinese model) that Jarvis never calls. |

### HUD (npm, in `ui/`)

Exact versions are locked in `ui/package-lock.json`. Installing them downloads from the npm
registry, and Electron's binary from GitHub (checked against its bundled SHA-256 list),
once, at setup.

| Package | License | Network behaviour |
| --- | --- | --- |
| `electron` 44.9.0 (bundles Chromium and Node.js) | MIT (Chromium's own licenses in `node_modules/electron/dist/LICENSES.chromium.html`) | the app enables no Google services, auto-update or crash reporting; every request from the page except the local backend socket is cancelled in code (see above) |
| `three` 0.186.1 (bundled into the HUD) | MIT | none |
| `highlight.js` 11.12.0 (bundled) | BSD-3-Clause | none |
| `@fontsource/rajdhani`, `@fontsource/jetbrains-mono`, `@fontsource/noto-sans-devanagari` 5.3.0 (font files bundled) | OFL-1.1 | none; fonts are served from the app, never from Google Fonts |
| `vite` 8.3.4 and its build tools (`rolldown`, `lightningcss`, `postcss`, ...) | MIT / MPL-2.0 (`lightningcss`) / ISC / BSD | build time only; not part of the running HUD |
| `@electron/get`, `@electron-internal/extract-zip`, `sumchecker`, `env-paths`, `undici`, `progress`, `semver`, `graceful-fs`, `debug` | MIT / BSD-2-Clause / Apache-2.0 / ISC | used only by `npm ci` to download and verify the Electron binary |

### Development only (not needed to run Jarvis)

| Package | License | Network behaviour |
| --- | --- | --- |
| `pytest`, `pluggy`, `iniconfig`, `Pygments`, `pytest-asyncio` | MIT / BSD-2-Clause | none |
| `ruff` | MIT | none |
| `mypy`, `mypy_extensions`, `librt`, `ast_serialize`, `pathspec`, `types-PyYAML` | MIT / MPL-2.0 / Apache-2.0 | none |
| `psutil` (benchmarks) | BSD-3-Clause | none |

## Licensing note for open-source release

Jarvis's own code is MIT. Several dependencies it *installs* are GPL-3.0: `phonemizer`,
espeak-ng inside `espeakng-loader`, the FFmpeg build inside PyAV, and the optional
`piper-tts`; `fpdf2` is LGPL-3.0 (used unmodified, as a separate library). Users install these themselves through pip and the repository doesn't contain
them, so publishing the source under MIT is fine. Any future **bundled installer**
(Phase 5) that ships these binaries must follow GPL-3.0 for those parts: provide their source
and license texts. The `hey_jarvis` wake-word model is CC BY-NC-SA 4.0, so a release that
includes it is non-commercial. This will be re-checked in the Phase 5 license audit.

## Safety rules enforced in code

- Apps launch only from `config/apps.yaml`, or from a Start Menu shortcut after you say yes.
  Nothing the model writes is ever executed as a command.
- `open_url` accepts only http/https and rejects addresses with embedded credentials.
- Web content is passed to Claude wrapped as untrusted data, and the persona tells Claude
  never to follow instructions inside it.
- `show_code` only displays code; it never runs it.
- Memory refuses sensitive categories at the database layer (see above). Forgetting several
  notes or everything requires a yes.
- Each request's output tokens are capped (`llm.max_output_tokens_per_turn`), and every call's
  estimated cost is logged locally.
- Spending per session is capped (`budget.session_cap_usd`, default $5): no Claude call is made
  whose worst-case cost could cross it, and Jarvis says so out loud at 80% and at the cap.
- The HUD is locked down as described in [The HUD](#the-hud-electron); the full checklist is
  in [docs/security-audit.md](docs/security-audit.md).
