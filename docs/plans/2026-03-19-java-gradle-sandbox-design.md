# Java Gradle Sandbox Design

## Context

The sandbox needs to run a broad set of Java 8 or Java 17 Gradle + Spring Boot projects with poor network conditions. The current repository already ships a dual-JDK coding image, preserves `./gradlew`, mounts a shared Gradle cache, and prewarms wrapper downloads. The remaining gap is making that setup reliable across different project wrappers and dependency graphs without forcing per-project customization.

## Goals

- Provide a general-purpose sandbox image that can run most Java 8 or Java 17 Gradle + Spring Boot projects without online dependency fetching on the common path.
- Keep project-specific Gradle behavior aligned with each repository by preferring the project wrapper.
- Default to Java 8 when version detection is inconclusive.
- Offer a manual override for edge cases and keep failure handling bounded and observable.

## Non-Goals

- Supporting every private Maven repository or every unusual plugin offline on day one.
- Replacing project wrappers with a single global Gradle version.
- Solving long-term dependency distribution purely through the sandbox image when an internal artifact proxy becomes available later.

## Decision Summary

- Ship both JDK 8 and JDK 17 in the sandbox image.
- Prefer `./gradlew` for build and test commands; use system `gradle` only as a fallback when a project has no wrapper.
- Expand Java version detection to cover Gradle toolchains, Maven compiler properties, `.java-version`, `.tool-versions`, and existing compatibility markers.
- If detection fails, default to Java 8.
- Allow a manual override with `SANDBOX_JAVA_VERSION=8|17`.
- Preload common Gradle distributions plus common Spring Boot plugin and dependency caches for offline-first execution.
- Keep a writable runtime cache for uncommon dependencies that are not covered by the prewarmed cache.
- Permit at most one Java-version retry when build output clearly shows a version mismatch.

## Architecture

### 1. Image Layering

- `platform/sandbox/Dockerfile.base` remains the common runtime base for Python, Node, and shared agent tooling.
- `platform/sandbox/Dockerfile.coding` remains the main Java-capable image and should continue to include:
  - Temurin JDK 8
  - Temurin JDK 17
  - a system Gradle installation for diagnostics and last-resort fallback
  - a prewarmed Gradle cache directory with wrapper distributions and common modules
- `platform/sandbox/Dockerfile.test` layers browser and test tooling on top of `coding`.

This preserves the current image split while adding an explicit offline dependency layer.

### 2. Runtime Decision Flow

The sandbox runtime should resolve Java and Gradle execution in this order:

1. Check `SANDBOX_JAVA_VERSION` for an explicit override.
2. Auto-detect Java from project files.
3. If no reliable signal is found, select Java 8.
4. Prefer `./gradlew` for Gradle commands.
5. Use system `gradle` only when the workspace lacks `gradlew`.
6. If the command fails with a clear Java-version mismatch, switch once to the other supported JDK and retry one time.

This flow keeps project compatibility high while avoiding silent loops or repeated environment churn.

### 3. Java Detection Rules

The runtime should scan these files when present:

- `pom.xml`
- `build.gradle`
- `build.gradle.kts`
- `gradle.properties`
- `settings.gradle`
- `settings.gradle.kts`
- `.java-version`
- `.tool-versions`

Signals should be ranked in this order:

1. Explicit `SANDBOX_JAVA_VERSION`
2. Gradle toolchain declarations such as `JavaLanguageVersion.of(8|17)`
3. `sourceCompatibility` and `targetCompatibility`
4. Maven compiler `source`, `target`, `release`, and `java.version`
5. version manager hints from `.java-version` or `.tool-versions`

If conflicting markers exist, the highest-ranked explicit signal wins, and the runtime logs the winning rule.

### 4. Gradle and Wrapper Strategy

`./gradlew` should remain the primary execution path because it carries the project-specific Gradle version and plugin resolution behavior. The sandbox should not try to standardize project builds onto one system Gradle version.

Instead, the sandbox should preload the resources that wrappers usually need to fetch:

- wrapper distributions for representative Gradle versions in the 6.x, 7.x, and 8.x lines
- Gradle plugin metadata and jars commonly used by Spring Boot builds
- common Maven Central modules used by Spring Boot starters and test dependencies

System `gradle` stays available for diagnostics such as `gradle -v` and as a fallback when `gradlew` is absent.

### 5. Offline Cache Model

The cache model should have two layers:

- A prewarmed base cache baked into the image or injected as a prepared cache artifact
- A writable runtime cache mounted as `GRADLE_USER_HOME` for project-specific misses

The prewarmed cache should include:

- wrapper distributions for selected Gradle versions
- plugin portal artifacts for Spring Boot and dependency management plugins
- common modules and metadata under Gradle's module cache

The writable layer captures rare dependencies without forcing a full image rebuild.

### 6. Cache Refresh Strategy

Refresh the base cache by running a representative project matrix in a controlled environment:

- Java 8 + Spring Boot 2.x + Gradle 6.x
- Java 8 + Spring Boot 2.x + Gradle 7.x
- Java 17 + Spring Boot 2.7 + Gradle 7.x
- Java 17 + Spring Boot 3.x + Gradle 8.x

For each representative project, run:

- `./gradlew --no-daemon help`
- `./gradlew --no-daemon dependencies`
- `./gradlew --no-daemon testClasses`

This strategy prefetches the Gradle distributions, plugins, starter dependencies, test dependencies, and most metadata needed by common projects.

## Error Handling

- If Java detection fails, log that no explicit signal was found and continue with Java 8.
- If the manual override references an unavailable JDK, fail fast with a clear configuration error.
- Retry only once when build output clearly indicates a Java-version incompatibility.
- If both Java 8 and Java 17 fail, surface the original command, selected JDK, retry decision, and the final error in logs and task output.

## Testing Strategy

Add or extend tests in the sandbox suite to verify:

- default Java 8 selection when no markers are present
- Java 17 detection for Gradle toolchains
- Java 8 detection for legacy Gradle or Maven properties
- explicit override handling through environment variables
- wrapper-first execution behavior
- Gradle cache and prewarm environment wiring through the Docker run contract
- coding image assertions for dual JDK plus offline cache preparation hooks

Add representative sandbox fixtures or smoke validation inputs for:

- Java 8 + Spring Boot 2.x
- Java 17 + Spring Boot 2.7
- Java 17 + Spring Boot 3.x

## Risks And Mitigations

- Large image size: keep the writable cache separate and refresh only the shared offline layer when possible.
- Cache staleness: refresh the matrix on a scheduled cadence or alongside sandbox release candidates.
- Private repositories: document that private artifacts still need runtime access or a mirrored internal repository.
- False-positive detection: keep a manual override and log the matched detection rule for every task.

## Recommended Implementation Order

1. Expand Java detection and add explicit override support.
2. Change runtime defaulting behavior to Java 8 plus bounded retry.
3. Add offline cache preparation hooks in the coding image.
4. Extend Docker env wiring and contract tests for any new cache paths or override variables.
5. Add representative fixture-based verification for Java 8 and Java 17 Spring Boot projects.

## Open Assumptions

- Most target projects are standard Gradle + Spring Boot applications using public Maven-style dependencies.
- The sandbox may still need runtime network access for rare or private dependencies, but the common path should succeed offline.
- The current untracked `gradle-8.5-wrapper-cache.tgz` artifact is treated as unrelated local state and is not part of this design.
