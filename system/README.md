# System 系统模块

系统基础设施层，提供配置管理、路径定义和环境校验等全局基础服务。

## 文件说明

### config.py - 配置管理
- 基于 Pydantic 的配置模型，加载 `config.json` (json5格式)
- 管理服务端口分配（API: 8000, Agent: 8001, MCP: 8003）
- 支持配置热重载与监听器模式 (`add_config_listener()`)
- 自动设置线程/MPS兼容性等环境变量

### paths.py - 路径定义
- 定义运行时数据路径（缓存、渲染、下载、文本文件等）
- 提供 `ensure_dir()` 工具函数自动创建目录

### system_checker.py - 环境校验
- 启动时验证 Neo4j 数据库连接状态
- 检查服务端口可用性
- 管理全局 `is_neo4j_available()` 状态标记

## 使用方式

```python
from system.config import load_config, get_server_port
config = load_config()
port = get_server_port("agent_server")  # 获取配置端口

from system.paths import RuntimePath
cache_dir = RuntimePath.CACHE_DIR

from system.system_checker import is_neo4j_available
if is_neo4j_available():
    # 使用知识图谱功能
    pass
```