from __future__ import annotations

import logging
import secrets
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import ProjectIntegrationModel
from app.models.project import ProjectModel
from app.schemas.integration import (
    VALID_PROVIDERS,
    IntegrationCreateRequest,
    IntegrationResponse,
    IntegrationUpdateRequest,
)

logger = logging.getLogger(__name__)


class IntegrationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _check_project_exists(self, project_id: str) -> None:
        result = await self.session.execute(
            select(ProjectModel.id).where(ProjectModel.id == project_id)
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="项目不存在")

    async def list_integrations(self, project_id: str) -> list[IntegrationResponse]:
        await self._check_project_exists(project_id)
        result = await self.session.execute(
            select(ProjectIntegrationModel)
            .where(ProjectIntegrationModel.project_id == project_id)
            .order_by(ProjectIntegrationModel.created_at)
        )
        return [self._to_response(m) for m in result.scalars().all()]

    async def get_integration(
        self, project_id: str, provider: str
    ) -> IntegrationResponse:
        model = await self._get_model(project_id, provider)
        if model is None:
            raise HTTPException(status_code=404, detail="集成配置不存在")
        return self._to_response(model)

    async def create_integration(
        self, project_id: str, request: IntegrationCreateRequest
    ) -> IntegrationResponse:
        await self._check_project_exists(project_id)
        if request.provider not in VALID_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的 provider: {request.provider}，可选: {', '.join(sorted(VALID_PROVIDERS))}",
            )
        model = ProjectIntegrationModel(
            project_id=project_id,
            provider=request.provider,
            access_token=request.access_token,
            extra_config=request.extra_config,
            enabled=request.enabled,
        )
        self.session.add(model)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"项目已存在 {request.provider} 集成配置",
            )
        await self.session.refresh(model)
        return self._to_response(model)

    async def update_integration(
        self, project_id: str, provider: str, request: IntegrationUpdateRequest
    ) -> IntegrationResponse:
        model = await self._get_model(project_id, provider)
        if model is None:
            raise HTTPException(status_code=404, detail="集成配置不存在")
        for key, value in request.model_dump(exclude_none=True).items():
            setattr(model, key, value)
        await self.session.commit()
        await self.session.refresh(model)
        return self._to_response(model)

    async def delete_integration(self, project_id: str, provider: str) -> None:
        model = await self._get_model(project_id, provider)
        if model is None:
            raise HTTPException(status_code=404, detail="集成配置不存在")
        await self.session.delete(model)
        await self.session.commit()

    async def regenerate_secret(
        self, project_id: str, provider: str
    ) -> IntegrationResponse:
        model = await self._get_model(project_id, provider)
        if model is None:
            raise HTTPException(status_code=404, detail="集成配置不存在")
        model.webhook_secret = secrets.token_hex(32)
        await self.session.commit()
        await self.session.refresh(model)
        return self._to_response(model)

    async def get_integration_by_project_provider(
        self, project_id: str, provider: str
    ) -> Optional[ProjectIntegrationModel]:
        """内部使用：返回原始 model（webhook 验签用）。"""
        return await self._get_model(project_id, provider)

    # ── 内部辅助 ──────────────────────────────────────────────────────────────

    async def _get_model(
        self, project_id: str, provider: str
    ) -> Optional[ProjectIntegrationModel]:
        result = await self.session.execute(
            select(ProjectIntegrationModel).where(
                ProjectIntegrationModel.project_id == project_id,
                ProjectIntegrationModel.provider == provider,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _mask_token(token: Optional[str]) -> Optional[str]:
        if not token:
            return None
        if len(token) <= 4:
            return "****"
        return "****" + token[-4:]

    def _to_response(self, model: ProjectIntegrationModel) -> IntegrationResponse:
        return IntegrationResponse(
            id=model.id,
            project_id=model.project_id,
            provider=model.provider,
            webhook_secret=model.webhook_secret,
            access_token=self._mask_token(model.access_token),
            extra_config=model.extra_config,
            enabled=model.enabled,
            webhook_url=f"/webhooks/{model.provider}/{model.project_id}",
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
