from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Iterable


@dataclasses.dataclass(frozen=True)
class PayloadSpec:
    relative_path: str
    sha256: str
    size_bytes: int


@dataclasses.dataclass(frozen=True)
class ModelSpec:
    id: str
    kind: str
    display_name: str
    archive_url: str
    asset_dir: str
    install_dir: str
    prune: tuple[str, ...]
    payloads: tuple[PayloadSpec, ...]


MODEL_SPECS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="piper-amy-low-tts",
        kind="tts",
        display_name="Piper en_US Amy (low)",
        archive_url=(
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
            "tts-models/vits-piper-en_US-amy-low.tar.bz2"
        ),
        asset_dir="vits-piper-en_US-amy-low",
        install_dir="piper_en_us_amy_low",
        prune=(),
        payloads=(
            PayloadSpec(
                relative_path="en_US-amy-low.onnx",
                sha256="8275b02c37c4ce6483b26a91704807e6de0c3f0bf8068e5d3b3598ab0da4253e",
                size_bytes=63_104_657,
            ),
            PayloadSpec(
                relative_path="en_US-amy-low.onnx.json",
                sha256="2250a9a605b8dc35a116717fadc5056695dd809e34a15d02f72a0f52d53d3ebb",
                size_bytes=4_164,
            ),
            PayloadSpec(
                relative_path="tokens.txt",
                sha256="42d1a69ed2b91a51928a711aa228ed9f3dc021c6d359a3e9c4f37eb1d20f80bd",
                size_bytes=763,
            ),
        ),
    ),
    ModelSpec(
        id="sherpa-streaming-zipformer-en-20m",
        kind="asr",
        display_name="sherpa-onnx streaming zipformer en 20M (int8)",
        archive_url=(
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
            "asr-models/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17.tar.bz2"
        ),
        asset_dir="sherpa-onnx-streaming-zipformer-en-20M-2023-02-17",
        install_dir="sherpa_streaming_en_20m",
        prune=(
            "decoder-epoch-99-avg-1.onnx",
            "encoder-epoch-99-avg-1.onnx",
            "joiner-epoch-99-avg-1.onnx",
            "export-onnx-en-20M.sh",
            "test_wavs",
        ),
        payloads=(
            PayloadSpec(
                relative_path="encoder-epoch-99-avg-1.int8.onnx",
                sha256="3810755ce7c3ab26b42a8bcf39d191308fa27fb0f53358823ba46141d03b7eb3",
                size_bytes=42_845_182,
            ),
            PayloadSpec(
                relative_path="decoder-epoch-99-avg-1.int8.onnx",
                sha256="21e2a2acd961b3ac72f55be2f10f1a285e1b0b0ba010d7c0b6eab141411b163c",
                size_bytes=539_499,
            ),
            PayloadSpec(
                relative_path="joiner-epoch-99-avg-1.int8.onnx",
                sha256="e085d73b593cf9b0707f370dbd656d58327d3fe36d80d849202ef81df02cb01e",
                size_bytes=259_572,
            ),
            PayloadSpec(
                relative_path="tokens.txt",
                sha256="49e3c2646595fd907228b3c6787069658f67b17377c60aeb8619c4551b2316fb",
                size_bytes=5_048,
            ),
        ),
    ),
)


def log_json(severity: str, event: str, **fields: object) -> None:
    record = {"severity": severity, "event": event}
    record.update(fields)
    sys.stdout.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
    sys.stdout.flush()


def download_archive(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url) as response:
        with dest.open("wb") as outfile:
            shutil.copyfileobj(response, outfile)


def extract_tar_bz2(archive_path: Path, dest_dir: Path) -> None:
    with tarfile.open(archive_path, mode="r:bz2") as tar:
        tar.extractall(path=dest_dir)


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_payloads(spec: ModelSpec, base_dir: Path) -> bool:
    for payload in spec.payloads:
        candidate = base_dir / payload.relative_path
        if not candidate.exists():
            log_json("DEBUG", "payload.missing", model=spec.id, path=str(payload.relative_path))
            return False
        actual_size = candidate.stat().st_size
        if actual_size != payload.size_bytes:
            log_json(
                "DEBUG",
                "payload.size_mismatch",
                model=spec.id,
                path=str(payload.relative_path),
                expected=payload.size_bytes,
                actual=actual_size,
            )
            return False
        actual_hash = compute_sha256(candidate)
        if actual_hash != payload.sha256:
            log_json(
                "DEBUG",
                "payload.sha_mismatch",
                model=spec.id,
                path=str(payload.relative_path),
                expected=payload.sha256,
                actual=actual_hash,
            )
            return False
    return True


def prune_paths(spec: ModelSpec, base_dir: Path) -> None:
    for entry in spec.prune:
        target = base_dir / entry
        if not target.exists():
            continue
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()


def ensure_model(spec: ModelSpec, models_dir: Path) -> None:
    asset_dir = models_dir / spec.asset_dir
    if validate_payloads(spec, asset_dir):
        log_json("INFO", "model.ok", model=spec.id, action="skip")
        return

    if asset_dir.exists():
        log_json("INFO", "model.reset", model=spec.id, reason="stale_or_missing_payloads")
        shutil.rmtree(asset_dir)

    models_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{spec.id}-", dir=models_dir) as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        archive_path = temp_dir / "model.tar.bz2"
        log_json("INFO", "model.download.start", model=spec.id, url=spec.archive_url)
        download_archive(spec.archive_url, archive_path)
        log_json("INFO", "model.download.complete", model=spec.id, bytes=archive_path.stat().st_size)
        extract_tar_bz2(archive_path, models_dir)

    prune_paths(spec, asset_dir)
    if not validate_payloads(spec, asset_dir):
        raise RuntimeError(f"failed to validate payloads for {spec.id}")
    log_json("INFO", "model.ready", model=spec.id, path=str(asset_dir))


def build_manifest(specs: Iterable[ModelSpec]) -> dict[str, object]:
    models = []
    for spec in specs:
        models.append(
            {
                "id": spec.id,
                "kind": spec.kind,
                "display_name": spec.display_name,
                "asset_dir": spec.asset_dir,
                "install_dir": spec.install_dir,
                "payloads": [
                    {
                        "relative_path": payload.relative_path,
                        "sha256": payload.sha256,
                        "size_bytes": payload.size_bytes,
                    }
                    for payload in spec.payloads
                ],
            }
        )
    manifest_core = {"schema_version": 1, "models": models}
    digest_source = json.dumps(manifest_core, sort_keys=True).encode("utf-8")
    manifest_digest = hashlib.sha256(digest_source).hexdigest()
    manifest_core["content_sha256"] = manifest_digest
    return manifest_core


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ensure local ASR/TTS models are present and write manifest.")
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=Path("android/third_party/models"),
        help="Directory where third-party model assets should live.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("android/app/src/main/assets/model_manifest.json"),
        help="Path to write the manifest JSON file.",
    )
    parser.add_argument(
        "--no-manifest",
        action="store_true",
        help="Skip writing the manifest but still validate assets.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    models_dir: Path = args.models_dir.resolve()
    for spec in MODEL_SPECS:
        ensure_model(spec, models_dir)

    if args.no_manifest:
        log_json("INFO", "manifest.skip", reason="no_manifest_flag")
        return 0

    manifest_path = args.manifest.resolve()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(MODEL_SPECS)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    log_json(
        "INFO",
        "manifest.written",
        path=str(manifest_path),
        models=len(manifest["models"]),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
