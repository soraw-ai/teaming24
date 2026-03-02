---
name: code-review
description: "Perform thorough code reviews: analyze code quality, security issues, best practices, and suggest improvements. Use when user asks for a code review, wants to check code quality, or asks to find bugs in code."
license: Apache-2.0
metadata:
  author: Teaming24
  version: "1.0.0"
  category: coding
  tags: "code-review, quality, security"
allowed-tools: shell_exec file_read file_search
---

# Code Review Skill

## When to Use

- User asks to review code, check code quality, or audit a codebase
- User wants to find bugs, security issues, or anti-patterns
- User mentions "code review", "review PR", "check my code"

## Workflow

1. **Understand scope**: Identify which files/modules are under review.
2. **Read the code**: Use `file_read` to examine each file.
3. **Search for patterns**: Use `file_search` to find anti-patterns, TODOs, security issues.
4. **Run linters**: Use `shell_exec` to run available linters (pylint, eslint, etc.).
5. **Produce report**: Summarize findings with severity levels (critical, warning, info).

## What to Check

- **Security**: SQL injection, XSS, hardcoded secrets, insecure deserialization
- **Performance**: N+1 queries, unnecessary loops, missing caches
- **Maintainability**: Code duplication, overly complex functions, missing types
- **Best Practices**: Error handling, logging, test coverage, documentation
- **Style**: Naming conventions, file organization, import order

## Examples

- "Review the authentication module for security issues" — focus on auth-related files, check for common vulnerabilities
- "Check this PR for code quality" — review changed files, compare against project standards

## Report Format

```
## Code Review Summary

### Critical Issues
- [file:line] Description of critical issue

### Warnings
- [file:line] Description of warning

### Suggestions
- [file:line] Improvement suggestion

### Positive Observations
- Good patterns observed
```
