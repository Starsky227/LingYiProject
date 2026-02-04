# Memory 记忆管理模块

## 主要功能

### 核心模块

#### knowledge_graph_manager.py
- **功能**: 知识图谱管理器
- **主要特性**: 
  - ai主导的记忆数据写入Neo4j数据库
  - 统一图谱操作接口
  - 批量和单条数据处理

#### quintuples_extractor.py
- **功能**: 旧的对话记忆提取器（准备废弃）
- **主要特性**:
  - 基于对话内容判断可记忆性
  - 自动分类三元组/五元组
  - 异步记忆提取任务

#### relevant_memory_search.py
- **功能**: 相关记忆搜索（等待更新，暂时记忆搜索功能在knowledge graph manager里）
- **主要特性**:
  - 关键词提取与知识图谱查询
  - 基于上下文的智能搜索
  - 相关记忆检索

#### memorygraph_visualizer.py
- **功能**: 记忆图谱可视化
- **主要特性**:
  - HTML交互式图谱展示
  - 节点详情查看，修改，保存和删除
  - 运行方式为直接启动该.py文件

### 数据管理

#### memory_upload_to_neo4j.py
- **功能**: 记忆数据上传到Neo4j数据库

#### memory_download_from_neo4j.py
- **功能**: 从Neo4j下载记忆数据到本地JSON

#### clear_neo4j.py
- **功能**: 清空Neo4j数据库

### 目录结构

- **logs/**: 日志文件存储
- **logs_to_load/**: 待加载的日志文件
- **memory_graph/**: 本地记忆图谱JSON文件
- **prompt/**: AI提示词模板
  - `dialogue_summarizer.txt` - 对话摘要提示词
  - `event_extract.txt` - 事件提取提示词
  - `memory_record.txt` - 记忆记录提示词
  - `quintuple_auditor.txt` - 五元组审核提示词