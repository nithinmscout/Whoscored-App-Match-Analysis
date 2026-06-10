from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.core.config import guess_browser_path


def _seleniumbase_runtime_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".cache"

    runtime = base / "whoscored_app_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "downloaded_files").mkdir(parents=True, exist_ok=True)
    return runtime


@contextmanager
def seleniumbase_local_cwd() -> Iterator[Path]:
    runtime = _seleniumbase_runtime_dir()
    old_cwd: str | None = None
    try:
        old_cwd = os.getcwd()
    except Exception:
        old_cwd = None

    os.chdir(runtime)
    try:
        yield runtime
    finally:
        if old_cwd:
            try:
                os.chdir(old_cwd)
            except Exception:
                pass


@contextmanager
def setup_driver(*, headless: bool = True, browserpath: str | None = None, uc: bool = False):
    from seleniumbase import SB

    resolved_browser = str(browserpath).strip() if browserpath else guess_browser_path()
    sb_kwargs: dict[str, object] = {"headless": headless, "browser": "chrome"}
    if uc:
        profile_dir = _seleniumbase_runtime_dir() / "chrome_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        sb_kwargs["uc"] = True
        sb_kwargs["user_data_dir"] = str(profile_dir)
    if resolved_browser:
        sb_kwargs["binary_location"] = str(Path(resolved_browser))

    with seleniumbase_local_cwd():
        with SB(**sb_kwargs) as sb:
            yield sb