# ViralForge Operator Workflow Redesign Report

## Outcome

The default Discord experience is now a guided creative workflow rather than an engineering control panel. The existing approval gates, audit trail, idempotency, recovery behavior, tenancy controls, and explicit publishing confirmation remain unchanged.

## Before and after

| Before | After |
| --- | --- |
| Control Center listed discovery, projects, analysis, opportunities, clips, and queue records. | `/home` opens one brand-aware operator screen answering what needs attention and what to do next. |
| Operators manually initiated download, analysis, and opportunity generation. | Accepting a source queues the safe processing chain; completed analysis queues suggestion generation. |
| Clip approval stopped at a queue record. | Clip approval also queues evidence-bound post-details generation; it does not publish. |
| Project cards exposed lifecycle states and provider data. | Guided project cards show a plain-language timeline, next decision, and progress. Advanced Details retains the original diagnostics. |
| Queue implied readiness even when publishing was not configured. | Content-ready cards state when no publishing account/configuration is available. |

## Default operator journey

1. Open `/home` or `/viralforge home`.
2. Select **Continue Working**. It opens the first human decision in this order: source review, suggested clip, finished clip, discovered video, then content-ready items.
3. Choose **Add Video** to submit a YouTube URL without memorizing a command.
4. Once a source is accepted, ViralForge downloads it, analyzes it, and prepares clip suggestions automatically.
5. Choosing a suggested clip renders it through the existing renderer.
6. Approving a finished clip queues post-details generation automatically. Approval still does not publish.
7. Review post details and use the existing explicit publishing flow only when a destination account and all safety gates are satisfied.

## Simplified interface

The home screen has six actions:

- Continue Working
- Find Videos
- Add Video
- Review
- Ready To Post
- More

Technical details, project lists, system readiness, and brand selection are available from **More**. The default cards use `recommended video`, `suggested clip`, `finished clip`, `post details`, and `content ready` rather than internal lifecycle vocabulary.

## Empty and blocked states

- No discovery sources: the active brand is named and the screen explains that an approved source must be added before discovery can run.
- Background processing: the screen says ViralForge is preparing the video and that no action is required yet.
- Publishing unavailable: content is described as ready, with the explicit explanation that no publishing account is connected/configured.
- Project errors: the guided view tells the operator to open More Details for the safe diagnostic reference.

## Safety boundaries retained

- Source acceptance is still the first human approval before download.
- Opportunity approval is still required before rendering.
- Clip approval is still required before post-details generation and queue eligibility.
- Content-package approval, rights, moderation, destination selection, and explicit publish/schedule confirmation are not bypassed.
- No automatic public upload is introduced.
- Worker handoffs use existing idempotent operations, so retry delivery cannot create duplicate processing or analysis records.

## Verification

- Focused Discord tests: passed.
- Ruff for changed Discord/worker modules: passed.
- Mypy for changed Discord/worker modules: passed.

## Deployment and visual verification

Deploy the bot and worker together, then open `/home` as an authorized operator in the BodycamsDailyHQ brand. Confirm the six-button home view, source acceptance handoff, background processing message, suggested-clip review, finished-clip approval, and content-ready blocked state when publishing is not configured. This repository session cannot capture authenticated Discord screenshots.
