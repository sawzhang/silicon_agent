# GitHub Issue Template Workflow Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a stable `github_issue_template` workflow that routes every matched GitHub issue through `issue distribution agent`, dispatches security-encryption issues to `安全加密agent`, and posts branch plus Silicon task URL back to the originating issue.

**Architecture:** Reuse the existing webhook -> trigger -> task -> worker pipeline, but formalize it with one built-in template, seeded agent roles, a fixed two-stage definition, and stable GitHub issue metadata propagation. Keep the first version intentionally static: distribution always runs first, while the security worker remains the only execution agent.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, Pydantic, existing worker engine, pytest, GitHub Enterprise REST API

---

### Task 1: Lock the spec and plan files into the repo

**Files:**
- Create: `docs/plans/2026-03-21-github-issue-template-design.md`
- Create: `docs/plans/2026-03-21-github-issue-template-workflow.md`
- Create: `platform/docs/specs/feature-009-GitHubIssue任务分发工作流/01_requirements.md`
- Create: `platform/docs/specs/feature-009-GitHubIssue任务分发工作流/02_interface.md`
- Create: `platform/docs/specs/feature-009-GitHubIssue任务分发工作流/03_implementation.md`

**Step 1: Verify the new spec files exist**

Run: `find docs/plans platform/docs/specs/feature-009-GitHubIssue任务分发工作流 -maxdepth 2 -type f | sort`
Expected: all 5 files are listed

**Step 2: Commit the documentation checkpoint**

Run: `git add docs/plans/2026-03-21-github-issue-template-design.md docs/plans/2026-03-21-github-issue-template-workflow.md platform/docs/specs/feature-009-GitHubIssue任务分发工作流/01_requirements.md platform/docs/specs/feature-009-GitHubIssue任务分发工作流/02_interface.md platform/docs/specs/feature-009-GitHubIssue任务分发工作流/03_implementation.md && git commit -m "docs: define github issue template workflow"`
Expected: commit created

### Task 2: Write failing tests for built-in template and seeded agent definitions

**Files:**
- Modify: `platform/tests/test_template_service.py`
- Modify: `platform/tests/test_agents_api.py`
- Modify: `platform/tests/test_agents.py`
- Modify: `platform/app/services/template_service.py`
- Modify: `platform/app/services/seed_service.py`

**Step 1: Write the failing template seed test**

Add a test asserting a built-in template named `github_issue_template` exists and has exactly two ordered stages:
- `dispatch_issue` / `issue distribution agent`
- `process_security_issue` / `安全加密agent`

**Step 2: Run the focused test to verify it fails**

Run: `cd platform && pytest tests/test_template_service.py -k github_issue_template -v`
Expected: FAIL because template is not seeded yet

**Step 3: Write the failing agent seed test**

Add tests asserting the seeded agent catalog contains:
- `issue distribution agent`
- `安全加密agent`

And verify their configs include the skill directories and prompt append needed for dispatch / feedback behavior.

**Step 4: Run the focused agent tests to verify they fail**

Run: `cd platform && pytest tests/test_agents.py tests/test_agents_api.py -k "issue distribution agent or 安全加密agent" -v`
Expected: FAIL because the roles are not seeded consistently yet

**Step 5: Implement the minimal seed changes**

Update the built-in template seed and the built-in agent seed path so the two new roles and the new template are created deterministically.

**Step 6: Re-run the focused tests**

Run: `cd platform && pytest tests/test_template_service.py tests/test_agents.py tests/test_agents_api.py -k "github_issue_template or issue distribution agent or 安全加密agent" -v`
Expected: PASS

**Step 7: Commit**

Run: `git add platform/app/services/template_service.py platform/app/services/seed_service.py platform/tests/test_template_service.py platform/tests/test_agents.py platform/tests/test_agents_api.py && git commit -m "feat: seed github issue template agents"`

### Task 3: Write failing tests for GitHub issue metadata propagation

**Files:**
- Modify: `platform/tests/test_webhook_project.py`
- Modify: `platform/tests/test_mock_webhook.py`
- Modify: `platform/tests/test_trigger_complex.py`
- Modify: `platform/tests/test_task_service.py`
- Modify: `platform/app/api/webhooks/github.py`
- Modify: `platform/app/services/trigger_service.py`
- Modify: `platform/app/services/task_service.py`
- Modify: `platform/app/schemas/task.py`

**Step 1: Write the failing project-webhook test**

Add a test asserting a real GitHub issue webhook:
- creates a task
- stores `github_issue_number`
- preserves issue URL and repo context inside task description or structured prompt input

**Step 2: Write the failing mock-webhook regression test**

Add a test asserting the same metadata is preserved when using `/mock-webhook`.

**Step 3: Run the focused webhook tests to verify they fail**

Run: `cd platform && pytest tests/test_webhook_project.py tests/test_mock_webhook.py -k "issue and github_issue_number" -v`
Expected: FAIL because real webhook flow does not persist all issue metadata yet

**Step 4: Implement the minimal metadata propagation**

Update webhook normalization and trigger task creation so `issue_number`, `issue_url`, `repo_full_name`, and issue body flow into task creation consistently.

**Step 5: Re-run the focused tests**

Run: `cd platform && pytest tests/test_webhook_project.py tests/test_mock_webhook.py tests/test_task_service.py -k "github_issue_number or issue_url or repo_full_name" -v`
Expected: PASS

**Step 6: Commit**

Run: `git add platform/app/api/webhooks/github.py platform/app/services/trigger_service.py platform/app/services/task_service.py platform/app/schemas/task.py platform/tests/test_webhook_project.py platform/tests/test_mock_webhook.py platform/tests/test_trigger_complex.py platform/tests/test_task_service.py && git commit -m "fix: propagate github issue metadata into tasks"`

### Task 4: Write failing tests for dispatch and worker prompt contracts

**Files:**
- Modify: `platform/tests/test_prompts.py`
- Modify: `platform/tests/test_worker.py`
- Modify: `platform/app/worker/prompts.py`
- Modify: `platform/app/worker/agents.py`

**Step 1: Write the failing prompt contract test for distribution**

Assert that the `dispatch_issue` stage instruction and `issue distribution agent` system prompt require structured dispatch output with `selected_agent_role`, `issue_number`, `repo_full_name`, `work_summary`, and `acceptance_criteria`.

**Step 2: Write the failing prompt contract test for security worker**

Assert that `process_security_issue` and `安全加密agent` require:
- strict execution by skill
- git branch push
- GitHub issue feedback with branch and task URL

**Step 3: Write the failing skill directory / tool exposure test**

Assert that:
- distribution agent can load shared dispatch skills
- security worker can load both shared feedback skills and the repository-level `des_encrypt`
- security worker has the tool permissions needed for code change and git execution

**Step 4: Run the focused tests to verify they fail**

Run: `cd platform && pytest tests/test_prompts.py tests/test_worker.py tests/test_agents.py -k "dispatch_issue or process_security_issue or 安全加密agent" -v`
Expected: FAIL because current prompts and role configuration are incomplete

**Step 5: Implement the minimal prompt and role updates**

Update role prompts, stage instructions, and role skill/tool configuration so the contracts become explicit and testable.

**Step 6: Re-run the focused tests**

Run: `cd platform && pytest tests/test_prompts.py tests/test_worker.py tests/test_agents.py -k "dispatch_issue or process_security_issue or 安全加密agent" -v`
Expected: PASS

**Step 7: Commit**

Run: `git add platform/app/worker/prompts.py platform/app/worker/agents.py platform/tests/test_prompts.py platform/tests/test_worker.py platform/tests/test_agents.py && git commit -m "feat: formalize github issue dispatch contracts"`

### Task 5: Validate the real issue #13 classification path

**Files:**
- Modify: `platform/tests/test_integration_service.py`
- Create: `platform/tests/test_github_issue_template_real_sample.py`

**Step 1: Write a sample-backed test using issue #13 facts**

Use the known issue facts:
- repo `china/starbucks-asg-api`
- issue number `13`
- title `安全加密`
- body `安全加密agent，对本项目的phone字段进行安全加密`

Assert the dispatch stage contract would select `安全加密agent`.

**Step 2: Run the focused test to verify the behavior**

Run: `cd platform && pytest tests/test_github_issue_template_real_sample.py -v`
Expected: PASS once prompt and metadata contracts are in place

**Step 3: Commit**

Run: `git add platform/tests/test_github_issue_template_real_sample.py platform/tests/test_integration_service.py && git commit -m "test: cover github issue template real sample"`

### Task 6: End-to-end verification in the local environment

**Files:**
- Modify only if verification exposes a defect in existing implementation

**Step 1: Run the main targeted backend suite**

Run: `cd platform && pytest tests/test_template_service.py tests/test_mock_webhook.py tests/test_webhook_project.py tests/test_prompts.py tests/test_worker.py tests/test_agents.py tests/test_github_issue_template_real_sample.py -v`
Expected: PASS

**Step 2: Start or restart the local services if needed**

Run: `./skills/start-project-services/scripts/start_services.sh`
Expected: frontend and backend healthy

**Step 3: Trigger a local mock GitHub issue for the new template**

Run the project mock-webhook endpoint with a payload matching issue #13 semantics.
Expected: task created with the two expected stages and the correct GitHub metadata

**Step 4: If credentials and remote repo permissions are valid, run the real issue workflow**

Expected:
- `issue distribution agent` runs first
- `安全加密agent` receives the dispatch context
- branch name is recorded on the task
- issue comment contains branch + task URL

**Step 5: If any verification fails, fix with the same TDD loop before closing**

Run: focused failing pytest target
Expected: PASS after the fix

**Step 6: Final commit**

Run: `git add <touched files> && git commit -m "feat: complete github issue template workflow"`
