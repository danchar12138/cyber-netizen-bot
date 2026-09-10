"""拟人评测集、自动回放和匿名人工盲评的内存及 PostgreSQL 仓储。"""

import asyncio
import secrets
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cnb_application import (
    EvaluationCaseDraft,
    EvaluationConflictError,
    EvaluationNotFoundError,
)
from cnb_domain import (
    BlindReview,
    BlindReviewAssignment,
    BlindReviewPreference,
    BlindReviewScore,
    EvaluationCaseDefinition,
    EvaluationCaseRunResult,
    EvaluationCheck,
    EvaluationComparison,
    EvaluationComparisonEntry,
    EvaluationComparisonStatus,
    EvaluationReport,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationSuiteDefinition,
    EvaluationSuiteStatus,
)
from cnb_infrastructure.models import (
    AuditLog,
    BlindReviewAssignmentModel,
    BlindReviewModel,
    EvaluationCaseModel,
    EvaluationCaseResultModel,
    EvaluationComparisonEntryModel,
    EvaluationComparisonModel,
    EvaluationRunModel,
    EvaluationSuiteModel,
)


class MemoryEvaluationRepository:
    """测试与无数据库联调使用的并发安全评测仓储。"""

    def __init__(self) -> None:
        self._suites: dict[UUID, EvaluationSuiteDefinition] = {}
        self._runs: dict[UUID, EvaluationRun] = {}
        self._comparisons: dict[UUID, EvaluationComparison] = {}
        self._assignments: dict[UUID, BlindReviewAssignment] = {}
        self._reviews: dict[UUID, BlindReview] = {}
        self._lock = asyncio.Lock()

    async def list_suites(
        self, *, tenant_id: UUID, agent_id: UUID
    ) -> tuple[EvaluationSuiteDefinition, ...]:
        async with self._lock:
            rows = (
                item
                for item in self._suites.values()
                if item.tenant_id == tenant_id and item.agent_id == agent_id
            )
            return tuple(sorted(rows, key=lambda item: (item.key, item.version), reverse=True))

    async def create_suite(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        key: str,
        name: str,
        description: str | None,
        minimum_pass_rate: float,
        max_output_tokens: int,
        cases: tuple[EvaluationCaseDraft, ...],
        actor_id: UUID,
    ) -> EvaluationSuiteDefinition:
        async with self._lock:
            version = 1 + max(
                (
                    item.version
                    for item in self._suites.values()
                    if item.tenant_id == tenant_id and item.agent_id == agent_id and item.key == key
                ),
                default=0,
            )
            suite_id = uuid4()
            now = datetime.now(UTC)
            suite = EvaluationSuiteDefinition(
                id=suite_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                key=key,
                name=name,
                version=version,
                status=EvaluationSuiteStatus.DRAFT,
                description=description,
                minimum_pass_rate=minimum_pass_rate,
                max_output_tokens=max_output_tokens,
                cases=tuple(
                    EvaluationCaseDefinition(
                        id=uuid4(),
                        suite_id=suite_id,
                        case_key=item.case_key.strip(),
                        category=item.category.strip(),
                        input_text=item.input_text.strip(),
                        expected_action=item.expected_action,
                        reference_response=(
                            item.reference_response.strip() if item.reference_response else None
                        ),
                        required_phrases=tuple(phrase.strip() for phrase in item.required_phrases),
                        forbidden_phrases=tuple(
                            phrase.strip() for phrase in item.forbidden_phrases
                        ),
                        sort_order=index,
                    )
                    for index, item in enumerate(cases, start=1)
                ),
                created_by=actor_id,
                created_at=now,
                published_at=None,
            )
            self._suites[suite.id] = suite
            return suite

    async def publish_suite(
        self,
        *,
        suite_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
    ) -> EvaluationSuiteDefinition:
        del actor_id
        async with self._lock:
            suite = self._owned_suite(suite_id, tenant_id, agent_id)
            if suite.status is not EvaluationSuiteStatus.DRAFT:
                raise EvaluationConflictError("只有草稿评测集可以发布")
            for item_id, item in tuple(self._suites.items()):
                if (
                    item.tenant_id == tenant_id
                    and item.agent_id == agent_id
                    and item.key == suite.key
                    and item.status is EvaluationSuiteStatus.PUBLISHED
                ):
                    self._suites[item_id] = replace(item, status=EvaluationSuiteStatus.SUPERSEDED)
            published = replace(
                suite,
                status=EvaluationSuiteStatus.PUBLISHED,
                published_at=datetime.now(UTC),
            )
            self._suites[published.id] = published
            return published

    async def get_suite(
        self, *, suite_id: UUID, tenant_id: UUID, agent_id: UUID
    ) -> EvaluationSuiteDefinition | None:
        async with self._lock:
            suite = self._suites.get(suite_id)
            if suite is None or suite.tenant_id != tenant_id or suite.agent_id != agent_id:
                return None
            return suite

    async def save_run(self, run: EvaluationRun) -> EvaluationRun:
        async with self._lock:
            self._runs[run.id] = run
            return run

    async def save_comparison(self, comparison: EvaluationComparison) -> EvaluationComparison:
        async with self._lock:
            if comparison.id in self._comparisons:
                raise EvaluationConflictError("模型对比实验已经存在")
            if len(comparison.entries) < 2:
                raise EvaluationConflictError("模型对比实验至少需要两个候选运行")
            if any(
                entry.run.tenant_id != comparison.tenant_id
                or entry.run.agent_id != comparison.agent_id
                for entry in comparison.entries
            ):
                raise EvaluationConflictError("模型对比候选运行不属于实验作用域")
            run_ids = {entry.run.id for entry in comparison.entries}
            if len(run_ids) != len(comparison.entries) or run_ids & self._runs.keys():
                raise EvaluationConflictError("模型对比候选运行已经存在或发生重复")
            for entry in comparison.entries:
                self._runs[entry.run.id] = entry.run
            self._comparisons[comparison.id] = comparison
            return comparison

    async def list_comparisons(
        self, *, tenant_id: UUID, agent_id: UUID, limit: int
    ) -> tuple[EvaluationComparison, ...]:
        async with self._lock:
            rows = (
                item
                for item in self._comparisons.values()
                if item.tenant_id == tenant_id and item.agent_id == agent_id
            )
            return tuple(
                replace(
                    comparison,
                    entries=tuple(
                        replace(entry, run=replace(entry.run, results=()))
                        for entry in comparison.entries
                    ),
                )
                for comparison in sorted(rows, key=lambda item: item.created_at, reverse=True)[
                    :limit
                ]
            )

    async def get_comparison(
        self, *, comparison_id: UUID, tenant_id: UUID, agent_id: UUID
    ) -> EvaluationComparison | None:
        async with self._lock:
            comparison = self._comparisons.get(comparison_id)
            if (
                comparison is None
                or comparison.tenant_id != tenant_id
                or comparison.agent_id != agent_id
            ):
                return None
            return comparison

    async def list_runs(
        self, *, tenant_id: UUID, agent_id: UUID, limit: int
    ) -> tuple[EvaluationRun, ...]:
        async with self._lock:
            rows = (
                item
                for item in self._runs.values()
                if item.tenant_id == tenant_id and item.agent_id == agent_id
            )
            return tuple(sorted(rows, key=lambda item: item.created_at, reverse=True)[:limit])

    async def get_run(
        self, *, run_id: UUID, tenant_id: UUID, agent_id: UUID
    ) -> EvaluationRun | None:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.tenant_id != tenant_id or run.agent_id != agent_id:
                return None
            return run

    async def claim_blind_assignment(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        reviewer_id: UUID,
        run_id: UUID | None,
    ) -> BlindReviewAssignment | None:
        async with self._lock:
            reviewed_assignments = {item.assignment_id for item in self._reviews.values()}
            existing = next(
                (
                    item
                    for item in self._assignments.values()
                    if item.tenant_id == tenant_id
                    and item.agent_id == agent_id
                    and item.reviewer_id == reviewer_id
                    and item.id not in reviewed_assignments
                    and (run_id is None or item.run_id == run_id)
                ),
                None,
            )
            if existing:
                return existing
            assigned_results = {
                item.result_id
                for item in self._assignments.values()
                if item.reviewer_id == reviewer_id
            }
            runs = sorted(
                (
                    item
                    for item in self._runs.values()
                    if item.tenant_id == tenant_id
                    and item.agent_id == agent_id
                    and (run_id is None or item.id == run_id)
                ),
                key=lambda item: item.created_at,
                reverse=True,
            )
            result = next(
                (
                    item
                    for run in runs
                    for item in run.results
                    if item.id not in assigned_results
                    and bool((item.candidate_response or "").strip())
                    and bool((item.reference_response or "").strip())
                ),
                None,
            )
            if result is None:
                return None
            candidate_is_a = bool(secrets.randbits(1))
            candidate = cast(str, result.candidate_response)
            reference = cast(str, result.reference_response)
            assignment = BlindReviewAssignment(
                id=uuid4(),
                tenant_id=tenant_id,
                agent_id=agent_id,
                run_id=result.run_id,
                result_id=result.id,
                reviewer_id=reviewer_id,
                case_key=result.case_key,
                category=result.category,
                input_text=result.input_text,
                response_a=candidate if candidate_is_a else reference,
                response_b=reference if candidate_is_a else candidate,
                candidate_is_a=candidate_is_a,
                created_at=datetime.now(UTC),
            )
            self._assignments[assignment.id] = assignment
            return assignment

    async def get_blind_assignment(
        self,
        *,
        assignment_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        reviewer_id: UUID,
    ) -> BlindReviewAssignment | None:
        async with self._lock:
            item = self._assignments.get(assignment_id)
            if (
                item is None
                or item.tenant_id != tenant_id
                or item.agent_id != agent_id
                or item.reviewer_id != reviewer_id
            ):
                return None
            return item

    async def save_blind_review(self, review: BlindReview) -> BlindReview:
        async with self._lock:
            if any(item.assignment_id == review.assignment_id for item in self._reviews.values()):
                raise EvaluationConflictError("该盲评任务已经提交")
            self._reviews[review.id] = review
            return review

    async def get_report(
        self, *, tenant_id: UUID, agent_id: UUID, reviewer_id: UUID
    ) -> EvaluationReport:
        async with self._lock:
            runs = sorted(
                (
                    item
                    for item in self._runs.values()
                    if item.tenant_id == tenant_id and item.agent_id == agent_id
                ),
                key=lambda item: item.created_at,
                reverse=True,
            )
            reviews = tuple(
                item
                for item in self._reviews.values()
                if item.tenant_id == tenant_id and item.agent_id == agent_id
            )
            reviewed_result_ids = {
                item.result_id for item in reviews if item.reviewer_id == reviewer_id
            }
            eligible_result_ids = {
                result.id
                for run in runs
                for result in run.results
                if bool((result.candidate_response or "").strip())
                and bool((result.reference_response or "").strip())
            }
            return self.summarize(
                runs=runs,
                reviews=reviews,
                pending_reviews=len(eligible_result_ids - reviewed_result_ids),
            )

    def _owned_suite(
        self, suite_id: UUID, tenant_id: UUID, agent_id: UUID
    ) -> EvaluationSuiteDefinition:
        suite = self._suites.get(suite_id)
        if suite is None or suite.tenant_id != tenant_id or suite.agent_id != agent_id:
            raise EvaluationNotFoundError(f"评测集不存在：{suite_id}")
        return suite

    @staticmethod
    def summarize(
        *, runs: list[EvaluationRun], reviews: tuple[BlindReview, ...], pending_reviews: int
    ) -> EvaluationReport:
        candidate_scores = [item.candidate_score.average for item in reviews]
        reference_scores = [item.reference_score.average for item in reviews]
        return EvaluationReport(
            total_runs=len(runs),
            gate_passed_runs=sum(item.gate_passed for item in runs),
            latest_pass_rate=runs[0].pass_rate if runs else None,
            pending_reviews=pending_reviews,
            completed_reviews=len(reviews),
            candidate_wins=sum(
                item.preference is BlindReviewPreference.CANDIDATE for item in reviews
            ),
            reference_wins=sum(
                item.preference is BlindReviewPreference.REFERENCE for item in reviews
            ),
            ties=sum(item.preference is BlindReviewPreference.TIE for item in reviews),
            candidate_average_score=(
                sum(candidate_scores) / len(candidate_scores) if candidate_scores else None
            ),
            reference_average_score=(
                sum(reference_scores) / len(reference_scores) if reference_scores else None
            ),
        )


class SqlAlchemyEvaluationRepository:
    """使用 PostgreSQL 提供租户隔离、不可变评测与盲评去盲。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_suites(
        self, *, tenant_id: UUID, agent_id: UUID
    ) -> tuple[EvaluationSuiteDefinition, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(EvaluationSuiteModel)
                    .where(
                        EvaluationSuiteModel.tenant_id == tenant_id,
                        EvaluationSuiteModel.agent_id == agent_id,
                    )
                    .order_by(EvaluationSuiteModel.key, EvaluationSuiteModel.version.desc())
                )
            ).all()
            return tuple([await self._suite(session, row) for row in rows])

    async def create_suite(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        key: str,
        name: str,
        description: str | None,
        minimum_pass_rate: float,
        max_output_tokens: int,
        cases: tuple[EvaluationCaseDraft, ...],
        actor_id: UUID,
    ) -> EvaluationSuiteDefinition:
        async with self._session_factory() as session, session.begin():
            latest = await session.scalar(
                select(func.max(EvaluationSuiteModel.version)).where(
                    EvaluationSuiteModel.tenant_id == tenant_id,
                    EvaluationSuiteModel.agent_id == agent_id,
                    EvaluationSuiteModel.key == key,
                )
            )
            row = EvaluationSuiteModel(
                id=uuid4(),
                tenant_id=tenant_id,
                agent_id=agent_id,
                key=key,
                name=name,
                version=(latest or 0) + 1,
                status=EvaluationSuiteStatus.DRAFT.value,
                description=description,
                minimum_pass_rate=minimum_pass_rate,
                max_output_tokens=max_output_tokens,
                created_by=actor_id,
                created_at=datetime.now(UTC),
            )
            session.add(row)
            case_rows = [
                EvaluationCaseModel(
                    id=uuid4(),
                    suite_id=row.id,
                    case_key=item.case_key.strip(),
                    category=item.category.strip(),
                    input_text=item.input_text.strip(),
                    expected_action=item.expected_action,
                    reference_response=(
                        item.reference_response.strip() if item.reference_response else None
                    ),
                    required_phrases=[phrase.strip() for phrase in item.required_phrases],
                    forbidden_phrases=[phrase.strip() for phrase in item.forbidden_phrases],
                    sort_order=index,
                )
                for index, item in enumerate(cases, start=1)
            ]
            session.add_all(case_rows)
            self._audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="evaluation.suite_created",
                resource_id=row.id,
                detail={"key": key, "version": row.version, "case_count": len(cases)},
            )
            await session.flush()
            return self._suite_from_rows(row, case_rows)

    async def publish_suite(
        self,
        *,
        suite_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        actor_id: UUID,
    ) -> EvaluationSuiteDefinition:
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(EvaluationSuiteModel).where(
                    EvaluationSuiteModel.id == suite_id,
                    EvaluationSuiteModel.tenant_id == tenant_id,
                    EvaluationSuiteModel.agent_id == agent_id,
                )
            )
            if row is None:
                raise EvaluationNotFoundError(f"评测集不存在：{suite_id}")
            if row.status != EvaluationSuiteStatus.DRAFT.value:
                raise EvaluationConflictError("只有草稿评测集可以发布")
            await session.execute(
                update(EvaluationSuiteModel)
                .where(
                    EvaluationSuiteModel.tenant_id == tenant_id,
                    EvaluationSuiteModel.agent_id == agent_id,
                    EvaluationSuiteModel.key == row.key,
                    EvaluationSuiteModel.status == EvaluationSuiteStatus.PUBLISHED.value,
                )
                .values(status=EvaluationSuiteStatus.SUPERSEDED.value)
            )
            row.status = EvaluationSuiteStatus.PUBLISHED.value
            row.published_at = datetime.now(UTC)
            self._audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="evaluation.suite_published",
                resource_id=row.id,
                detail={"key": row.key, "version": row.version},
            )
            await session.flush()
            return await self._suite(session, row)

    async def get_suite(
        self, *, suite_id: UUID, tenant_id: UUID, agent_id: UUID
    ) -> EvaluationSuiteDefinition | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(EvaluationSuiteModel).where(
                    EvaluationSuiteModel.id == suite_id,
                    EvaluationSuiteModel.tenant_id == tenant_id,
                    EvaluationSuiteModel.agent_id == agent_id,
                )
            )
            return await self._suite(session, row) if row else None

    async def save_run(self, run: EvaluationRun) -> EvaluationRun:
        async with self._session_factory() as session, session.begin():
            await self._add_run(session, run)
            self._audit(
                session,
                tenant_id=run.tenant_id,
                actor_id=run.created_by,
                action="evaluation.run_completed",
                resource_id=run.id,
                detail={
                    "suite_key": run.suite_key,
                    "suite_version": run.suite_version,
                    "passed": run.passed,
                    "total": run.total,
                    "gate_passed": run.gate_passed,
                    "provider": run.provider,
                    "model": run.model,
                },
            )
        return run

    async def save_comparison(self, comparison: EvaluationComparison) -> EvaluationComparison:
        async with self._session_factory() as session, session.begin():
            session.add(
                EvaluationComparisonModel(
                    id=comparison.id,
                    tenant_id=comparison.tenant_id,
                    agent_id=comparison.agent_id,
                    suite_id=comparison.suite_id,
                    suite_key=comparison.suite_key,
                    suite_version=comparison.suite_version,
                    suite_name=comparison.suite_name,
                    status=comparison.status.value,
                    configuration_version=comparison.configuration_version,
                    persona_version=comparison.persona_version,
                    prompt_version=comparison.prompt_version,
                    policy_version=comparison.policy_version,
                    model_route_version=comparison.model_route_version,
                    created_by=comparison.created_by,
                    created_at=comparison.created_at,
                    completed_at=comparison.completed_at,
                )
            )
            for entry in comparison.entries:
                await self._add_run(session, entry.run)
                session.add(
                    EvaluationComparisonEntryModel(
                        comparison_id=comparison.id,
                        run_id=entry.run.id,
                        position=entry.position,
                        profile_key=entry.profile_key,
                        profile_version=entry.profile_version,
                    )
                )
            self._audit(
                session,
                tenant_id=comparison.tenant_id,
                actor_id=comparison.created_by,
                action="evaluation.comparison_completed",
                resource_id=comparison.id,
                detail={
                    "suite_key": comparison.suite_key,
                    "suite_version": comparison.suite_version,
                    "candidate_count": len(comparison.entries),
                    "model_route_version": comparison.model_route_version,
                },
            )
        return comparison

    async def list_comparisons(
        self, *, tenant_id: UUID, agent_id: UUID, limit: int
    ) -> tuple[EvaluationComparison, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(EvaluationComparisonModel)
                    .where(
                        EvaluationComparisonModel.tenant_id == tenant_id,
                        EvaluationComparisonModel.agent_id == agent_id,
                    )
                    .order_by(EvaluationComparisonModel.created_at.desc())
                    .limit(limit)
                )
            ).all()
            return tuple(
                [await self._comparison(session, row, include_results=False) for row in rows]
            )

    async def get_comparison(
        self, *, comparison_id: UUID, tenant_id: UUID, agent_id: UUID
    ) -> EvaluationComparison | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(EvaluationComparisonModel).where(
                    EvaluationComparisonModel.id == comparison_id,
                    EvaluationComparisonModel.tenant_id == tenant_id,
                    EvaluationComparisonModel.agent_id == agent_id,
                )
            )
            if row is None:
                return None
            return await self._comparison(session, row, include_results=True)

    async def list_runs(
        self, *, tenant_id: UUID, agent_id: UUID, limit: int
    ) -> tuple[EvaluationRun, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(EvaluationRunModel)
                    .where(
                        EvaluationRunModel.tenant_id == tenant_id,
                        EvaluationRunModel.agent_id == agent_id,
                    )
                    .order_by(EvaluationRunModel.created_at.desc())
                    .limit(limit)
                )
            ).all()
            return tuple(self._run_from_row(row, ()) for row in rows)

    async def get_run(
        self, *, run_id: UUID, tenant_id: UUID, agent_id: UUID
    ) -> EvaluationRun | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(EvaluationRunModel).where(
                    EvaluationRunModel.id == run_id,
                    EvaluationRunModel.tenant_id == tenant_id,
                    EvaluationRunModel.agent_id == agent_id,
                )
            )
            if row is None:
                return None
            results = (
                await session.scalars(
                    select(EvaluationCaseResultModel)
                    .where(EvaluationCaseResultModel.run_id == row.id)
                    .order_by(EvaluationCaseResultModel.case_key)
                )
            ).all()
            return self._run_from_row(row, results)

    async def claim_blind_assignment(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        reviewer_id: UUID,
        run_id: UUID | None,
    ) -> BlindReviewAssignment | None:
        async with self._session_factory() as session, session.begin():
            existing_statement = (
                select(BlindReviewAssignmentModel, EvaluationCaseResultModel)
                .join(
                    EvaluationCaseResultModel,
                    EvaluationCaseResultModel.id == BlindReviewAssignmentModel.result_id,
                )
                .outerjoin(
                    BlindReviewModel,
                    BlindReviewModel.assignment_id == BlindReviewAssignmentModel.id,
                )
                .where(
                    BlindReviewAssignmentModel.tenant_id == tenant_id,
                    BlindReviewAssignmentModel.agent_id == agent_id,
                    BlindReviewAssignmentModel.reviewer_id == reviewer_id,
                    BlindReviewModel.id.is_(None),
                )
                .order_by(BlindReviewAssignmentModel.created_at)
                .limit(1)
            )
            if run_id is not None:
                existing_statement = existing_statement.where(
                    BlindReviewAssignmentModel.run_id == run_id
                )
            existing = (await session.execute(existing_statement)).first()
            if existing:
                return self._assignment(existing[0], existing[1])

            assignment_alias = BlindReviewAssignmentModel
            statement = (
                select(EvaluationCaseResultModel, EvaluationRunModel)
                .join(EvaluationRunModel, EvaluationRunModel.id == EvaluationCaseResultModel.run_id)
                .outerjoin(
                    assignment_alias,
                    and_(
                        assignment_alias.result_id == EvaluationCaseResultModel.id,
                        assignment_alias.reviewer_id == reviewer_id,
                    ),
                )
                .where(
                    EvaluationRunModel.tenant_id == tenant_id,
                    EvaluationRunModel.agent_id == agent_id,
                    EvaluationCaseResultModel.candidate_response.is_not(None),
                    EvaluationCaseResultModel.candidate_response != "",
                    EvaluationCaseResultModel.reference_response.is_not(None),
                    EvaluationCaseResultModel.reference_response != "",
                    assignment_alias.id.is_(None),
                )
                .order_by(EvaluationRunModel.created_at.desc(), EvaluationCaseResultModel.case_key)
                .limit(1)
                .with_for_update(of=EvaluationCaseResultModel, skip_locked=True)
            )
            if run_id is not None:
                statement = statement.where(EvaluationRunModel.id == run_id)
            selected = (await session.execute(statement)).first()
            if selected is None:
                return None
            result = selected[0]
            row = BlindReviewAssignmentModel(
                id=uuid4(),
                tenant_id=tenant_id,
                agent_id=agent_id,
                run_id=result.run_id,
                result_id=result.id,
                reviewer_id=reviewer_id,
                candidate_is_a=bool(secrets.randbits(1)),
                created_at=datetime.now(UTC),
            )
            session.add(row)
            await session.flush()
            return self._assignment(row, result)

    async def get_blind_assignment(
        self,
        *,
        assignment_id: UUID,
        tenant_id: UUID,
        agent_id: UUID,
        reviewer_id: UUID,
    ) -> BlindReviewAssignment | None:
        async with self._session_factory() as session:
            selected = (
                await session.execute(
                    select(BlindReviewAssignmentModel, EvaluationCaseResultModel)
                    .join(
                        EvaluationCaseResultModel,
                        EvaluationCaseResultModel.id == BlindReviewAssignmentModel.result_id,
                    )
                    .where(
                        BlindReviewAssignmentModel.id == assignment_id,
                        BlindReviewAssignmentModel.tenant_id == tenant_id,
                        BlindReviewAssignmentModel.agent_id == agent_id,
                        BlindReviewAssignmentModel.reviewer_id == reviewer_id,
                    )
                )
            ).first()
            return self._assignment(selected[0], selected[1]) if selected else None

    async def save_blind_review(self, review: BlindReview) -> BlindReview:
        async with self._session_factory() as session, session.begin():
            duplicate = await session.scalar(
                select(BlindReviewModel.id).where(
                    BlindReviewModel.assignment_id == review.assignment_id
                )
            )
            if duplicate is not None:
                raise EvaluationConflictError("该盲评任务已经提交")
            session.add(
                BlindReviewModel(
                    id=review.id,
                    tenant_id=review.tenant_id,
                    agent_id=review.agent_id,
                    assignment_id=review.assignment_id,
                    run_id=review.run_id,
                    result_id=review.result_id,
                    reviewer_id=review.reviewer_id,
                    preference=review.preference.value,
                    candidate_score=self._score_payload(review.candidate_score),
                    reference_score=self._score_payload(review.reference_score),
                    note=review.note,
                    created_at=review.created_at,
                )
            )
            self._audit(
                session,
                tenant_id=review.tenant_id,
                actor_id=review.reviewer_id,
                action="evaluation.blind_review_submitted",
                resource_id=review.assignment_id,
                detail={"preference": review.preference.value},
            )
        return review

    async def get_report(
        self, *, tenant_id: UUID, agent_id: UUID, reviewer_id: UUID
    ) -> EvaluationReport:
        async with self._session_factory() as session:
            run_rows = (
                await session.scalars(
                    select(EvaluationRunModel)
                    .where(
                        EvaluationRunModel.tenant_id == tenant_id,
                        EvaluationRunModel.agent_id == agent_id,
                    )
                    .order_by(EvaluationRunModel.created_at.desc())
                )
            ).all()
            review_rows = (
                await session.scalars(
                    select(BlindReviewModel).where(
                        BlindReviewModel.tenant_id == tenant_id,
                        BlindReviewModel.agent_id == agent_id,
                    )
                )
            ).all()
            eligible_ids = set(
                (
                    await session.scalars(
                        select(EvaluationCaseResultModel.id)
                        .join(
                            EvaluationRunModel,
                            EvaluationRunModel.id == EvaluationCaseResultModel.run_id,
                        )
                        .where(
                            EvaluationRunModel.tenant_id == tenant_id,
                            EvaluationRunModel.agent_id == agent_id,
                            EvaluationCaseResultModel.candidate_response.is_not(None),
                            EvaluationCaseResultModel.candidate_response != "",
                            EvaluationCaseResultModel.reference_response.is_not(None),
                            EvaluationCaseResultModel.reference_response != "",
                        )
                    )
                ).all()
            )
            reviewed_ids = {row.result_id for row in review_rows if row.reviewer_id == reviewer_id}
            reviews = tuple(self._review(row) for row in review_rows)
            runs = [self._run_from_row(row, ()) for row in run_rows]
            return MemoryEvaluationRepository.summarize(
                runs=runs,
                reviews=reviews,
                pending_reviews=len(eligible_ids - reviewed_ids),
            )

    @staticmethod
    async def _add_run(session: AsyncSession, run: EvaluationRun) -> None:
        """在调用方事务内写入一次完整回放，不单独提交。"""
        session.add(
            EvaluationRunModel(
                id=run.id,
                tenant_id=run.tenant_id,
                agent_id=run.agent_id,
                suite_id=run.suite_id,
                suite_key=run.suite_key,
                suite_version=run.suite_version,
                suite_name=run.suite_name,
                status=run.status.value,
                passed=run.passed,
                total=run.total,
                pass_rate=run.pass_rate,
                gate_passed=run.gate_passed,
                minimum_pass_rate=run.minimum_pass_rate,
                configuration_version=run.configuration_version,
                persona_version=run.persona_version,
                prompt_version=run.prompt_version,
                policy_version=run.policy_version,
                model_route_version=run.model_route_version,
                provider=run.provider,
                model=run.model,
                input_tokens=run.input_tokens,
                output_tokens=run.output_tokens,
                estimated_cost_microusd=run.estimated_cost_microusd,
                error_code=run.error_code,
                created_by=run.created_by,
                created_at=run.created_at,
                completed_at=run.completed_at,
            )
        )
        # 未声明 ORM relationship 时，先落运行主记录再写用例外键；仍处于同一事务。
        await session.flush()
        session.add_all(
            EvaluationCaseResultModel(
                id=item.id,
                run_id=run.id,
                case_key=item.case_key,
                category=item.category,
                input_text=item.input_text,
                expected_action=item.expected_action,
                actual_action=item.actual_action,
                candidate_response=item.candidate_response,
                reference_response=item.reference_response,
                passed=item.passed,
                checks=[
                    {"key": check.key, "passed": check.passed, "detail": check.detail}
                    for check in item.checks
                ],
                summary=item.summary,
                latency_ms=item.latency_ms,
            )
            for item in run.results
        )

    async def _comparison(
        self,
        session: AsyncSession,
        row: EvaluationComparisonModel,
        *,
        include_results: bool,
    ) -> EvaluationComparison:
        selected = (
            await session.execute(
                select(EvaluationComparisonEntryModel, EvaluationRunModel)
                .join(
                    EvaluationRunModel,
                    EvaluationRunModel.id == EvaluationComparisonEntryModel.run_id,
                )
                .where(EvaluationComparisonEntryModel.comparison_id == row.id)
                .order_by(EvaluationComparisonEntryModel.position)
            )
        ).all()
        results_by_run: dict[UUID, list[EvaluationCaseResultModel]] = {}
        if include_results:
            run_ids = tuple(run.id for _, run in selected)
            if run_ids:
                result_rows = (
                    await session.scalars(
                        select(EvaluationCaseResultModel)
                        .where(EvaluationCaseResultModel.run_id.in_(run_ids))
                        .order_by(
                            EvaluationCaseResultModel.run_id,
                            EvaluationCaseResultModel.case_key,
                        )
                    )
                ).all()
                for result in result_rows:
                    results_by_run.setdefault(result.run_id, []).append(result)
        return EvaluationComparison(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            suite_id=row.suite_id,
            suite_key=row.suite_key,
            suite_version=row.suite_version,
            suite_name=row.suite_name,
            status=EvaluationComparisonStatus(row.status),
            configuration_version=row.configuration_version,
            persona_version=row.persona_version,
            prompt_version=row.prompt_version,
            policy_version=row.policy_version,
            model_route_version=row.model_route_version,
            entries=tuple(
                EvaluationComparisonEntry(
                    position=entry.position,
                    profile_key=entry.profile_key,
                    profile_version=entry.profile_version,
                    run=self._run_from_row(run, results_by_run.get(run.id, ())),
                )
                for entry, run in selected
            ),
            created_by=row.created_by,
            created_at=row.created_at,
            completed_at=row.completed_at,
        )

    async def _suite(
        self, session: AsyncSession, row: EvaluationSuiteModel
    ) -> EvaluationSuiteDefinition:
        cases = (
            await session.scalars(
                select(EvaluationCaseModel)
                .where(EvaluationCaseModel.suite_id == row.id)
                .order_by(EvaluationCaseModel.sort_order)
            )
        ).all()
        return self._suite_from_rows(row, cases)

    @staticmethod
    def _suite_from_rows(
        row: EvaluationSuiteModel, cases: Sequence[EvaluationCaseModel]
    ) -> EvaluationSuiteDefinition:
        return EvaluationSuiteDefinition(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            key=row.key,
            name=row.name,
            version=row.version,
            status=EvaluationSuiteStatus(row.status),
            description=row.description,
            minimum_pass_rate=row.minimum_pass_rate,
            max_output_tokens=row.max_output_tokens,
            cases=tuple(
                EvaluationCaseDefinition(
                    id=item.id,
                    suite_id=item.suite_id,
                    case_key=item.case_key,
                    category=item.category,
                    input_text=item.input_text,
                    expected_action=item.expected_action,
                    reference_response=item.reference_response,
                    required_phrases=tuple(item.required_phrases),
                    forbidden_phrases=tuple(item.forbidden_phrases),
                    sort_order=item.sort_order,
                )
                for item in cases
            ),
            created_by=row.created_by,
            created_at=row.created_at,
            published_at=row.published_at,
        )

    @staticmethod
    def _run_from_row(
        row: EvaluationRunModel, results: Sequence[EvaluationCaseResultModel]
    ) -> EvaluationRun:
        return EvaluationRun(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            suite_id=row.suite_id,
            suite_key=row.suite_key,
            suite_version=row.suite_version,
            suite_name=row.suite_name,
            status=EvaluationRunStatus(row.status),
            passed=row.passed,
            total=row.total,
            pass_rate=row.pass_rate,
            gate_passed=row.gate_passed,
            minimum_pass_rate=row.minimum_pass_rate,
            configuration_version=row.configuration_version,
            persona_version=row.persona_version,
            prompt_version=row.prompt_version,
            policy_version=row.policy_version,
            model_route_version=row.model_route_version,
            provider=row.provider,
            model=row.model,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            estimated_cost_microusd=row.estimated_cost_microusd,
            error_code=row.error_code,
            results=tuple(
                EvaluationCaseRunResult(
                    id=item.id,
                    run_id=item.run_id,
                    case_key=item.case_key,
                    category=item.category,
                    input_text=item.input_text,
                    expected_action=item.expected_action,
                    actual_action=item.actual_action,
                    candidate_response=item.candidate_response,
                    reference_response=item.reference_response,
                    passed=item.passed,
                    checks=tuple(
                        EvaluationCheck(
                            key=cast(str, check["key"]),
                            passed=cast(bool, check["passed"]),
                            detail=cast(str, check["detail"]),
                        )
                        for check in item.checks
                    ),
                    summary=item.summary,
                    latency_ms=item.latency_ms,
                )
                for item in results
            ),
            created_by=row.created_by,
            created_at=row.created_at,
            completed_at=row.completed_at,
        )

    @staticmethod
    def _assignment(
        row: BlindReviewAssignmentModel, result: EvaluationCaseResultModel
    ) -> BlindReviewAssignment:
        candidate = cast(str, result.candidate_response)
        reference = cast(str, result.reference_response)
        return BlindReviewAssignment(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            run_id=row.run_id,
            result_id=row.result_id,
            reviewer_id=row.reviewer_id,
            case_key=result.case_key,
            category=result.category,
            input_text=result.input_text,
            response_a=candidate if row.candidate_is_a else reference,
            response_b=reference if row.candidate_is_a else candidate,
            candidate_is_a=row.candidate_is_a,
            created_at=row.created_at,
        )

    @classmethod
    def _review(cls, row: BlindReviewModel) -> BlindReview:
        return BlindReview(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            assignment_id=row.assignment_id,
            run_id=row.run_id,
            result_id=row.result_id,
            reviewer_id=row.reviewer_id,
            preference=BlindReviewPreference(row.preference),
            candidate_score=cls._score(row.candidate_score),
            reference_score=cls._score(row.reference_score),
            note=row.note,
            created_at=row.created_at,
        )

    @staticmethod
    def _score(payload: dict[str, int]) -> BlindReviewScore:
        return BlindReviewScore(
            persona_consistency=payload["persona_consistency"],
            naturalness=payload["naturalness"],
            empathy=payload["empathy"],
            boundary_respect=payload["boundary_respect"],
        )

    @staticmethod
    def _score_payload(score: BlindReviewScore) -> dict[str, int]:
        return {
            "persona_consistency": score.persona_consistency,
            "naturalness": score.naturalness,
            "empathy": score.empathy,
            "boundary_respect": score.boundary_respect,
        }

    @staticmethod
    def _audit(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        action: str,
        resource_id: UUID,
        detail: dict[str, object],
    ) -> None:
        session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=action,
                resource_type="evaluation",
                resource_id=str(resource_id),
                detail=detail,
            )
        )
