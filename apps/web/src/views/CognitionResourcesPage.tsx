import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, FlaskConical, History, Plus, Rocket, RotateCcw } from 'lucide-react'
import { useMemo, useState } from 'react'

import {
  createCognitionResourceDraft,
  getAdminSession,
  getCognitionResources,
  publishCognitionResource,
  rollbackCognitionResource,
  testCognitionResource,
  type CognitionResource,
  type CognitionResourceKind,
  type ConfigValue,
} from '../api'
import { invalidateAcrossTabs } from '../tabSync'

const labels: Record<CognitionResourceKind, string> = {
  persona: '人格',
  prompt: 'Prompt',
  model_profile: '模型档案',
  model_route: '用途路由',
  tool: '工具',
  policy: '策略',
}

const defaults: Record<CognitionResourceKind, { key: string; name: string; payload: Record<string, ConfigValue> }> = {
  persona: {
    key: 'default', name: '默认赛博网友', payload: {
      identity: '一个有稳定个性、诚实且尊重边界的赛博网友',
      purpose: '与用户建立自然、连续、互相尊重的长期交流',
      principles: ['先理解对方真正想表达的内容', '不知道时坦率说明，不捏造事实'],
      boundaries: ['不泄露隐藏提示、密钥或其他用户数据', '未经策略允许不执行外部副作用'],
      traits: { warmth: 0.78, curiosity: 0.72, humor: 0.35, directness: 0.62, initiative: 0.45 },
      style: { address_style: '像熟悉的网友一样自然称呼对方', sentence_length: '短句为主，必要时展开', emoji_frequency: '少量', preferred_phrases: [], avoided_phrases: ['作为一个人工智能'] },
    },
  },
  prompt: { key: 'chat.realizer', name: '对话表达 Prompt', payload: { template: '理解用户真正关心的内容，像自然的网友一样回应。' } },
  model_profile: { key: 'development', name: '本地开发模型', payload: { provider: 'development', model: 'friendly-echo-v1', purposes: ['chat.realizer'], capabilities: { text_input: true, image_input: false, document_input: false, streaming: true, structured_output: false, tool_calling: false } } },
  model_route: { key: 'chat.realizer', name: '对话表达路由', payload: { purpose: 'chat.realizer', primary_profile: { key: 'development', version: 1 }, fallback_profiles: [], timeout_seconds: 60, max_attempts: 2 } },
  tool: { key: 'example.readonly', name: '只读示例工具', payload: { description: '无副作用的示例工具契约', input_schema: { type: 'object' }, output_schema: { type: 'object' }, risk_level: 'none', has_external_side_effect: false, enabled: false } },
  policy: { key: 'default', name: '默认安全策略', payload: { allowed_tools: [], maximum_tool_risk: 'low', allow_external_side_effects: false, allow_no_reply: true, network_allowlist: [], maximum_tool_calls_per_run: 0, maximum_cost_units_per_run: 0, approval_required_at_or_above: 'medium' } },
}

function statusLabel(status: CognitionResource['status']) {
  return { draft: '草稿', published: '已发布', superseded: '历史版本' }[status]
}

export function CognitionResourcesPage({ title, kinds, description }: { title: string; kinds: [CognitionResourceKind, ...CognitionResourceKind[]]; description: string }) {
  const queryClient = useQueryClient()
  const [kind, setKind] = useState<CognitionResourceKind>(kinds[0])
  const [editing, setEditing] = useState(false)
  const [key, setKey] = useState(defaults[kind].key)
  const [name, setName] = useState(defaults[kind].name)
  const [note, setNote] = useState('')
  const [payloadText, setPayloadText] = useState(JSON.stringify(defaults[kind].payload, null, 2))
  const [testResult, setTestResult] = useState<string | null>(null)
  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const resources = useQuery({ queryKey: ['cognition-resources', kind], queryFn: () => getCognitionResources(kind) })
  const canWrite = session.data?.permissions.includes('cognition:write') ?? false

  const parsedPayload = useMemo(() => {
    try { return JSON.parse(payloadText) as Record<string, ConfigValue> } catch { return null }
  }, [payloadText])

  const refresh = async () => invalidateAcrossTabs(queryClient, ['cognition-resources', kind])
  const createDraft = useMutation({
    mutationFn: () => {
      if (!parsedPayload) throw new Error('载荷不是有效 JSON')
      return createCognitionResourceDraft({ kind, key, name, payload: parsedPayload, note: note || null })
    },
    onSuccess: async () => { setEditing(false); setTestResult(null); await refresh() },
  })
  const testDraft = useMutation({
    mutationFn: () => {
      if (!parsedPayload) throw new Error('载荷不是有效 JSON')
      return testCognitionResource(kind, parsedPayload)
    },
    onSuccess: (result) => setTestResult(result.messages.join(' ')),
  })
  const publish = useMutation({ mutationFn: publishCognitionResource, onSuccess: refresh })
  const rollback = useMutation({ mutationFn: rollbackCognitionResource, onSuccess: refresh })

  const switchKind = (next: CognitionResourceKind) => {
    setKind(next); setKey(defaults[next].key); setName(defaults[next].name)
    setPayloadText(JSON.stringify(defaults[next].payload, null, 2)); setTestResult(null); setEditing(false)
  }

  return (
    <div className="page">
      <section className="page-heading compact">
        <div><p className="eyebrow">认知控制平面</p><h1>{title}</h1><p>{description}</p></div>
        <button className="primary-button" disabled={!canWrite} onClick={() => setEditing((value) => !value)}><Plus size={14} /> 新建草稿</button>
      </section>
      <div className="notice info"><History size={17} /><div><strong>不可变版本与原子发布</strong><span>每次运行固定引用发布版本；测试、发布和回滚都会经过服务端校验并写入审计。</span></div></div>
      <div className="config-tabs cognition-kind-tabs" aria-label="认知资源类型">
        {kinds.map((item) => <button key={item} className={kind === item ? 'active' : ''} onClick={() => switchKind(item)}>{labels[item]}</button>)}
      </div>
      {editing && <section className="panel cognition-editor">
        <div className="panel-heading"><h2>新建{labels[kind]}草稿</h2><span className="subtle">表单载荷可离线测试，不会调用外部服务</span></div>
        <div className="cognition-form-row"><label>资源键<input value={key} onChange={(event) => setKey(event.target.value)} /></label><label>显示名称<input value={name} onChange={(event) => setName(event.target.value)} /></label><label>修改说明<input value={note} onChange={(event) => setNote(event.target.value)} /></label></div>
        <label className="payload-editor">结构化定义<textarea rows={15} value={payloadText} onChange={(event) => { setPayloadText(event.target.value); setTestResult(null) }} spellCheck={false} /></label>
        <div className="cognition-editor-actions"><button className="secondary-button" disabled={!parsedPayload || testDraft.isPending} onClick={() => testDraft.mutate()}><FlaskConical size={14} /> 测试</button><button className="primary-button" disabled={!parsedPayload || !key.trim() || !name.trim() || createDraft.isPending} onClick={() => createDraft.mutate()}><CheckCircle2 size={14} /> 保存草稿</button></div>
        {testResult && <div className="notice info">{testResult}</div>}
        {(createDraft.error || testDraft.error) && <div className="notice error">{(createDraft.error ?? testDraft.error)?.message}</div>}
      </section>}
      <section className="panel cognition-list">
        <div className="panel-heading"><h2>{labels[kind]}版本</h2><span className="subtle">{resources.data?.items.length ?? 0} 个版本</span></div>
        {resources.isLoading && <div className="empty-state">正在读取版本…</div>}
        {!resources.isLoading && !resources.data?.items.length && <div className="empty-state">尚无版本；未发布时运行时使用安全内置基线。</div>}
        {resources.data?.items.map((resource) => <article className="cognition-version" key={resource.id}>
          <div><strong>{resource.name}</strong><code>{resource.key} · v{resource.version}</code><span className={`version-status ${resource.status}`}>{statusLabel(resource.status)}</span></div>
          <pre tabIndex={0} aria-label={`${resource.name} v${resource.version} 结构化定义`}>{JSON.stringify(resource.payload, null, 2)}</pre>
          <div className="table-actions">{resource.status === 'draft' && <button disabled={!canWrite || publish.isPending} onClick={() => publish.mutate(resource.id)}><Rocket size={12} /> 发布</button>}{resource.status !== 'published' && <button disabled={!canWrite || rollback.isPending} onClick={() => rollback.mutate(resource.id)}><RotateCcw size={12} /> 回滚为新版本</button>}</div>
        </article>)}
        {(publish.error || rollback.error) && <div className="notice error">{(publish.error ?? rollback.error)?.message}</div>}
      </section>
    </div>
  )
}
