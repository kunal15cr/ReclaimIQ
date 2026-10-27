# Human review, refund authorization, and trace access

Date: 2026-10-27

The governing requirements are in INSTRUCTIONS.md: a global emergency stop,
human QA review, independent appeals, human-only refund authorization, authenticated
case access, and state changes committed with their audit records.

Category overrides now inherit the global kill switch. Missing global settings
fail closed. QA-selected approvals are escalated before finalization; an agreement
sample is recorded only after a human decides. Unanswered information requests
stay outside the worker queue; an answer and its new outbox event commit together.

Auto-approval leaves settlement pending until a reviewer authorizes it through
the dashboard. The settlement job only selects returns with a recorded human
approver. Governance updates and settlements use the same database transaction as
their audit entries.

Appeal resolution locks the parent return before the appeal, rejects a repeated
resolution, enforces reviewer exclusion, and requires an explicit denial
confirmation. Ordinary decision actions cannot bypass an open appeal.

Trace endpoints enforce ownership or a staff role. Browser WebSockets use an
opaque, case-scoped ticket, issued after authentication, valid for 60 seconds and
consumed once in Redis. This keeps provider bearer tokens out of browser code.
The Auth.js public session omits provider tokens; server-only code decrypts the
HttpOnly session cookie when forwarding an authenticated backend request.

Live AI calls and the manual scenario catalog require separate verification when
provider credentials are available. This record does not certify those checks.
