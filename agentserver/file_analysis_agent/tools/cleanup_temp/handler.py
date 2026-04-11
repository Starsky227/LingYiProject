from typing import Any, Dict
import logging
import time

logger = logging.getLogger(__name__)


async def execute(args: Dict[str, Any], context: Dict[str, Any]) -> str:
    """清理系统生成的临时文件和缓存"""
    max_age_hours = args.get("max_age_hours")
    max_files = args.get("max_files")

    from agentserver.file_analysis_agent.tools.download_file.handler import (
        DEFAULT_CLEANUP_MAX_AGE_HOURS,
        DEFAULT_CLEANUP_MAX_FILES,
        _cleanup_download_cache,
        _load_download_index,
        _prune_missing_index_entries,
    )
    from system.paths import DOWNLOAD_CACHE_DIR, DOWNLOAD_CACHE_INDEX_FILE

    cache_dir = DOWNLOAD_CACHE_DIR

    if not cache_dir.exists():
        return "下载缓存目录不存在"

    try:
        before_files = [item for item in cache_dir.iterdir() if item.is_file()]
        before_index = _prune_missing_index_entries(_load_download_index(DOWNLOAD_CACHE_INDEX_FILE))

        _cleanup_download_cache(
            cache_dir=cache_dir,
            index_file=DOWNLOAD_CACHE_INDEX_FILE,
            max_age_hours=int(max_age_hours or DEFAULT_CLEANUP_MAX_AGE_HOURS),
            max_files=int(max_files or DEFAULT_CLEANUP_MAX_FILES),
        )

        after_files = [item for item in cache_dir.iterdir() if item.is_file()]
        after_index = _prune_missing_index_entries(_load_download_index(DOWNLOAD_CACHE_INDEX_FILE))
        removed_files = len(before_files) - len(after_files)
        removed_entries = len(before_index) - len(after_index)

        return (
            f"下载缓存清理完成：删除 {removed_files} 个文件，"
            f"同步 {removed_entries} 条索引记录，当前剩余 {len(after_files)} 个缓存文件。"
        )

    except Exception as e:
        logger.exception(f"清理下载缓存目录失败: {e}")
        return "清理下载缓存目录失败，请稍后重试"
