# CI verification and local-only delivery

Date: 2026-10-28

GitHub Actions runs on pushes, pull requests, and manual dispatch. Pin actions to
verified release commit SHAs and grant only repository-content read access.
Pull requests receive no provider credentials or deployment secrets.

Fast jobs check the frontend build/types/lint/dependency audit, Python syntax and
undefined names, the MCP image build, workflow validity, and source secrets.
Integration then uses the real local infrastructure and existing HTTP/database
verification scripts. Deterministic governance inputs exercise approval boundaries;
they are not fabricated model responses or a substitute for paid-model validation.

The integration runner generates ephemeral credentials, masks them in GitHub logs,
owns a separate Compose project, and cleans up only its own services and volumes.
An explicit existing-stack mode permits local verification without deleting the
developer's data. It requires the AI worker to be stopped.

The project's local-only requirement remains in force. Delivery on v-prefixed tags
produces an artifact containing the verified commit's source, its commit identity,
and a SHA-256 checksum after all required checks pass. It does not deploy an app,
push images to a registry, or publish a public GitHub Release. Hosted-model tests
and the manual scenario catalog remain outside this key-free pipeline.

Action/tool releases were checked against their official GitHub release endpoints
on this date. The hosted workflow's first run still requires committing and pushing
these files; local verification does not represent a completed GitHub-hosted run.
