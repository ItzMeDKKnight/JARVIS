# Third-party notices

## openWakeWord (code, ported)

`backend/jarvis/audio/wakeword.py` contains a port of the streaming inference
in openWakeWord 0.6.0 (`openwakeword/utils.py`, `openwakeword/model.py`):
the melspectrogram → embedding → wake-word pipeline, its buffer sizes and its
feature rescaling.

- Project: https://github.com/dscripka/openWakeWord
- Copyright 2022 David Scripka
- License: Apache License 2.0 (https://www.apache.org/licenses/LICENSE-2.0)

Changes: rewritten against onnxruntime directly (ONNX only, no TFLite),
single wake word, deterministic history seed, type hints. No other
openWakeWord code is included.

## openWakeWord pretrained models (downloaded, not included)

`melspectrogram.onnx`, `embedding_model.onnx` and `hey_jarvis_v0.1.onnx` are
fetched by `scripts/download_models.py` from the openWakeWord v0.5.1 release.
They are **not** part of this repository and are licensed separately under
**Creative Commons Attribution-NonCommercial-ShareAlike 4.0**
(https://creativecommons.org/licenses/by-nc-sa/4.0/). That means they are
for non-commercial use only. A commercial deployment would need a
self-trained wake-word model.

## Silero VAD (bundled with faster-whisper)

The voice-activity model `silero_vad_v6.onnx` ships inside the faster-whisper
package (MIT). Silero VAD is MIT-licensed (https://github.com/snakers4/silero-vad).

## Other dependencies

Installed through pip, not vendored. Their licenses, including the GPL-3.0
ones (phonemizer, espeak-ng, the FFmpeg build in PyAV, optional piper-tts) and
the LGPL-3.0 fpdf2 used for PDFs,
are listed in [PRIVACY.md](PRIVACY.md#dependencies-and-their-network-behaviour).

## HUD: bundled into `ui/dist` by the build

These are compiled into the HUD bundle (`npm run build`). The bundle isn't in the repository
(`ui/dist/` is git-ignored); anyone who distributes a built HUD must ship these notices with it.

- **three.js** 0.186.1, Copyright © 2010-2026 three.js authors, MIT License
  (https://github.com/mrdoob/three.js/blob/dev/LICENSE). Includes the `EffectComposer`,
  `RenderPass`, `ShaderPass` and `UnrealBloomPass` addons.
- **highlight.js** 11.12.0, Copyright (c) 2006, Ivan Sagalaev, BSD 3-Clause License
  (https://github.com/highlightjs/highlight.js/blob/main/LICENSE).
- **Rajdhani** (Indian Type Foundry), **JetBrains Mono** (JetBrains), **Noto Sans
  Devanagari** (Google), packaged by Fontsource 5.3.0. All under the SIL Open Font License
  1.1 (https://openfontlicense.org). The font files are bundled unmodified.
- The signed-distance triangle in `ui/src/hologram/shaders/common.glsl` is adapted from
  Inigo Quilez's article on 2D distance functions
  (https://iquilezles.org/articles/distfunctions2d/). It's a short mathematical routine and
  is credited here. Re-check its terms before a commercial release.

## HUD: runtime (installed by npm, not bundled in the repository)

- **Electron** 44.9.0, MIT License. Electron's binary includes Chromium, Node.js and many
  other components. Their licenses ship with it in
  `ui/node_modules/electron/dist/LICENSES.chromium.html` and `LICENSE`. An installer that
  redistributes Electron must include those files.
