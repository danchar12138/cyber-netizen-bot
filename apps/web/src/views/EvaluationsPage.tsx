import { useMutation, useQuery } from '@tanstack/react-query'
import { CheckCircle2, CircleX, FlaskConical, Play } from 'lucide-react'

import { getAdminSession, runCognitionEvaluationSuite } from '../api'

export function EvaluationsPage() {
  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const evaluation = useMutation({ mutationFn: runCognitionEvaluationSuite })
  const canRun = session.data?.permissions.includes('cognition:evaluate') ?? false

  return (
    <div className="page">
      <section className="page-heading compact">
        <div><p className="eyebrow">行为质量</p><h1>拟人评测实验室</h1><p>确定性回放人格一致性、自然追问、关系边界、不回复与外部副作用拒绝，不连接真实模型或工具。</p></div>
        <button className="primary-button" disabled={!canRun || evaluation.isPending} onClick={() => evaluation.mutate()}><Play size={14} /> {evaluation.isPending ? '运行中…' : '运行内置回放集'}</button>
      </section>
      <div className="notice info"><FlaskConical size={17} /><div><strong>零副作用回放</strong><span>用例检查结构化行动和策略选择；模型自然语言质量将在后续人工盲评中继续扩充。</span></div></div>
      {evaluation.error && <div className="notice error">{evaluation.error.message}</div>}
      <section className="panel evaluation-panel">
        <div className="panel-heading"><h2>最近一次结果</h2><span className="subtle">{evaluation.data ? `${evaluation.data.passed} / ${evaluation.data.total} 通过` : '尚未运行'}</span></div>
        {!evaluation.data && <div className="empty-state">运行回放集后，这里会显示每条边界和行动判定。</div>}
        <div className="evaluation-list">{evaluation.data?.cases.map((item) => <article key={item.case_id}>
          {item.passed ? <CheckCircle2 className="passed" size={18} /> : <CircleX className="failed" size={18} />}
          <div><strong>{item.category}</strong><code>{item.case_id}</code><p>“{item.input_text}”</p><small>期望 {item.expected_action} · 实际 {item.actual_action} · {item.summary}</small></div>
        </article>)}</div>
      </section>
    </div>
  )
}
