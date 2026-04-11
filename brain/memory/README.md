# Memory 记忆管理模块

基于 Neo4j 知识图谱的长期记忆系统，支持AI主导的记忆录入、关键词+向量语义搜索、图谱可视化编辑。

## 组件需求

- **Neo4j 数据库** + **APOC 插件**
- **Embedding API**（用于语义搜索）

## 核心文件

### knowledge_graph_manager.py - 知识图谱管理器
- Neo4j 节点/关系的 CRUD 操作（创建、修改、删除）
- 加载 prompt/ 目录下的记忆分析提示词
- 使用 Embedding API 进行语义理解
- 支持批量写入与事务管理

### record_memory.py - 记忆录入
- `MemoryWriter` 类：通过 `_agent_runner` 调度记忆Agent异步录入
- `full_memory_record(message, related_memory)` 方法触发记忆提取
- 使用 XML 格式提示词模板 (`memory_record_prompt.xml`)

### search_memory.py - 记忆检索
- 关键词提取 (`extract_keyword_from_text()`) + 向量相似度搜索
- 意图分析引导搜索策略（`1_intent_analyze.txt` 提示词）
- `full_memory_search()` 为主入口

### memorygraph_visualizer.py - 图谱可视化
- `MemoryGraphViewer` 类：生成交互式 HTML 图谱
- 支持节点/关系的可视化增删改查
- 独立运行：直接执行该 .py 文件启动

### _agent_runner.py - 本地Agent执行器
- 独立于 agentserver 的轻量Agent循环
- 处理工具发现、LLM迭代、工具执行
- 最大迭代次数默认20，带重试逻辑

### 数据管理工具
- **memory_download_from_neo4j.py**: 从 Neo4j 导出节点/关系到本地 JSON
- **clear_neo4j.py**: 清空 Neo4j 数据库

## 记忆Tools（tools/）

Agent 通过以下工具操作知识图谱：

| 工具 | 功能 |
|------|------|
| `create_character_nodes/` | 创建角色节点 |
| `create_entity_nodes/` | 创建实体节点 |
| `create_location_nodes/` | 创建地点节点 |
| `create_time_nodes/` | 创建时间节点 |
| `create_relation/` | 创建节点间关系 |
| `modify_character_nodes/` | 修改角色属性 |
| `modify_entity_nodes/` | 修改实体属性 |
| `modify_location_nodes/` | 修改地点属性 |
| `modify_relation/` | 修改关系属性 |

## Prompt模板（prompt/）

- `memory_record.txt` - 记忆录入策略
- `memory_record_prompt.xml` - XML格式系统提示词
- `keyword_extract.txt` - 关键词提取规则
- `memory_filter.txt` - 事件过滤规则
- `event_extract.txt` - 事件识别规则

## 数据存储

- **data/memory_graph/**: 本地记忆图谱 JSONL 文件（按日期）
- **data/chat_logs/**: 对话日志