#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件编辑工具包 - 适配agentserver架构
提供文件读写、编辑功能，支持SEARCH/REPLACE格式的diff操作
"""

import re
import shutil
import logging
from datetime import datetime
from pathlib import Path

from .base_toolkit import AsyncBaseToolkit, register_tool, ToolkitConfig

logger = logging.getLogger(__name__)


class FileEditToolkit(AsyncBaseToolkit):
    """文件编辑工具包"""
    
    def __init__(self, config: ToolkitConfig = None) -> None:
        super().__init__(config)
        workspace_root = self.config.config.get("workspace_root", "/tmp/")
        self.setup_workspace(workspace_root)

        self.default_encoding = self.config.config.get("default_encoding", "utf-8")
        self.backup_enabled = self.config.config.get("backup_enabled", False)
        logger.info(
            f"FileEditToolkit初始化完成 - 工作目录: {self.work_dir}, 编码: {self.default_encoding}"
        )

    def setup_workspace(self, workspace_root: str):
        """设置工作空间"""
        self.work_dir = Path(workspace_root).resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def _sanitize_filename(self, filename: str) -> str:
        """清理文件名，移除不安全字符"""
        safe = re.sub(r"[^\w\-.]", "_", filename)
        return safe

    def _resolve_filepath(self, file_path: str) -> Path:
        """解析文件路径，处理相对路径和安全检查"""
        path_obj = Path(file_path)
        if not path_obj.is_absolute():
            path_obj = self.work_dir / path_obj

        sanitized_filename = self._sanitize_filename(path_obj.name)
        path_obj = path_obj.parent / sanitized_filename
        resolved_path = path_obj.resolve()
        self._create_backup(resolved_path)
        return resolved_path

    def _create_backup(self, file_path: Path) -> None:
        """创建文件备份"""
        if not self.backup_enabled or not file_path.exists():
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = file_path.parent / f"{file_path.name}.{timestamp}.bak"
        shutil.copy2(file_path, backup_path)
        logger.info(f"创建备份文件: {backup_path}")

    @register_tool
    async def edit_file(self, path: str, diff: str) -> str:
        """编辑文件，使用SEARCH/REPLACE格式的diff
        
        参数:
            path (str): 要编辑的文件路径
            diff (str): SEARCH/REPLACE格式的diff内容，格式如下:
                ```
                <<<<<<< SEARCH
                [要查找的确切内容]
                =======
                [要替换的新内容]
                >>>>>>> REPLACE
                ```
        """
        try:
            resolved_path = self._resolve_filepath(path)

            with open(resolved_path, encoding=self.default_encoding) as f:
                content = f.read()
            modified_content = content
            pattern = r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE"
            matches = re.findall(pattern, diff, re.DOTALL)
            if not matches:
                return "错误！在提供的diff中未找到有效的SEARCH/REPLACE块"

            # 应用每个搜索/替换对
            for search_text, replace_text in matches:
                if search_text in modified_content:
                    modified_content = modified_content.replace(search_text, replace_text)
                else:
                    logger.warning(f"在文件中未找到搜索文本: {search_text[:50]}...")

            with open(resolved_path, "w", encoding=self.default_encoding) as f:
                f.write(modified_content)
            return f"成功编辑文件: {resolved_path}"
        except Exception as e:
            logger.error(f"编辑文件失败: {e}")
            return f"编辑文件失败: {str(e)}"

    @register_tool
    async def write_file(self, path: str, file_text: str) -> str:
        """写入文本内容到文件
        
        参数:
            path (str): 要写入的文件路径
            file_text (str): 要写入的完整文本内容
        """
        try:
            path_obj = self._resolve_filepath(path)
            path_obj.parent.mkdir(parents=True, exist_ok=True)
            path_obj.write_text(file_text, encoding=self.default_encoding)
            return f"成功写入文件: {path_obj}"
        except Exception as e:
            logger.error(f"写入文件失败: {e}")
            return f"写入文件失败: {str(e)}"

    @register_tool
    async def read_file(self, path: str) -> str:
        """读取并返回文件内容
        
        参数:
            path (str): 要读取的文件路径
        """
        try:
            path_obj = self._resolve_filepath(path)
            if not path_obj.exists():
                return f"文件不存在: {path_obj}"
            return path_obj.read_text(encoding=self.default_encoding)
        except Exception as e:
            logger.error(f"读取文件失败: {e}")
            return f"读取文件失败: {str(e)}"

    @register_tool
    async def list_files(self, directory: str = ".") -> str:
        """列出目录中的文件
        
        参数:
            directory (str): 要列出的目录路径，默认为当前目录
        """
        try:
            if directory == ".":
                target_dir = self.work_dir
            else:
                target_dir = self.work_dir / directory
                
            if not target_dir.exists():
                return f"目录不存在: {target_dir}"
                
            files = []
            for item in target_dir.iterdir():
                if item.is_file():
                    files.append(f"文件: {item.name}")
                elif item.is_dir():
                    files.append(f"目录: {item.name}/")
                    
            if not files:
                return f"目录为空: {target_dir}"
                
            return f"目录 {target_dir} 内容:\n" + "\n".join(files)
        except Exception as e:
            logger.error(f"列出文件失败: {e}")
            return f"列出文件失败: {str(e)}"

    @register_tool
    async def create_directory(self, path: str) -> str:
        """创建目录
        
        参数:
            path (str): 要创建的目录路径
        """
        try:
            path_obj = self.work_dir / path
            path_obj.mkdir(parents=True, exist_ok=True)
            return f"成功创建目录: {path_obj}"
        except Exception as e:
            logger.error(f"创建目录失败: {e}")
            return f"创建目录失败: {str(e)}"

    @register_tool
    async def delete_file(self, path: str) -> str:
        """删除文件
        
        参数:
            path (str): 要删除的文件路径
        """
        try:
            path_obj = self._resolve_filepath(path)
            if not path_obj.exists():
                return f"文件不存在: {path_obj}"
            path_obj.unlink()
            return f"成功删除文件: {path_obj}"
        except Exception as e:
            logger.error(f"删除文件失败: {e}")
            return f"删除文件失败: {str(e)}"
