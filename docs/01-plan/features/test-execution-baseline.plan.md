# test-execution-baseline Planning Document

> **Summary**: Restore a reliable local and CI test execution baseline.
>
> **Project**: IRMS
> **Version**: N/A
> **Author**: Codex
> **Date**: 2026-06-18
> **Status**: Final

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | `pytest` collected repository-root temporary runtime directories and failed before reaching the real test suite. CI also installed only runtime dependencies plus `pytest`, diverging from the checked-in dev dependency set. |
| **Solution** | Add explicit pytest collection configuration and align GitHub Actions dependency installation with `requirements-dev.txt`. |
| **Function/UX Effect** | Developers and CI can run the intended Python and JavaScript tests without being blocked by unrelated temporary directories or missing test-only packages. |
| **Core Value** | Restores the project safety net so later security, performance, and feature improvements can be verified quickly. |

---

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | The first improvement should make the existing test suite reliably executable before larger code changes. |
| **WHO** | IRMS maintainers running local tests and GitHub Actions. |
| **RISK** | Over-broad test collection could hide valid tests if configured too narrowly. |
| **SUCCESS** | `python -m pytest -q` and `node --test tests/js/*.test.js` pass from the repository root. |
| **SCOPE** | Pytest collection configuration, CI dependency installation, verification documentation. |

---

## 1. Overview

### 1.1 Purpose

Make the current test suite runnable from the project root on Windows and in CI.

### 1.2 Background

The repository contains temporary runtime directories under the root. Without an explicit pytest collection scope, pytest attempts to traverse them and can fail with `PermissionError` before collecting real tests.

### 1.3 Related Documents

- Existing review: `CODE_REVIEW.md`
- CI workflow: `.github/workflows/test.yml`

---

## 2. Scope

### 2.1 In Scope

- [x] Limit pytest collection to the `tests` directory.
- [x] Preserve standard `test_*.py` discovery inside `tests`.
- [x] Ignore known generated/cache/runtime directories.
- [x] Install `requirements-dev.txt` in CI.
- [x] Run Python and JavaScript tests.

### 2.2 Out of Scope

- Adding new product tests.
- Refactoring application code.
- Removing existing temporary runtime directories.

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | Pytest must collect only intended test files from `tests`. | High | Complete |
| FR-02 | Root-level generated temporary directories must not break collection. | High | Complete |
| FR-03 | CI must install the same dev dependencies expected by the test suite. | High | Complete |
| FR-04 | Existing Node test command must continue to pass. | Medium | Complete |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| Reliability | Test commands complete from repository root. | Direct command execution |
| Maintainability | Test configuration is explicit and small. | File review |
| CI Consistency | Workflow uses checked-in dependency manifest. | Workflow review |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [x] `pytest.ini` exists and scopes collection to `tests`.
- [x] GitHub Actions installs `requirements-dev.txt`.
- [x] Python tests pass locally.
- [x] JavaScript tests pass locally.
- [x] PDCA documents completed.

### 4.2 Quality Criteria

- [x] No application behavior changes.
- [x] No broad refactor.
- [x] Test command output shows zero failures.

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Tests outside `tests` are no longer collected by default. | Medium | Low | Current indexed test suite lives under `tests`; root `tmp_*.py` files are utilities, not stable tests. |
| CI install time increases slightly. | Low | Low | `requirements-dev.txt` is small and already references runtime requirements. |

---

## 6. Impact Analysis

### 6.1 Changed Resources

| Resource | Type | Change Description |
|----------|------|--------------------|
| `pytest.ini` | Test config | Added explicit pytest collection and ignore rules. |
| `.github/workflows/test.yml` | CI config | Changed dependency install to `requirements-dev.txt`. |

### 6.2 Current Consumers

| Resource | Operation | Code Path | Impact |
|----------|-----------|-----------|--------|
| `pytest.ini` | READ | `python -m pytest -q` | Collection becomes deterministic. |
| `.github/workflows/test.yml` | READ | GitHub Actions | CI installs test dependencies consistently. |

### 6.3 Verification

- [x] All consumers listed above verified.
- [x] No auth/permission changes.
- [x] No application schema changes.

---

## 7. Architecture Considerations

### 7.1 Project Level Selection

| Level | Characteristics | Recommended For | Selected |
|-------|-----------------|-----------------|:--------:|
| **Starter** | Simple structure | Static/simple apps | |
| **Dynamic** | Backend, services, tests, CI | IRMS FastAPI app | Yes |
| **Enterprise** | Strict layered platform | Larger org systems | |

### 7.2 Key Architectural Decisions

| Decision | Options | Selected | Rationale |
|----------|---------|----------|-----------|
| Test config location | CLI flags / `pytest.ini` / workflow-only | `pytest.ini` | Works both locally and in CI. |
| Collection scope | Root / `tests` only | `tests` only | Matches stable suite layout and avoids generated files. |
| CI dependencies | Runtime + pytest / dev manifest | `requirements-dev.txt` | Single source for test dependencies. |

### 7.3 Clean Architecture Approach

Selected Level: Dynamic. This change stays in tooling/configuration and does not alter application layers.

---

## 8. Convention Prerequisites

### 8.1 Existing Project Conventions

- [x] `requirements-dev.txt` exists.
- [x] `.github/workflows/test.yml` exists.
- [x] Python tests live under `tests`.
- [x] JavaScript tests live under `tests/js`.

### 8.2 Conventions to Define/Verify

| Category | Current State | To Define | Priority |
|----------|---------------|-----------|:--------:|
| Test discovery | Implicit | Explicit `tests` path | High |
| Dev dependencies | Manifest exists but CI bypassed it | CI uses manifest | High |

---

## 9. Next Steps

1. [x] Write design document.
2. [x] Implement config and CI changes.
3. [x] Run verification.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-06-18 | Initial final plan | Codex |

