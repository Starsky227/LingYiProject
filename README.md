# LingYiProject 0.1
This is a project started by a total beginner, a 3A project: AI coding, AI drawing, AI service.
这是一个纯萌新打造的AI智能体计划，纯正的3A大作：AI编程，AI立绘，AI聊天。

### 🎯 重点感谢
本萌新大量借鉴了：
NagaAgent：https://github.com/Xxiii8322766509/NagaAgent

## 🚀 快速开始
**使用方法：**
```bash
# 首先使用一键配置
.\setup.ps1

# 然后双击start.bat启动
```

### 📋 系统要求

- **操作系统**: Windows 11 （10应该也行？）
- **Python**: 3.10+ (推荐 3.13)
- **内存**: 建议 4GB 以上
- **存储**: 建议 2GB 以上可用空间

### 📋 版本信息
**现版本内容总结：**
1. 正常对话
2. 自定义立绘，路径ui/image（记得修改config.json->ui->image_name）
3. neo4j知识图谱（但尚不支持自动上传记忆）

**版本：0.1.0**
1. 实装了三/五元组提取（参考了Naga）
2. 实装了neo4j记忆节点查阅
3. 修改了意图识别（不成功）

**版本：0.0.1**
1. 可以链接到模型了
2. 尝试了意图识别（不成功）
3. 有了立绘和ui
4. 上传了crawl4ai（但无法启用）
5. 复制了大量Naga的代码在库中，并未实际使用。