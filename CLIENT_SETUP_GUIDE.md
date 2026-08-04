# ViralForge Client Setup Guide

## Live-readiness status

The redesigned Discord experience is implemented locally and ready to deploy.
It has not yet been confirmed as commercially complete in the live Discord
server. The currently configured local bot credential is invalid, so a safe
live `/setup` run could not connect to Discord. The existing live server also
still shows legacy/demo clutter that must be reviewed by the guild owner.

Do not present the live Discord as client-ready until the deployment checklist
below is complete and the final screenshot-based visual QA has passed.

## Quick Start

After the guild owner has completed the live deployment checklist:

1. Open `#welcome` and read the short platform overview.
2. Select **Set Up Access** and accept the community standards.
3. In `#access`, choose your account type and optional notifications.
4. Select **Continue Onboarding** to begin workspace setup.
5. Use `#workspace-guide` for the brand-scoped review workflow, or select
   **Open Ticket** in `#support` for private help.

ViralForge is a review-first content operations workspace. It prepares
authorized source material and recommendations, but a person makes every
publishing decision.

## What is already implemented locally

- Distinct, dark SaaS-style assets and concise panels for access, overview,
  workflow, plans, workspace, review, analytics, support, and Operations.
- The final Discord-native hero banner set is installed for welcome, access,
  announcements, workflow, plans, workspace, review, analytics, support,
  Operations, ready-for-decision, and private-ticket panels. The copy is kept
  short so the images carry the visual identity.
- Major landing pages place the hero banner before the compact information card,
  so the Discord journey reads like a product experience rather than a document.
- A rules-first access path, public support entry point, member-only workspace
  area, staff-only Operations area, and private ticket flow.
- Human-readable workflow labels: **Source Added**, **Preparing Video**,
  **Ready for Review**, **Clips Ready**, **Content Package Ready**, **Ready for
  Publishing Decision**, **Published**, and **Needs Attention**.
- A non-destructive `/setup` cleanup preview for detected legacy/demo channels.
  It recommends cleanup but never deletes without separate owner approval.

## What is blocked in live Discord

- The local Discord bot credential returns `401 Unauthorized`, preventing a
  safe connection to the real guild for final setup and visual verification.
- The real guild still has visible legacy/demo items, including the prior
  `YOUTUBE STUFF`, `#test`, `#review`, and older start-area layout observed
  during QA.
- The final page, permission, mobile, ticket, and workflow-card visual checks
  must be performed after the current bot revision is deployed and `/setup`
  completes successfully.

## Set up access

1. In `#welcome`, accept the community standards.
2. In `#access`, choose the account type that best describes you: Creator,
   Brand, or Agency.
3. Choose only the optional notifications you want.
4. Select **Continue Onboarding** to begin setting up a workspace, or **Open
   Support** for a private request.

Choosing an account type or notification role never grants staff access or
publishing authority.

## Use a workspace

After rules acceptance, use the **WORKSPACES** category:

- `#workspace-guide` explains brand-scoped sources, review, rules, and analytics.
- `#review-and-publish` explains each approval gate.
- `#analytics` explains which official performance signals are available.

The normal workflow is:

1. Add an authorized source video.
2. Let ViralForge prepare and analyze it.
3. Review suggested clips.
4. Review the finished clip and content package.
5. Make an explicit human decision when content is ready to post.

**Ready for Decision** does not upload, schedule, or publish content
by itself. It tells you the review gates are ready for a human decision.

## Get help

`#support` is public and safe to read. Select **Open Ticket** to create a
private request for account access, workspace setup, source/video issues,
publishing questions, plans, bugs, or custom workflows.

Never put credentials, passwords, private media, private source files, or tokens
in a public channel.

## Required live deployment and action checklist

The guild owner or designated administrator must complete this sequence:

- [ ] Rotate or replace the Discord bot token in the Discord Developer Portal.
- [ ] Update the protected deployed bot secret; never place the token in Git,
  Discord, screenshots, or public configuration files.
- [ ] Restart the deployed Discord bot runtime and verify it logs in without a
  `401 Unauthorized` error.
- [ ] Run `/setup apply_changes: False` to inspect the planned managed resources.
- [ ] Run `/setup apply_changes: True` as the guild owner to apply or repair the
  managed layout.
- [ ] Review the cleanup preview returned by `/setup`.
- [ ] Run `/setup-reset apply_changes: False` if the preview indicates a broader
  legacy cleanup is needed.
- [ ] Remove old demo/test clutter only after the guild owner approves each
  candidate.
- [ ] Complete the screenshot-based visual QA in
  `DISCORD_VISUAL_QA_CHECKLIST.md` on desktop and mobile.

Only after every item passes can the live Discord be described as client-ready.
