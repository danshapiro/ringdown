from __future__ import annotations

import hashlib
from pathlib import Path

import android.scripts.prepare_local_models as prepare_local_models


def test_main_downloads_binary_dependencies(tmp_path: Path, monkeypatch) -> None:
    dependency_bytes = b"fake-sherpa-aar"
    dependency_spec = prepare_local_models.DependencySpec(
        id="sherpa-runtime-aar",
        display_name="Sherpa runtime AAR",
        download_url="https://example.test/sherpa.aar",
        relative_path="sherpa-onnx.aar",
        sha256=hashlib.sha256(dependency_bytes).hexdigest(),
        size_bytes=len(dependency_bytes),
    )

    downloads: list[str] = []

    def fake_download(url: str, dest: Path) -> None:
        downloads.append(url)
        dest.write_bytes(dependency_bytes)

    monkeypatch.setattr(prepare_local_models, "DEPENDENCY_SPECS", (dependency_spec,))
    monkeypatch.setattr(prepare_local_models, "MODEL_SPECS", ())
    monkeypatch.setattr(prepare_local_models, "download_archive", fake_download)

    third_party_root = tmp_path / "android" / "third_party"
    manifest_path = tmp_path / "android" / "app" / "src" / "main" / "assets" / "model_manifest.json"

    result = prepare_local_models.main(
        [
            "--models-dir",
            str(third_party_root / "models"),
            "--manifest",
            str(manifest_path),
        ]
    )

    assert result == 0
    assert downloads == ["https://example.test/sherpa.aar"]
    assert (third_party_root / "sherpa-onnx.aar").read_bytes() == dependency_bytes
