import hashlib
import json
import mimetypes
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse, unquote
from typing import Any, Dict
import logging
import httpx
import aiofiles

logger = logging.getLogger(__name__)

SIZE_LIMITS = {
    "text": 10 * 1024 * 1024,
    "code": 5 * 1024 * 1024,
    "pdf": 50 * 1024 * 1024,
    "docx": 20 * 1024 * 1024,
    "pptx": 20 * 1024 * 1024,
    "xlsx": 10 * 1024 * 1024,
    "image": 10 * 1024 * 1024,
    "audio": 50 * 1024 * 1024,
    "video": 100 * 1024 * 1024,
    "archive": 100 * 1024 * 1024,
}

DEFAULT_SIZE_LIMIT = 100 * 1024 * 1024
DEFAULT_CLEANUP_MAX_AGE_HOURS = 120
DEFAULT_CLEANUP_MAX_FILES = 200
PNG_SIGNATURE = b"\x89\x50\x4e\x47\x0d\x0a\x1a\x0a"

CONTENT_TYPE_EXTENSION_MAP = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/msword": ".doc",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.ms-excel": ".xls",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/x-7z-compressed": ".7z",
    "application/x-rar-compressed": ".rar",
    "text/plain": ".txt",
    "application/json": ".json",
}


def _load_download_index(index_file: Path) -> dict[str, dict[str, Any]]:
    if not index_file.exists():
        return {}

    try:
        data = json.loads(index_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"读取下载缓存索引失败: {e}")
        return {}

    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for file_hash, metadata in files.items():
        if isinstance(file_hash, str) and isinstance(metadata, dict):
            normalized[file_hash] = dict(metadata)
    return normalized


def _save_download_index(index_file: Path, index_data: dict[str, dict[str, Any]]) -> None:
    payload = {
        "version": 1,
        "files": index_data,
    }
    temp_file = index_file.with_suffix(".tmp")
    try:
        temp_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_file.replace(index_file)
    except OSError as e:
        logger.warning(f"写入下载缓存索引失败: {e}")
        try:
            temp_file.unlink(missing_ok=True)
        except OSError:
            pass


def _prune_missing_index_entries(index_data: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    pruned: dict[str, dict[str, Any]] = {}
    for file_hash, metadata in index_data.items():
        path_str = str(metadata.get("path", "")).strip()
        if path_str and Path(path_str).exists():
            pruned[file_hash] = metadata
    return pruned


def _cleanup_download_cache(
    cache_dir: Path,
    index_file: Path,
    max_age_hours: int = DEFAULT_CLEANUP_MAX_AGE_HOURS,
    max_files: int = DEFAULT_CLEANUP_MAX_FILES,
) -> None:
    """清理下载缓存目录，避免长期累计。"""
    if not cache_dir.exists():
        return

    now = time.time()
    removed_by_age = 0
    removed_by_count = 0
    max_age_seconds = max_age_hours * 3600

    index_data = _prune_missing_index_entries(_load_download_index(index_file))

    files = [f for f in cache_dir.iterdir() if f.is_file()]

    if max_age_hours > 0:
        for file_path in files:
            try:
                age = now - file_path.stat().st_mtime
                if age > max_age_seconds:
                    file_path.unlink(missing_ok=True)
                    index_data.pop(file_path.stem, None)
                    removed_by_age += 1
            except Exception as e:
                logger.warning(f"清理过期下载缓存失败: {file_path}, {e}")

    files = [f for f in cache_dir.iterdir() if f.is_file()]
    if max_files > 0 and len(files) > max_files:
        files.sort(key=lambda p: p.stat().st_mtime)
        to_remove = files[: len(files) - max_files]
        for file_path in to_remove:
            try:
                file_path.unlink(missing_ok=True)
                index_data.pop(file_path.stem, None)
                removed_by_count += 1
            except Exception as e:
                logger.warning(f"按数量清理下载缓存失败: {file_path}, {e}")

    _save_download_index(index_file, _prune_missing_index_entries(index_data))

    if removed_by_age or removed_by_count:
        logger.info(
            "下载缓存自动清理完成: removed_by_age=%s, removed_by_count=%s",
            removed_by_age,
            removed_by_count,
        )


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _extract_filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    filename = Path(unquote(parsed.path)).name
    return filename


def _normalize_file_marker(raw: str) -> str:
    token = (raw or "").strip().strip('"').strip("'")
    if len(token) >= 2 and token[0] == "[" and token[-1] == "]":
        token = token[1:-1].strip()
    if len(token) >= 2 and token[0] == "<" and token[-1] == ">":
        token = token[1:-1].strip()
    return token


def _content_type_to_extension(content_type: str) -> str:
    if not content_type:
        return ""
    mime = content_type.split(";")[0].strip().lower()
    if mime in CONTENT_TYPE_EXTENSION_MAP:
        return CONTENT_TYPE_EXTENSION_MAP[mime]
    guessed = mimetypes.guess_extension(mime)
    return guessed or ""


def _build_target_filename(
    source_hint: str,
    fallback_stem: str,
    content_type: str = "",
) -> str:
    hint_name = Path(source_hint).name if source_hint else ""
    if hint_name and Path(hint_name).suffix:
        return hint_name

    ext = _content_type_to_extension(content_type)
    if not ext and hint_name and "." in hint_name:
        ext = Path(hint_name).suffix

    if ext:
        return f"{fallback_stem}{ext}"
    return fallback_stem


def _build_temp_file_path(cache_dir: Path, source_hint: str = "") -> Path:
    suffix = Path(source_hint).suffix if source_hint else ""
    temp_name = f".{secrets.token_hex(8)}{suffix}.part"
    return cache_dir / temp_name


def _hash_file(file_path: Path, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _register_downloaded_file(
    index_file: Path,
    file_hash: str,
    file_path: Path,
    source_hint: str = "",
    content_type: str = "",
) -> None:
    index_data = _prune_missing_index_entries(_load_download_index(index_file))
    now = int(time.time())
    existing = index_data.get(file_hash, {})
    sources = existing.get("sources", [])
    if not isinstance(sources, list):
        sources = []
    if source_hint and source_hint not in sources:
        sources.append(source_hint)

    index_data[file_hash] = {
        "hash": file_hash,
        "path": str(file_path),
        "size": file_path.stat().st_size,
        "content_type": content_type,
        "extension": file_path.suffix.lower(),
        "created_at": int(existing.get("created_at", now)),
        "last_accessed_at": now,
        "sources": sources,
    }
    _save_download_index(index_file, index_data)


def _touch_downloaded_file(index_file: Path, file_hash: str, file_path: Path) -> None:
    try:
        file_path.touch()
    except OSError:
        pass

    index_data = _prune_missing_index_entries(_load_download_index(index_file))
    metadata = index_data.get(file_hash)
    if not metadata:
        return
    metadata["last_accessed_at"] = int(time.time())
    metadata["path"] = str(file_path)
    index_data[file_hash] = metadata
    _save_download_index(index_file, index_data)


async def _finalize_cached_file(
    temp_path: Path,
    cache_dir: Path,
    index_file: Path,
    source_hint: str = "",
    content_type: str = "",
) -> str:
    file_hash = _hash_file(temp_path)
    index_data = _prune_missing_index_entries(_load_download_index(index_file))
    existing = index_data.get(file_hash)

    if existing:
        existing_path = Path(str(existing.get("path", "")).strip())
        if existing_path.exists():
            temp_path.unlink(missing_ok=True)
            _touch_downloaded_file(index_file, file_hash, existing_path)
            return str(existing_path)

    extension = Path(source_hint).suffix.lower()
    if not extension:
        extension = _content_type_to_extension(content_type)

    final_name = f"{file_hash}{extension}" if extension else file_hash
    final_path = cache_dir / final_name

    if final_path.exists():
        temp_path.unlink(missing_ok=True)
    else:
        temp_path.replace(final_path)

    _touch_downloaded_file(index_file, file_hash, final_path)
    _register_downloaded_file(
        index_file=index_file,
        file_hash=file_hash,
        file_path=final_path,
        source_hint=source_hint,
        content_type=content_type,
    )
    return str(final_path)


async def _validate_downloaded_file(file_path: Path, source_hint: str = "") -> str:
    if not file_path.exists() or not file_path.is_file():
        return f"错误：下载后文件不存在: {file_path}"

    file_size = file_path.stat().st_size
    if file_size <= 0:
        return f"错误：下载后文件为空: {file_path.name}"

    suffix = file_path.suffix.lower()
    source_lower = source_hint.lower()
    expects_png = suffix == ".png" or ".png" in source_lower

    if expects_png:
        try:
            async with aiofiles.open(file_path, "rb") as f:
                head = await f.read(8)
            if head != PNG_SIGNATURE:
                return f"错误：文件不是有效 PNG: {file_path.name}"
        except Exception as e:
            logger.exception(f"PNG 校验失败: {e}")
            return "错误：PNG 校验失败"

    return ""


async def _copy_local_file(local_source: str, cache_dir: Path, index_file: Path) -> str:
    local_path = Path(local_source)
    if not local_path.exists() or not local_path.is_file():
        return ""

    temp_path = _build_temp_file_path(cache_dir, local_path.name)

    async with aiofiles.open(local_path, "rb") as src, aiofiles.open(temp_path, "wb") as dst:
        while True:
            chunk = await src.read(8192)
            if not chunk:
                break
            await dst.write(chunk)

    validation_error = await _validate_downloaded_file(temp_path, local_source)
    if validation_error:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return validation_error

    cached_path = await _finalize_cached_file(
        temp_path=temp_path,
        cache_dir=cache_dir,
        index_file=index_file,
        source_hint=local_path.name,
    )
    logger.info(f"本地文件已缓存到: {cached_path}")
    return cached_path


async def _resolve_file_source_from_context(
    file_source: str, context: Dict[str, Any]
) -> str:
    source = _normalize_file_marker(file_source)

    resolver = context.get("resolve_file_source_callback")
    if callable(resolver):
        try:
            resolved = await resolver(source, context.get("address_info"))
            resolved_str = str(resolved or "").strip()
            if resolved_str:
                return resolved_str
        except Exception as e:
            logger.exception(f"地址讯息解析失败: {e}")
            return "错误：通过地址讯息解析文件源失败"

    address_info = context.get("address_info") or {}
    attachments = address_info.get("attachments", []) or []
    source_name = Path(source).name or source
    image_resolver = context.get("get_image_url_callback")

    for item in attachments:
        token = _normalize_file_marker(str(item.get("token", "")))
        display = _normalize_file_marker(str(item.get("display", "")))
        name = _normalize_file_marker(str(item.get("name", "")))
        resolver_name = str(item.get("resolver", "")).strip()
        downloadable = bool(item.get("downloadable"))

        candidates = {token, display, name}
        candidates = {c for c in candidates if c}
        if source not in candidates and source_name not in candidates:
            continue

        if resolver_name == "qq_image_token" and callable(image_resolver):
            try:
                resolved = await image_resolver(token)
                resolved_str = str(resolved or "").strip()
                if resolved_str:
                    logger.info("通过 file_analysis_agent 下载模块回溯 QQ token 成功")
                    return resolved_str
            except Exception as e:
                logger.warning(f"通过地址讯息回溯 QQ token 失败: {e}")

        if downloadable and token:
            return token

    return ""


async def execute(args: Dict[str, Any], context: Dict[str, Any]) -> str:
    """下载指定文件到临时缓存目录

    参数:
        args: 包含 file_source (URL 或 file_id) 和可选 max_size_mb
        context: 包含回调函数的上下文

    返回:
        下载后的本地磁盘路径或错误信息
    """
    file_source: str = _normalize_file_marker(str(args.get("file_source", "")))
    max_size_mb: float = args.get("max_size_mb", 100)

    if not file_source:
        return "错误：文件源不能为空"

    from system.paths import DOWNLOAD_CACHE_DIR, DOWNLOAD_CACHE_INDEX_FILE, ensure_dir

    cleanup_max_age_hours = _safe_int(
        args.get("cleanup_max_age_hours", DEFAULT_CLEANUP_MAX_AGE_HOURS),
        DEFAULT_CLEANUP_MAX_AGE_HOURS,
    )
    cleanup_max_files = _safe_int(
        args.get("cleanup_max_files", DEFAULT_CLEANUP_MAX_FILES),
        DEFAULT_CLEANUP_MAX_FILES,
    )

    cache_dir: Path = ensure_dir(DOWNLOAD_CACHE_DIR)
    _cleanup_download_cache(
        cache_dir=cache_dir,
        index_file=DOWNLOAD_CACHE_INDEX_FILE,
        max_age_hours=cleanup_max_age_hours,
        max_files=cleanup_max_files,
    )

    is_url: bool = file_source.startswith("http://") or file_source.startswith(
        "https://"
    )

    local_file_path = await _copy_local_file(
        file_source,
        cache_dir,
        DOWNLOAD_CACHE_INDEX_FILE,
    )
    if local_file_path.startswith("错误："):
        return local_file_path
    if local_file_path:
        return local_file_path

    if is_url:
        return await _download_from_url(
            file_source,
            cache_dir,
            max_size_mb,
            DOWNLOAD_CACHE_INDEX_FILE,
        )

    resolved_source = await _resolve_file_source_from_context(file_source, context)
    if resolved_source.startswith("错误："):
        return resolved_source
    if resolved_source:
        local_resolved_path = await _copy_local_file(
            resolved_source,
            cache_dir,
            DOWNLOAD_CACHE_INDEX_FILE,
        )
        if local_resolved_path.startswith("错误："):
            return local_resolved_path
        if local_resolved_path:
            return local_resolved_path

        resolved_is_url = resolved_source.startswith("http://") or resolved_source.startswith(
            "https://"
        )
        if resolved_is_url:
            return await _download_from_url(
                resolved_source,
                cache_dir,
                max_size_mb,
                DOWNLOAD_CACHE_INDEX_FILE,
            )
        return await _download_from_file_id(
            resolved_source,
            cache_dir,
            context,
            max_size_mb,
            DOWNLOAD_CACHE_INDEX_FILE,
        )

    return await _download_from_file_id(
        file_source,
        cache_dir,
        context,
        max_size_mb,
        DOWNLOAD_CACHE_INDEX_FILE,
    )


async def _download_from_url(
    url: str,
    cache_dir: Path,
    max_size_mb: float,
    index_file: Path,
) -> str:
    """从 Web URL 进行下载，使用流式传输并做下载后校验。"""
    max_size_bytes: int = int(max_size_mb * 1024 * 1024)

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            logger.info("正在下载文件...")
            async with client.stream("GET", url, timeout=120.0, follow_redirects=True) as response:
                response.raise_for_status()

                content_type = response.headers.get("content-type", "")
                source_name = _extract_filename_from_url(str(response.request.url)) or _extract_filename_from_url(url)
                filename = _build_target_filename(
                    source_hint=source_name,
                    fallback_stem="downloaded_file",
                    content_type=content_type,
                )
                file_path = _build_temp_file_path(cache_dir, filename)

                downloaded = 0
                async with aiofiles.open(file_path, "wb") as f:
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > max_size_bytes:
                            await f.close()
                            try:
                                file_path.unlink(missing_ok=True)
                            except Exception:
                                pass
                            return f"错误：文件大小超过限制 ({max_size_mb}MB)"
                        await f.write(chunk)

            validation_error = await _validate_downloaded_file(file_path, url)
            if validation_error:
                try:
                    file_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return validation_error

            cached_path = await _finalize_cached_file(
                temp_path=file_path,
                cache_dir=cache_dir,
                index_file=index_file,
                source_hint=source_name,
                content_type=content_type,
            )
            logger.info(f"文件已缓存到: {cached_path}")
            return cached_path

        except httpx.TimeoutException:
            return "错误：下载超时"
        except httpx.HTTPStatusError as e:
            return f"错误：HTTP 错误 {e.response.status_code}"
        except Exception as e:
            logger.exception(f"下载失败: {e}")
            return "错误：下载失败"


async def _download_from_file_id(
    file_id: str,
    cache_dir: Path,
    context: Dict[str, Any],
    max_size_mb: float,
    index_file: Path,
) -> str:
    """从 OneBot file_id 进行下载或解析"""
    file_id = _normalize_file_marker(file_id)
    get_image_url_callback = context.get("get_image_url_callback")
    if not get_image_url_callback:
        return "错误：file_id 模式需要 get_image_url_callback"

    try:
        logger.info(f"正在解析 file_id: {file_id}")
        url = await get_image_url_callback(file_id)
        if not url:
            return f"错误：无法将 file_id {file_id} 解析为 URL"

        logger.info(f"获取到 URL: {url}")

        # 检查是否为 HTTP/HTTPS URL
        is_http_url = url.startswith("http://") or url.startswith("https://")

        if is_http_url:
            # URL 模式走统一下载逻辑，便于复用验证与扩展名推断
            return await _download_from_url(
                url,
                cache_dir,
                max_size_mb=max_size_mb,
                index_file=index_file,
            )
        else:
            # 处理本地文件路径
            local_path = Path(url)
            if not local_path.exists():
                return f"错误：本地文件不存在: {url}"

            return await _copy_local_file(str(local_path), cache_dir, index_file)

    except Exception as e:
        logger.exception(f"下载失败（file_id 模式）: {e}")
        return "错误：下载失败，请检查 file_id 或网络状态"
