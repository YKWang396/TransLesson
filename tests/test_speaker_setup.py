"""Downloader integrity and archive-path protections without network access."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile

import pytest


@pytest.fixture
def setup_tool():
    path = Path(__file__).resolve().parents[1] / "tools" / "setup-speakers.py"
    spec = importlib.util.spec_from_file_location("mathbridge_speaker_setup_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extracts_only_the_selected_regular_model(setup_tool, tmp_path):
    archive = tmp_path / "models.tar.bz2"
    with tarfile.open(archive, "w:bz2") as output:
        safe = tarfile.TarInfo(setup_tool.SEGMENTATION_MEMBER)
        safe.size = 5
        output.addfile(safe, io.BytesIO(b"model"))
        hostile = tarfile.TarInfo("../outside.txt")
        hostile.size = 3
        output.addfile(hostile, io.BytesIO(b"bad"))
    target = tmp_path / "segmentation.onnx"
    setup_tool._extract_model(archive, target)
    assert target.read_bytes() == b"model"
    assert not (tmp_path.parent / "outside.txt").exists()
    assert not list(tmp_path.glob(".segmentation-*"))


def test_selected_symlink_is_rejected_without_overwriting_previous_model(setup_tool, tmp_path):
    archive = tmp_path / "models.tar.bz2"
    with tarfile.open(archive, "w:bz2") as output:
        link = tarfile.TarInfo(setup_tool.SEGMENTATION_MEMBER)
        link.type = tarfile.SYMTYPE
        link.linkname = "../outside.txt"
        output.addfile(link)
    target = tmp_path / "segmentation.onnx"
    target.write_bytes(b"previous good model")
    with pytest.raises(ValueError, match="regular file"):
        setup_tool._extract_model(archive, target)
    assert target.read_bytes() == b"previous good model"
    assert not list(tmp_path.glob(".segmentation-*"))


class Opener:
    def __init__(self, value):
        self.value = value

    def open(self, request, timeout):
        response = io.BytesIO(self.value)
        response.headers = {"Content-Length": str(len(self.value))}
        return response


def test_release_digest_is_checked_and_bad_download_is_removed(setup_tool, tmp_path):
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        setup_tool._download(Opener(b"wrong"), "tag", "model.onnx", tmp_path,
                             expected_digest="sha256:" + "0" * 64)
    assert not list(tmp_path.iterdir())


def test_download_records_source_and_verified_bytes(setup_tool, tmp_path):
    payload = b"known model bytes"
    digest = hashlib.sha256(payload).hexdigest()
    path, entry = setup_tool._download(Opener(payload), "tag", "model.onnx", tmp_path,
                                      expected_digest="sha256:" + digest)
    assert path.read_bytes() == payload
    assert entry["download_sha256"] == digest
    assert entry["github_asset_digest"] == "sha256:" + digest
    assert entry["source_url"].endswith("/tag/model.onnx")


def test_oversized_download_is_rejected(setup_tool, tmp_path):
    with pytest.raises(ValueError, match="size limit"):
        setup_tool._download(Opener(b"too large"), "tag", "model.onnx", tmp_path, max_bytes=2)
    assert not list(tmp_path.iterdir())
