"""Common runtime paths."""

from pathlib import Path

DATA_DIR = Path("data")
CACHE_DIR = DATA_DIR / "cache"
RENDER_CACHE_DIR = CACHE_DIR / "render"
IMAGE_CACHE_DIR = CACHE_DIR / "images"
DOWNLOAD_CACHE_DIR = CACHE_DIR / "downloads"
DOWNLOAD_CACHE_INDEX_FILE = CACHE_DIR / "download_index.json"
MEDIA_HISTORY_CACHE_FILE = CACHE_DIR / "media_history.json"
TEXT_FILE_CACHE_DIR = CACHE_DIR / "text_files"
URL_FILE_CACHE_DIR = CACHE_DIR / "url_files"
WEBUI_FILE_CACHE_DIR = CACHE_DIR / "webui_files"


def ensure_dir(path: Path) -> Path:
    """Ensure a directory exists and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path