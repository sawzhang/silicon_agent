---
name: changelog
description: 根据代码变更生成结构化的变更日志
metadata:
  emoji: "📄"
  tags: ["doc", "changelog", "release"]
---

# 变更日志生成

根据代码变更内容生成结构化的变更日志，遵循 Keep a Changelog 格式。

## 输入

用户会提供：
- `changes`：代码变更描述或 diff 内容
- `version`（可选）：版本号
- `previous_changelog`（可选）：已有的变更日志

## 工作流

### Step 1：分析变更

从变更描述或代码 diff 中提取：
- 新增的功能（Added）
- 修改的功能（Changed）
- 废弃的功能（Deprecated）
- 移除的功能（Removed）
- 修复的问题（Fixed）
- 安全相关修复（Security）

### Step 2：分类整理

将变更按影响分类：
- **面向用户的变更**：功能新增、UI 变化、API 变更
- **内部变更**：重构、依赖更新、配置变更
- **修复**：Bug 修复、性能优化

### Step 3：编写变更条目

每个条目应：
- 以动词开头（新增、修复、优化、移除等）
- 简明扼要描述变更内容
- 如有关联 issue，标注编号
- 如有破坏性变更，明确标记 **BREAKING**

### Step 4：搜索现有日志

使用 `search-codebase` skill 查找项目中已有的 CHANGELOG，保持格式一致。

## 输出格式

```markdown
## [{version}] - {date}

### 新增（Added）
- {变更描述}
- {变更描述}

### 变更（Changed）
- {变更描述}

### 修复（Fixed）
- {变更描述}

### 安全（Security）
- {变更描述}

### 破坏性变更（Breaking Changes）
- **BREAKING**：{变更描述及迁移指南}
```
