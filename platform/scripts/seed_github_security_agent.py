import asyncio
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
import json
import sys

from app.models.agent import AgentModel
from app.models.skill import SkillModel, SkillVersionModel
from app.models.template import TaskTemplateModel
from app.models.trigger import TriggerRuleModel
from app.config import settings
import os

async def main():
    # Read SKILL.md from the skills directory
    skill_file_path = os.path.join(os.path.dirname(__file__), '../../skills/des_encrypt/SKILL.md')
    with open(skill_file_path, 'r', encoding='utf-8') as f:
        skill_markdown_content = f.read()
        
    # Setup DB connection
    engine = create_async_engine(settings.DATABASE_URL)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        # 1. Create or verify 'des_encrypt' skill
        result = await session.execute(select(SkillModel).where(SkillModel.name == 'des_encrypt'))
        skill = result.scalars().first()
        
        if not skill:
            skill_id = str(uuid.uuid4())
            skill = SkillModel(
                id=skill_id,
                name="des_encrypt",
                display_name="DES Encryption Skill",
                description="Provides DES encryption utilities. Useful when writing security-related code.",
                layer="L1",
                tags=["security", "encryption"],
                status="active",
                version="1.0.0"
            )
            
            skill_version = SkillVersionModel(
                id=str(uuid.uuid4()),
                skill_id=skill_id,
                version="1.0.0",
                content=skill_markdown_content,
                change_summary="Initial commit for DES encryption skill parsed from CLAUDE.md"
            )
            session.add(skill)
            session.add(skill_version)
            print(f"Created skill: des_encrypt ({skill_id})")
        else:
            print(f"Skill des_encrypt already exists ({skill.id}), updating content.")
            result_v = await session.execute(select(SkillVersionModel).where(SkillVersionModel.skill_id == skill.id).order_by(SkillVersionModel.created_at.desc()))
            latest_version = result_v.scalars().first()
            if latest_version:
                latest_version.content = skill_markdown_content
            else:
                skill_version = SkillVersionModel(
                    id=str(uuid.uuid4()),
                    skill_id=skill.id,
                    version="1.0.0",
                    content=skill_markdown_content,
                    change_summary="Update DES encryption skill content"
                )
                session.add(skill_version)

        # 2. Create Agent "安全加密agent"
        result = await session.execute(select(AgentModel).where(AgentModel.role == '安全加密agent'))
        agent = result.scalars().first()
        
        if not agent:
            agent = AgentModel(
                id=str(uuid.uuid4()),
                role="安全加密agent",
                display_name="安全加密专家",
                status="idle",
                model_name=settings.LLM_MODEL,
                config={
                    "skills": ["des_encrypt", "github-issue", "github-repo-manager"],
                    "system_prompt": "You are a specialized agent for security and encryption. Your job is to read GitHub issues regarding security encryption, modify or write code using the des_encrypt skill, push to a remote repository branch, and then update the issue status."
                }
            )
            session.add(agent)
            print(f"Created agent: {agent.role} ({agent.id})")
        else:
            print(f"Agent {agent.role} already exists ({agent.id})")

        # 3. Create Task Template for GitHub issues
        template_name = "github_security_issue_handler"
        result = await session.execute(select(TaskTemplateModel).where(TaskTemplateModel.name == template_name))
        template = result.scalars().first()
        
        if not template:
            template = TaskTemplateModel(
                id=str(uuid.uuid4()),
                name=template_name,
                display_name="GitHub Security Issue Auto-Encryption",
                description="Template for processing GitHub issues for security encryption requests.",
                is_builtin=False,
                stages=json.dumps([
                    {
                        "stage_name": "process_security_issue",
                        "agent_role": "安全加密agent"
                    }
                ]),
                gates="[]"
            )
            session.add(template)
            print(f"Created task template: {template.name} ({template.id})")
        else:
            print(f"Template {template.name} already exists ({template.id})")
            
        # 4. Create Trigger Rule
        trigger_name = "github_issue_security"
        result = await session.execute(select(TriggerRuleModel).where(TriggerRuleModel.name == trigger_name))
        trigger = result.scalars().first()
        
        if not trigger:
            trigger = TriggerRuleModel(
                id=str(uuid.uuid4()),
                name=trigger_name,
                source="github",
                event_type="issue_created",
                filters={
                    "title_contains": "encrypt"  # Simplified filter, can be customized
                },
                template_id=template.id,
                title_template="处理 GitHub 安全加密 Issue: {issue_title}",
                desc_template="URL: {issue_url}\\nBody: {issue_body}",
                dedup_key_template="github:issue:{issue_number}",
                dedup_window_hours=24,
                enabled=True
            )
            session.add(trigger)
            print(f"Created trigger: {trigger.name} ({trigger.id})")
        else:
            print(f"Trigger {trigger.name} already exists ({trigger.id})")
            
        await session.commit()
        print("Database seeding completed securely.")

if __name__ == "__main__":
    # Workaround for running from asyncio script without throwing an error if event loop is running
    asyncio.run(main())
