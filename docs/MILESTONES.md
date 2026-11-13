# Petrix AI delivery milestones

Each milestone joins frontend behaviour to the matching backend functionality before moving on.

| Milestone | Scope |
| --- | --- |
| 1 | New project, passwordless authentication, independent registration, personal workspace, Owner membership, onboarding, dashboard shell |
| 2 — implemented | Default agents; create/import/export/delete; personas, languages, identity, voice preferences; permissions, capacity, versions, change log, network, and optional real model-connected tester |
| 3 — implemented | Workflow builder, trigger/action trees, insertion and ordering, enable/disable, Send message, manual translations, quick replies, and event-driven tester |
| 3.1 — implemented | Permanent step numbers and layered fuzzy/semantic/Groq intent recognition with ambiguity handling, clarification, entity/context results, and tester diagnostics |
| 4 — in progress | Set variable, Jump, Ask survey, Branch, Send API, SQL Query, Switch Language, Send Attachment, Show Carousel, Collect Feedback, Close Chat, and LLM Processing are implemented with editors and runtime handlers. Close Chat invokes Chat Closed once; feedback provides Rating/Survey/Settings, persisted answer variables, safe post-submission continuation, and channel previews in the tester. LLM Processing uses Groq with text/Python prompts, response variables, outcome paths, and enabled per-agent Knowledge text sources. Remaining actions include graph, handoff, ticket, and other data actions. |
| 5 — text sources implemented | Per-agent text Knowledge supports LLM Processing. Uploads, URLs, processing status, and retrieval-backed answers remain future work. |
| 6 | Channel publishing: webchat first, then WhatsApp and other required channels |
| 7 | Livechats, inbox, teams, routing, human handoff, tickets, and people |
| 8 | Analytics, moderation, billing integration, production deployment, and extended governance |

Advisor, Perception, live voice processing, and Co-pilot need separate feature requirements before their implementation. Voice preferences, configuration versions, and audit history are already part of Milestone 2. The screenshot establishes their navigation position; it does not define their full backend behaviour.

Milestone 1 visual direction: a dark signup layout with a product illustration and authentication panel; a dark dashboard with left navigation, central agent tabs, and a right conversation-testing panel. Petrix AI has its own name, logo mark, and warm accent colour.
