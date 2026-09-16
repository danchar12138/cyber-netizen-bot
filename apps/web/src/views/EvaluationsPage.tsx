import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, CircleX, Eye, FlaskConical, Plus, Rocket } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import {
  claimBlindReviewAssignment,
  createEvaluationSuite,
  getAdminSession,
  getEvaluationComparison,
  getEvaluationComparisons,
  getEvaluationComparisonTargets,
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
  type EvaluationSuiteDraft,
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

function formatQualityWindow(minutes: number) {
  if (minutes % 1_440 === 0) return `近 ${minutes / 1_440} 天`
  if (minutes % 60 === 0) return `近 ${minutes / 60} 小时`
  return `近 ${minutes} 分钟`
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
  const comparisonTargets = useQuery({
    queryKey: ['evaluation-comparison-targets', selectedAgentId],
    queryFn: getEvaluationComparisonTargets,
  })
  const comparisons = useQuery({
    queryKey: ['evaluation-comparisons', selectedAgentId],
    queryFn: getEvaluationComparisons,
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

  useEffect(() => {
    setSelectedRun(null)
    setSelectedComparison(null)
    setSelectedProfileKeys([])
  }, [selectedAgentId])
  const canManage = session.data?.permissions.includes('cognition:write') ?? false
  const canRun = session.data?.permissions.includes('cognition:evaluate') ?? false
  const canReview = session.data?.permissions.includes('evaluation:review') ?? false

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
    },
  })

  const operationError = createSuite.error ?? publishSuite.error ?? runSuite.error
    ?? runComparison.error
    ?? claimReview.error ?? submitReview.error

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
