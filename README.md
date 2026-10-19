# J.A.R.V.I.S.

A local, privacy-first, J.A.R.V.I.S.-style voice assistant for Windows that understands
English, Hindi and Hinglish. Speech stays on your machine; only the transcribed text of a
request is sent to the Claude API.

> **Status: all 5 phases built.** Say "Hey Jarvis" (or hold a key), speak in English or
> Hindi, and Jarvis answers out loud while a holographic arc reactor reacts to your voice and
> its own. It opens apps and websites, searches the web, shows code in a side panel,
> remembers things about you across restarts (locally, in SQLite), and never spends more
> than $5 per session.

## Requirements

- Windows 11 (other OSes later, via `backend/jarvis/os_adapters/`)
- Python 3.11 or newer (3.12 recommended), from [python.org](https://www.python.org/downloads/)
- Node.js 20 or newer (LTS), from [nodejs.org](https://nodejs.org/), for the HUD
- A microphone and speakers. A **headset** gives the best results.
- About 2 GB of disk: ~1 GB speech models, ~350 MB for Electron and the HUD packages
- An Anthropic API key, with **web search enabled** for your organisation in the
  [Anthropic Console](https://console.anthropic.com) (needed for the `web_search` tool)

## Setup on a fresh Windows machine

1. Install Python and Node.js (links above). Tick "Add python.exe to PATH" in the Python
   installer.
2. Get the code into a folder, for example `Desktop\J.A.R.V.I.S`.
3. In PowerShell, from that folder, run the one-command setup:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
   ```

   It creates `.venv`, installs the backend, downloads the speech models (once, pinned and
   checksum-verified, resumable if your connection drops), installs and builds the HUD, and
   creates `.env`. Re-running it is safe; finished steps are skipped.
4. Open `.env` in Notepad and paste your key after `ANTHROPIC_API_KEY=`.
   Optional: copy `config\config.local.example.yaml` to `config\config.local.yaml` and put
   your name in it, so Jarvis addresses you by name (the default is "Tony").
5. Start the HUD:

   ```powershell
   cd ui
   npm start
   ```

   The HUD starts the backend itself and connects to it. Close the window to hide it in the
   tray; quit from the tray icon's menu.

<details>
<summary>Manual setup (what the script does)</summary>

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python scripts\download_models.py     # what config.yaml uses, ~1 GB
cd ui; npm ci; npm run build; cd ..
copy .env.example .env; notepad .env
```
</details>

Optional: install the Piper fallback voice engine (GPL-3.0) with
`.venv\Scripts\python -m pip install -e ".[piper]"`, then run
`scripts\download_models.py --group piper`.

## Usage

**The HUD** (`cd ui; npm start`) is the main way to use Jarvis:

- The arc reactor shows what Jarvis is doing. Dim and breathing means idle; a **white aura**
  that follows your voice means listening; the coils light up in a chase while it's
  thinking; a **cyan aura** that follows its voice means speaking. Amber means an error;
  grey and desaturated means muted; very dim means the backend is offline.
- Bottom: the live transcript and a text box for typing instead of talking.
- Right: **Code** (full code with highlighting and a Copy button), **Sources** (from web
  searches; click to open in your browser), and **Memory** (everything Jarvis remembers;
  delete single facts, or "Forget everything" with a second click to confirm).
- Status bar: state, microphone, model in use, and spending against the $5 session cap.
- Keys: hold **Space** to talk (when the text box isn't focused), **M** mutes the
  microphone, **Esc** stops Jarvis. Left Ctrl push-to-talk and "Hey Jarvis" work as well.
- The title-bar square button switches to a **compact always-on-top** window. The tray menu
  has mute, compact mode, **start on login**, and quit.
- With reduced motion turned on in Windows (Settings, Accessibility, Visual effects,
  Animation effects off), the reactor animates more calmly.

**Without the HUD**, from the repository folder:

```powershell
.venv\Scripts\python -m jarvis --voice         # voice mode (wake word + push-to-talk)
.venv\Scripts\python -m jarvis                 # text mode; /quit to leave
.venv\Scripts\python -m jarvis --once "open vs code"
.venv\Scripts\python -m jarvis --export-memory memory.json   # everything Jarvis remembers, as JSON
.venv\Scripts\python scripts\wipe_data.py                    # delete memory, logs, debug audio, HUD data
.venv\Scripts\python scripts\check_caching.py                # prove prompt caching works (live API)
```

**Voice mode, hands-free:** say **"Hey Jarvis"** and pause briefly.
- If you haven't talked to Jarvis for 10 minutes (or it just started), it greets you with
  "Hello Tony, whatsup?".
- Then say your request. Jarvis notices when you stop talking, answers out loud, and keeps
  listening for about 6 seconds, so you can follow up without saying "Hey Jarvis" again.
  If you stay quiet, it goes back to waiting for the wake word.
- Saying "Hey Jarvis" while it's talking interrupts it and listens again.
- If Jarvis asks a yes/no question (for example before opening an app that isn't on the
  allowlist), just answer "yes" / "haan" or "no" / "nahi".

**Push-to-talk** works too: hold **Left Ctrl** by itself, speak, then release. Pressing it
while Jarvis is talking interrupts. Shortcuts such as Ctrl+C and Ctrl+V do **not** trigger
recording, because the key has to be held alone for 0.3 s. Ctrl+C in the Jarvis window
quits.

Choose the triggers with `voice.triggers` in `config/config.yaml`. With `[push_to_talk]`
only, the microphone is opened only while you hold the key. With the wake word on, the
microphone stays open, processed locally, so Jarvis can hear "Hey Jarvis". Windows'
microphone indicator stays on while Jarvis runs.

Things to try:

| You say (or type) | What happens |
| --- | --- |
| `open vs code` | "Opening VS Code." then VS Code starts |
| `calculator kholo` / `कैलकुलेटर खोलो` | opens Calculator (narration in Hindi for Devanagari input) |
| `open mysql workbench` | not on the allowlist: Jarvis finds the Start Menu shortcut and asks before opening |
| `open chrome` | on the allowlist but not installed on this laptop: Jarvis says so |
| `what is the weather in Mumbai` | "Searching for that now.", a short answer, sources listed |
| `open youtube.com` | opens the site in your default browser (http/https only) |
| `write a python function for binary search` | a short spoken-style explanation, full code in a code block |
| `remember that my sister is called Meera` | stored in long-term memory; survives restarts |
| `what do you remember about my sister?` / `मेरी बहन का नाम क्या है?` | answers from memory |
| `forget that my sister is Meera` / `भूल जाओ` | deleted (asks first if several notes match) |
| `forget everything about me` | asks "Are you sure?", then erases all memory |
| `remember my wifi password is tiger123` | politely refused: passwords, PINs, ID and card numbers are never stored |

**Memory.** Besides explicit "remember that ...", Jarvis keeps a short rolling summary of
your conversations and picks out lasting facts (your projects, people you mention,
preferences). That runs as a small background Claude call every few requests, when the
conversation pauses, and on exit. It never delays a reply. Before answering, Jarvis looks up
facts related to what you just said (locally, no extra call) and passes only those along.
Everything lives in `%LOCALAPPDATA%\Jarvis\memory.db`; see [PRIVACY.md](PRIVACY.md).

## Configuration

- `config/config.local.yaml` (git-ignored, optional): personal overrides merged on top of
  `config.yaml`, e.g. `assistant.user_name` / `user_name_hi`. `{user_name}` and
  `{user_name_hi}` in `config.yaml` lines are filled from these. See
  `config/config.local.example.yaml`.
- `config/config.yaml`: models and routing, timeouts, narration and fallback lines, logging,
  memory (`memory:`; set `enabled: false` to turn it off), list prices for the cost log
  (`pricing:`), and the per-request output-token cap (`llm.max_output_tokens_per_turn`).
- `config/apps.yaml`: the app allowlist with English and Hindi aliases. To find a Store app's
  id, run `Get-StartApps` in PowerShell.
- `config/persona.md`: Jarvis's personality and rules. Edit freely.
- `ui/src/hologram/states.js`: the reactor's look: proportions, colours, `coreShape`
  (`'circle'` or the Mark II `'triangle'`), and the visual parameters for each state.
- Spending cap: `budget.session_cap_usd` (default 5.0). Before every Claude call Jarvis
  estimates its worst case and won't make it if that could cross the cap. It warns out
  loud once at 80%. The cap resets when Jarvis restarts.
- Date and time: every request carries a short note with your local date and time
  (`llm.time_note`), so "what's the date?" needs no web search.

Logs are JSON lines in `%LOCALAPPDATA%\Jarvis\logs\jarvis.log`. They record timings, token
usage, cache hits and estimated cost per call, plus a `session usage` / `session cost`
summary when Jarvis exits, but not what you said (unless you set
`logging.log_transcripts: true`).

## Development

```powershell
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check backend scripts
.venv\Scripts\python -m ruff format backend scripts
.venv\Scripts\python -m mypy
.venv\Scripts\python scripts\export_protocol_schema.py   # after changing jarvis/events.py
cd ui; npm test; npm run build                             # HUD unit tests and bundle
```

HUD developer aids (environment variables / flags for `npx electron .` in `ui/`):
`JARVIS_HUD_DEVTOOLS=1` enables DevTools; `--capture=out.png` saves a screenshot after
`JARVIS_HUD_CAPTURE_DELAY_MS` (default 4000) and quits; `JARVIS_PYTHON` picks the Python
executable; `JARVIS_BACKEND_ARGS` (a JSON array) replaces `-m jarvis --serve`, for running
the HUD against a test backend.

See [docs/architecture.md](docs/architecture.md), [docs/protocol.md](docs/protocol.md),
[docs/adding-a-tool.md](docs/adding-a-tool.md) and [PRIVACY.md](PRIVACY.md).

## Troubleshooting

- **HUD shows "Jarvis's backend could not start: …"**: the message is the backend's own
  error. Most often the API key is missing (see the next item), or `.venv` doesn't exist
  yet (run `scripts\setup.ps1`).
- **HUD says "Backend offline. Reconnecting…"**: the backend crashed and is being restarted
  automatically. The reason is in `%LOCALAPPDATA%\Jarvis\logs\jarvis.log`.
- **HUD banner "Voice is unavailable"**: the microphone or speakers couldn't be opened, or
  model files are missing. Typing still works. Check the items below about microphones and
  models, then restart the HUD.
- **The HUD window disappeared**: closing hides it to the tray (notification area). Click
  the reactor icon to show it again.
- **"ANTHROPIC_API_KEY is not set"**: create `.env` from `.env.example` and add your key.
- **"I've reached this session's 5 dollar spending limit"**: the session cap
  (`budget.session_cap_usd`) was reached. Restart Jarvis to reset it, or raise the cap.
- **Model download keeps failing**: the downloader resumes where it stopped. Just run
  `scripts\download_models.py` again, as often as needed. `--verify` checks the files you
  already have against their SHA-256.
- **"I could not reach my brain right now"**: no internet, an invalid key, or a wrong model
  name. The log file has the error code (`llm_network`, `llm_auth`, `llm_bad_model`, ...).
- **Search always fails**: web search must be enabled for your organisation in the Console.
- **An app won't open**: check that it's installed and that its entry in `config/apps.yaml`
  is right. For Store apps, use the id from `Get-StartApps`.
- **Hindi shows as boxes**: use Windows Terminal (its default font renders Devanagari).
- **"Voice mode could not start: model file missing"**: run `scripts\download_models.py`.
  `--verify` checks the files you already have.
- **Wrong microphone or speakers**: list devices with
  `.venv\Scripts\python -c "import sounddevice; print(sounddevice.query_devices())"` and set
  `audio.input_device` / `audio.output_device` in `config/config.yaml` to part of the name.
- **Nothing happens when holding Left Ctrl**: hold it alone for at least 0.3 s, with no other
  key or mouse button. You can change the key with `voice.push_to_talk_key`.
- **Jarvis hears itself / echo**: Jarvis never transcribes what it hears while it is talking
  (half-duplex). With laptop speakers, its own voice can still reach the wake-word detector,
  so interrupting needs a clearer "Hey Jarvis" (`wake_word.speaking_threshold`). **A headset
  gives the best experience**: no echo, easy barge-in, better transcription.
- **"Hey Jarvis" triggers by itself (TV, conversation)**: raise `wake_word.threshold` (e.g.
  0.6–0.7). Measure first with `scripts\wakeword_soak.py --source mic --minutes 10`.
  Phrases that rhyme, like "Hey Travis", are the usual culprits.
- **"Hey Jarvis" doesn't respond**: say it clearly with a short pause after it, or lower
  `wake_word.threshold` (e.g. 0.4). Check that the mic isn't muted: the soak script warns
  when it only hears silence.
- **Jarvis cuts you off, or waits too long after you finish**: tune `vad.end_silence_ms`
  (default 800 ms of silence ends your turn).
- **Microphone records silence**: check the laptop's mic-mute key (usually an F-key with a
  mic icon and LED) and Settings → Sound → Input.
- **Hindi is transcribed poorly**: try a larger Whisper model (`stt.model: large-v3-turbo`
  after `download_models.py --group whisper-large-v3-turbo`). It's slower; see the
  benchmark in `docs/voice-benchmarks.md`.
- **Jarvis doesn't seem to remember something**: facts you didn't explicitly ask it to
  remember are saved every few requests (`memory.consolidate_every_turns`) and on a clean
  exit. Ask "what do you remember about ...?" to check, or export with `--export-memory`.
- **"Memory problem: ... newer Jarvis"**: the memory file was written by a newer version.
  Update Jarvis, or export/wipe it.
- **`wipe_data.py` says "Could not delete ... Is Jarvis still running?"**: quit Jarvis first;
  Windows keeps the log file locked while it runs.
- **"That one needs more work than I'm allowed to spend on a single request"**: the request
  hit `llm.max_output_tokens_per_turn`. Raise it if that happens for legitimate requests.
- **Voice sounds odd or too "processed"**: set `tts.postfx.enabled: false`, or try other
  voices with `scripts/audition_voices.py`.
