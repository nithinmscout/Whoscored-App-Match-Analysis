from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import requests

from app.core.config import DATA_ROOT


_MARKER_FILE = ".data_zip_ready"


def _env_value(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _is_true(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _safe_extract(archive: zipfile.ZipFile, target_dir: Path) -> None:
    target_root = target_dir.resolve()

    for member in archive.infolist():
        member_path = (target_root / member.filename).resolve()
        if not str(member_path).startswith(str(target_root)):
            raise ValueError(f"Unsafe zip path blocked: {member.filename}")

    archive.extractall(target_root)


def bootstrap_data_zip() -> None:
    zip_url = _env_value("DATA_ZIP_URL", "WS_DATA_ZIP_URL")
    if not zip_url:
        return

    DATA_ROOT.mkdir(parents=True, exist_ok=True)

    data_dir = DATA_ROOT / "data"
    marker_path = DATA_ROOT / _MARKER_FILE
    force_refresh = _is_true(os.environ.get("DATA_ZIP_FORCE", ""))

    if data_dir.exists() and marker_path.exists() and not force_refresh:
        return

    if force_refresh and data_dir.exists():
        shutil.rmtree(data_dir)

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_file:
        zip_path = Path(tmp_file.name)

    try:
        with requests.get(zip_url, stream=True, timeout=(15, 900)) as response:
            response.raise_for_status()
            with zip_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)

        with zipfile.ZipFile(zip_path) as archive:
            _safe_extract(archive, DATA_ROOT)

        if not data_dir.exists():
            raise FileNotFoundError(
                "The downloaded zip did not create a data folder. The zip must contain a top level data folder."
            )

        marker_path.write_text("ready", encoding="utf-8")
    finally:
        zip_path.unlink(missing_ok=True)
