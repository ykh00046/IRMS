# test-execution-baseline Design Document

> **Summary**: Deterministic pytest collection and CI dev dependency alignment.
>
> **Project**: IRMS
> **Version**: N/A
> **Author**: Codex
> **Date**: 2026-06-18
> **Status**: Final
> **Planning Doc**: [test-execution-baseline.plan.md](../../01-plan/features/test-execution-baseline.plan.md)

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

### 1.1 Design Goals

Make test execution deterministic without changing application behavior.

### 1.2 Design Principles

- Keep test policy in versioned project configuration.
- Use the existing dependency manifest instead of duplicating package lists.
- Prefer a narrow, explicit change over cleanup of generated local artifacts.

---

## 2. Architecture Options

### 2.0 Architecture Comparison

| Criteria | Option A: Minimal | Option B: Clean | Option C: Pragmatic |
|----------|:-:|:-:|:-:|
| **Approach** | Add CI flags only | Restructure tests and temp generation | Add `pytest.ini` and align CI deps |
| **New Files** | 0 | 1+ | 1 |
| **Modified Files** | 1 | Many | 1 |
| **Complexity** | Low | Medium | Low |
| **Maintainability** | Low | High | High |
| **Effort** | Low | High | Low |
| **Risk** | Medium | Medium | Low |
| **Recommendation** | Not selected | Not selected | Selected |

**Selected**: Option C: Pragmatic. It fixes both local and CI execution while keeping the change small.

### 2.1 Component Diagram

```text
Developer / CI
    -> pytest.ini
        -> tests/test_*.py
    -> requirements-dev.txt
        -> requirements.txt + test-only dependencies
```

### 2.2 Data Flow

```text
Command -> pytest config load -> collect tests directory -> execute tests -> report result
CI job -> install dev requirements -> run Python tests -> run Node tests
```

### 2.3 Dependencies

| Component | Depends On | Purpose |
|-----------|------------|---------|
| `pytest.ini` | pytest | Collection policy |
| `.github/workflows/test.yml` | `requirements-dev.txt` | Test environment setup |

---

## 3. Data Model

No application data model changes.

---

## 4. API Specification

No API changes.

---

## 5. UI/UX Design

No UI changes.

---

## 6. Error Handling

| Condition | Handling |
|-----------|----------|
| Temporary runtime directory under root | Not collected by pytest because `testpaths = tests`. |
| Missing test-only dependency in CI | Avoided by installing `requirements-dev.txt`. |

---

## 7. Security Considerations

- No production runtime behavior changes.
- CI installs only checked-in dependency manifests.

---

## 8. Test Plan

### 8.1 Test Scope

| Type | Target | Tool | Phase |
|------|--------|------|-------|
| L1 | Python test suite | pytest | Do/QA |
| L1 | JavaScript unit tests | Node test runner | Do/QA |
| L2 | CI workflow review | File inspection | Check |

### 8.2 L1 Test Scenarios

| # | Command | Expected |
|---|---------|----------|
| 1 | `python -m pytest -q` | All Python tests pass. |
| 2 | `node --test tests/js/*.test.js` | All JavaScript tests pass. |

### 8.3 L2/L3 Scenarios

Not applicable. This is a test tooling change with no UI or end-to-end user journey.

---

## 9. Clean Architecture

### 9.1 Layer Structure

This change belongs to project tooling and CI. It does not touch presentation, application, domain, or infrastructure application code.

### 9.2 Dependency Rules

No runtime dependency direction changes.

### 9.3 This Feature's Layer Assignment

| Component | Layer | Location |
|-----------|-------|----------|
| Pytest config | Tooling | `pytest.ini` |
| CI dependency install | CI/CD | `.github/workflows/test.yml` |

---

## 10. Coding Convention Reference

| Item | Convention Applied |
|------|-------------------|
| Config format | Standard INI for pytest |
| Workflow format | Existing GitHub Actions YAML style |
| Comments | None added; config is self-explanatory |

---

## 11. Implementation Guide

### 11.1 File Structure

```text
pytest.ini
.github/workflows/test.yml
```

### 11.2 Implementation Order

1. [x] Add `pytest.ini` with `testpaths = tests`.
2. [x] Add ignored generated/cache directories.
3. [x] Update CI to install `requirements-dev.txt`.
4. [x] Run Python tests.
5. [x] Run JavaScript tests.

### 11.3 Session Guide

| Module | Scope Key | Description | Estimated Turns |
|--------|-----------|-------------|:---------------:|
| Test config | `module-1` | Pytest collection policy | 1 |
| CI deps | `module-2` | Workflow dependency alignment | 1 |
| Verification | `module-3` | Local command execution | 1 |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-06-18 | Initial final design | Codex |

