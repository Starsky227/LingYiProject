import uuid
import mimetypes
import time
import shutil
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
DEFAULT_CLEANUP_MAX_DIRS = 200
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


def _cleanup_download_cache(
    cache_dir: Path,
    max_age_hours: int = DEFAULT_CLEANUP_MAX_AGE_HOURS,
    max_dirs: int = DEFAULT_CLEANUP_MAX_DIRS,
) -> None:
    """清理下载缓存目录，避免长期累计。"""
    if not cache_dir.exists():
        return

    now = time.time()
    removed_by_age = 0
    removed_by_count = 0
    max_age_seconds = max_age_hours * 3600

    dirs = [d for d in cache_dir.iterdir() if d.is_dir()]

    if max_age_hours > 0:
        for d in dirs:
            try:
                age = now - d.stat().st_mtime
                if age > max_age_seconds:
                    shutil.rmtree(d, ignore_errors=False)
                    removed_by_age += 1
            except Exception as e:
                logger.warning(f"清理过期下载缓存失败: {d}, {e}")

    dirs = [d for d in cache_dir.iterdir() if d.is_dir()]
    if max_dirs > 0 and len(dirs) > max_dirs:
        dirs.sort(key=lambda p: p.stat().st_mtime)
        to_remove = dirs[: len(dirs) - max_dirs]
        for d in to_remove:
            try:
                shutil.rmtree(d, ignore_errors=False)
                removed_by_count += 1
            except Exception as e:
                logger.warning(f"按数量清理下载缓存失败: {d}, {e}")

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


async def _copy_local_file(local_source: str, temp_dir: Path) -> str:
    local_path = Path(local_source)
    if not local_path.exists() or not local_path.is_file():
        return ""

    file_path = temp_dir / local_path.name
    async with aiofiles.open(local_path, "rb") as f:
        content = await f.read()
    file_path.write_bytes(content)

    validation_error = await _validate_downloaded_file(file_path, local_source)
    if validation_error:
        try:
            file_path.unlink(missing_ok=True)
        except Exception:
            pass
        return validation_error

    logger.info(f"本地文件已复制到: {file_path}")
    return str(file_path)


async def _resolve_file_source_from_context(
    file_source: str, context: Dict[str, Any]
) -> str:
    resolver = context.get("resolve_file_source_callback")
    if not callable(resolver):
        return ""

    try:
        resolved = await resolver(file_source, context.get("address_info"))
    except Exception as e:
        logger.exception(f"地址讯息解析失败: {e}")
        return "错误：通过地址讯息解析文件源失败"

    return str(resolved or "").strip()


async def execute(args: Dict[str, Any], context: Dict[str, Any]) -> str:
    """下载指定文件到临时缓存目录

    参数:
        args: 包含 file_source (URL 或 file_id) 和可选 max_size_mb
        context: 包含回调函数的上下文

    返回:
        下载后的本地磁盘路径或错误信息
    """
    file_source: str = args.get("file_source", "")
    max_size_mb: float = args.get("max_size_mb", 100)

    if not file_source:
        return "错误：文件源不能为空"

    task_uuid: str = uuid.uuid4().hex[:16]
    from system.paths import DOWNLOAD_CACHE_DIR, ensure_dir

    cleanup_max_age_hours = _safe_int(
        args.get("cleanup_max_age_hours", DEFAULT_CLEANUP_MAX_AGE_HOURS),
        DEFAULT_CLEANUP_MAX_AGE_HOURS,
    )
    cleanup_max_dirs = _safe_int(
        args.get("cleanup_max_dirs", DEFAULT_CLEANUP_MAX_DIRS),
        DEFAULT_CLEANUP_MAX_DIRS,
    )

    cache_dir: Path = ensure_dir(DOWNLOAD_CACHE_DIR)
    _cleanup_download_cache(
        cache_dir=cache_dir,
        max_age_hours=cleanup_max_age_hours,
        max_dirs=cleanup_max_dirs,
    )
    temp_dir: Path = ensure_dir(cache_dir / task_uuid)

    is_url: bool = file_source.startswith("http://") or file_source.startswith(
        "https://"
    )

    local_file_path = await _copy_local_file(file_source, temp_dir)
    if local_file_path.startswith("错误："):
        return local_file_path
    if local_file_path:
        return local_file_path

    if is_url:
        return await _download_from_url(file_source, temp_dir, max_size_mb, task_uuid)

    resolved_source = await _resolve_file_source_from_context(file_source, context)
    if resolved_source.startswith("错误："):
        return resolved_source
    if resolved_source:
        local_resolved_path = await _copy_local_file(resolved_source, temp_dir)
        if local_resolved_path.startswith("错误："):
            return local_resolved_path
        if local_resolved_path:
            return local_resolved_path

        resolved_is_url = resolved_source.startswith("http://") or resolved_source.startswith(
            "https://"
        )
        if resolved_is_url:
            return await _download_from_url(
                resolved_source, temp_dir, max_size_mb, task_uuid
            )
        return await _download_from_file_id(
            resolved_source, temp_dir, context, task_uuid, max_size_mb
        )

    return await _download_from_file_id(file_source, temp_dir, context, task_uuid, max_size_mb)


async def _download_from_url(
    url: str, temp_dir: Path, max_size_mb: float, task_uuid: str
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
                    fallback_stem=f"downloaded_{task_uuid}",
                    content_type=content_type,
                )
                file_path = temp_dir / filename

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

            logger.info(f"文件已保存到: {file_path}")
            return str(file_path)

        except httpx.TimeoutException:
            return "错误：下载超时"
        except httpx.HTTPStatusError as e:
            return f"错误：HTTP 错误 {e.response.status_code}"
        except Exception as e:
            logger.exception(f"下载失败: {e}")
            return "错误：下载失败"


async def _download_from_file_id(
    file_id: str,
    temp_dir: Path,
    context: Dict[str, Any],
    task_uuid: str,
    max_size_mb: float,
) -> str:
    """从 OneBot file_id 进行下载或解析"""
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
            return await _download_from_url(url, temp_dir, max_size_mb=max_size_mb, task_uuid=task_uuid)
        else:
            # 处理本地文件路径
            local_path = Path(url)
            if not local_path.exists():
                return f"错误：本地文件不存在: {url}"

            # 使用 aiofiles 读取本地文件
            async with aiofiles.open(local_path, "rb") as f:
                content = await f.read()

            filename = local_path.name
            file_path = temp_dir / filename
            file_path.write_bytes(content)

            validation_error = await _validate_downloaded_file(file_path, file_id)
            if validation_error:
                try:
                    file_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return validation_error

            logger.info(f"本地文件已复制到: {file_path}")
            return str(file_path)

    except Exception as e:
        logger.exception(f"下载失败（file_id 模式）: {e}")
        return "错误：下载失败，请检查 file_id 或网络状态"
