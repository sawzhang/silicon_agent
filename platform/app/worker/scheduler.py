"""Cron Scheduler: 每分钟检查到期的 cron 触发规则并创建任务。"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.trigger import TriggerRuleModel
from app.services.trigger_service import TriggerService

logger = logging.getLogger(__name__)

_running = False
_task: Optional[asyncio.Task] = None


async def start_scheduler() -> None:
    global _running, _task
    if _running:
        return
    _running = True
    _task = asyncio.create_task(_cron_loop())
    logger.info("Cron scheduler started")


async def stop_scheduler() -> None:
    global _running, _task
    _running = False
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    logger.info("Cron scheduler stopped")


async def _cron_loop() -> None:
    """每 60s 扫描一次到期的 cron 规则。"""
    while _running:
        try:
            await _fire_due_rules()
        except Exception:
            logger.exception("Cron scheduler error")
        await asyncio.sleep(60)


async def _fire_due_rules() -> None:
    now = datetime.now()

    async with async_session_factory() as session:
        result = await session.execute(
            select(TriggerRuleModel).where(
                TriggerRuleModel.source == "cron",
                TriggerRuleModel.enabled.is_(True),
                TriggerRuleModel.cron_expr.isnot(None),
            )
        )
        rules = result.scalars().all()

        for rule in rules:
            if not _is_due(rule, now):
                continue

            logger.info("Cron rule due: %s (%s)", rule.name, rule.cron_expr)

            payload = {
                "scheduled_at": now.isoformat(),
                "rule_name": rule.name,
                "cron_expr": rule.cron_expr,
                "event_type": "scheduled",
            }

            service = TriggerService(session)
            task_id = await service.process_event("cron", "scheduled", payload)

            # 无论是否创建任务（可能被过滤/去重），都更新触发时间避免重复检查
            rule.last_triggered_at = now
            await session.commit()

            if task_id:
                logger.info("Cron rule %s triggered task %s", rule.name, task_id)
            else:
                logger.debug("Cron rule %s fired but no task created (filtered/deduped)", rule.name)


def _is_due(rule: TriggerRuleModel, now: datetime) -> bool:
    """判断 cron 规则是否到期。

    逻辑：取 last_triggered_at（或 1 分钟前）为基准，
    计算下一个计划触发时间，若该时间 <= now 则到期。
    """
    if not rule.cron_expr:
        return False

    try:
        from croniter import CroniterBadCronError, croniter  # type: ignore[import]

        # 基准时间：上次触发时间，或首次运行时向前推 1 分钟
        base = rule.last_triggered_at
        if base is None:
            base = now - timedelta(minutes=1)
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)

        cron = croniter(rule.cron_expr, base)
        next_time: datetime = cron.get_next(datetime)
        if next_time.tzinfo is None:
            next_time = next_time.replace(tzinfo=timezone.utc)

        return next_time <= now

    except CroniterBadCronError:
        logger.warning("规则 %s 的 cron 表达式无效: %s", rule.name, rule.cron_expr)
        return False
    except Exception:
        logger.exception("检查 cron 规则 %s 时出错", rule.name)
        return False


def validate_cron_expr(expr: str) -> bool:
    """校验 cron 表达式是否合法，供 API 层调用。"""
    try:
        from croniter import CroniterBadCronError, croniter  # type: ignore[import]
        return croniter.is_valid(expr)
    except (ImportError, CroniterBadCronError):
        return False
