---
name: design-database
description: 设计数据库 schema，包括表结构、字段类型、索引和关联关系
metadata:
  emoji: "🗄️"
  tags: ["spec", "database", "schema"]
---

# 数据库 Schema 设计

根据需求设计数据库表结构，包括字段定义、类型约束、索引策略和表间关联。

## 输入

用户会提供：
- `feature`：功能描述
- `entities`：涉及的业务实体
- `db_type`（可选）：数据库类型（默认 PostgreSQL）

## 工作流

### Step 1：实体分析

从功能描述中提取：
- 核心实体及其属性
- 实体间关系（1:1, 1:N, M:N）
- 业务规则和约束

### Step 2：表结构设计

为每个实体设计表结构：
- 表名使用 snake_case 复数形式
- 每个表包含 `id`（主键）、`created_at`、`updated_at`
- 外键使用 `{entity}_id` 命名
- 枚举字段使用 VARCHAR + CHECK 约束或独立枚举表

### Step 3：索引策略

根据查询模式设计索引：
- 外键字段自动建索引
- 高频查询字段建索引
- 组合查询考虑联合索引

### Step 4：搜索现有模型

使用 `search-codebase` skill 查找项目中已有的数据模型，确保新表与现有命名和风格一致。

## 输出格式

```markdown
## 数据库设计：{feature}

### ER 关系
{用文字描述实体间关系}

### 表结构

#### {table_name}
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | 主键 |
| {field} | {type} | {constraints} | {说明} |
| created_at | TIMESTAMP | NOT NULL DEFAULT now() | 创建时间 |
| updated_at | TIMESTAMP | NOT NULL DEFAULT now() | 更新时间 |

**索引**：
- `idx_{table}_{field}` ON ({field}) — {用途}

### 迁移 SQL
（如有需要，提供 DDL 语句）
```
