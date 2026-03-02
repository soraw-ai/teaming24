---
name: debugging
description: "Systematically debug errors and failures: reproduce issues, trace root causes, and implement fixes. Use when user reports a bug, error, or unexpected behavior and needs help investigating."
license: Apache-2.0
metadata:
  author: Teaming24
  version: "1.0.0"
  category: coding
  tags: "debugging, troubleshooting, errors"
allowed-tools: shell_exec file_read file_edit python_exec
---

# Debugging Skill

## When to Use

- User reports a bug, error, or test failure
- User shares an error message or stack trace
- User mentions "debug", "fix", "broken", "not working", "error"

## Workflow

1. **Reproduce**: Run the failing command/test to observe the error.
2. **Gather context**: Read error messages, stack traces, and relevant logs.
3. **Hypothesize**: Form theories about the root cause based on the error type.
4. **Investigate**: Use `file_search` and `file_read` to examine suspect code.
5. **Fix**: Use `file_edit` to apply the fix.
6. **Verify**: Re-run the failing command to confirm the fix works.
7. **Regression check**: Run related tests to ensure nothing else broke.

## Common Patterns

### Import Errors
- Check if the module exists and is in PYTHONPATH
- Verify package is installed: `pip list | grep <package>`
- Check for circular imports

### Type Errors
- Verify function signatures match call sites
- Check for None values where objects are expected
- Look for API changes in updated dependencies

### Runtime Errors
- Examine the full stack trace bottom-up
- Check boundary conditions (empty lists, None, zero division)
- Verify external service connectivity

## Examples

- "My tests are failing with a TypeError" — reproduce, read traceback, find type mismatch
- "The API returns 500 on this endpoint" — check server logs, trace request handler

## Guidelines

- Always reproduce before fixing
- Fix the root cause, not the symptom
- Verify the fix doesn't introduce regressions
- Document the fix with a brief explanation
