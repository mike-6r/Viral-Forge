# ViralForge remaining defects and follow-ups

Date: 2026-08-02

## P2: violent-source content-package warning may be empty

The accepted body-camera clip visibly includes a firearm and has a violence-related source title, but its generated local-template content package had zero persisted warnings because the source-quality warning list was empty. The package was otherwise evidence-bound and human-approved.

This is a content-safety quality gap, not a publishing escape: the package remains behind an explicit human publishing decision and no publish request was created. A focused future hardening pass should derive conservative review warnings from persisted analysis evidence and/or explicit operator classification, without inventing facts.

## P3: ephemeral Discord cards are restart-sensitive

Old ephemeral cards cannot be resumed after a Discord bot restart. A fresh `/viralforge project <project_id>` command successfully recovered the project in this acceptance run.

## Preserved VPS-local items

The deploy preserved the pre-existing executable-bit change on `scripts/production/deploy-ip-bootstrap.sh`, protected environment files, the VPS build directory, and unrelated untracked VPS-local files. They were not committed or removed by this acceptance pass.
