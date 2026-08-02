"""Safe, explicit correction planning and immutable clip revision rendering."""

import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.common.config import Settings, get_settings
from app.content.models import MediaAsset
from app.corrections.models import ClipCorrectionAction, ClipCorrectionPlan, CorrectionPlanStatus
from app.ingestion.storage import LocalFilesystemStorage
from app.media_preview.service import ensure_clip_asset
from app.production.models import ProductionClip
from app.rendered_media.models import (
    RenderedMediaInspection,
    RenderedMediaInspectionIssue,
    RenderedMediaInspectionStatus,
)


class CorrectionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


SAFE_ACTIONS = {
    "TRIM_START", "TRIM_END", "REMOVE_OPENING_DEAD_AIR", "REMOVE_ENDING_DEAD_AIR",
    "NORMALIZE_AUDIO", "ADJUST_GAIN", "LIMIT_AUDIO_PEAKS", "RERENDER_WITH_EXISTING_SETTINGS",
    "REQUIRE_MANUAL_REVIEW", "REJECT_TECHNICAL_FAILURE",
}
TIMING_ACTIONS = {"TRIM_START", "TRIM_END", "REMOVE_OPENING_DEAD_AIR", "REMOVE_ENDING_DEAD_AIR"}
MAX_TIMING_SECONDS = 5.0
MAX_GAIN_DB = 6.0


def _now() -> datetime:
    return datetime.now(UTC)


def _asset(session: Session, clip: ProductionClip) -> MediaAsset | None:
    return session.scalar(select(MediaAsset).where(MediaAsset.clip_id == clip.id, MediaAsset.asset_type == "RENDERED_CLIP"))


def _bounded_number(value: object, default: float, maximum: float) -> float:
    if not isinstance(value, (int, float)):
        return default
    return min(maximum, float(value))


def _config(session: Session, clip: ProductionClip) -> dict[str, object]:
    from app.brands.models import ContentProfile

    profile = session.scalar(select(ContentProfile).where(ContentProfile.brand_id == clip.brand_id))
    raw = (profile.clip_correction_json if profile else {}) or {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "maximum_timing_adjustment_seconds": _bounded_number(raw.get("maximum_timing_adjustment_seconds"), 2.0, MAX_TIMING_SECONDS),
        "maximum_gain_db": _bounded_number(raw.get("maximum_gain_db"), 3.0, MAX_GAIN_DB),
        "rerun_inspection_automatically": bool(raw.get("rerun_inspection_automatically", True)),
        "maximum_revisions": max(1, int(_bounded_number(raw.get("maximum_revisions"), 3.0, 10.0))),
    }


def _suggestion(issue: RenderedMediaInspectionIssue, maximum_timing: float) -> tuple[str, dict[str, object], str]:
    kind = issue.issue_type.upper()
    if "OPEN" in kind and ("BLACK" in kind or "SILENCE" in kind or "DEAD" in kind):
        seconds = min(maximum_timing, max(0.1, issue.end_seconds or issue.start_seconds or 1.0))
        return "REMOVE_OPENING_DEAD_AIR", {"seconds": round(seconds, 2)}, "Remove a bounded non-content opening."
    if "END" in kind and ("BLACK" in kind or "SILENCE" in kind or "DEAD" in kind):
        seconds = min(maximum_timing, max(0.1, (issue.end_seconds or 0) - (issue.start_seconds or 0)))
        return "REMOVE_ENDING_DEAD_AIR", {"seconds": round(seconds, 2)}, "Remove a bounded non-content ending."
    if "CLIP" in kind or "PEAK" in kind:
        return "LIMIT_AUDIO_PEAKS", {"limit": 0.95}, "Limit peaks without changing speech content."
    if "AUDIO" in kind or "LOUD" in kind or "QUIET" in kind:
        return "NORMALIZE_AUDIO", {"target_lufs": -16.0}, "Normalize presentation loudness within the safe target."
    return "REQUIRE_MANUAL_REVIEW", {}, "No supported renderer control can safely correct this finding."


def create_plan(session: Session, actor_id: uuid.UUID, clip: ProductionClip) -> ClipCorrectionPlan:
    cfg = _config(session, clip)
    if not cfg["enabled"]:
        raise CorrectionError("WORKFLOW_DISABLED", "clip correction is disabled for this brand")
    inspection = session.scalar(select(RenderedMediaInspection).where(RenderedMediaInspection.clip_id == clip.id, RenderedMediaInspection.status == RenderedMediaInspectionStatus.COMPLETED).order_by(RenderedMediaInspection.inspection_version.desc()))
    if inspection is None:
        raise CorrectionError("INSPECTION_REQUIRED", "a completed media-quality inspection is required")
    existing = session.scalar(select(func.max(ClipCorrectionPlan.plan_version)).where(ClipCorrectionPlan.source_clip_id == clip.id)) or 0
    media_asset = _asset(session, clip)
    plan = ClipCorrectionPlan(brand_id=clip.brand_id, project_id=clip.project_id, source_clip_id=clip.id, source_media_asset_id=(media_asset.id if media_asset else None), source_inspection_id=inspection.id, plan_version=existing + 1, created_by_id=actor_id, summary="Operator review required before any revised render.", confidence=inspection.confidence, expected_score_improvement=0.0)
    session.add(plan)
    session.flush()
    issues = list(session.scalars(select(RenderedMediaInspectionIssue).where(RenderedMediaInspectionIssue.inspection_id == inspection.id).order_by(RenderedMediaInspectionIssue.created_at)))
    for order, issue in enumerate(issues, 1):
        maximum_timing = cfg["maximum_timing_adjustment_seconds"]
        action_type, proposed, reason = _suggestion(issue, maximum_timing if isinstance(maximum_timing, float) else 2.0)
        session.add(ClipCorrectionAction(plan_id=plan.id, originating_issue_id=issue.id, action_order=order, action_type=action_type, start_seconds=issue.start_seconds, end_seconds=issue.end_seconds, proposed_value=proposed, minimum_value={"seconds": 0.0}, maximum_value={"seconds": cfg["maximum_timing_adjustment_seconds"]}, reason=reason, evidence=issue.evidence_json, confidence=issue.confidence, operator_selected=action_type != "REQUIRE_MANUAL_REVIEW", renderer_parameters=proposed if action_type in SAFE_ACTIONS else {}))
    session.add(AuditEvent(actor_id=actor_id, brand_id=clip.brand_id, entity_type="clip_correction_plan", entity_id=plan.id, event_name="clip_correction.plan.created", payload={"clip_id": str(clip.id), "inspection_id": str(inspection.id)}))
    session.commit()
    return plan


def actions(session: Session, plan: ClipCorrectionPlan) -> list[ClipCorrectionAction]:
    return list(session.scalars(select(ClipCorrectionAction).where(ClipCorrectionAction.plan_id == plan.id).order_by(ClipCorrectionAction.action_order)))


def validate_plan(session: Session, plan: ClipCorrectionPlan) -> list[str]:
    selected = [item for item in actions(session, plan) if item.operator_selected]
    problems: list[str] = []
    names = {item.action_type for item in selected}
    if "TRIM_START" in names and "EXTEND_START" in names:
        problems.append("Choose either trim-start or extend-start, not both.")
    if "TRIM_END" in names and "EXTEND_END" in names:
        problems.append("Choose either trim-end or extend-end, not both.")
    for item in selected:
        if item.action_type not in SAFE_ACTIONS:
            problems.append(f"{item.action_type} is not supported by the current renderer.")
        seconds = item.proposed_value.get("seconds") if isinstance(item.proposed_value, dict) else None
        if seconds is not None and (not isinstance(seconds, (int, float)) or seconds < 0 or seconds > MAX_TIMING_SECONDS):
            problems.append(f"{item.action_type} is outside the permitted timing bound.")
        gain = item.proposed_value.get("gain_db") if isinstance(item.proposed_value, dict) else None
        if gain is not None and (not isinstance(gain, (int, float)) or abs(gain) > MAX_GAIN_DB):
            problems.append("Requested gain is outside the permitted range.")
    return problems


def set_action_selected(session: Session, actor_id: uuid.UUID, plan: ClipCorrectionPlan, action_id: uuid.UUID, selected: bool, expected_version: int) -> ClipCorrectionPlan:
    if plan.status != CorrectionPlanStatus.DRAFT or plan.review_version != expected_version:
        raise CorrectionError("STALE_PLAN", "plan is no longer editable; refresh and try again")
    item = session.get(ClipCorrectionAction, action_id)
    if item is None or item.plan_id != plan.id:
        raise CorrectionError("ACTION_NOT_FOUND", "correction action was not found")
    item.operator_selected = selected
    plan.review_version += 1
    session.add(AuditEvent(actor_id=actor_id, brand_id=plan.brand_id, entity_type="clip_correction_plan", entity_id=plan.id, event_name="clip_correction.action.selected", payload={"action": item.action_type, "selected": selected}))
    session.commit()
    return plan


def submit_for_confirmation(session: Session, actor_id: uuid.UUID, plan: ClipCorrectionPlan, expected_version: int) -> ClipCorrectionPlan:
    if plan.status != CorrectionPlanStatus.DRAFT or plan.review_version != expected_version:
        raise CorrectionError("STALE_PLAN", "plan is no longer a draft")
    conflicts = validate_plan(session, plan)
    if conflicts:
        raise CorrectionError("PLAN_CONFLICT", " ".join(conflicts))
    plan.status = CorrectionPlanStatus.AWAITING_CONFIRMATION
    plan.expected_review_version = plan.review_version
    plan.review_version += 1
    session.add(AuditEvent(actor_id=actor_id, brand_id=plan.brand_id, entity_type="clip_correction_plan", entity_id=plan.id, event_name="clip_correction.plan.submitted", payload={}))
    session.commit()
    return plan


def confirm_plan(session: Session, actor_id: uuid.UUID, plan: ClipCorrectionPlan, expected_version: int) -> ClipCorrectionPlan:
    if plan.status != CorrectionPlanStatus.AWAITING_CONFIRMATION or plan.review_version != expected_version:
        raise CorrectionError("CONFIRMATION_REQUIRED", "review the plan and explicitly confirm the current version")
    if validate_plan(session, plan):
        raise CorrectionError("PLAN_CONFLICT", "plan has unresolved conflicts")
    plan.status = CorrectionPlanStatus.QUEUED
    plan.approved_by_id, plan.approved_at, plan.review_version = actor_id, _now(), plan.review_version + 1
    session.add(AuditEvent(actor_id=actor_id, brand_id=plan.brand_id, entity_type="clip_correction_plan", entity_id=plan.id, event_name="clip_correction.plan.confirmed", payload={}))
    session.commit()
    return plan


def cancel_plan(session: Session, actor_id: uuid.UUID, plan: ClipCorrectionPlan, expected_version: int) -> ClipCorrectionPlan:
    if plan.status not in {CorrectionPlanStatus.DRAFT, CorrectionPlanStatus.AWAITING_CONFIRMATION, CorrectionPlanStatus.QUEUED} or plan.review_version != expected_version:
        raise CorrectionError("CANNOT_CANCEL", "only an unstarted current plan can be cancelled")
    plan.status, plan.review_version = CorrectionPlanStatus.CANCELLED, plan.review_version + 1
    session.add(AuditEvent(actor_id=actor_id, brand_id=plan.brand_id, entity_type="clip_correction_plan", entity_id=plan.id, event_name="clip_correction.plan.cancelled", payload={}))
    session.commit()
    return plan


def _render_command(settings: Settings, source: Path, output: Path, start: float, duration: float, selected: list[ClipCorrectionAction]) -> list[str]:
    audio_filters: list[str] = []
    for action in selected:
        if action.action_type == "NORMALIZE_AUDIO": audio_filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        elif action.action_type == "LIMIT_AUDIO_PEAKS": audio_filters.append("alimiter=limit=0.95")
        elif action.action_type == "ADJUST_GAIN":
            audio_filters.append(
                f"volume={_bounded_number(action.proposed_value.get('gain_db'), 0.0, MAX_GAIN_DB):.2f}dB"
            )
    command = [settings.ffmpeg_path, "-y", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source), "-map", "0:v:0", "-map", "0:a?", "-c:v", settings.output_video_codec, "-c:a", settings.output_audio_codec]
    if audio_filters: command.extend(["-af", ",".join(audio_filters)])
    return [*command, "-movflags", "+faststart", str(output)]


def render_confirmed_plan(session: Session, plan_id: uuid.UUID, storage: LocalFilesystemStorage, settings: Settings | None = None) -> ClipCorrectionPlan:
    settings = settings or get_settings()
    plan = session.get(ClipCorrectionPlan, plan_id)
    if plan is None: raise CorrectionError("PLAN_NOT_FOUND", "correction plan was not found")
    if plan.result_clip_id: return plan
    if plan.status != CorrectionPlanStatus.QUEUED: raise CorrectionError("PLAN_NOT_QUEUED", "plan has not been explicitly confirmed")
    source = session.get(ProductionClip, plan.source_clip_id)
    if source is None or not source.storage_key: raise CorrectionError("SOURCE_ASSET_MISSING", "source rendered media is unavailable")
    selected = [item for item in actions(session, plan) if item.operator_selected and item.action_type not in {"REQUIRE_MANUAL_REVIEW", "REJECT_TECHNICAL_FAILURE"}]
    if not selected: raise CorrectionError("NO_RENDER_ACTION", "select at least one supported correction")
    trim_start = sum(
        _bounded_number(item.proposed_value.get("seconds"), 0.0, MAX_TIMING_SECONDS)
        for item in selected
        if item.action_type in {"TRIM_START", "REMOVE_OPENING_DEAD_AIR"}
    )
    trim_end = sum(
        _bounded_number(item.proposed_value.get("seconds"), 0.0, MAX_TIMING_SECONDS)
        for item in selected
        if item.action_type in {"TRIM_END", "REMOVE_ENDING_DEAD_AIR"}
    )
    duration = source.duration_seconds - trim_start - trim_end
    if duration <= 1.0: raise CorrectionError("INVALID_TIMING", "correction would leave an invalid clip duration")
    root = Path(settings.video_work_root).resolve() / "corrections" / plan.id.hex
    root.mkdir(parents=True, exist_ok=True); input_path, output_path = root / "source.mp4", root / "revision.mp4"
    plan.status, plan.rendering_started_at = CorrectionPlanStatus.RENDERING, _now(); session.commit()
    try:
        with storage.open(source.storage_key) as source_file, input_path.open("wb") as target: shutil.copyfileobj(source_file, target)
        subprocess.run(_render_command(settings, input_path, output_path, trim_start, duration, selected), capture_output=True, text=True, timeout=max(60, int(duration * 10)), check=True)
        temporary = storage.create_temporary()
        with output_path.open("rb") as handle:
            while chunk := handle.read(262_144): storage.write_chunk(temporary, chunk)
        key = storage.finalize(temporary, ".mp4").key
        number = (session.scalar(select(func.max(ProductionClip.clip_number)).where(ProductionClip.project_id == source.project_id)) or 0) + 1
        root_id = source.root_clip_id or source.id
        revised = ProductionClip(project_id=source.project_id, brand_id=source.brand_id, clip_number=number, start_seconds=source.start_seconds + trim_start, end_seconds=source.end_seconds - trim_end, duration_seconds=duration, storage_key=key, render_status="SUCCEEDED", approval_status="PENDING", publication_status="NOT_QUEUED", root_clip_id=root_id, parent_clip_id=source.id, revision_number=source.revision_number + 1, correction_plan_id=plan.id, is_current_operator_selection=False)
        session.add(revised); session.flush()
        asset = ensure_clip_asset(session, revised, storage, settings); asset.administrative_hold = True
        plan.result_clip_id, plan.result_media_asset_id, plan.status, plan.rendering_completed_at = revised.id, asset.id, CorrectionPlanStatus.REINSPECTING, _now()
        plan.renderer_config_json = {"start_offset_seconds": trim_start, "end_offset_seconds": trim_end, "audio_actions": [item.action_type for item in selected if item.action_type not in TIMING_ACTIONS]}
        session.add(AuditEvent(actor_id=plan.approved_by_id, brand_id=plan.brand_id, entity_type="clip_correction_plan", entity_id=plan.id, event_name="clip_correction.rendered", payload={"revision_number": revised.revision_number}))
        session.commit()
        return plan
    except (OSError, subprocess.SubprocessError) as exc:
        plan.status, plan.failed_at, plan.failure_category = CorrectionPlanStatus.FAILED, _now(), "RENDER_FAILED"; session.commit(); raise CorrectionError("RENDER_FAILED", "corrective rendering failed safely") from exc
    finally:
        shutil.rmtree(root, ignore_errors=True)


def comparison(session: Session, plan: ClipCorrectionPlan) -> dict[str, object]:
    original = session.get(RenderedMediaInspection, plan.source_inspection_id)
    revised = session.scalar(select(RenderedMediaInspection).where(RenderedMediaInspection.clip_id == plan.result_clip_id, RenderedMediaInspection.status == RenderedMediaInspectionStatus.COMPLETED).order_by(RenderedMediaInspection.inspection_version.desc())) if plan.result_clip_id else None
    if original is None or revised is None:
        return {"outcome": "Inconclusive", "reason": "A completed inspection is required for both versions."}
    plan.result_inspection_id = revised.id
    old_issues = {issue.issue_type for issue in session.scalars(select(RenderedMediaInspectionIssue).where(RenderedMediaInspectionIssue.inspection_id == original.id))}
    new_issues = {issue.issue_type for issue in session.scalars(select(RenderedMediaInspectionIssue).where(RenderedMediaInspectionIssue.inspection_id == revised.id))}
    delta = (revised.overall_score or 0) - (original.overall_score or 0)
    outcome = "Improved" if delta > 1 else "Regressed" if delta < -1 else "Unchanged"
    result = {"outcome": outcome, "overall_score_change": round(delta, 2), "original_score": original.overall_score, "revised_score": revised.overall_score, "issues_resolved": sorted(old_issues - new_issues), "issues_remaining": sorted(old_issues & new_issues), "new_issues": sorted(new_issues - old_issues)}
    plan.comparison_json = result; plan.status = CorrectionPlanStatus.COMPLETED; session.commit(); return result


def select_revision(session: Session, actor_id: uuid.UUID, plan: ClipCorrectionPlan, use_revised: bool, expected_version: int) -> ProductionClip:
    if plan.review_version != expected_version or not plan.result_clip_id: raise CorrectionError("STALE_PLAN", "a current revised clip is required")
    original, revised = session.get(ProductionClip, plan.source_clip_id), session.get(ProductionClip, plan.result_clip_id)
    if original is None or revised is None: raise CorrectionError("REVISION_NOT_FOUND", "revision relationship is incomplete")
    chosen, other = (revised, original) if use_revised else (original, revised)
    chosen.is_current_operator_selection, other.is_current_operator_selection = True, False
    if use_revised:
        # Packages are tied to exact media revisions. Keep them historical but
        # prevent a package built for the original from accompanying this revision.
        from app.content_packages.models import ContentPackage, ContentPackageStatus

        original.superseded_by_clip_id = revised.id
        for package in session.scalars(
            select(ContentPackage).where(ContentPackage.clip_id == original.id)
        ):
            package.status = ContentPackageStatus.STALE
    plan.review_version += 1
    session.add(AuditEvent(actor_id=actor_id, brand_id=plan.brand_id, entity_type="clip_correction_plan", entity_id=plan.id, event_name="clip_correction.revision.selected", payload={"use_revised": use_revised}))
    session.commit(); return chosen
