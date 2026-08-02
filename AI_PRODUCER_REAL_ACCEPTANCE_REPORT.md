# AI Producer real acceptance report

Status: pending a real BodycamsDailyHQ VPS acceptance run. No synthetic project or fabricated outcome is used.

## Full production-container verification

After deployment, run:

```bash
cd /root/ViralForge
export VIRALFORGE_PRODUCTION_ENV_FILE=.env.ip-bootstrap
export VIRALFORGE_IP_BOOTSTRAP_ENV_FILE=.env.ip-bootstrap
compose() { docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ip-bootstrap.yml --env-file .env.ip-bootstrap "$@"; }

compose run --rm api python -m pytest -q
compose run --rm api python -m ruff check .
compose run --rm api python -m mypy app
compose run --rm api python scripts/schema_drift.py /tmp/ai-producer-full-schema.db
compose run --rm migrate alembic check
```

`tests/test_analysis.py` is intentionally included. Record failures separately from dependency deprecation warnings.

## Select the real test project

```bash
compose exec -T postgres psql -U viralforge -d viralforge -P pager=off -c "
select p.id as project_id, b.name as brand, p.status, p.source_title, c.id as clip_id,
       c.approval_status as clip_approval, c.render_status, cp.status as package_status
from production_projects p
join brands b on b.id = p.brand_id
join production_clips c on c.project_id = p.id
left join lateral (
  select status from content_packages where clip_id = c.id order by generation_version desc limit 1
) cp on true
where b.name = 'BodycamsDailyHQ'
order by p.created_at desc, c.clip_number
limit 20;"
```

Choose one row with a real rendered/approved clip and approved package, then paste its values into the next command. It reads the actual project evidence and creates advisory records only; it never advances production or publishing.

```bash
PROJECT_ID='PASTE_PROJECT_UUID'
CLIP_ID='PASTE_CLIP_UUID'
compose exec -T api python - "$PROJECT_ID" "$CLIP_ID" <<'PY'
import sys
from sqlalchemy import select
from app.common.db import get_session
from app.production.models import ProductionProject, ProductionClip, ProductionSource
from app.analysis.models import VideoAnalysis, AnalysisStatus, TranscriptSegment, AnalysisEvent
from app.opportunities.models import ClipOpportunity, OpportunityReviewStatus
from app.content_packages.models import ContentPackage, ContentPackageStatus
from app.producer.models import ProducerRecommendation, ClipQualityReport
from app.producer.service import generate_project_recommendations, generate_clip_quality_report, generate_clip_recommendations

project_id, clip_id = sys.argv[1:]
session = next(get_session())
try:
    project = session.get(ProductionProject, project_id)
    clip = session.get(ProductionClip, clip_id)
    assert project and clip and project.brand_id == clip.brand_id, 'project/clip brand mismatch or missing'
    source = session.get(ProductionSource, project.selected_source_id) if project.selected_source_id else None
    analysis = session.scalar(select(VideoAnalysis).where(VideoAnalysis.project_id == project.id, VideoAnalysis.status == AnalysisStatus.COMPLETED).order_by(VideoAnalysis.created_at.desc()))
    opportunity = session.scalar(select(ClipOpportunity).where(ClipOpportunity.generated_clip_id == clip.id, ClipOpportunity.review_status == OpportunityReviewStatus.APPROVED))
    package = session.scalar(select(ContentPackage).where(ContentPackage.clip_id == clip.id, ContentPackage.status == ContentPackageStatus.APPROVED).order_by(ContentPackage.generation_version.desc()))
    assert source and analysis and opportunity and package, 'required real acceptance evidence is incomplete'
    assert session.scalar(select(TranscriptSegment.id).where(TranscriptSegment.analysis_id == analysis.id).limit(1))
    assert session.scalar(select(AnalysisEvent.id).where(AnalysisEvent.analysis_id == analysis.id).limit(1))
    advice = generate_project_recommendations(session, None, project)
    report = generate_clip_quality_report(session, None, clip)
    clip_advice = generate_clip_recommendations(session, None, clip)
    all_advice = list(session.scalars(select(ProducerRecommendation).where(ProducerRecommendation.project_id == project.id).order_by(ProducerRecommendation.created_at)))
    print({'brand_id_match': all(row.brand_id == project.brand_id for row in all_advice), 'project_advice': len(advice), 'clip_advice': len(clip_advice), 'quality_report_id': str(report.id), 'report_version': report.report_version})
    for row in all_advice:
        print({'type': row.recommendation_type, 'status': row.status, 'confidence': row.confidence, 'recommendation': row.recommendation_json.get('recommendation'), 'evidence_count': len(row.evidence_json)})
finally:
    session.close()
PY
```

## Operator acceptance worksheet

In Discord select Project → **Producer Advice**, then Finished Clip → **Quality Report**. For each recommendation, record the recommendation, confidence band, strongest evidence, agreement/disagreement, note, and approve/reject decision. Approval/rejection must not change project, metadata, queue, or publishing state.

For the quality report, record a 1–5 operator score for hook, pacing, context, subtitle coverage, title, caption, hashtags, and overall readiness. Treat retention as a prediction only; it is not measured performance.

For a final analytics comparison, only use an existing official snapshot or an operator-imported snapshot. Confirm the comparison record is stored and that no production setting changes.

