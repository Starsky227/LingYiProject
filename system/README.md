# System 系统模块

## 文件说明

### 🎯 重点感谢
本萌新大量借鉴了：
NagaAgent：https://github.com/Xxiii8322766509/NagaAgent

### config.py
- **功能**: 系统配置管理
- **主要特性**: 
  - 加载和管理项目配置文件 (config.json)
  - 定义系统、API、服务器等配置模型
  - 配置变更监听机制
  - 环境变量自动设置

### task_manager.py  
- **功能**: 后台任务调度器
- **主要特性**:
  - 异步任务队列管理
  - 支持任务优先级 (LOW/NORMAL/HIGH/URGENT)
  - 任务状态追踪 (pending/running/completed/failed/cancelled)
  - 失败重试机制 (默认最多3次)
  - 多工作线程并发执行 (默认5个)

## 使用方式

### 配置管理
```python
from system.config import load_config
config = load_config()
```

### 任务提交  
```python
from system.task_manager import TaskManager, TaskPriority
task_manager = TaskManager()
task_id = task_manager.submit_task("任务名称", function, *args, priority=TaskPriority.HIGH)
```