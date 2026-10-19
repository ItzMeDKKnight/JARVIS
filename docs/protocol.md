# Event protocol (v2)

JSON messages between jarvis-core and its front-ends. Source of truth:
`backend/jarvis/events.py`. Generated schema: `protocol/events.schema.json`, the one shared
file both sides use. The HUD reads the protocol version and the list of message types from it
(`ui/src/protocol.js`). Every message has `"v": 2` and a `"type"` discriminator. Unknown
fields and unknown types are rejected.

The terminal front-end gets these events in-process. The HUD gets the same JSON over a
WebSocket (see [Transport](#transport)).

## Backend → UI

| type | fields | notes |
| --- | --- | --- |
| `state_changed` | `state`: `idle` \| `listening` \| `thinking` \| `speaking` \| `error` \| `muted` | single source of truth for the HUD |
| `audio_level` | `source`: `mic` \| `tts`, `level`: 0..1 | ~31 Hz; mic drives the white aura, tts the cyan one |
| `transcript` | `turn_id`, `role`: `user` \| `assistant`, `text`, `language`: `en` \| `hi`, `final` | partial updates repeat the same `turn_id` with the **full text so far** |
| `tool_started` | `call_id`, `name`, `summary` | `summary` is the narration line (may be empty) |
| `tool_finished` | `call_id`, `name`, `ok`, `summary` | |
| `code_block` | `title`, `language`, `code` | display only, never executed |
| `sources` | `items`: `[{title, url}]` | from web search |
| `error` | `code`, `message` | e.g. `llm_network`, `llm_auth`, `internal`, `voice_unavailable`, `bad_command` |
| `status` | `muted`, `voice`, `hands_free`, `push_to_talk_key` | on connect and on change. `voice: false` means typed input only |
| `usage` | `model`, `route`, `session_cost_usd`, `cap_usd` | after every conversation call (status bar) |
| `memory` | `enabled`, `facts`: `[{id, text, category, source, created_at}]`, `summary` | on connect, on `memory_list`, after HUD deletions |

## UI → backend

| type | fields | notes |
| --- | --- | --- |
| `auth` | `token` (16–256 chars) | **must be the first message**; the per-launch token |
| `text_input` | `text` (1–4000 chars) | typed request, or the answer to a pending yes/no question |
| `mute_toggle` | none | closes / reopens the microphone |
| `interrupt` | none | stop speaking / thinking / listening |
| `ptt_start` / `ptt_stop` | none | HUD push-to-talk (Space held); same 300 ms hold rule as the keyboard |
| `memory_list` | none | asks for a `memory` event |
| `memory_delete` | `ids` (1–500 ints) | forgets those facts (same guarantees as a spoken "forget") |
| `memory_forget_all` | none | erases all facts and the summary (the HUD confirms first) |
| `shutdown` | none | clean exit (saves the last memory summary and the cost log) |

## Transport

- `python -m jarvis --serve` binds **127.0.0.1** on a free port (`server.port: 0`) and prints
  `JARVIS_READY {"port": N, "protocol": 2}` on stdout.
- The HUD's Electron process generates a random 256-bit token for each launch and passes it
  in the `JARVIS_HUD_TOKEN` environment variable. The renderer gets the port and token
  through the preload bridge.
- Connections need `Origin: app://jarvis` (`server.allowed_origins`); others are refused with
  HTTP 403 during the handshake. Then `auth` must arrive within `server.auth_timeout_s`, or
  the connection is closed with code **4401**. A third client is refused with **4429**
  (`server.max_clients`). Messages are capped at `server.max_message_bytes`.
- On connect the backend sends a snapshot: `state_changed`, `status`, the last `usage` (if
  any), and `memory`.
- If Electron goes away, the backend's stdin closes and it shuts down cleanly.

## Example

```json
{"v": 2, "type": "auth", "token": "9f3c…"}
{"v": 2, "type": "state_changed", "state": "idle"}
{"v": 2, "type": "text_input", "text": "open vs code"}
{"v": 2, "type": "tool_started", "call_id": "toolu_01", "name": "open_app", "summary": "Opening VS Code."}
```

## Changing the protocol

1. Edit the models in `events.py`. Bump `PROTOCOL_VERSION` if the change is breaking.
2. Run `scripts/export_protocol_schema.py`.
3. Update this file. `test_committed_schema_matches_models` fails until step 2 is done, and
   the HUD tests (`cd ui; npm test`) read the regenerated schema.
