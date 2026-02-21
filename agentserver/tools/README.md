# AgentServer 工具包系统

## 概述

AgentServer 工具包系统提供了模块化的工具管理功能，支持文件编辑、电脑控制等多种工具。

## 文件编辑工具包 (FileEditToolkit)

### 功能特性

- **文件读写**：支持读取、写入文本文件
- **文件编辑**：使用 SEARCH/REPLACE 格式进行精确编辑
- **目录管理**：创建目录、列出文件
- **安全特性**：文件名清理、自动备份、工作空间隔离
- **多编码支持**：支持 UTF-8 等多种编码格式

### 可用工具

| 工具名称 | 功能描述 | 参数 |
|---------|---------|------|
| `read_file` | 读取文件内容 | `path`: 文件路径 |
| `write_file` | 写入文件内容 | `path`: 文件路径, `file_text`: 文件内容 |
| `edit_file` | 编辑文件（SEARCH/REPLACE格式） | `path`: 文件路径, `diff`: diff内容 |
| `list_files` | 列出目录文件 | `directory`: 目录路径（可选，默认当前目录） |
| `create_directory` | 创建目录 | `path`: 目录路径 |
| `delete_file` | 删除文件 | `path`: 文件路径 |

### 配置

配置文件：`agentserver/configs/file_edit.yaml`

```yaml
name: file_edit
mode: builtin
activated_tools: null  # null表示激活所有工具
config:
  workspace_root: "/tmp/naga_file_edit"  # 工作目录
  backup_enabled: true  # 启用备份
  default_encoding: "utf-8"  # 默认编码
```

### 使用示例

#### 1. 直接使用工具包

```python
from agentserver.tools import FileEditToolkit
from agentserver.tools.base_toolkit import ToolkitConfig

# 创建配置
config = ToolkitConfig({
    "workspace_root": "/tmp/my_workspace",
    "backup_enabled": True,
    "default_encoding": "utf-8"
})

# 创建工具包实例
toolkit = FileEditToolkit(config)

# 写入文件
await toolkit.write_file("hello.txt", "Hello World!")

# 读取文件
content = await toolkit.read_file("hello.txt")

# 编辑文件
diff = """<<<<<<< SEARCH
Hello World!
=======
Hello NagaAgent!
>>>>>>> REPLACE"""
await toolkit.edit_file("hello.txt", diff)
```

#### 2. 通过API使用

```bash
# 写入文件
curl -X POST "http://localhost:8001/file/write" \
  -H "Content-Type: application/json" \
  -d '{"path": "test.txt", "content": "Hello World!"}'

# 读取文件
curl "http://localhost:8001/file/read?path=test.txt"

# 编辑文件
curl -X POST "http://localhost:8001/file/edit" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "test.txt",
    "diff": "<<<<<<< SEARCH\nHello World!\n=======\nHello NagaAgent!\n>>>>>>> REPLACE"
  }'

# 列出文件
curl "http://localhost:8001/file/list"
```

#### 3. 通过工具包管理器使用

```python
from agentserver.toolkit_manager import toolkit_manager

# 调用工具
result = await toolkit_manager.call_tool("file_edit", "write_file", {
    "path": "test.txt",
    "file_text": "Hello World!"
})

# 获取所有工具
tools = toolkit_manager.get_all_tools()
```

### SEARCH/REPLACE 格式说明

文件编辑使用特殊的 diff 格式：

```

**示例：**

```python
diff = """<<<<<<< SEARCH
def hello():
    print("Hello")
=======
def hello():
    print("Hello NagaAgent!")
    print("欢迎使用文件编辑工具包")
>>>>>>> REPLACE"""
```

### 安全特性

1. **工作空间隔离**：所有文件操作限制在指定工作目录内
2. **文件名清理**：自动清理文件名中的不安全字符
3. **自动备份**：编辑前自动创建备份文件
4. **路径解析**：自动处理相对路径和绝对路径

### 错误处理

所有工具都会返回操作结果字符串，包含成功信息或错误详情：

- 成功：`"成功写入文件: /path/to/file"`
- 失败：`"写入文件失败: [错误详情]"`

### 扩展工具包

要添加新的工具包，需要：

1. 继承 `AsyncBaseToolkit` 基类
2. 使用 `@register_tool` 装饰器注册工具方法
3. 在 `toolkit_manager.py` 中注册工具包类型
4. 创建对应的配置文件

**示例：**

```python
from agentserver.tools.base_toolkit import AsyncBaseToolkit, register_tool

class MyToolkit(AsyncBaseToolkit):
    @register_tool
    async def my_tool(self, param: str) -> str:
        """我的工具"""
        return f"处理结果: {param}"
```

## API 端点

### 工具包管理

- `GET /toolkits` - 列出所有工具包
- `GET /toolkits/{toolkit_name}` - 获取工具包信息
- `GET /tools` - 列出所有工具
- `POST /tools/{toolkit_name}/{tool_name}` - 调用工具

### 文件编辑专用

- `POST /file/write` - 写入文件
- `GET /file/read` - 读取文件
- `POST /file/edit` - 编辑文件
- `GET /file/list` - 列出文件
