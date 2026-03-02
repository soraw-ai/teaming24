# ID Generation

This project now uses centralized helpers for ID generation to avoid
inconsistent formats and duplicated random logic.

## Teaming24 Backend (Python)

- Canonical module: `teaming24/utils/ids.py`
- Core helpers:
  - `uuid_str()`
  - `random_hex(length)`
  - `prefixed_id(prefix, length, separator)`
- Rule:
  - New backend ID generation should use this module only.
  - Do not call `uuid.uuid4()` directly in feature modules.

## Teaming24 Frontend (TypeScript)

- Canonical module: `teaming24/gui/src/utils/ids.ts`
- Core helpers:
  - `randomHex(length)`
  - `prefixedId(prefix, length, separator)`
  - `generateTempId(prefix)`
- Rule:
  - UI/transient IDs (toast, ws request, message, step, log) should use this module.
  - Avoid local `Math.random()` snippets in stores/components.

## AgentaNet Central Backend

- Canonical module: `agentanet_central/backend/app/id_utils.py`
- Core helpers:
  - `new_id()`
  - `random_hex(length)`
  - `prefixed_id(prefix, length, separator)`
- Rule:
  - SQLAlchemy model IDs and service-generated IDs should be created via this module.

## Notes

- Security tokens are separate from entity IDs and should continue to use
  cryptographic token APIs (`secrets.token_hex`) where appropriate.
- Existing persisted IDs are backward-compatible; this policy only changes
  how new IDs are generated.
