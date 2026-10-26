"""Explicitly download the small official CPU speaker models; never runs in-app."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import ssl
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parents[1]
BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEGMENTATION_TAG = "speaker-segmentation-models"
EMBEDDING_TAG = "speaker-recongition-models"  # The spelling is the official release tag.
SEGMENTATION_ARCHIVE = "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
SEGMENTATION_MEMBER = "sherpa-onnx-pyannote-segmentation-3-0/model.int8.onnx"
EMBEDDING_ASSET = "nemo_en_titanet_small.onnx"
TEST_ASSET = "0-four-speakers-zh.wav"
_HOSTS = {"github.com", "api.github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
_HUGGINGFACE = {
    "segmentation.onnx": ("https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/resolve/main/model.int8.onnx",
                          "sha256:10a438c2e0d90ed5f5da545cec2244d887315f6dbbbf1d3d564d00745b01952e"),
    "embedding.onnx": ("https://huggingface.co/csukuangfj/speaker-embedding-models/resolve/main/nemo_en_titanet_small.onnx",
                       "sha256:ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e"),
}


class _OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not (host in _HOSTS or host == "huggingface.co" or host.endswith(".hf.co")):
            raise ValueError("Model download redirected outside allowed model-source HTTPS hosts")
        return super().redirect_request(request, response, code, message, headers, newurl)


def _opener():
    return urllib.request.build_opener(_OfficialRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata(opener, tag):
    url = f"https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/tags/{tag}"
    request = urllib.request.Request(url, headers={"User-Agent": "MathBridge-local-model-setup", "Accept": "application/vnd.github+json"})
    try:
        with opener.open(request, timeout=30) as response:
            data = response.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            raise ValueError("GitHub release metadata exceeded size limit")
        assets = json.loads(data)["assets"]
        return {asset["name"]: asset.get("digest") for asset in assets}, "available"
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Release digest lookup unavailable ({type(exc).__name__}); HTTPS downloads will still record their local SHA-256.", flush=True)
        return {}, f"unavailable:{type(exc).__name__}"


def _download(opener, tag, name, directory, *, expected_digest=None, max_bytes=256 * 1024 * 1024, source_url=None):
    url = source_url or f"{BASE}/{tag}/{name}"
    request = urllib.request.Request(url, headers={"User-Agent": "MathBridge-local-model-setup"})
    descriptor, temporary_name = tempfile.mkstemp(prefix=".speaker-download-", dir=directory)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    count = 0
    try:
        print(f"Downloading {url}", flush=True)
        with os.fdopen(descriptor, "wb") as output, opener.open(request, timeout=60) as response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > max_bytes:
                raise ValueError("Model asset exceeds download size limit")
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > max_bytes:
                    raise ValueError("Model asset exceeds download size limit")
                output.write(chunk)
                digest.update(chunk)
            if not count or declared and count != int(declared):
                raise ValueError("Model asset is empty or truncated")
            output.flush()
            os.fsync(output.fileno())
        actual = digest.hexdigest()
        if expected_digest:
            if not isinstance(expected_digest, str) or not expected_digest.startswith("sha256:"):
                raise ValueError("Unsupported GitHub asset digest algorithm")
            if actual != expected_digest.removeprefix("sha256:"):
                raise ValueError("GitHub release SHA-256 mismatch; refusing the downloaded model")
        return temporary, {"source_url": url, "download_sha256": actual,
                           "github_asset_digest": expected_digest if url.startswith(BASE) else None,
                           "maintainer_lfs_digest": expected_digest if url.startswith("https://huggingface.co/") else None,
                           "download_bytes": count}
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _extract_model(archive, output):
    """Read exactly one regular file, without extracting archive paths/links."""
    descriptor, temporary_name = tempfile.mkstemp(prefix=".segmentation-", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as destination, tarfile.open(archive, mode="r:bz2") as source:
            member = source.getmember(SEGMENTATION_MEMBER)
            if not member.isfile() or not 0 < member.size <= 32 * 1024 * 1024:
                raise ValueError("Selected segmentation model is not a bounded regular file")
            extracted = source.extractfile(member)
            if extracted is None:
                raise ValueError("Selected segmentation model cannot be read")
            count = 0
            with extracted:
                while chunk := extracted.read(1024 * 1024):
                    count += len(chunk)
                    if count > member.size:
                        raise ValueError("Segmentation model size mismatch")
                    destination.write(chunk)
            if count != member.size:
                raise ValueError("Segmentation model was truncated")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _save_manifest(path, value):
    descriptor, temporary_name = tempfile.mkstemp(prefix=".speaker-manifest-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO / "models" / "speakers")
    parser.add_argument("--source", choices=("github", "huggingface"), default="github",
                        help="Use official GitHub Releases or the maintainer's Hugging Face copies")
    parser.add_argument("--with-test-audio", action="store_true", help="Also download the official four-speaker WAV for local verification")
    parser.add_argument("--test-audio-output", type=Path, default=REPO / "tmp" / "speaker-diarization" / TEST_ASSET)
    parser.add_argument("--force", action="store_true", help="Download again even when the local manifest hash matches")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    previous = {}
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if not isinstance(previous, dict) or not isinstance(previous.get("files", []), list):
        previous = {}
    entries = {entry.get("name"): entry for entry in previous.get("files", []) if isinstance(entry, dict)}
    opener = _opener()
    metadata = {}
    digest_status = {}
    wanted = [("segmentation.onnx", SEGMENTATION_TAG, SEGMENTATION_ARCHIVE, output / "segmentation.onnx"),
              ("embedding.onnx", EMBEDDING_TAG, EMBEDDING_ASSET, output / "embedding.onnx")]
    if args.with_test_audio:
        wanted.append((TEST_ASSET, SEGMENTATION_TAG, TEST_ASSET, args.test_audio_output.resolve()))
    completed = []
    for name, tag, asset, target in wanted:
        use_hf = args.source == "huggingface" and name in _HUGGINGFACE
        url = _HUGGINGFACE[name][0] if use_hf else f"{BASE}/{tag}/{asset}"
        old = entries.get(name, {})
        if (not args.force and target.is_file() and old.get("sha256") == _hash(target)
                and old.get("source_url") == url):
            print(f"Reusing verified {target}", flush=True)
            completed.append(old)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if not use_hf and tag not in metadata:
            metadata[tag], digest_status[tag] = _metadata(opener, tag)
        expected = _HUGGINGFACE[name][1] if use_hf else metadata[tag].get(asset)
        downloaded, entry = _download(opener, tag, asset, target.parent, expected_digest=expected, source_url=url)
        try:
            if name == "segmentation.onnx" and not use_hf:
                _extract_model(downloaded, target)
                entry["archive_member"] = SEGMENTATION_MEMBER
            else:
                os.replace(downloaded, target)
            entry.update(name=name, sha256=_hash(target), bytes=target.stat().st_size,
                         github_digest_lookup="maintainer-lfs-sha256-pinned" if use_hf else digest_status[tag])
            completed.append(entry)
            # Persist each complete file so an interrupted second download can resume.
            _save_manifest(manifest_path, {"schema_version": 1, "provider": "sherpa-onnx",
                                           "updated_at": datetime.now(timezone.utc).isoformat(),
                                           "files": completed + [value for key, value in entries.items()
                                                                 if key not in {item["name"] for item in completed}]})
            print(f"Saved {target} (SHA-256 {entry['sha256']})", flush=True)
        finally:
            downloaded.unlink(missing_ok=True)
    print(f"Models ready: {output}", flush=True)
    print("Set segmentation.onnx and embedding.onnx in MathBridge speaker settings. No course data was uploaded.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        print(f"Speaker model setup failed: {error}", file=sys.stderr)
        raise SystemExit(1)
