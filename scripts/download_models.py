"""One-time download of the local speech models, with checksum verification.

Every file is listed in config/models.yaml with a pinned URL (a fixed commit
or release, never "latest") and its SHA-256. A file is only moved into
models/ after its hash matches, so a truncated or tampered download is never
used. Only the Python standard library is used for the network access,
nothing that could add telemetry.

    .venv/Scripts/python scripts/download_models.py            # what config.yaml needs
    .venv/Scripts/python scripts/download_models.py --list
    .venv/Scripts/python scripts/download_models.py --group whisper-medium --group piper
    .venv/Scripts/python scripts/download_models.py --verify   # check files already present

After this, Jarvis runs fully offline (apart from the Claude API).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from jarvis.config import JarvisConfig, load_config
from jarvis.utils.console import use_utf8_console

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "config" / "models.yaml"
CHUNK = 1 << 20
TIMEOUT_S = 60
# Slow or flaky connections drop often; each attempt resumes where the last stopped.
MAX_ATTEMPTS = 20


def load_manifest() -> dict[str, dict[str, object]]:
    with MANIFEST_PATH.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    groups: dict[str, dict[str, object]] = data["groups"]
    return groups


def needed_groups(config: JarvisConfig) -> list[str]:
    """The groups the current config.yaml actually uses."""
    groups = [f"whisper-{config.stt.model}"]
    if config.tts.engine == "kokoro":
        model = (
            "kokoro-fp32" if config.tts.kokoro.model_file.endswith("v1.0.onnx") else "kokoro-int8"
        )
        groups += [model, "kokoro-voices"]
    else:
        groups.append("piper")
    return groups


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def _fetch_into(url: str, part: Path, size: int, label: str) -> None:
    """Append the rest of ``url`` to ``part``, resuming with an HTTP Range request.

    A dropped connection can end a response early *without* an error, so the
    caller must compare the final size; it never trusts a clean EOF alone.
    """
    have = part.stat().st_size if part.exists() else 0
    request = urllib.request.Request(url)  # noqa: S310 - https enforced by the caller
    if have:
        request.add_header("Range", f"bytes={have}-")
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
        if have and response.status != 206:
            have = 0  # server ignored the Range header: start over
        with part.open("ab" if have else "wb") as out:
            while block := response.read(CHUNK):
                out.write(block)
                have += len(block)
                print(f"\r  {label}: {have / 2**20:7.1f} / {size / 2**20:.1f} MB", end="")
    print()


def download(url: str, dest: Path, expected_sha256: str, size: int) -> None:
    """Download to a .part file (resuming after drops), verify size and hash, then move it."""
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-HTTPS URL: {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            _fetch_into(url, part, size, dest.name)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            print(f"\n  network error ({exc}); retry {attempt}/{MAX_ATTEMPTS}")
            time.sleep(min(60, 2**attempt))
            continue
        have = part.stat().st_size
        if have == size:
            break
        if have > size:
            part.unlink()
            raise RuntimeError(f"{dest.name} is larger than expected; the download was discarded")
        print(f"  connection ended early ({have} of {size} bytes); resuming")
    else:
        raise RuntimeError(f"giving up on {dest.name} after {MAX_ATTEMPTS} attempts; rerun later")

    if sha256_of(part) != expected_sha256:
        part.unlink()
        raise RuntimeError(f"checksum mismatch for {dest.name}; the download was discarded")
    part.replace(dest)


def process_group(
    name: str, group: dict[str, object], models_dir: Path, *, verify_only: bool
) -> bool:
    print(f"[{name}] {group.get('description', '')}")
    ok = True
    files = group["files"]
    if not isinstance(files, list):
        raise TypeError(f"group {name}: 'files' must be a list")
    for entry in files:
        dest = models_dir / entry["path"]
        if dest.is_file() and sha256_of(dest) == entry["sha256"]:
            print(f"  ok       {entry['path']}")
            continue
        if verify_only:
            print(
                f"  MISSING  {entry['path']}"
                if not dest.exists()
                else f"  BAD HASH {entry['path']}"
            )
            ok = False
            continue
        download(entry["url"], dest, entry["sha256"], entry["size"])
        print(f"  verified {entry['path']}")
    return ok


def main() -> None:
    use_utf8_console()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--group", action="append", help="download this group (repeatable)")
    parser.add_argument("--all", action="store_true", help="download every group")
    parser.add_argument("--list", action="store_true", help="list groups and exit")
    parser.add_argument("--verify", action="store_true", help="only check files already present")
    args = parser.parse_args()

    manifest = load_manifest()
    if args.list:
        for name, group in manifest.items():
            files = group["files"]
            if not isinstance(files, list):
                raise TypeError(f"group {name}: 'files' must be a list")
            size = sum(int(f["size"]) for f in files) / 2**20
            print(f"{name:24} {size:8.1f} MB  {group.get('description', '')}")
        return

    config = load_config()
    names = list(manifest) if args.all else (args.group or needed_groups(config))
    unknown = [n for n in names if n not in manifest]
    if unknown:
        sys.exit(f"unknown group(s): {', '.join(unknown)} (see --list)")

    models_dir = config.paths.resolved_models_dir()
    results = [process_group(n, manifest[n], models_dir, verify_only=args.verify) for n in names]
    if not all(results):
        sys.exit("some files are missing or corrupt; run without --verify to (re)download them")
    print(f"done: models are in {models_dir}")


if __name__ == "__main__":
    main()
