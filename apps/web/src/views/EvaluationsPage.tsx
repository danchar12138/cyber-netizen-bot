import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, CircleX, Eye, FlaskConical, Plus, Rocket } from 'lucide-react'
import { useMemo, useState } from 'react'

import {
  claimBlindReviewAssignment,
  createEvaluationSuite,
  getAdminSession,
  getEvaluationReport,
  getEvaluationRun,
  getEvaluationRuns,
  getEvaluationSuites,
  publishEvaluationSuite,
  runEvaluation,
  submitBlindReview,
  type BlindReviewAssignment,
  type BlindReviewScore,
  type EvaluationCaseDefinition,
  type EvaluationSuiteDraft,
} from '../api'
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
  const [editing, setEditing] = useState(false)
  const [selectedSuite, setSelectedSuite] = useState('')
  const [selectedRun, setSelectedRun] = useState<string | null>(null)
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
  const suites = useQuery({ queryKey: ['evaluation-suites'], queryFn: getEvaluationSuites })
  const runs = useQuery({ queryKey: ['evaluation-runs'], queryFn: getEvaluationRuns })
  const report = useQuery({ queryKey: ['evaluation-report'], queryFn: getEvaluationReport })
  const runDetail = useQuery({
    queryKey: ['evaluation-run', selectedRun],
    queryFn: () => getEvaluationRun(selectedRun ?? ''),
    enabled: Boolean(selectedRun),
  })
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
    await queryClient.invalidateQueries({ queryKey: ['evaluation-report'] })
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
      await queryClient.invalidateQueries({ queryKey: ['evaluation-report'] })
    },
  })

  const operationError = createSuite.error ?? publishSuite.error ?? runSuite.error
    ?? claimReview.error ?? submitReview.error

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

      <section className="evaluation-metrics" aria-label="评测概览">
        <article><span>自动回归</span><strong>{report.data?.total_runs ?? 0}</strong><small>{report.data?.gate_passed_runs ?? 0} 次通过门禁</small></article>
        <article><span>最近通过率</span><strong>{report.data?.latest_pass_rate?.toFixed(1) ?? '—'}%</strong><small>冻结配置与模型版本</small></article>
        <article><span>待我盲评</span><strong>{report.data?.pending_reviews ?? 0}</strong><small>已完成 {report.data?.completed_reviews ?? 0} 份</small></article>
        <article><span>候选 / 参考均分</span><strong>{report.data?.candidate_average_score?.toFixed(2) ?? '—'} / {report.data?.reference_average_score?.toFixed(2) ?? '—'}</strong><small>候选胜 {report.data?.candidate_wins ?? 0} · 平 {report.data?.ties ?? 0} · 参考胜 {report.data?.reference_wins ?? 0}</small></article>
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
