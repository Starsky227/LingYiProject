# DateTime Agent

这是一个用于获取系统时间的 MCP (Model Context Protocol) 工具，为 AI 提供时间相关功能。

## 功能特性

### 1. 获取当前时间
- 支持多种时间格式（ISO、可读格式、时间戳、自定义格式）
- 支持时区转换
- 支持中英文语言
- 提供详细的时间信息（年、月、日、时、分、秒、星期几）

### 2. 时间计算
- 支持时间加减运算
- 支持多种时间单位（秒、分钟、小时、天、周、月、年）
- 基于当前时间或指定时间进行计算

### 3. 时间格式转换
- 自动识别常见时间格式
- 支持自定义输入和输出格式
- 智能解析时间字符串

## 使用示例

### 获取当前时间
```json
{
  "tool_name": "获取当前时间",
  "format": "readable",
  "timezone": "Asia/Shanghai",
  "language": "zh"
}
```

### 时间计算
```json
{
  "tool_name": "时间计算",
  "operation": "add",
  "amount": 7,
  "unit": "days"
}
```

### 时间格式转换
```json
{
  "tool_name": "时间格式转换",
  "input_time": "2025-11-04 14:30:00",
  "output_format": "%Y年%m月%d日 %H时%M分"
}
```

## 支持的参数

### 时间格式
- `ISO`: ISO 8601 格式 (2025-11-04T14:30:00+08:00)
- `readable`: 可读格式 (2025年11月04日 14时30分00秒)
- `timestamp`: Unix 时间戳
- 自定义格式: 使用 Python strftime 格式

### 时区
- `Asia/Shanghai`: 中国标准时间
- `UTC`: 协调世界时
- `America/New_York`: 美国东部时间
- 等等...（支持所有 pytz 时区）

### 时间单位
- `seconds`: 秒
- `minutes`: 分钟
- `hours`: 小时
- `days`: 天
- `weeks`: 周
- `months`: 月
- `years`: 年

## 安装依赖

```bash
pip install -r requirements.txt
```

## 配置

可以在 agent-manifest.json 中配置默认参数：
- `default_timezone`: 默认时区
- `default_format`: 默认时间格式
- `default_language`: 默认语言

## 错误处理

工具提供完善的错误处理机制：
- 无效时区自动回退到本地时区
- 无效时间格式自动尝试解析
- 详细的错误信息返回

## 输出格式

所有工具都返回统一的 JSON 格式：
```json
{
  "status": "success|error",
  "message": "操作结果描述",
  "data": {
    // 具体数据
  },
  "error": "错误信息（仅在出错时）"
}
```