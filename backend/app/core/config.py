from pathlib import Path
import os
import shutil

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent

_env_dataroot = os.environ.get("WS_DATA_ROOT")

# Use env var if provided, otherwise use the current repo root
DATA_ROOT = Path(_env_dataroot).expanduser().resolve() if _env_dataroot else PROJECT_ROOT.resolve()

DATA_DIR = DATA_ROOT / "data"
EXPORTS_DIR = DATA_ROOT / "exports"
WHO_CACHE_DIR = Path.home() / "soccerdata" / "data" / "WhoScored"

DATAROOT = DATA_ROOT
DATADIR = DATA_DIR
EXPORTSDIR = EXPORTS_DIR
WHOCACHEDIR = WHO_CACHE_DIR

DATA_DIR.mkdir(parents=True, exist_ok=True)
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

def guess_browser_path() -> str:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("PROGRAMFILES", r"C:\\Program Files")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\\Program Files (x86)")

    candidates = [
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        
        Path(program_files) / "Google" / "Chromium" / "chrome.exe",
        Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe" if local_app_data else None,
        Path(program_files) / "Chromium" / "Application" / "chrome.exe",
        Path(program_files_x86) / "Chromium" / "Application" / "chrome.exe",

        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/opt/homebrew/bin/chromium",
        "/usr/local/bin/chromium",
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
    ]
    for path in candidates:
        if path:
            candidate = Path(path)
            if candidate.exists():
                return str(candidate)
    return ""

guessbrowserpath = guess_browser_path