## Skills
A skill is a set of local instructions to follow that is stored in a `SKILL.md` file. Below is the list of skills that can be used.

### Available skills
- start-project-services: Start/restart silicon_agent frontend+backend with health checks. (file: /Users/abnzhang/Project/silicon_agent/skills/start-project-services/SKILL.md)

### How to use skills
- Trigger rules: If user asks to 启动前后端、重启服务、拉起项目、启动 python/web 服务, use `start-project-services`.
- Missing/blocked: If skill file can't be read, continue with best-effort manual start.
