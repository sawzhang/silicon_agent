---
name: search-codebase
description: 搜索代码库中的文件、函数、类或关键字，快速定位相关代码
metadata:
  emoji: "🔍"
  tags: ["shared", "search", "codebase"]
---

# 搜索代码库

在当前工作目录的代码库中搜索文件、函数、类或关键字。

## 输入

用户会提供以下信息：
- `keyword`：要搜索的关键字、函数名、类名或文件模式
- `scope`（可选）：搜索范围，如特定目录或文件类型

## 工作流

### Step 1：确定搜索策略

根据关键字类型选择搜索方式：
- 文件名搜索：使用 `execute` 工具运行 `find . -name "pattern"` 或 `ls -R`
- 内容搜索：使用 `execute` 工具运行 `grep -rn "keyword" --include="*.py"` 等
- 结构搜索：搜索 class/def/import 等关键结构

### Step 2：执行搜索

运行搜索命令，收集结果。如果结果过多，缩小搜索范围。

### Step 3：整理结果

将搜索结果整理为结构化输出。

## 输出格式

```markdown
## 搜索结果：`{keyword}`

共找到 {count} 处匹配：

| 文件 | 行号 | 匹配内容 |
|------|------|---------|
| path/to/file.py | 42 | matched line content |
| ... | ... | ... |

### 相关上下文
- 简要说明匹配代码的用途和关联关系
```
