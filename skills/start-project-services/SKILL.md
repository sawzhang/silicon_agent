---
name: start-project-services
description: Start or restart the silicon_agent frontend and backend services, including port cleanup, process startup, and health checks. Trigger when user asks to 启动前后端、重启服务、拉起项目、启动python后端/web前端.
---

# 启动前后端服务

用于在本仓库一键启动或重启 `platform`(8000) 与 `web`(3000)。

## 触发词

- 启动前后端
- 重启前后端
- 拉起项目
- 重启 python 服务
- 启动 web / 启动后端

## 工作流

1. 清理端口
- 终止 `8000` 和 `3000` 监听进程（如存在）。

2. 启动服务
- 后端：在 `platform` 目录启动 `uvicorn app.main:app --host 0.0.0.0 --port 8000`。
- 前端：在 `web` 目录启动 `npm run dev -- --host 0.0.0.0 --port 3000`。

3. 探活验证
- 后端健康检查：`GET http://127.0.0.1:8000/health`
- 后端业务检查：`GET http://127.0.0.1:8000/api/v1/agents`
- 前端检查：`HEAD http://127.0.0.1:3000`

4. 返回结果
- 明确说明两个端口是否监听成功。
- 明确说明探活是否通过；失败时附关键错误。

## 执行脚本

优先使用：

```bash
bash /Users/abnzhang/Project/silicon_agent/skills/start-project-services/scripts/start_services.sh
```

仅重启后端：

```bash
bash /Users/abnzhang/Project/silicon_agent/skills/start-project-services/scripts/restart_backend.sh
```
