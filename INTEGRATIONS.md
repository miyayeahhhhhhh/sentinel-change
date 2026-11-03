# Integration roadmap — deferred

**This document is a future implementation guide, not a statement that integrations are active.** The base application intentionally does not initiate external connections for Sentinel-1, Dynamic World, JRC Global Surface Water, Neo4j, SMTP/email or Sarvam AI. Implement and validate these only after the base workflows and data governance are approved.

## Required acceptance sequence
1. Confirm source licensing, AOI provenance and expected data schemas.
2. Implement each connector behind a provider interface with timeouts and explicit unavailable/error states.
3. Record source name, retrieval time, spatial/temporal coverage, processing version and checksums for each evidence item.
4. Add mocked unit tests and integration tests that run only with explicit credentials.
5. Verify the connector against known reference cases before it contributes to any score.
6. Never interpret missing connector output as negative evidence.

## Planned integrations

| Integration | Intended responsibility | Acceptance requirement |
|---|---|---|
| Sentinel-1 | Real SAR observations and corroboration | Real acquisitions, orbit/polarisation metadata, calibration and coverage QC |
| Dynamic World | Water, built-up and bare-land probability changes | Real probability rasters aligned to the analysis grid; calibration before scoring |
| JRC Global Surface Water | Historical water occurrence and seasonality | Correctly label historical baseline; do not present it as a current observation |
| Neo4j | Relationships between districts, waterbodies, observations, cases and alerts | Idempotent graph sync; SQLite remains source of truth |
| Email | Evidence-backed notifications to responsible authorities | Explicit recipient configuration, report attachment, delivery log and retry policy |
| Sarvam AI | Telugu/English report and notification translation | Preserve canonical source text; store translation metadata and require review |
| Authentication | Admin, Analyst and Reviewer roles | Secure identity provider/password handling, server-side authorization and audit attribution; never rely on a UI-only role selector |

## Existing adapter code

Some optional adapter scaffolding is retained in `cs/evidence/` and `cs/integrations/`. It is not activated by the base UI, and its presence does not prove live service availability or successful external validation. Refer to `BUILD_STATUS.md` for the current release boundary.
