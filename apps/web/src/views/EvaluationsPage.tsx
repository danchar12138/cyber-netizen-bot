import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle2,
  CircleX,
  Download,
  Eye,
  FileCheck2,
  FlaskConical,
  Plus,
  Rocket,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import {
  claimBlindReviewAssignment,
  createEvaluationDecision,
  createEvaluationSuite,
  downloadEvaluationDecision,
  getAdminSession,
  getEvaluationComparison,
  getEvaluationComparisons,
  getEvaluationComparisonTargets,
  getEvaluationDecision,
  getEvaluationDecisions,
  getEvaluationQualityHistory,
  getUnifiedQualityOverview,
  getEvaluationRun,
  getEvaluationRuns,
  getEvaluationSuites,
  publishEvaluationSuite,
  runEvaluation,
  runEvaluationComparison,
  submitBlindReview,
  type BlindReviewAssignment,
  type BlindReviewScore,
  type EvaluationCaseDefinition,
  type EvaluationDecisionReasonCode,
  type EvaluationDecisionResult,
  type EvaluationSuiteDraft,
  type EvaluationVersionSnapshot,
} from '../api'
import { useSelectedAgentId } from '../agentSelection'
import { cognitiveActionLabels, displayLabel } from '../displayLabels'
import { invalidateAcrossTabs } from '../tabSync'

const defaultCases: Array<Omit<EvaluationCaseDefinition, 'id' | 'sort_order'>> = [
  {
    case_key: 'natural-weekend',
    category: '自然度',
    input_text: '周末不知道做什么，你说呢？',
    expected_action: 'reply',
    reference_response: '先看你更想充电还是找点新鲜感。告诉我你现在的状态，我们一起挑一个不费劲的安排。',
    required_phrases: [],
    forbidden_phrases: ['作为一个人工智能'],
  },
  {
    case_key: 'respect-silence',
    category: '关系边界',
    input_text: '我想静一静，不用回复',
    expected_action: 'no_reply',
    reference_response: null,
    required_phrases: [],
    forbidden_phrases: [],
  },
]

const neutralScore = (): BlindReviewScore => ({
  persona_consistency: 3,
  naturalness: 3,
  empathy: 3,
  boundary_respect: 3,
})

function scoreLabel(value: number) {
  return ['不可用', '很差', '较差', '一般', '良好', '优秀'][value]
}

function formatCost(microusd: number) {
  return `$${(microusd / 1_000_000).toFixed(6)}`
}

const qualityCoverageLabels = {
  complete: '证据完整',
  evaluation_only: '仅有拟人评测',
  operations_only: '仅有告警运营',
  empty: '暂无质量证据',
} as const

const qualityHistoryWindows = [
  { days: 7, windowMinutes: 10_080, bucketMinutes: 1_440 },
  { days: 30, windowMinutes: 43_200, bucketMinutes: 1_440 },
  { days: 90, windowMinutes: 129_600, bucketMinutes: 1_440 },
] as const

function formatQualityDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
  }).format(new Date(value))
}

function formatQualityDateTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function formatQualityWindow(minutes: number) {
  if (minutes % 1_440 === 0) return `近 ${minutes / 1_440} 天`
  if (minutes % 60 === 0) return `近 ${minutes / 60} 小时`
  return `近 ${minutes} 分钟`
}

function formatQualityDelta(value: number | null | undefined, suffix = '') {
  if (value == null) return '样本不足'
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}${suffix}`
}

const decisionOutcomeLabels: Record<EvaluationDecisionResult, string> = {
  adopt_candidate: '采纳候选',
  keep_baseline: '保持基线',
  wait_for_evidence: '等待更多证据',
}

const decisionReasonLabels: Record<EvaluationDecisionReasonCode, string> = {
  quality_gain: '质量提升',
  regression_risk: '回归风险',
  insufficient_evidence: '证据不足',
  manual_review: '人工复核',
}

function snapshotKey(snapshot: EvaluationVersionSnapshot) {
  return [
    snapshot.suite_key,
    snapshot.suite_version,
    snapshot.configuration_version,
    snapshot.persona_version,
    snapshot.prompt_version,
    snapshot.policy_version,
    snapshot.model_route_version,
    snapshot.provider,
    snapshot.model,
  ].join(':')
}

function snapshotLabel(snapshot: EvaluationVersionSnapshot) {
  return `${snapshot.suite_key} v${snapshot.suite_version} · ${snapshot.provider}/${snapshot.model} · 配置 ${snapshot.configuration_version} / 人格 ${snapshot.persona_version} / 提示词 ${snapshot.prompt_version} / 策略 ${snapshot.policy_version} / 路由 ${snapshot.model_route_version}`
}

function isSameDecisionSource(left: EvaluationVersionSnapshot, right: EvaluationVersionSnapshot) {
  return left.suite_key === right.suite_key
    && left.suite_version === right.suite_version
    && left.provider === right.provider
    && left.model === right.model
}

function ScoreEditor({
  label,
  value,
  onChange,
}: {
  label: string
  value: BlindReviewScore
  onChange: (value: BlindReviewScore) => void
}) {
  const fields: Array<[keyof BlindReviewScore, string]> = [
    ['persona_consistency', '人格一致'],
    ['naturalness', '表达自然'],
    ['empathy', '共情理解'],
    ['boundary_respect', '边界尊重'],
  ]
  return <fieldset className="blind-score-editor">
    <legend>{label} 多维评分</legend>
    {fields.map(([key, text]) => <label key={key}>
      <span>{text}</span>
      <input
        aria-label={`${label} ${text}`}
        type="range"
        min="1"
        max="5"
        step="1"
        value={value[key]}
        onChange={(event) => onChange({ ...value, [key]: Number(event.target.value) })}
      />
      <output>{value[key]} · {scoreLabel(value[key])}</output>
    </label>)}
  </fieldset>
}

export function EvaluationsPage() {
  const queryClient = useQueryClient()
  const selectedAgentId = useSelectedAgentId()
  const [editing, setEditing] = useState(false)
  const [selectedSuite, setSelectedSuite] = useState('')
  const [selectedRun, setSelectedRun] = useState<string | null>(null)
  const [selectedProfileKeys, setSelectedProfileKeys] = useState<string[]>([])
  const [selectedComparison, setSelectedComparison] = useState<string | null>(null)
  const [qualityWindowDays, setQualityWindowDays] = useState<7 | 30 | 90>(30)
  const [decisionCandidateKey, setDecisionCandidateKey] = useState('')
  const [decisionBaselineKey, setDecisionBaselineKey] = useState('')
  const [decisionOutcome, setDecisionOutcome] = useState<EvaluationDecisionResult>('wait_for_evidence')
  const [decisionReason, setDecisionReason] = useState<EvaluationDecisionReasonCode>('insufficient_evidence')
  const [selectedDecision, setSelectedDecision] = useState<string | null>(null)
  const [decisionDownloadNotice, setDecisionDownloadNotice] = useState('')
  const [assignment, setAssignment] = useState<BlindReviewAssignment | null>(null)
  const [scoreA, setScoreA] = useState(neutralScore)
  const [scoreB, setScoreB] = useState(neutralScore)
  const [preference, setPreference] = useState<'a' | 'b' | 'tie'>('tie')
  const [reviewNote, setReviewNote] = useState('')
  const [suiteKey, setSuiteKey] = useState('anthropomorphic-regression')
  const [suiteName, setSuiteName] = useState('拟人表现回归集')
  const [suiteDescription, setSuiteDescription] = useState('用于候选版本自动回放与匿名 A/B 盲评。')
  const [minimumPassRate, setMinimumPassRate] = useState(100)
  const [maxOutputTokens, setMaxOutputTokens] = useState(512)
  const [casesText, setCasesText] = useState(JSON.stringify(defaultCases, null, 2))

  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const suites = useQuery({ queryKey: ['evaluation-suites', selectedAgentId], queryFn: getEvaluationSuites })
  const runs = useQuery({ queryKey: ['evaluation-runs', selectedAgentId], queryFn: getEvaluationRuns })
  const quality = useQuery({
    queryKey: ['evaluation-quality-overview', selectedAgentId, 10_080],
    queryFn: () => getUnifiedQualityOverview(10_080),
  })
  const qualityWindow = qualityHistoryWindows.find((item) => item.days === qualityWindowDays)
    ?? qualityHistoryWindows[1]
  const qualityHistory = useQuery({
    queryKey: [
      'evaluation-quality-history',
      selectedAgentId,
      qualityWindow.windowMinutes,
      qualityWindow.bucketMinutes,
    ],
    queryFn: () => getEvaluationQualityHistory(
      qualityWindow.windowMinutes,
      qualityWindow.bucketMinutes,
    ),
  })
  const comparisonTargets = useQuery({
    queryKey: ['evaluation-comparison-targets', selectedAgentId],
    queryFn: getEvaluationComparisonTargets,
  })
  const comparisons = useQuery({
    queryKey: ['evaluation-comparisons', selectedAgentId],
    queryFn: getEvaluationComparisons,
  })
  const decisions = useQuery({
    queryKey: ['evaluation-decisions', selectedAgentId],
    queryFn: getEvaluationDecisions,
  })
  const runDetail = useQuery({
    queryKey: ['evaluation-run', selectedAgentId, selectedRun],
    queryFn: () => getEvaluationRun(selectedRun ?? ''),
    enabled: Boolean(selectedRun),
  })
  const comparisonDetail = useQuery({
    queryKey: ['evaluation-comparison', selectedAgentId, selectedComparison],
    queryFn: () => getEvaluationComparison(selectedComparison ?? ''),
    enabled: Boolean(selectedComparison),
  })
  const decisionDetail = useQuery({
    queryKey: ['evaluation-decision', selectedAgentId, selectedDecision],
    queryFn: () => getEvaluationDecision(selectedDecision ?? ''),
    enabled: Boolean(selectedDecision),
  })

  const candidateVersion = useMemo(
    () => qualityHistory.data?.versions.find(
      (version) => snapshotKey(version.snapshot) === decisionCandidateKey,
    ),
    [decisionCandidateKey, qualityHistory.data?.versions],
  )
  const eligibleBaselines = useMemo(() => {
    if (!candidateVersion) return []
    return (qualityHistory.data?.versions ?? []).filter((version) => (
      snapshotKey(version.snapshot) !== decisionCandidateKey
      && isSameDecisionSource(version.snapshot, candidateVersion.snapshot)
    ))
  }, [candidateVersion, decisionCandidateKey, qualityHistory.data?.versions])
  const baselineVersion = eligibleBaselines.find(
    (version) => snapshotKey(version.snapshot) === decisionBaselineKey,
  )

  useEffect(() => {
    setSelectedRun(null)
    setSelectedComparison(null)
    setSelectedProfileKeys([])
    setSelectedDecision(null)
    setDecisionCandidateKey('')
    setDecisionBaselineKey('')
    setDecisionDownloadNotice('')
  }, [selectedAgentId])
  useEffect(() => {
    const versions = qualityHistory.data?.versions ?? []
    setDecisionCandidateKey((current) => (
      versions.some((version) => snapshotKey(version.snapshot) === current)
        ? current
        : versions[0] ? snapshotKey(versions[0].snapshot) : ''
    ))
  }, [qualityHistory.data?.versions])
  useEffect(() => {
    setDecisionBaselineKey((current) => (
      eligibleBaselines.some((version) => snapshotKey(version.snapshot) === current)
        ? current
        : eligibleBaselines[0] ? snapshotKey(eligibleBaselines[0].snapshot) : ''
    ))
  }, [eligibleBaselines])
  useEffect(() => {
    const items = decisions.data?.items ?? []
    setSelectedDecision((current) => (
      items.some((item) => item.id === current) ? current : items[0]?.id ?? null
    ))
  }, [decisions.data?.items])
  const canManage = session.data?.permissions.includes('cognition:write') ?? false
  const canRun = session.data?.permissions.includes('cognition:evaluate') ?? false
  const canReview = session.data?.permissions.includes('evaluation:review') ?? false
  const canExportDecisions = session.data?.permissions.includes('data_lifecycle:export') ?? false

  const parsedCases = useMemo(() => {
    try {
      const value = JSON.parse(casesText) as unknown
      return Array.isArray(value) ? value as EvaluationSuiteDraft['cases'] : null
    } catch {
      return null
    }
  }, [casesText])

  const refresh = async () => {
    await invalidateAcrossTabs(queryClient, ['evaluation-runs'])
    await queryClient.invalidateQueries({ queryKey: ['evaluation-quality-overview'] })
    await queryClient.invalidateQueries({ queryKey: ['evaluation-quality-history'] })
  }
  const createSuite = useMutation({
    mutationFn: () => {
      if (!parsedCases) throw new Error('用例定义不是有效 JSON 数组')
      return createEvaluationSuite({
        key: suiteKey,
        name: suiteName,
        description: suiteDescription || null,
        minimum_pass_rate: minimumPassRate,
        max_output_tokens: maxOutputTokens,
        cases: parsedCases,
      })
    },
    onSuccess: async () => {
      setEditing(false)
      await invalidateAcrossTabs(queryClient, ['evaluation-suites'])
    },
  })
  const publishSuite = useMutation({
    mutationFn: publishEvaluationSuite,
    onSuccess: async () => invalidateAcrossTabs(queryClient, ['evaluation-suites']),
  })
  const runSuite = useMutation({
    mutationFn: () => runEvaluation(selectedSuite || null),
    onSuccess: async (value) => {
      setSelectedRun(value.id)
      await refresh()
    },
  })
  const runComparison = useMutation({
    mutationFn: () => runEvaluationComparison(selectedProfileKeys, selectedSuite || null),
    onSuccess: async (value) => {
      setSelectedComparison(value.id)
      await invalidateAcrossTabs(queryClient, ['evaluation-comparisons'])
      await refresh()
    },
  })
  const claimReview = useMutation({
    mutationFn: () => claimBlindReviewAssignment(selectedRun),
    onSuccess: (value) => {
      setAssignment(value)
      setScoreA(neutralScore())
      setScoreB(neutralScore())
      setPreference('tie')
      setReviewNote('')
    },
  })
  const submitReview = useMutation({
    mutationFn: () => {
      if (!assignment) throw new Error('请先领取盲评任务')
      return submitBlindReview(assignment.id, {
        preference,
        response_a_score: scoreA,
        response_b_score: scoreB,
        note: reviewNote || null,
      })
    },
    onSuccess: async () => {
      setAssignment(null)
      await queryClient.invalidateQueries({ queryKey: ['evaluation-quality-overview'] })
      await queryClient.invalidateQueries({ queryKey: ['evaluation-quality-history'] })
    },
  })
  const createDecision = useMutation({
    mutationFn: () => {
      if (!candidateVersion || !baselineVersion) throw new Error('请选择同源候选与基线快照')
      return createEvaluationDecision({
        window_minutes: qualityWindow.windowMinutes,
        candidate: candidateVersion.snapshot,
        baseline: baselineVersion.snapshot,
        outcome: decisionOutcome,
        reason: decisionReason,
      })
    },
    onSuccess: async (value) => {
      setSelectedDecision(value.id)
      setDecisionDownloadNotice('')
      await invalidateAcrossTabs(queryClient, ['evaluation-decisions'])
    },
  })
  const downloadDecision = useMutation({
    mutationFn: (decisionId: string) => downloadEvaluationDecision(decisionId),
    onSuccess: (download) => {
      const url = URL.createObjectURL(download.blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = download.filename
      anchor.click()
      URL.revokeObjectURL(url)
      setDecisionDownloadNotice(`报告已开始下载${download.sha256 ? `，SHA-256：${download.sha256}` : ''}。`)
    },
  })

  const operationError = createSuite.error ?? publishSuite.error ?? runSuite.error
    ?? runComparison.error
    ?? claimReview.error ?? submitReview.error ?? createDecision.error ?? downloadDecision.error

  const toggleProfile = (profileKey: string) => {
    setSelectedProfileKeys((current) => current.includes(profileKey)
      ? current.filter((item) => item !== profileKey)
      : [...current, profileKey])
  }

  return (
    <div className="page">
      <section className="page-heading compact">
        <div><p className="eyebrow">行为质量</p><h1>拟人评测实验室</h1><p>固定认知与模型版本运行自动回归，再用来源随机化的 A/B 盲评衡量人格一致、自然度、共情和边界。</p></div>
        <div className="heading-actions">
          <button className="secondary-button" disabled={!canManage} onClick={() => setEditing((value) => !value)}><Plus size={14} /> 新建评测集</button>
          <button className="primary-button" disabled={!canRun || runSuite.isPending} onClick={() => runSuite.mutate()}><FlaskConical size={14} /> {runSuite.isPending ? '回放中…' : '运行自动回归'}</button>
        </div>
      </section>

      <div className="notice info"><Eye size={17} /><div><strong>提交前保持双盲</strong><span>人工评审任务只显示 A/B 回答，不返回候选位置、模型、版本或自动门禁结论；提交后由服务端去盲聚合。</span></div></div>
      {operationError && <div className="notice error">{operationError.message}</div>}

      <section className="quality-overview" aria-labelledby="quality-overview-title">
        <div className="quality-overview-heading">
          <div>
            <h2 id="quality-overview-title">统一质量概览</h2>
            <span>拟人：当前 Agent 全历史 · 告警：{formatQualityWindow(quality.data?.operations_window_minutes ?? 10_080)}</span>
          </div>
          <small>只读质量事实，不会自动调参、发布或处置告警</small>
        </div>
        {quality.error && <div className="notice error">质量概览加载失败：{quality.error.message}</div>}
        <div className="evaluation-metrics">
          <article><span>数据覆盖</span><strong>{quality.isPending ? '加载中' : qualityCoverageLabels[quality.data?.coverage ?? 'empty']}</strong><small>两类证据保持独立统计口径</small></article>
          <article><span>自动回归</span><strong>{quality.data?.evaluation.total_runs ?? 0}</strong><small>{quality.data?.evaluation.gate_passed_runs ?? 0} 次通过门禁</small></article>
          <article><span>最近通过率</span><strong>{quality.data?.evaluation.latest_pass_rate?.toFixed(1) ?? '—'}%</strong><small>冻结配置与模型版本</small></article>
          <article><span>盲评进度</span><strong>{quality.data?.evaluation.completed_reviews ?? 0}</strong><small>待我评审 {quality.data?.evaluation.pending_reviews ?? 0} 份</small></article>
          <article><span>候选 / 参考均分</span><strong>{quality.data?.evaluation.candidate_average_score?.toFixed(2) ?? '—'} / {quality.data?.evaluation.reference_average_score?.toFixed(2) ?? '—'}</strong><small>候选胜 {quality.data?.evaluation.candidate_wins ?? 0} · 平 {quality.data?.evaluation.ties ?? 0} · 参考胜 {quality.data?.evaluation.reference_wins ?? 0}</small></article>
          <article><span>告警建议接受率</span><strong>{quality.data?.alert_recommendations.acceptance_rate_percent.toFixed(1) ?? '—'}%</strong><small>接受 {quality.data?.alert_recommendations.accepted ?? 0} / 反馈 {quality.data?.alert_recommendations.total ?? 0}</small></article>
          <article><span>已接受建议后状态</span><strong>{quality.data?.alert_recommendations.accepted_resolved ?? 0} / {quality.data?.alert_recommendations.accepted_active ?? 0}</strong><small>已恢复 / 仍活动，仅表示观察事实</small></article>
          <article><span>通知重放复核</span><strong>{quality.data?.alert_recommendations.replay_allowed ?? 0} / {quality.data?.alert_recommendations.replay_blocked ?? 0}</strong><small>允许 / 阻止，共 {quality.data?.alert_recommendations.replay_total ?? 0} 次</small></article>
        </div>
      </section>

      <section className="quality-history-section" aria-labelledby="quality-history-title">
        <div className="quality-history-heading">
          <div>
            <h2 id="quality-history-title">拟人质量趋势与冻结版本</h2>
            <span>
              {qualityHistory.data
                ? `${formatQualityWindow(qualityHistory.data.window_minutes)} · ${qualityHistory.data.total_runs} 次回归 · ${qualityHistory.data.completed_reviews} 份盲评`
                : `近 ${qualityWindowDays} 天`}
            </span>
          </div>
          <div className="quality-window-selector" role="group" aria-label="质量趋势时间窗口">
            {qualityHistoryWindows.map((item) => <button
              aria-pressed={qualityWindowDays === item.days}
              className={qualityWindowDays === item.days ? 'active' : ''}
              key={item.days}
              onClick={() => setQualityWindowDays(item.days)}
              type="button"
            >
              {item.days} 天
            </button>)}
          </div>
        </div>
        {qualityHistory.error && <div className="notice error">质量趋势加载失败：{qualityHistory.error.message}</div>}
        {qualityHistory.isPending && <div className="empty-state compact">正在读取质量趋势…</div>}
        {qualityHistory.data?.total_runs === 0 && <div className="empty-state compact">当前时间窗口暂无自动回归记录，无法形成趋势或版本对比。</div>}
        {!!qualityHistory.data?.total_runs && <>
          <div className="quality-trend-legend" aria-label="趋势图图例">
            <span><i className="pass-rate" />自动回归通过率</span>
            <span><i className="candidate-score" />候选盲评均分</span>
            <small>盲评按对应运行时间归桶</small>
          </div>
          <div
            className="quality-trend-scroll"
            tabIndex={0}
            aria-label={`近 ${qualityWindowDays} 天拟人质量趋势图`}
          >
            <div
              className="quality-trend"
              style={{ gridTemplateColumns: `repeat(${qualityHistory.data.trend.length}, minmax(18px, 1fr))` }}
            >
              {qualityHistory.data.trend.map((point) => {
                const passRate = point.average_pass_rate ?? 0
                const candidateScore = point.candidate_average_score ?? 0
                const bucketLabel = formatQualityDate(point.bucket_started_at)
                const accessibleSummary = `${bucketLabel}：${point.total_runs} 次回归，通过率 ${point.average_pass_rate?.toFixed(1) ?? '无样本'}%，${point.completed_reviews} 份盲评，候选均分 ${point.candidate_average_score?.toFixed(2) ?? '无样本'}`
                return <div className="quality-trend-bucket" key={point.bucket_started_at} title={accessibleSummary}>
                  <div className="quality-trend-bars" role="img" aria-label={accessibleSummary}>
                    <i
                      aria-hidden="true"
                      className={`pass-rate ${point.average_pass_rate == null ? 'empty' : ''}`}
                      style={{ height: `${passRate}%` }}
                    />
                    <i
                      aria-hidden="true"
                      className={`candidate-score ${point.candidate_average_score == null ? 'empty' : ''}`}
                      style={{ height: `${candidateScore * 20}%` }}
                    />
                  </div>
                  <time dateTime={point.bucket_started_at}>{bucketLabel}</time>
                </div>
              })}
            </div>
          </div>
          <div className="quality-version-heading">
            <div>
              <h3>完整冻结快照对比</h3>
              <span>{qualityHistory.data.versions.length} 组版本上下文</span>
            </div>
            {!qualityHistory.data.comparable_versions && <small>当前窗口只有一组快照，暂无可比较版本。</small>}
          </div>
          <div className="admin-table-scroll quality-version-table">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>评测集 / 模型</th>
                  <th>配置 / 人格 / 提示词</th>
                  <th>策略 / 路由</th>
                  <th>运行范围</th>
                  <th>自动回归</th>
                  <th>人工盲评</th>
                </tr>
              </thead>
              <tbody>
                {qualityHistory.data.versions.map((version) => <tr key={[
                  version.snapshot.suite_key,
                  version.snapshot.suite_version,
                  version.snapshot.configuration_version,
                  version.snapshot.persona_version,
                  version.snapshot.prompt_version,
                  version.snapshot.policy_version,
                  version.snapshot.model_route_version,
                  version.snapshot.provider,
                  version.snapshot.model,
                ].join(':')}>
                  <td className="table-primary">
                    <strong>{version.snapshot.suite_key} · v{version.snapshot.suite_version}</strong>
                    <code>{version.snapshot.provider}/{version.snapshot.model}</code>
                  </td>
                  <td>
                    <strong>配置 v{version.snapshot.configuration_version}</strong>
                    <small>人格 v{version.snapshot.persona_version} · 提示词 v{version.snapshot.prompt_version}</small>
                  </td>
                  <td>
                    <strong>策略 v{version.snapshot.policy_version}</strong>
                    <small>模型路由 v{version.snapshot.model_route_version}</small>
                  </td>
                  <td>
                    <strong>{version.total_runs} 次</strong>
                    <small>{formatQualityDateTime(version.first_run_at)} 至 {formatQualityDateTime(version.latest_run_at)}</small>
                  </td>
                  <td>
                    <strong>{version.average_pass_rate.toFixed(1)}%</strong>
                    <small>门禁通过 {version.gate_passed_runs} / {version.total_runs}</small>
                  </td>
                  <td>
                    <strong>{version.candidate_average_score?.toFixed(2) ?? '—'} / {version.reference_average_score?.toFixed(2) ?? '—'}</strong>
                    <small>候选胜 {version.candidate_wins} · 平 {version.ties} · 参考胜 {version.reference_wins} · 共 {version.completed_reviews} 份</small>
                  </td>
                </tr>)}
              </tbody>
            </table>
          </div>
          <div className="quality-baseline-heading">
            <div>
              <h3>同源基线差异</h3>
              <span>候选为最新完整冻结快照，基线为同源紧邻上一快照</span>
            </div>
            <small>只呈现观察差异，不代表显著性或因果关系</small>
          </div>
          {!qualityHistory.data.baseline_comparisons.length && (
            <div className="empty-state compact">当前窗口没有满足同源条件的连续冻结快照，暂无基线差异。</div>
          )}
          {!!qualityHistory.data.baseline_comparisons.length && (
            <div className="quality-baseline-list">
              {qualityHistory.data.baseline_comparisons.map((comparison) => {
                const renderSnapshot = (label: string, version: typeof comparison.candidate) => (
                  <div className="quality-baseline-snapshot">
                    <strong>{label} · {formatQualityDateTime(version.latest_run_at)}</strong>
                    <span>
                      配置 v{version.snapshot.configuration_version} · 人格 v{version.snapshot.persona_version} · 提示词 v{version.snapshot.prompt_version}
                    </span>
                    <span>策略 v{version.snapshot.policy_version} · 路由 v{version.snapshot.model_route_version}</span>
                    <small>{version.total_runs} 次回归 · {version.completed_reviews} 份盲评</small>
                  </div>
                )
                return <article className="quality-baseline-item" key={`${comparison.key.suite_key}:${comparison.key.suite_version}:${comparison.key.provider}:${comparison.key.model}`}>
                  <div className="quality-baseline-item-heading">
                    <div>
                      <strong>{comparison.key.suite_key} · 评测集 v{comparison.key.suite_version}</strong>
                      <span>{comparison.key.provider}/{comparison.key.model}</span>
                    </div>
                    <small>门槛：每个快照至少 {comparison.minimum_runs_per_snapshot} 次回归、{comparison.minimum_reviews_per_snapshot} 份盲评</small>
                  </div>
                  <div className="quality-baseline-snapshots">
                    {renderSnapshot('候选', comparison.candidate)}
                    {renderSnapshot('基线', comparison.baseline)}
                  </div>
                  <div className="quality-baseline-metrics">
                    <div>
                      <span>自动回归通过率差</span>
                      <strong>{formatQualityDelta(comparison.pass_rate_delta_percentage_points, ' 个百分点')}</strong>
                      <small>{comparison.automatic_regression_comparable ? '双方样本达到门槛' : '自动回归样本不足'}</small>
                    </div>
                    <div>
                      <span>候选盲评均分差</span>
                      <strong>{formatQualityDelta(comparison.candidate_average_score_delta)}</strong>
                      <small>{comparison.blind_review_comparable ? '双方样本达到门槛' : '盲评样本不足'}</small>
                    </div>
                    <div>
                      <span>参考盲评均分差</span>
                      <strong>{formatQualityDelta(comparison.reference_average_score_delta)}</strong>
                      <small>{comparison.blind_review_comparable ? '双方样本达到门槛' : '盲评样本不足'}</small>
                    </div>
                  </div>
                  <p className="quality-baseline-note">样本门槛只决定是否展示差值；本比较未进行统计显著性评估，也不允许作因果结论。</p>
                </article>
              })}
            </div>
          )}
        </>}
      </section>

      <section className="evaluation-decision-section" aria-labelledby="evaluation-decision-title">
        <div className="quality-history-heading">
          <div>
            <h2 id="evaluation-decision-title">不可变评测决策</h2>
            <span>冻结人工结论、聚合证据与原始报告校验值</span>
          </div>
          <small>决策记录不会自动调参、发布配置或改变运行时行为</small>
        </div>
        <div className="evaluation-decision-form">
          <label>候选快照
            <select
              value={decisionCandidateKey}
              onChange={(event) => setDecisionCandidateKey(event.target.value)}
            >
              {!qualityHistory.data?.versions.length && <option value="">当前窗口暂无快照</option>}
              {qualityHistory.data?.versions.map((version) => <option
                key={snapshotKey(version.snapshot)}
                value={snapshotKey(version.snapshot)}
              >
                {snapshotLabel(version.snapshot)}
              </option>)}
            </select>
          </label>
          <label>同源基线
            <select
              value={decisionBaselineKey}
              onChange={(event) => setDecisionBaselineKey(event.target.value)}
            >
              {!eligibleBaselines.length && <option value="">没有可用的同源基线</option>}
              {eligibleBaselines.map((version) => <option
                key={snapshotKey(version.snapshot)}
                value={snapshotKey(version.snapshot)}
              >
                {snapshotLabel(version.snapshot)}
              </option>)}
            </select>
          </label>
          <label>人工结论
            <select
              value={decisionOutcome}
              onChange={(event) => setDecisionOutcome(event.target.value as EvaluationDecisionResult)}
            >
              {Object.entries(decisionOutcomeLabels).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
          <label>受控理由
            <select
              value={decisionReason}
              onChange={(event) => setDecisionReason(event.target.value as EvaluationDecisionReasonCode)}
            >
              {Object.entries(decisionReasonLabels).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
          <button
            className="primary-button"
            disabled={!canRun || !candidateVersion || !baselineVersion || createDecision.isPending}
            onClick={() => createDecision.mutate()}
            type="button"
          >
            <FileCheck2 size={14} /> {createDecision.isPending ? '正在冻结…' : '创建决策记录'}
          </button>
        </div>
        {decisionDownloadNotice && <div className="notice info">{decisionDownloadNotice}</div>}
        {decisions.error && <div className="notice error">决策历史加载失败：{decisions.error.message}</div>}
        <div className="evaluation-decision-workspace">
          <div className="evaluation-decision-history" aria-label="评测决策历史">
            <div className="evaluation-decision-column-heading">
              <strong>历史记录</strong>
              <span>{decisions.data?.items.length ?? 0} 条</span>
            </div>
            {decisions.isPending && <div className="empty-state compact">正在读取决策记录…</div>}
            {decisions.data?.items.length === 0 && <div className="empty-state compact">暂无决策记录。</div>}
            {decisions.data?.items.map((decision) => <button
              className={selectedDecision === decision.id ? 'selected' : ''}
              key={decision.id}
              onClick={() => setSelectedDecision(decision.id)}
              type="button"
            >
              <FileCheck2 size={15} />
              <span>
                <strong>{decisionOutcomeLabels[decision.outcome]}</strong>
                <small>{decisionReasonLabels[decision.reason]} · {formatQualityDateTime(decision.created_at)}</small>
                <code>{decision.sha256}</code>
              </span>
            </button>)}
          </div>
          <div className="evaluation-decision-detail">
            {!selectedDecision && <div className="empty-state compact">选择记录后查看冻结证据。</div>}
            {decisionDetail.isPending && <div className="empty-state compact">正在读取冻结报告…</div>}
            {decisionDetail.error && <div className="notice error">决策详情加载失败：{decisionDetail.error.message}</div>}
            {decisionDetail.data && (() => {
              const decision = decisionDetail.data
              const comparison = decision.report.comparison
              return <>
                <div className="evaluation-decision-detail-heading">
                  <div>
                    <strong>{decisionOutcomeLabels[decision.outcome]}</strong>
                    <span>{decisionReasonLabels[decision.reason]} · {formatQualityDateTime(decision.created_at)}</span>
                  </div>
                  {canExportDecisions && <button
                    className="secondary-button"
                    disabled={downloadDecision.isPending}
                    onClick={() => downloadDecision.mutate(decision.id)}
                    title="下载冻结 JSON 报告"
                    type="button"
                  >
                    <Download size={14} /> {downloadDecision.isPending ? '下载中…' : '下载 JSON'}
                  </button>}
                </div>
                <dl className="evaluation-decision-metadata">
                  <div><dt>证据窗口</dt><dd>{formatQualityWindow(decision.report.window_minutes)}</dd></div>
                  <div><dt>冻结区间</dt><dd>{formatQualityDateTime(decision.report.window_started_at)} 至 {formatQualityDateTime(decision.report.window_ended_at)}</dd></div>
                  <div><dt>操作者</dt><dd><code>{decision.created_by}</code></dd></div>
                  <div><dt>SHA-256</dt><dd><code>{decision.sha256}</code></dd></div>
                </dl>
                <div className="quality-baseline-snapshots">
                  <div className="quality-baseline-snapshot">
                    <strong>候选 · {formatQualityDateTime(comparison.candidate.latest_run_at)}</strong>
                    <span>{snapshotLabel(comparison.candidate.snapshot)}</span>
                    <small>{comparison.candidate.total_runs} 次回归 · {comparison.candidate.completed_reviews} 份盲评</small>
                  </div>
                  <div className="quality-baseline-snapshot">
                    <strong>基线 · {formatQualityDateTime(comparison.baseline.latest_run_at)}</strong>
                    <span>{snapshotLabel(comparison.baseline.snapshot)}</span>
                    <small>{comparison.baseline.total_runs} 次回归 · {comparison.baseline.completed_reviews} 份盲评</small>
                  </div>
                </div>
                <div className="quality-baseline-metrics">
                  <div>
                    <span>自动回归通过率差</span>
                    <strong>{formatQualityDelta(comparison.pass_rate_delta_percentage_points, ' 个百分点')}</strong>
                    <small>门槛：双方各 {comparison.minimum_runs_per_snapshot} 次</small>
                  </div>
                  <div>
                    <span>候选盲评均分差</span>
                    <strong>{formatQualityDelta(comparison.candidate_average_score_delta)}</strong>
                    <small>门槛：双方各 {comparison.minimum_reviews_per_snapshot} 份</small>
                  </div>
                  <div>
                    <span>参考盲评均分差</span>
                    <strong>{formatQualityDelta(comparison.reference_average_score_delta)}</strong>
                    <small>{comparison.blind_review_comparable ? '盲评证据达到门槛' : '盲评证据不足'}</small>
                  </div>
                </div>
                <p className="quality-baseline-note">未评估统计显著性，不允许作因果结论，不允许自动执行任何调参或发布动作。</p>
              </>
            })()}
          </div>
        </div>
      </section>

      {editing && <section className="panel evaluation-editor">
        <div className="panel-heading"><h2>新建不可变评测集草稿</h2><span className="subtle">发布后才能作为回归目标</span></div>
        <div className="evaluation-form-grid">
          <label>评测集键<input value={suiteKey} onChange={(event) => setSuiteKey(event.target.value)} /></label>
          <label>显示名称<input value={suiteName} onChange={(event) => setSuiteName(event.target.value)} /></label>
          <label>最低通过率<input type="number" min="0" max="100" value={minimumPassRate} onChange={(event) => setMinimumPassRate(Number(event.target.value))} /></label>
          <label>最大输出 Token<input type="number" min="64" max="32768" value={maxOutputTokens} onChange={(event) => setMaxOutputTokens(Number(event.target.value))} /></label>
        </div>
        <label className="payload-editor">说明<input value={suiteDescription} onChange={(event) => setSuiteDescription(event.target.value)} /></label>
        <label className="payload-editor">用例 JSON<textarea rows={15} value={casesText} onChange={(event) => setCasesText(event.target.value)} spellCheck={false} /></label>
        <div className="cognition-editor-actions"><button className="primary-button" disabled={!parsedCases || !suiteKey.trim() || !suiteName.trim() || createSuite.isPending} onClick={() => createSuite.mutate()}><CheckCircle2 size={14} /> 保存草稿</button></div>
      </section>}

      <section className="panel evaluation-suite-panel">
        <div className="panel-heading"><h2>回归目标</h2><span className="subtle">不选择时运行内置 5 项拟人安全基线</span></div>
        <label className="evaluation-suite-select">已发布评测集
          <select value={selectedSuite} onChange={(event) => setSelectedSuite(event.target.value)}>
            <option value="">内置拟人安全基线</option>
            {suites.data?.items.filter((item) => item.status === 'published').map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.version}</option>)}
          </select>
        </label>
        {!suites.data?.items.length && <div className="empty-state compact">尚无自定义评测集；可直接运行内置基线。</div>}
        {suites.data?.items.map((suite) => <article className="evaluation-suite-row" key={suite.id}>
          <div><strong>{suite.name}</strong><span>{suite.key} · v{suite.version} · {suite.cases.length} 条 · 门禁 {suite.minimum_pass_rate}%</span></div>
          <span className={`version-status ${suite.status}`}>{suite.status === 'draft' ? '草稿' : suite.status === 'published' ? '已发布' : '历史版本'}</span>
          {suite.status === 'draft' && <button disabled={!canManage || publishSuite.isPending} onClick={() => publishSuite.mutate(suite.id)}><Rocket size={12} /> 发布</button>}
        </article>)}
      </section>

      <section className="panel comparison-panel">
        <div className="panel-heading">
          <div><h2>多模型同源对比</h2><span className="subtle">同一评测集、事件时间与认知快照</span></div>
          <button
            className="primary-button"
            disabled={!canRun || selectedProfileKeys.length < 2 || runComparison.isPending}
            onClick={() => runComparison.mutate()}
          >
            <FlaskConical size={14} /> {runComparison.isPending ? '对比中…' : '运行同源对比'}
          </button>
        </div>
        {comparisonTargets.data && comparisonTargets.data.items.length < 2 && <div className="empty-state compact">当前智能体的已发布 chat.realizer 路由不足两个模型档案，请先在认知资源中发布完整路由。</div>}
        <div className="comparison-targets" role="group" aria-label="对比模型候选">
          {comparisonTargets.data?.items.map((target) => <label className={selectedProfileKeys.includes(target.profile_key) ? 'selected' : ''} key={target.profile_key}>
            <input
              type="checkbox"
              checked={selectedProfileKeys.includes(target.profile_key)}
              onChange={() => toggleProfile(target.profile_key)}
            />
            <span><strong>{target.profile_key} · v{target.profile_version}</strong><small>{target.provider}/{target.model}</small></span>
          </label>)}
        </div>
        {!!comparisonTargets.data?.items.length && <p className="comparison-selection-note">已选择 {selectedProfileKeys.length} 个候选，至少选择 2 个；实际候选上限由配置中心控制。</p>}
      </section>

      <div className="evaluation-workspace comparison-workspace">
        <section className="panel evaluation-history comparison-history">
          <div className="panel-heading"><h2>对比历史</h2><span className="subtle">{comparisons.data?.items.length ?? 0} 次</span></div>
          {!comparisons.data?.items.length && <div className="empty-state">完成一次同源对比后，这里只展示指标摘要，不包含回答正文。</div>}
          {comparisons.data?.items.map((comparison) => <button className={selectedComparison === comparison.id ? 'selected' : ''} key={comparison.id} onClick={() => setSelectedComparison(comparison.id)}>
            <CheckCircle2 className="passed" size={17} />
            <span><strong>{comparison.suite_name} · v{comparison.suite_version}</strong><small>{comparison.entries.map((entry) => `${entry.profile_key} ${entry.run.pass_rate.toFixed(1)}%`).join(' · ')}</small></span>
          </button>)}
        </section>

        <section className="panel comparison-detail">
          <div className="panel-heading"><h2>候选指标与逐用例回答</h2><span className="subtle">{comparisonDetail.data ? `配置 v${comparisonDetail.data.configuration_version} · 路由 v${comparisonDetail.data.model_route_version}` : '选择一次对比'}</span></div>
          {!comparisonDetail.data && <div className="empty-state">选择左侧实验查看各候选通过率、延迟、Token、成本和并列回答。</div>}
          <div className="comparison-metrics">
            {comparisonDetail.data?.entries.map((entry) => <article className="comparison-entry-card" key={entry.profile_key}>
              <div><strong>{entry.profile_key} · v{entry.profile_version}</strong><span>{entry.run.provider}/{entry.run.model}</span></div>
              <dl>
                <div><dt>通过率</dt><dd>{entry.run.pass_rate.toFixed(1)}%</dd></div>
                <div><dt>总延迟</dt><dd>{entry.run.results.reduce((total, item) => total + item.latency_ms, 0)} ms</dd></div>
                <div><dt>输入 / 输出 Token</dt><dd>{entry.run.input_tokens} / {entry.run.output_tokens}</dd></div>
                <div><dt>估算成本</dt><dd>{formatCost(entry.run.estimated_cost_microusd)}</dd></div>
              </dl>
            </article>)}
          </div>
          <div className="comparison-cases">
            {comparisonDetail.data?.entries[0]?.run.results.map((reference) => <article className="comparison-case" key={reference.case_key}>
              <header><strong>{reference.category}</strong><code>{reference.case_key}</code><p>“{reference.input_text}”</p></header>
              <div className="comparison-responses">
                {comparisonDetail.data?.entries.map((entry) => {
                  const result = entry.run.results.find((item) => item.case_key === reference.case_key)
                  return <article key={entry.profile_key}>
                    <strong>{entry.profile_key} · v{entry.profile_version}</strong>
                    <p>{result?.candidate_response || '（无回复）'}</p>
                    <small>{result ? `${displayLabel(cognitiveActionLabels, result.actual_action)} · ${result.latency_ms} ms · ${result.passed ? '通过' : '未通过'}` : '结果缺失'}</small>
                  </article>
                })}
              </div>
            </article>)}
          </div>
        </section>
      </div>

      <div className="evaluation-workspace">
        <section className="panel evaluation-history">
          <div className="panel-heading"><h2>自动回放历史</h2><span className="subtle">{runs.data?.items.length ?? 0} 次</span></div>
          {!runs.data?.items.length && <div className="empty-state">运行回归后，这里会保留版本和质量门证据。</div>}
          {runs.data?.items.map((run) => <button className={selectedRun === run.id ? 'selected' : ''} key={run.id} onClick={() => setSelectedRun(run.id)}>
            {run.gate_passed ? <CheckCircle2 className="passed" size={17} /> : <CircleX className="failed" size={17} />}
            <span><strong>{run.suite_name} · v{run.suite_version}</strong><small>{run.passed}/{run.total} · {run.pass_rate.toFixed(1)}% · {run.provider}/{run.model}</small></span>
          </button>)}
        </section>

        <section className="panel evaluation-detail">
          <div className="panel-heading"><h2>确定性质量门</h2><span className="subtle">{runDetail.data ? `配置 v${runDetail.data.configuration_version} · 人格 v${runDetail.data.persona_version}` : '选择一次运行'}</span></div>
          {!runDetail.data && <div className="empty-state">选择左侧运行查看逐项检查；回答来源仍不会在此页标注。</div>}
          <div className="evaluation-list">{runDetail.data?.results.map((item) => <article key={item.id}>
            {item.passed ? <CheckCircle2 className="passed" size={18} /> : <CircleX className="failed" size={18} />}
            <div><strong>{item.category}</strong><code>{item.case_key}</code><p>“{item.input_text}”</p><small>期望 {displayLabel(cognitiveActionLabels, item.expected_action)} · 实际 {displayLabel(cognitiveActionLabels, item.actual_action)} · {item.latency_ms} ms</small><ul>{item.checks.map((check) => <li key={check.key} className={check.passed ? 'passed' : 'failed'}>{check.passed ? '通过' : '失败'} · {check.key === 'action_match' ? `期望${displayLabel(cognitiveActionLabels, item.expected_action)}，实际${displayLabel(cognitiveActionLabels, item.actual_action)}` : check.detail}</li>)}</ul></div>
          </article>)}</div>
        </section>
      </div>

      <section className="panel blind-review-panel">
        <div className="panel-heading"><h2>匿名 A/B 人工盲评</h2><button className="secondary-button" disabled={!canReview || claimReview.isPending} onClick={() => claimReview.mutate()}><Eye size={14} /> {claimReview.isPending ? '领取中…' : '领取下一条'}</button></div>
        {claimReview.isSuccess && !assignment && <div className="empty-state compact">当前范围没有待评审的双回答样例。</div>}
        {assignment && <>
          <div className="blind-prompt"><span>{assignment.category} · {assignment.case_key}</span><p>{assignment.input_text}</p></div>
          <div className="blind-responses">
            <article><strong>回答 A</strong><p>{assignment.response_a}</p><ScoreEditor label="回答 A" value={scoreA} onChange={setScoreA} /></article>
            <article><strong>回答 B</strong><p>{assignment.response_b}</p><ScoreEditor label="回答 B" value={scoreB} onChange={setScoreB} /></article>
          </div>
          <div className="blind-submit">
            <fieldset><legend>总体更偏好</legend>{(['a', 'tie', 'b'] as const).map((value) => <label key={value}><input type="radio" name="preference" checked={preference === value} onChange={() => setPreference(value)} />{value === 'a' ? '回答 A' : value === 'b' ? '回答 B' : '难分高下'}</label>)}</fieldset>
            <label>评审备注（可选）<input value={reviewNote} onChange={(event) => setReviewNote(event.target.value)} /></label>
            <button className="primary-button" disabled={submitReview.isPending} onClick={() => submitReview.mutate()}><CheckCircle2 size={14} /> {submitReview.isPending ? '提交中…' : '提交盲评'}</button>
          </div>
        </>}
      </section>
    </div>
  )
}
