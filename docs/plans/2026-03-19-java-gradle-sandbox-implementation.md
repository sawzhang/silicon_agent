# Java Gradle Sandbox Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the sandbox reliably run common Java 8 and Java 17 Gradle + Spring Boot projects offline-first by default, while preferring each project's Gradle wrapper and defaulting unknown projects to Java 8.

**Architecture:** Extend the sandbox runtime to choose Java deterministically, prefer `./gradlew`, and optionally retry once on clear Java-version mismatches. Expand the coding image with prewarmed Gradle distribution and dependency caches, then lock the behavior down with targeted unit and contract tests.

**Tech Stack:** Docker, Temurin JDK 8/17, Gradle wrapper, Python 3.11, aiohttp sandbox agent, pytest

---

### Task 1: Expand Java version detection inputs

**Files:**
- Modify: `platform/sandbox/agent_server.py`
- Test: `platform/tests/test_sandbox_agent_server.py`

**Step 1: Write the failing tests**

```python
def test_detect_java_version_from_gradle_toolchain(tmp_path):
    agent_server = _load_agent_server_with_fake_skillkit()
    gradle = tmp_path / "build.gradle.kts"
    gradle.write_text(
        'java { toolchain { languageVersion.set(JavaLanguageVersion.of(17)) } }',
        encoding="utf-8",
    )
    assert agent_server._detect_java_major_version(str(tmp_path)) == 17


def test_detect_java_version_defaults_to_none_without_markers(tmp_path):
    agent_server = _load_agent_server_with_fake_skillkit()
    (tmp_path / "settings.gradle").write_text('rootProject.name = "demo"', encoding="utf-8")
    assert agent_server._detect_java_major_version(str(tmp_path)) is None
```

**Step 2: Run test to verify it fails**

Run: `cd platform && pytest tests/test_sandbox_agent_server.py -k "toolchain or defaults_to_none" -v`
Expected: FAIL because toolchain markers are not detected yet.

**Step 3: Write minimal implementation**

```python
_JAVA_DETECT_FILES = (
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "gradle.properties",
    "settings.gradle",
    "settings.gradle.kts",
    ".java-version",
    ".tool-versions",
)

_JAVA17_PATTERNS = (
    r"JavaLanguageVersion\.of\(\s*17\s*\)",
    r"languageVersion\s*(?:=|\.set\()\s*JavaLanguageVersion\.of\(\s*17\s*\)",
)
```

**Step 4: Run test to verify it passes**

Run: `cd platform && pytest tests/test_sandbox_agent_server.py -k "toolchain or defaults_to_none" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add platform/sandbox/agent_server.py platform/tests/test_sandbox_agent_server.py
git commit -m "feat: expand sandbox java version detection"
```

### Task 2: Add explicit Java override and Java 8 defaulting

**Files:**
- Modify: `platform/sandbox/agent_server.py`
- Modify: `platform/app/config.py`
- Modify: `platform/app/worker/sandbox.py`
- Test: `platform/tests/test_sandbox_agent_server.py`
- Test: `platform/tests/test_sandbox_env_contract.py`

**Step 1: Write the failing tests**

```python
def test_configure_java_runtime_respects_explicit_override(tmp_path, monkeypatch):
    agent_server = _load_agent_server_with_fake_skillkit()
    monkeypatch.setenv("SANDBOX_JAVA_VERSION", "17")
    monkeypatch.setenv("JAVA17_HOME", "/opt/jdk17")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    selected = agent_server._configure_java_runtime_for_workspace(str(tmp_path))
    assert selected == 17


def test_build_docker_run_cmd_includes_java_override_env(monkeypatch, tmp_path):
    from app.worker import sandbox as sandbox_mod
    monkeypatch.setattr(sandbox_mod.settings, "SANDBOX_DEFAULT_JAVA_VERSION", 8)
    backend = DockerSandboxBackend()
    cmd = backend._build_docker_run_cmd("sbx-test", "sandbox-image:latest", "/tmp/workspace", "task-123")
    env = _extract_env_vars_from_docker_cmd(cmd)
    assert env["SANDBOX_DEFAULT_JAVA_VERSION"] == "8"
```

**Step 2: Run test to verify it fails**

Run: `cd platform && pytest tests/test_sandbox_agent_server.py tests/test_sandbox_env_contract.py -k "override or DEFAULT_JAVA_VERSION" -v`
Expected: FAIL because the env plumbing and defaulting do not exist yet.

**Step 3: Write minimal implementation**

```python
override_raw = (os.environ.get("SANDBOX_JAVA_VERSION") or "").strip()
if override_raw in {"8", "17"}:
    major = int(override_raw)
else:
    major = _detect_java_major_version(workdir) or _env_int("SANDBOX_DEFAULT_JAVA_VERSION", 8)
```

**Step 4: Run test to verify it passes**

Run: `cd platform && pytest tests/test_sandbox_agent_server.py tests/test_sandbox_env_contract.py -k "override or DEFAULT_JAVA_VERSION" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add platform/sandbox/agent_server.py platform/app/config.py platform/app/worker/sandbox.py platform/tests/test_sandbox_agent_server.py platform/tests/test_sandbox_env_contract.py
git commit -m "feat: add sandbox java override and defaulting"
```

### Task 3: Add bounded Java-version fallback on known mismatch errors

**Files:**
- Modify: `platform/sandbox/agent_server.py`
- Test: `platform/tests/test_sandbox_agent_server.py`

**Step 1: Write the failing tests**

```python
def test_should_retry_gradle_command_on_java_version_mismatch():
    assert _should_retry_with_other_java("Unsupported class file major version 61") is True
    assert _should_retry_with_other_java("Execution failed for task ':test'") is False
```

**Step 2: Run test to verify it fails**

Run: `cd platform && pytest tests/test_sandbox_agent_server.py -k "retry_gradle_command_on_java_version_mismatch" -v`
Expected: FAIL because the helper does not exist yet.

**Step 3: Write minimal implementation**

```python
_JAVA_MISMATCH_PATTERNS = (
    r"Unsupported class file major version",
    r"invalid source release",
    r"release version .* not supported",
)

def _should_retry_with_other_java(output: str) -> bool:
    return any(re.search(pattern, output, re.IGNORECASE) for pattern in _JAVA_MISMATCH_PATTERNS)
```

**Step 4: Run test to verify it passes**

Run: `cd platform && pytest tests/test_sandbox_agent_server.py -k "retry_gradle_command_on_java_version_mismatch" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add platform/sandbox/agent_server.py platform/tests/test_sandbox_agent_server.py
git commit -m "feat: add sandbox java mismatch fallback"
```

### Task 4: Add offline Gradle cache preparation to the coding image

**Files:**
- Modify: `platform/sandbox/Dockerfile.coding`
- Create: `platform/sandbox/scripts/prewarm_gradle_cache.sh`
- Test: `platform/tests/test_sandbox_env_contract.py`

**Step 1: Write the failing test**

```python
def test_coding_sandbox_image_prepares_offline_gradle_cache():
    dockerfile_path = Path(__file__).resolve().parents[1] / "sandbox" / "Dockerfile.coding"
    content = dockerfile_path.read_text(encoding="utf-8")
    assert "prewarm_gradle_cache.sh" in content
    assert "GRADLE_USER_HOME" in content
```

**Step 2: Run test to verify it fails**

Run: `cd platform && pytest tests/test_sandbox_env_contract.py -k "offline_gradle_cache" -v`
Expected: FAIL because the prewarm hook is not present yet.

**Step 3: Write minimal implementation**

```bash
#!/usr/bin/env bash
set -euo pipefail
export GRADLE_USER_HOME="${GRADLE_USER_HOME:-/opt/gradle-offline-cache}"
for version in 6.9.4 7.6.4 8.5; do
  gradle -g "$GRADLE_USER_HOME" -v >/dev/null 2>&1 || true
done
```

**Step 4: Run test to verify it passes**

Run: `cd platform && pytest tests/test_sandbox_env_contract.py -k "offline_gradle_cache" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add platform/sandbox/Dockerfile.coding platform/sandbox/scripts/prewarm_gradle_cache.sh platform/tests/test_sandbox_env_contract.py
git commit -m "feat: prewarm offline gradle cache in sandbox image"
```

### Task 5: Add representative sandbox fixture coverage

**Files:**
- Create: `platform/tests/fixtures/sandbox/java8-springboot-gradle/build.gradle`
- Create: `platform/tests/fixtures/sandbox/java17-springboot-gradle/build.gradle.kts`
- Modify: `platform/tests/test_sandbox_agent_server.py`

**Step 1: Write the failing tests**

```python
def test_detect_java_version_from_java8_fixture():
    fixture = Path(__file__).resolve().parent / "fixtures" / "sandbox" / "java8-springboot-gradle"
    agent_server = _load_agent_server_with_fake_skillkit()
    assert agent_server._detect_java_major_version(str(fixture)) == 8


def test_detect_java_version_from_java17_fixture():
    fixture = Path(__file__).resolve().parent / "fixtures" / "sandbox" / "java17-springboot-gradle"
    agent_server = _load_agent_server_with_fake_skillkit()
    assert agent_server._detect_java_major_version(str(fixture)) == 17
```

**Step 2: Run test to verify it fails**

Run: `cd platform && pytest tests/test_sandbox_agent_server.py -k "java8_fixture or java17_fixture" -v`
Expected: FAIL because the fixtures do not exist yet.

**Step 3: Write minimal implementation**

```groovy
plugins {
    id 'org.springframework.boot' version '2.7.18'
}

sourceCompatibility = JavaVersion.VERSION_1_8
```

```kotlin
plugins {
    id("org.springframework.boot") version "3.2.4"
}

java {
    toolchain {
        languageVersion.set(JavaLanguageVersion.of(17))
    }
}
```

**Step 4: Run test to verify it passes**

Run: `cd platform && pytest tests/test_sandbox_agent_server.py -k "java8_fixture or java17_fixture" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add platform/tests/fixtures/sandbox/java8-springboot-gradle/build.gradle platform/tests/fixtures/sandbox/java17-springboot-gradle/build.gradle.kts platform/tests/test_sandbox_agent_server.py
git commit -m "test: add sandbox java fixture coverage"
```

### Task 6: Run focused regression coverage

**Files:**
- Test: `platform/tests/test_sandbox_agent_server.py`
- Test: `platform/tests/test_sandbox_env_contract.py`

**Step 1: Run the focused regression suite**

Run: `cd platform && pytest tests/test_sandbox_agent_server.py tests/test_sandbox_env_contract.py -v`
Expected: PASS

**Step 2: Run a Dockerfile assertion smoke**

Run: `cd platform && pytest tests/test_sandbox_env_contract.py -k "coding_sandbox_image or offline_gradle_cache" -v`
Expected: PASS

**Step 3: Review git diff**

Run: `git diff --stat`
Expected: Only sandbox runtime, image, script, fixture, and test files changed.

**Step 4: Commit the verified implementation**

```bash
git add platform/sandbox/agent_server.py platform/app/config.py platform/app/worker/sandbox.py platform/sandbox/Dockerfile.coding platform/sandbox/scripts/prewarm_gradle_cache.sh platform/tests/test_sandbox_agent_server.py platform/tests/test_sandbox_env_contract.py platform/tests/fixtures/sandbox
git commit -m "feat: harden sandbox java gradle offline support"
```
