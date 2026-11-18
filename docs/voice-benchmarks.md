# Voice benchmarks (Phase 2)

Measured on 2026-10-18 on the target laptop: HP OmniBook 7, Intel Core Ultra 5 225H
(14 cores), 16 GB RAM, CPU inference only. All numbers are real measurements from
`scripts/benchmark_stt.py` and one-off timing runs, not estimates.

**Test speech:** 5 English and 5 Hindi command-style sentences, spoken by the local Kokoro
voices (`bm_george`, `hm_omega`). Synthetic speech is cleaner than a real voice, so the
accuracy numbers are an **optimistic upper bound**. Run
`scripts/benchmark_stt.py --source mic` to measure with your own voice.

## Speech-to-text (faster-whisper 1.2.1, int8, beam 5)

Latency is from the end of speech to the final text, for 2–4 s utterances.

| Model | Threads | English median | Hindi median | English WER | Hindi CER* | RAM | Load |
|---|---|---|---|---|---|---|---|
| **small (default)** | 8 | **1.36 s** | **1.63 s** | 0% | 8.6% | +340 MB | 0.9 s |
| small | 4 | 1.47 s | 1.61 s | 0% | 8.6% | +332 MB | 0.9 s |
| medium | 8 | 3.08 s | 3.50 s | 0% | 18.7% | +870 MB | 2.7 s |
| large-v3-turbo | 8 | 4.17 s | 4.25 s | 0% | 14.9% | +929 MB | 3.0 s |

\* The CER compares against an all-Devanagari reference. Medium and turbo write English
loanwords in Latin script ("Python", "binary search", "code"), which counts as errors here
but is often what you want for Hinglish. By eye, **turbo's Hindi was clearly the most
accurate** (मौसम, कैलकुलेटर and राजधानी all correct). Small made typical errors: मोसम,
कैल्कु लेटर, पायतन, कोर (for कोड), राजदानी. The language was correct 10/10 for every model
after restricting detection to en/hi.

**Decision:** `small` with 8 threads. Turbo is about 3× slower (around 4 s before Jarvis
even starts thinking), which is too slow for conversation on this CPU. Medium is slower
than small and not better in Hindi. Turbo stays available as a config switch
(`stt.model: large-v3-turbo`) if Hindi accuracy matters more to you than speed. Running
it on the Arc iGPU via OpenVINO (the brief's stretch goal) is the realistic way to make it
fast.

### Why the encoder dominates, and what was done about it

Whisper encodes a padded 30-second window whatever the utterance length, which costs
~1.0 s for `small` on this CPU. faster-whisper's `transcribe(language=None)` encoded twice
(once for language detection, once for decoding): 4.5 s per utterance. Jarvis now:
1. runs the VAD once and skips Whisper entirely for silence;
2. detects the language on exactly the frames that will be decoded;
3. reuses that encoder output for decoding.

That brought it to 1.36–1.63 s. `test_models_integration.py` checks that the cache is hit.

## Text-to-speech (time to synthesise one chunk, no playback)

| Engine | Threads | "Good evening, Tony." (19 chars) | 75-char sentence | Hindi, 32 chars |
|---|---|---|---|---|
| **Kokoro int8 (default)** | 6 | **408 ms** | 1105 ms | 728 ms |
| Kokoro int8 | 4 | 463 ms | 1365 ms | 872 ms |
| Kokoro fp32 | 6 | 542 ms | 1510 ms | 881 ms |
| Piper medium | default | 65 ms | 126 ms | 173 ms |

Postfx adds 30–40 ms per chunk. Kokoro's speed is about 0.2–0.3× real time, so later
sentences are synthesised while earlier ones play. Only the first chunk adds latency.

## End-to-end estimate (honest)

From the end of your speech to the first audio for a short English answer:

| Stage | Time | Source |
|---|---|---|
| Speech-to-text (small) | ~1.36 s | measured |
| Claude, time to first token | **not yet measured** (needs your API key) | `first_token_ms` in the log |
| Streaming until the first clause is complete (≥25 chars) | ~0.1–0.3 s | depends on model speed |
| Kokoro first chunk + postfx | ~0.45 s | measured |
| Audio device start | ~0.02–0.05 s | 20 ms output blocks |

**Local stages alone add about 1.9 s, so the brief's "under ~2 s" target is not met on
this hardware once Claude's latency is added.** A realistic expectation is 2.5–3.5 s. The
log records the real figure every turn (`turn timing`: `stt_ms`, `first_token_ms`,
`first_audio_ms`, `total_ms`).

Options to get closer, none applied without your decision:
- Use Piper as the engine: saves about 0.35 s on the first chunk, but it's a different voice
  quality. Audition it first.
- Lower `tts.segmenter.first_chunk_min_chars` (e.g. 15) so the first piece is shorter.
- Use Whisper on the Arc iGPU via OpenVINO (stretch goal; new dependency).

## Resource use (real `python -m jarvis --voice` process)

| | |
|---|---|
| Startup to ready | 5.9 s |
| RAM (Whisper small + Kokoro int8 loaded) | 563 MB |
| CPU while idle (waiting for push-to-talk) | 1.2% of one core (0.09% of the whole CPU) |
| CPU during transcription | ~7–8 cores for ~1.4 s |

## Wake word "Hey Jarvis" (Phase 3)

Measured with `scripts/wakeword_soak.py --source tts` on 2026-10-18. The speech is the
local Kokoro voices, 7 English voices at 3 speeds. The negatives have light noise added
and include 4 Hindi sentences in 2 Hindi voices. This is synthetic speech, not a real
room, so it's a sanity check, not a guarantee.

| Threshold | Detected (84 clips) | False triggers (402 clips, ~25 min of speech) |
|---|---|---|
| 0.5 (idle default) | 79 (94%) | 7 |
| 0.7 | 78 (93%) | 6 |

- **All 5 misses were the very deep `am_onyx` voice.** Every other voice was detected at
  all three speeds.
- **False triggers:**
  - 5× "Hey Travis, pass me the salt", a near-rhyme chosen as a stress test.
  - 2× "I watched Iron Man yesterday and Jarvis was brilliant" (0.85 and 0.79).
  - Everything else scored ≤ 0.1, including "Hey Siri / Hey Google / Alexa ..." and all
    Hindi sentences.
- **Decision:**
  - The idle threshold stays at 0.5.
  - The **barge-in threshold while Jarvis talks is 0.9**. Jarvis saying its own name
    through laptop speakers must not interrupt itself, and genuine "Hey Jarvis" clips almost
    all scored above 0.95.
  - Movie or TV dialogue containing "Jarvis" can still wake it. Raise `wake_word.threshold`
    if that happens in practice.
- **Cost:** 5.2 ms of one thread per 80 ms frame (~6.5% of one core).

**Real room test: not done yet.** The laptop microphone delivered digital silence
(−96.7 dBFS on every input device; most likely muted). Once it works, run
`scripts/wakeword_soak.py --source mic --minutes 10` with TV/music, and again with normal
conversation, to measure the real false-trigger rate.

## Resource use, hands-free (real `python -m jarvis --voice`, wake word on)

| | |
|---|---|
| CPU while idle (wake word scoring every block) | 11.4% of one core (0.81% of the whole CPU) |
| RAM (Whisper small + Kokoro + wake word + VAD) | 594 MB |
| Startup to ready | ~15 s (see below) |

**Startup:** the Anthropic SDK (1.15) imports 2,676 modules, which takes about 8.5 s on
this laptop on every start, even with bytecode caches present (measured with
`python -X importtime`). The likely multiplier is antivirus scanning of each file read;
that's an assumption, not measured. Loading the speech models adds about 6.4 s
(Whisper 3.1 s, Kokoro 2.1 s, wake word 0.3 s). This doesn't affect response latency, only
the time until Jarvis is ready. Options, not applied: load the speech models in parallel
with the SDK import, or exclude the `.venv` folder from real-time scanning (a security
tradeoff for you to decide).

## Response latency, 2026-11-18 (before / after the latency pass)

**How it was measured.** 50 real voice turns from the HUD on 2026-10-19 (from `jarvis.log`)
gave the starting picture. Median from the end of speech:

| Stage | Median | Note |
| --- | --- | --- |
| End-of-speech window (`vad.end_silence_ms`) | 800 ms | before the timer starts |
| Transcription (Whisper small) | **2,525 ms** | 1.36 s in the Phase 2 benchmark |
| Claude, first token after transcription | 1,482 ms | Haiku 5.5; tool rounds 1,704 ms |
| First audio after the first token | 860 ms | first sentence streamed + Kokoro + device buffer |
| **End of speech → first audio** | **5,087 ms** | + 0.8 s window |

The biggest stage, transcription, ran almost twice as slow live as in the benchmark. The
cause, measured: **Windows power throttling (EcoQoS)**. The backend runs without a window
under the HUD, so Windows treats it as background work and slows it down. Same
process, same audio (Kokoro speech, 4 sentences × 2, on battery):

| Throttling | Whisper median |
| --- | --- |
| Windows default | 3,192 ms |
| forced on | 3,210 ms |
| **opted out** (`SetProcessInformation`, now done at voice start-up) | **2,376 ms** |

Thread count with throttling off: 4 → 2,704 ms, 6 → 2,423, **8 → 2,356** (kept), 10 →
2,411, 12 → 2,339. No gain from changing it.

**End to end, real models and real Claude calls, before vs after**
(`build/scratch/e2e_latency.py`): Kokoro speech → Whisper → Claude Haiku 5.5 → tools (app
launch faked) → Kokoro → device buffer. Medians of 3 runs per case, both arms interleaved
in one process, on battery. "Before" = Windows' default throttling, `output_latency: high`,
no phrase cache; "after" = this change. Times in ms from the end of speech. Two runs, because
battery runs are noisy:

| Case | Stage | Run 1 before | Run 1 after | Run 2 before | Run 2 after (+ phrase cache) |
| --- | --- | --- | --- | --- | --- |
| "Open calculator" | transcribed | 3,076 | 2,243 | 4,274 | 2,429 |
| | **app launched** | **3,979** | **3,116** | **5,501** | **3,334** |
| | "Opening Calculator." heard | 5,159 | 3,830 | 7,209 | **3,434** |
| Spoken question (en) | transcribed | 3,159 | 2,353 | 3,789 | 2,591 |
| | Claude first token | 3,797 | 3,015 | 5,046 | 3,278 |
| | **first word heard** | **5,515** | **4,321** | **6,994** | **4,829** |
| Hindi question | transcribed | 3,477 | 2,636 | 4,294 | 3,307 |
| | first word heard | 5,924 | 4,522 | 6,411 | 5,266 |

Run 1 had no phrase cache in either arm. In run 2 the "after" arm has it, so the narration
plays the moment the app launches. Claude's own time to first token was 0.6–0.9 s after the
transcript in both arms (unchanged, as expected). The transcripts were identical in both
arms, Hindi included (Whisper small's usual "बारत … राजदानी"), so accuracy is unaffected.
Kokoro alone barely changes with throttling (854 vs 895 ms median per sentence).

All of these come on top of the 0.8 s end-of-speech window, which was left unchanged: it
can't be tuned safely without recordings of your own pauses (Hindi speakers often pause
longer mid-sentence). Try 600–650 ms if replies feel slow to start and you aren't cut off.

**Already in place, so not changed:** streaming from Claude with sentence-by-sentence
speech (the first piece may end at a comma after 25 characters); tool narration queued the
moment a tool starts, with the tool running in parallel (the action doesn't wait for the
narration); models loaded once and kept in memory.

**Stop:** `AudioPlayer.stop()` measured at 12 ms median (17 ms max) on the real output
device, including discarding the device buffer. Before this change, a stop left up to
`stream.latency` (200 ms) of already-buffered speech playing.
