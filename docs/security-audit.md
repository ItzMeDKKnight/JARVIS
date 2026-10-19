# Final security, privacy and license audit (Phase 5, 2026-10-19)

Each item says how it was checked. "Test" means an automated test that fails if the property
breaks; "manual" means checked by hand during this audit.

## Electron hardening

| Check | Status | How |
| --- | --- | --- |
| `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, `webSecurity: true` | ✅ | `ui/electron/main.js` webPreferences (manual) |
| Preload exposes only named calls, no `ipcRenderer` / Node APIs | ✅ | `ui/electron/preload.cjs` (manual) |
| IPC handlers accept only our page as sender, and validate/clamp arguments | ✅ | `isTrustedSender` (test `electron.test.js`); clipboard length cap, booleans coerced, URLs checked |
| Content only from bundled files over a privileged custom scheme (`app://jarvis`) | ✅ | `resolveAppFile` blocks `..`, encoded separators and other hosts (test); unknown file types → 404 |
| Strict CSP: `default-src 'none'`, no `unsafe-inline` / `unsafe-eval`, `connect-src ws://127.0.0.1:*` | ✅ | sent as a response header on every file **and** as a `<meta>` tag (test checks the policy string) |
| Every network request from the renderer cancelled except the backend socket | ✅ | `session.webRequest.onBeforeRequest` + `isAllowedRequest` (test: https, http, file, wrong port, `localhost` all refused). Verified live: the old `file://` fetch path was blocked by this guard, so files are now read from disk instead |
| No remote content, no CDN scripts, no Google Fonts | ✅ | everything bundled by Vite; `assetsInlineLimit: 0` keeps fonts as files under `font-src 'self'` |
| All permission requests (mic, camera, notifications, …) denied | ✅ | `setPermissionRequestHandler` / `setPermissionCheckHandler` (manual) |
| New windows and navigation blocked; http(s) links open in the default browser | ✅ | `setWindowOpenHandler`, `will-navigate`, `will-attach-webview` (manual); `isSafeExternalUrl` (test) |
| DevTools off unless `JARVIS_HUD_DEVTOOLS=1` | ✅ | manual |
| Spellcheck service (which can contact Google for dictionaries) off | ✅ | `spellcheck: false` |
| Single instance (no second backend) | ✅ | `requestSingleInstanceLock` |
| Renderer warnings/errors visible | ✅ | forwarded to the main process stderr; a full capture run printed **no** warnings or errors |

## Backend link (WebSocket)

| Check | Status | How |
| --- | --- | --- |
| Bound to 127.0.0.1 only | ✅ | config type allows only `127.0.0.1`; test reads the bound socket address |
| Per-launch random token (256-bit), passed via env var, never on the command line | ✅ | `ui/electron/backend.js` (test checks a 64-hex-char token reaches the child) |
| Token required as the first message, constant-time comparison, timeout | ✅ | tests: wrong token → 4401, command before auth → closed, silence → closed after `auth_timeout_s` |
| Foreign `Origin` refused at the handshake (blocks web pages guessing the port) | ✅ | test: `https://evil.example`, `http://localhost:5173` and no Origin → HTTP 403 |
| Every command validated against the protocol models; unknown types rejected | ✅ | test: `run_shell` → `bad_command` error, not dispatched |
| Client count and message size capped | ✅ | test (4429); `max_size` from config |
| Token masked in logs | ✅ | `register_secret(hud_token)` in `main.py` |
| Clean shutdown when Electron exits or dies | ✅ | stdin watcher; verified live: log shows `server closed`, `session cost`, `jarvis stopped` |

## Secrets

| Check | Status | How |
| --- | --- | --- |
| API key only from env or git-ignored `.env`; never logged, printed or sent to the HUD | ✅ | `load_api_key`; `redact()` on every log line (tests in `test_utils.py`); the HUD never receives it (the protocol has no field for it) |
| Secrets scan of the repository | ✅ | manual grep of all non-ignored files for Anthropic keys (`sk-ant-…`), private keys, AWS and GitHub tokens: the only match is the deliberately fake key in `backend/tests/test_utils.py` (redaction test) |
| `.env`, `models/`, `ui/node_modules/`, `ui/dist/`, `build/` git-ignored | ✅ | `git check-ignore` |
| User data never inside the repo | ✅ | config rejects `paths.data_dir` inside the repo (test); HUD data in `%LOCALAPPDATA%\Jarvis\hud` |

## Privacy (unchanged guarantees, re-checked)

- Audio stays in the backend's memory; nothing audio-related crosses the HUD link except
  levels (0..1 numbers).
- Outbound connections: only the Anthropic API (conversation, search, memory consolidation),
  plus one-time setup downloads. Full list in [PRIVACY.md](../PRIVACY.md).
- New in Phase 5: the per-request time note (local date/time + UTC offset) goes to Anthropic
  as part of call 1; it can be switched off with `llm.time_note: false`.
- Spending: hard $5 per-session cap, enforced before every call (tests in `test_cost.py`,
  `test_assistant.py`).

## License check for an open-source release

Jarvis's own code: **MIT**.

| Component | License | Implication |
| --- | --- | --- |
| Python runtime deps | MIT / BSD / Apache-2.0 / PSF / MPL-2.0 | permissive; installed by pip, not vendored |
| `phonemizer`, espeak-ng (in `espeakng-loader`), PyAV's FFmpeg build, optional `piper-tts` | **GPL-3.0** | fine for an MIT source release (installed by the user). A bundled installer must comply with GPL-3.0 for these: ship their source and licenses |
| openWakeWord `hey_jarvis` model | **CC BY-NC-SA 4.0** | **non-commercial only**. A commercial release needs a self-trained model |
| HUD npm packages (inventory of every installed package, 2026-10-19) | MIT (electron, three, vite, …), BSD-2/3 (highlight.js, extract-zip), ISC, Apache-2.0, **OFL-1.1** (fonts), **MPL-2.0** (lightningcss, build-time only) | all permissive. Fonts and three.js / highlight.js notices must accompany a built HUD (THIRD_PARTY_NOTICES.md); Electron's Chromium licenses ship inside its `dist/` |

No package with an unknown or proprietary license is installed.

## Known issues (final list)

1. **Not yet run against Claude.** No `.env` exists on the development laptop, so the full
   end-to-end hands-free demo with Claude, the 30-minute stability run, measured latency
   including Claude, and the live cache-hit check (`scripts/check_caching.py`) are still
   pending. Every piece was tested with fakes, and serve mode ran live with the real voice
   stack and an invalid key.
2. **Laptop microphone recorded digital silence** in Phases 2–3 (probably the hardware mute
   key), so wake-word false-trigger behaviour in a real room is unmeasured.
3. **Latency target (< 2 s to first audio) isn't met on this CPU** by the local stages alone
   (~1.9 s Whisper + Kokoro); see docs/voice-benchmarks.md.
4. Typed requests sent before the voice stack finishes loading (~6 s) are answered by the
   text session, whose conversation history is separate from the voice session's.
5. After "forget", Claude still sees the forgotten text in the current session's history
   until Jarvis restarts.
6. The worst-case cost estimate is deliberately pessimistic, so the last few cents below
   the $5 cap can't be used (a Sonnet coding turn reserves about $0.09).
7. `pause_turn` web-search continuations are each checked against the budget, but a
   single search response is assumed to fit in `search_result_tokens_estimate` (30,000) input
   tokens.
8. Idle CPU (HUD + backend, measured with psutil over 15 s on this laptop, 2558×1640 window):
   **about 10–33 % of one core** (≤ 2.4 % of the 14-core machine), down from 42 % before calm
   states switched from requestAnimationFrame to a 20 fps timer and skipped the invisible
   aura. The spread depends on whether the window is fully visible (Chromium throttles covered
   windows). Long-run GPU use (30+ minutes) is still unmeasured.
9. No packaged installer: the HUD runs from the source tree (`npm start`). Packaging would
   need the GPL-3.0 compliance described above.
