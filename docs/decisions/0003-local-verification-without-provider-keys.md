# Local operation and verification without provider keys

Date: 2026-10-27

The current Windows machine has Python, Node, and Docker Desktop, but no GNU Make.
The user deferred paid provider credentials and requested continuing local checks.

Use a standard-library Python task runner for local startup, migrations, seeding,
verification, and the existing full-stack operations. The local task stops the AI
worker, starts the real infrastructure and API, waits for health, migrates, and
seeds the actual catalog and photos. It does not replace an agent with a canned
response. Returns stay pending until a real worker is enabled later.

Split the worker Dockerfile into a lightweight base and the existing ML runtime.
The base image can exercise the actual governance gate, audit transactions, and
settlement job against real Postgres, Redis, Vault, MailHog, and OPA. Explicit input
fixtures test deterministic approval boundaries; they do not validate a model or
represent an end-to-end AI review. The separate Compose verification file keeps
these checks out of normal runtime services.

OPA outages now deny tool and model access. An offline grant table is useful for
setup and diagnostics, but cannot authorize a call while the policy engine is
unavailable. Tool errors propagate to the bounded retry/dead-letter path instead
of silently reducing the evidence used by later agents.

Full model, gateway, detector, tracing, outage-fallback, load, and manual scenario
verification remain separate requirements for completion.

Bind published services to IPv4 loopback. Host-side API and identity verification
use 127.0.0.1 to avoid Windows IPv6 connection delays, while Keycloak advertises
the canonical public issuer http://localhost:8081 for all tokens and browser
flows. The issuer must remain stable regardless of the address used to connect.
