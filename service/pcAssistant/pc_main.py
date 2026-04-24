"""PCAssistant 入口（兼容薄壳）

历史上本文件是一个独立的 PC 启动器，与项目根目录的 ``main.py`` 大量重复。
现在统一以 ``main.py`` 为唯一入口。本文件保留仅为兼容旧脚本/快捷方式：

    python -m service.pcAssistant.pc_main
    python service\\pcAssistant\\pc_main.py

两种方式都会直接转交给根目录 ``main.py`` 启动。
"""

from __future__ import annotations

import os
import runpy
import sys

_PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def main() -> None:
    """转交给根目录 main.py 的 ``__main__`` 启动逻辑。"""
    runpy.run_path(os.path.join(_PROJECT_ROOT, "main.py"), run_name="__main__")


if __name__ == "__main__":
    main()
