from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import requests

from app.core.config import DATA_ROOT


_MARKER_FILE = ".data_zip_ready"
_BOOTSTRAP_LOCK = threading.Lock()
_BOOTSTRAP_STARTED = False

_BOOTSTRAP_STATUS: dict[str, Any] = {
    "enabled": False,
    "running": False,
    "ready": False,
    "error": "",
    "message": "Data bootstrap has not started.",
    "started_at": None,
    "completed_at": None,
}


def _env_value(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _is_true(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _schedule_dir() -> Path:
    return DATA_ROOT / "data" / "Schedule"


def _events_dir() -> Path:
    preferred = DATA_ROOT / "data" / "Event Data"
    alternate = DATA_ROOT / "data" / "Event data"
    return preferred if preferred.exists() else alternate


def _data_is_ready() -> bool:
    return _schedule_dir().exists() and _events_dir().exists()


def _safe_extract(archive: zipfile.ZipFile, target_dir: Path) -> None:
    target_root = target_dir.resolve()

    for member in archive.infolist():
        member_path = (target_root / member.filename).resolve()
        if not str(member_path).startswith(str(target_root)):
            raise ValueError(f"Unsafe zip path blocked: {member.filename}")

    archive.extractall(target_root)


def _move_if_needed(source: Path, target: Path) -> None:
    if source.exists() and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))


def _normalise_extracted_layout() -> None:
    data_dir = DATA_ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    _move_if_needed(DATA_ROOT / "Schedule", data_dir / "Schedule")
    _move_if_needed(DATA_ROOT / "Event Data", data_dir / "Event Data")
    _move_if_needed(DATA_ROOT / "Event data", data_dir / "Event data")
    _move_if_needed(DATA_ROOT / "_cache", data_dir / "_cache")


def data_bootstrap_status() -> dict[str, Any]:
    data_dir = DATA_ROOT / "data"
    marker_path = DATA_ROOT / _MARKER_FILE
    schedule_dir = _schedule_dir()
    events_dir = _events_dir()

    payload = dict(_BOOTSTRAP_STATUS)
    payload["data_root"] = str(DATA_ROOT)
    payload["data_dir"] = str(data_dir)
    payload["schedule_dir"] = str(schedule_dir)
    payload["events_dir"] = str(events_dir)
    payload["data_dir_exists"] = data_dir.exists()
    payload["schedule_dir_exists"] = schedule_dir.exists()
    payload["events_dir_exists"] = events_dir.exists()
    payload["marker_exists"] = marker_path.exists()
    payload["ready"] = bool(payload.get("ready")) or _data_is_ready()
    return payload


def bootstrap_data_zip() -> None:
    zip_url = _env_value("DATA_ZIP_URL", "WS_DATA_ZIP_URL")
    if not zip_url:
        _BOOTSTRAP_STATUS.update(
            {
                "enabled": False,
                "running": False,
                "ready": _data_is_ready(),
                "message": "DATA_ZIP_URL is not set.",
            }
        )
        return

    DATA_ROOT.mkdir(parents=True, exist_ok=True)

    data_dir = DATA_ROOT / "data"
    marker_path = DATA_ROOT / _MARKER_FILE
    force_refresh = _is_true(os.environ.get("DATA_ZIP_FORCE", ""))

    _BOOTSTRAP_STATUS.update(
        {
            "enabled": True,
            "running": True,
            "ready": False,
            "error": "",
            "message": "Checking deployed data files.",
            "started_at": time.time(),
            "completed_at": None,
        }
    )

    try:
        if _data_is_ready() and marker_path.exists() and not force_refresh:
            _BOOTSTRAP_STATUS.update(
                {
                    "running": False,
                    "ready": True,
                    "message": "Data folder already exists.",
                    "completed_at": time.time(),
                }
            )
            return

        if data_dir.exists():
            shutil.rmtree(data_dir)

        marker_path.unlink(missing_ok=True)

        _BOOTSTRAP_STATUS["message"] = "Downloading data.zip from DATA_ZIP_URL."

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_file:
            zip_path = Path(tmp_file.name)

        try:
            with requests.get(zip_url, stream=True, timeout=(15, 1800)) as response:
                response.raise_for_status()
                with zip_path.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)

            _BOOTSTRAP_STATUS["message"] = "Extracting data.zip."

            with zipfile.ZipFile(zip_path) as archive:
                _safe_extract(archive, DATA_ROOT)

            _normalise_extracted_layout()

            if not _data_is_ready():
                raise FileNotFoundError(
                    "The downloaded zip must contain both data/Schedule and data/Event Data."
                )

            marker_path.write_text("ready", encoding="utf-8")

            _BOOTSTRAP_STATUS.update(
                {
                    "running": False,
                    "ready": True,
                    "message": "Data bootstrap complete.",
                    "completed_at": time.time(),
                }
            )
        finally:
            zip_path.unlink(missing_ok=True)

    except Exception as exc:
        _BOOTSTRAP_STATUS.update(
            {
                "running": False,
                "ready": False,
                "error": f"{type(exc).__name__}: {exc}",
                "message": "Data bootstrap failed.",
                "completed_at": time.time(),
            }
        )


def start_data_bootstrap_thread() -> None:
    global _BOOTSTRAP_STARTED

    with _BOOTSTRAP_LOCK:
        if _BOOTSTRAP_STARTED:
            return
        _BOOTSTRAP_STARTED = True

    thread = threading.Thread(target=bootstrap_data_zip, name="data-bootstrap", daemon=True)
    thread.start()
