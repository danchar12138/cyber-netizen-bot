import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cable, FlaskConical, KeyRound, Plus, RadioTower, RefreshCw, Send, ShieldCheck, Unplug } from 'lucide-react'
import { useCallback, useMemo, useState, type FormEvent } from 'react'

import {
  clearChannelCredential,
  createChannelInstance,
  deliverChannelMessage,
  getAdminSession,
  getChannelCatalog,
  getChannelEvents,
  getChannelInstances,
  getModelCapabilities,
  setChannelCredential,
  simulateChannel,
  testChannelConnection,
  updateChannelInstance,
  type ChannelDiagnosticEvent,
  type ChannelInstance,
  type ChannelPlatform,
  type ModelCapabilityProfile,
} from '../api'
import { useSelectedAgentId } from '../agentSelection'
import { AdminDataTable, type AdminTableColumn } from '../components/AdminDataTable'
import {
  channelCapabilityLabels,
  channelDegradationLabels,
  channelEventDirectionLabels,
  channelEventStatusLabels,
  channelEventTypeLabels,
  channelHealthLabels,
  channelPlatformLabels,
  displayLabel,
  formatMetadataEntries,
} from '../displayLabels'
import { invalidateAcrossTabs } from '../tabSync'

function capabilityLabels(instance: ChannelInstance) {
  const capabilities = instance.capabilities
  return [
    capabilities.text && '文本', capabilities.markdown && 'Markdown', capabilities.images && '图片',
    capabilities.files && '文件', capabilities.streaming && '流式', capabilities.reactions && '反应',
    capabilities.threads && '线程', capabilities.message_edit && '编辑',
    capabilities.proactive_messages && '主动消息',
  ].filter(Boolean) as string[]
}

function degradationLabels(values: string[]) {
  return values.map((value) => displayLabel(channelDegradationLabels, value))
}

function modelInputs(profile: ModelCapabilityProfile) {
  return [profile.text_input && '文本', profile.image_input && '图片', profile.document_input && '文档']
    .filter(Boolean).join(' / ')
}

export function ChannelsPage() {
  const selectedAgentId = useSelectedAgentId()
  return <ChannelsPageContent key={selectedAgentId ?? 'default'} selectedAgentId={selectedAgentId} />
}

function ChannelsPageContent({ selectedAgentId }: { selectedAgentId: string | null }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('内部 Web')
  const [platform, setPlatform] = useState<ChannelPlatform>('web')
  const [rateLimit, setRateLimit] = useState(60)
  const [settingsText, setSettingsText] = useState('{}')
  const [credential, setCredential] = useState('')
  const [credentialChannelId, setCredentialChannelId] = useState('')
  const [simulationPlatform, setSimulationPlatform] = useState<ChannelPlatform>('discord')
  const [simulationText, setSimulationText] = useState('这是一条 **Markdown** 渠道能力协商测试。')
  const [requestStreaming, setRequestStreaming] = useState(true)
  const [requestThread, setRequestThread] = useState(true)
  const [requestEdit, setRequestEdit] = useState(true)
  const [requestProactive, setRequestProactive] = useState(false)
  const [deliveryChannelId, setDeliveryChannelId] = useState('')
  const [recipientId, setRecipientId] = useState('')
  const [deliveryText, setDeliveryText] = useState('这是一条来自赛博网友管理后台的测试消息。')
  const [deliveryThreadId, setDeliveryThreadId] = useState('')
  const [deliveryEditMessageId, setDeliveryEditMessageId] = useState('')
  const [formError, setFormError] = useState('')

  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const catalog = useQuery({ queryKey: ['channel-catalog'], queryFn: getChannelCatalog })
  const models = useQuery({ queryKey: ['model-capabilities'], queryFn: getModelCapabilities })
  const instances = useQuery({
    queryKey: ['channel-instances', selectedAgentId],
    queryFn: getChannelInstances,
  })
  const events = useQuery({
    queryKey: ['channel-events', selectedAgentId],
    queryFn: () => getChannelEvents(),
  })
  const canWrite = session.data?.permissions.includes('channel:write') ?? false
  const canSend = session.data?.permissions.includes('channel:send') ?? false
  const canManageCredential = session.data?.permissions.includes('channel_credential:manage') ?? false
  const deliveryTarget = instances.data?.items.find((item) => item.id === deliveryChannelId)

  const refresh = async () => {
    await Promise.all([
      invalidateAcrossTabs(queryClient, ['channel-instances', selectedAgentId]),
      invalidateAcrossTabs(queryClient, ['channel-events', selectedAgentId]),
    ])
  }
  const createMutation = useMutation({
    mutationFn: createChannelInstance,
    onSuccess: async (created) => {
      setCredential('')
      setCredentialChannelId(created.id)
      await refresh()
    },
  })
  const testMutation = useMutation({ mutationFn: testChannelConnection, onSuccess: refresh })
  const updateMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: ChannelInstance['status'] }) =>
      updateChannelInstance(id, { status, confirmed: true }),
    onSuccess: refresh,
  })
  const credentialMutation = useMutation({
    mutationFn: ({ id, value }: { id: string; value: string }) => setChannelCredential(id, value),
    onSuccess: async () => { setCredential(''); await refresh() },
  })
  const clearCredentialMutation = useMutation({ mutationFn: clearChannelCredential, onSuccess: refresh })
  const simulation = useMutation({
    mutationFn: () => simulateChannel({
      platform: simulationPlatform,
      blocks: [{ kind: 'markdown', text: simulationText }],
      request_streaming: requestStreaming,
      thread_id: requestThread ? 'simulated-thread' : null,
      edit_message_id: requestEdit ? 'simulated-message' : null,
      proactive: requestProactive,
    }),
  })
  const delivery = useMutation({
    mutationFn: () => deliverChannelMessage(deliveryChannelId, {
      recipient_id: recipientId.trim(),
      blocks: [{ kind: 'text', text: deliveryText }],
      idempotency_key: crypto.randomUUID(),
      request_streaming: false,
      thread_id: deliveryThreadId.trim() || null,
      edit_message_id: deliveryEditMessageId.trim() || null,
      proactive: true,
    }),
    onSuccess: refresh,
  })

  const submitCreate = (event: FormEvent) => {
    event.preventDefault()
    setFormError('')
    try {
      const parsed = JSON.parse(settingsText) as unknown
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('公开设置必须是 JSON 对象')
      createMutation.mutate({
        name: name.trim(), platform, status: 'disabled', rate_limit_per_minute: rateLimit,
        settings: parsed as Record<string, string | number | boolean | null>,
        credential: platform === 'web' || !credential ? null : credential,
      })
    } catch (error) {
      setFormError(error instanceof Error ? error.message : '公开设置 JSON 无效')
    }
  }
  const runToggle = useCallback((item: ChannelInstance) => {
    const next = item.status === 'enabled' ? 'disabled' : 'enabled'
    if (window.confirm(`确认${next === 'enabled' ? '启用' : '停用'}渠道“${item.name}”？`)) {
      updateMutation.mutate({ id: item.id, status: next })
    }
  }, [updateMutation])
  const runClearCredential = useCallback((item: ChannelInstance) => {
    if (window.confirm(`确认清除“${item.name}”的加密凭证？`)) clearCredentialMutation.mutate(item.id)
  }, [clearCredentialMutation])

  const instanceColumns = useMemo<Array<AdminTableColumn<ChannelInstance>>>(() => [
    {
      key: 'instance', label: '渠道实例',
      render: (row) => <div className="table-primary"><strong>{row.name}</strong><code>{channelPlatformLabels[row.platform]} · {row.implementation_status === 'ready' ? '正式实现' : '占位实现'}</code></div>,
    },
    {
      key: 'status', label: '状态 / 健康',
      render: (row) => <div className="channel-status-stack"><span className={`entity-status ${row.status === 'enabled' ? 'active' : 'disabled'}`}>{row.status === 'enabled' ? '已启用' : '已停用'}</span><span>{channelHealthLabels[row.health_status]}</span></div>,
    },
    { key: 'credential', label: '凭证', render: (row) => row.platform === 'web' ? '无需凭证' : row.credential_configured ? '已加密配置' : '尚未配置' },
    { key: 'limit', label: '限流', render: (row) => `${row.rate_limit_per_minute} / 分钟` },
    { key: 'capabilities', label: '能力', render: (row) => <div className="permission-tags">{capabilityLabels(row).map((item) => <span key={item}>{item}</span>)}</div> },
    {
      key: 'actions', label: '操作',
      render: (row) => <div className="table-actions">
        <button disabled={!canWrite || testMutation.isPending} onClick={() => testMutation.mutate(row.id)}><RefreshCw size={12} />连接测试</button>
        <button disabled={!canWrite} onClick={() => runToggle(row)}>{row.status === 'enabled' ? <Unplug size={12} /> : <RadioTower size={12} />}{row.status === 'enabled' ? '停用' : '启用'}</button>
        <button disabled={!canSend || row.status !== 'enabled' || row.implementation_status !== 'ready' || (row.platform !== 'web' && !row.credential_configured)} onClick={() => setDeliveryChannelId(row.id)}><Send size={12} />选择发送</button>
        {row.platform !== 'web' && row.credential_configured && <button disabled={!canManageCredential} onClick={() => runClearCredential(row)}><KeyRound size={12} />清除凭证</button>}
      </div>,
    },
  ], [canManageCredential, canSend, canWrite, runClearCredential, runToggle, testMutation])
  const eventColumns = useMemo<Array<AdminTableColumn<ChannelDiagnosticEvent>>>(() => [
    { key: 'event', label: '事件', render: (row) => <div className="table-primary"><strong>{displayLabel(channelEventTypeLabels, row.event_type)}</strong><code>{channelEventDirectionLabels[row.direction]} · {row.id}</code></div> },
    { key: 'status', label: '结果', render: (row) => <span className={`entity-status task-${row.status}`}>{channelEventStatusLabels[row.status]}</span> },
    { key: 'summary', label: '安全摘要', render: (row) => <span className="audit-detail">{formatMetadataEntries(row.payload_summary)}</span> },
    { key: 'degradation', label: '降级 / 错误', render: (row) => degradationLabels(row.degradations).join('、') || row.error_code || '无' },
    { key: 'time', label: '时间', render: (row) => new Date(row.occurred_at).toLocaleString('zh-CN') },
  ], [])
  const operationError = createMutation.error ?? testMutation.error ?? updateMutation.error ?? credentialMutation.error ?? clearCredentialMutation.error ?? simulation.error ?? delivery.error

  return (
    <div className="page">
      <section className="page-heading compact">
        <div><p className="eyebrow">多模态与平台接入控制平面</p><h1>渠道与适配器</h1><p>统一管理能力协商、实例、凭证、健康、限流和安全诊断；Telegram 已开放正式出站，飞书与 Discord 仍为零副作用占位。</p></div>
        <span className="phase-tag">Telegram 出站就绪</span>
      </section>

      {(catalog.isError || models.isError || instances.isError || events.isError) && <div className="notice error">渠道数据读取失败，请检查 API 与迁移状态。</div>}
      {(formError || operationError) && <div className="notice error">{formError || operationError?.message}</div>}
      <div className="notice info"><ShieldCheck size={17} /><div><strong>凭证只写入信封加密存储</strong><span>API、页面、诊断事件和审计记录只展示是否已配置；能力降级会明确列出，不会静默丢弃图片、文件、线程或流式语义。</span></div></div>
      <div className="notice info"><RadioTower size={17} /><div><strong>渠道严格归属当前智能体</strong><span>创建、凭证、连接测试、收发模拟和诊断事件均按全局选择隔离；当前标识：{selectedAgentId ?? '默认智能体'}。</span></div></div>
      <div className="notice info"><Send size={17} /><div><strong>Telegram 当前只开放安全出站</strong><span>支持纯文本主动消息、Forum 话题和消息编辑；Markdown、流式与附件会透明降级，真实入站将在后续独立阶段接通。</span></div></div>

      <section className="channel-catalog-grid" aria-label="渠道适配器能力目录">
        {(catalog.data?.items ?? []).map((item) => <article className="panel channel-catalog-card" key={item.platform}>
          <div className="panel-heading"><div><span>{item.implementation_status === 'ready' ? '正式实现' : '契约占位'}</span><h2>{item.display_name}</h2></div><Cable size={19} /></div>
          <div className="permission-tags">{Object.entries(item.capabilities).filter(([, enabled]) => enabled === true).map(([key]) => <span key={key}>{displayLabel(channelCapabilityLabels, key)}</span>)}</div>
          <p>文本上限 {item.capabilities.max_text_chars.toLocaleString('zh-CN')} 字符 · 内容块 {item.capabilities.max_blocks} · {item.credential_required ? '需要凭证' : '无需凭证'}</p>
        </article>)}
      </section>

      <section className="panel channel-form-panel">
        <div className="panel-heading"><div><span>实例与密钥</span><h2>新建渠道实例</h2></div></div>
        <form className="channel-create-form" onSubmit={submitCreate}>
          <label><span>名称</span><input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} required /></label>
          <label><span>平台</span><select value={platform} onChange={(event) => setPlatform(event.target.value as ChannelPlatform)}>{Object.entries(channelPlatformLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
          <label><span>每分钟上限</span><input type="number" min={1} max={10_000} value={rateLimit} onChange={(event) => setRateLimit(Number(event.target.value))} required /></label>
          <label><span>凭证（只写）</span><input type="password" autoComplete="new-password" value={credential} disabled={platform === 'web'} onChange={(event) => setCredential(event.target.value)} placeholder={platform === 'web' ? '内部 Web 无需凭证' : '可稍后安全写入'} /></label>
          <label className="channel-json-field"><span>公开设置 JSON</span><textarea value={settingsText} onChange={(event) => setSettingsText(event.target.value)} rows={3} spellCheck={false} /></label>
          <button className="primary-button" type="submit" disabled={!canWrite || createMutation.isPending}><Plus size={14} />创建实例</button>
        </form>
      </section>

      {(instances.data?.items.some((item) => item.platform !== 'web') ?? false) && <section className="panel channel-credential-panel">
        <div className="panel-heading"><div><span>只写操作</span><h2>写入或轮换渠道凭证</h2></div></div>
        <form className="channel-credential-form" onSubmit={(event) => { event.preventDefault(); if (credentialChannelId && credential) credentialMutation.mutate({ id: credentialChannelId, value: credential }) }}>
          <label><span>渠道实例</span><select value={credentialChannelId} onChange={(event) => setCredentialChannelId(event.target.value)} required><option value="">选择外部渠道</option>{instances.data?.items.filter((item) => item.platform !== 'web').map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
          <label><span>新凭证</span><input type="password" autoComplete="new-password" value={credential} onChange={(event) => setCredential(event.target.value)} required /></label>
          <button className="secondary-button" type="submit" disabled={!canManageCredential || !credentialChannelId || !credential}><KeyRound size={14} />安全写入</button>
        </form>
      </section>}

      <section className="panel table-panel">
        <div className="panel-heading task-panel-heading"><div><span>运行实例</span><h2>渠道实例</h2></div><small>{instances.data?.items.length ?? 0} 个实例</small></div>
        <AdminDataTable rows={instances.data?.items ?? []} columns={instanceColumns} rowKey={(row) => row.id} searchableText={(row) => `${row.name} ${row.platform} ${row.status} ${row.health_status}`} searchPlaceholder="搜索渠道、平台或健康状态" emptyMessage={instances.isLoading ? '正在读取渠道…' : '尚未创建渠道实例'} />
      </section>

      <section className="channel-lab-grid">
        <article className="panel channel-simulator">
          <div className="panel-heading"><div><span>无外部副作用</span><h2>平台能力模拟器</h2></div><FlaskConical size={19} /></div>
          <div className="channel-simulator-form">
            <label><span>目标平台</span><select value={simulationPlatform} onChange={(event) => setSimulationPlatform(event.target.value as ChannelPlatform)}>{Object.entries(channelPlatformLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            <label><span>Markdown 内容</span><textarea value={simulationText} onChange={(event) => setSimulationText(event.target.value)} rows={4} required /></label>
            <div className="channel-options"><label><input type="checkbox" checked={requestStreaming} onChange={(event) => setRequestStreaming(event.target.checked)} />流式</label><label><input type="checkbox" checked={requestThread} onChange={(event) => setRequestThread(event.target.checked)} />线程</label><label><input type="checkbox" checked={requestEdit} onChange={(event) => setRequestEdit(event.target.checked)} />编辑</label><label><input type="checkbox" checked={requestProactive} onChange={(event) => setRequestProactive(event.target.checked)} />主动消息</label></div>
            <button className="secondary-button" disabled={!simulationText.trim() || simulation.isPending} onClick={() => simulation.mutate()}><FlaskConical size={14} />运行能力协商</button>
          </div>
          {simulation.data && <div className="channel-simulation-result" aria-live="polite"><strong>{simulation.data.degradations.length ? '发生透明降级' : '目标能力完整支持'}</strong><span>{degradationLabels(simulation.data.degradations).join('、') || '无降级'}</span><small>输出 {simulation.data.blocks.length} 个内容块 · {simulation.data.buffered ? '缓冲后发送' : '保持流式'}</small></div>}
        </article>

        <article className="panel">
          <div className="panel-heading"><div><span>输入与运行能力</span><h2>模型能力矩阵</h2></div></div>
          <div className="model-capability-list">{(models.data?.items ?? []).map((item) => <div key={`${item.provider}:${item.model_family}`}><strong>{item.provider} / {item.model_family}</strong><span>输入：{modelInputs(item)}</span><small>{item.streaming ? '流式' : '非流式'} · {item.structured_output ? '结构化输出' : '文本输出'} · {item.tool_calling ? '工具调用' : '无工具调用'}</small></div>)}</div>
        </article>
      </section>

      <section className="panel channel-form-panel">
        <div className="panel-heading"><div><span>真实外部副作用</span><h2>出站消息联调</h2></div><Send size={19} /></div>
        <form className="channel-create-form" onSubmit={(event) => {
          event.preventDefault()
          if (deliveryTarget?.platform !== 'web' && !window.confirm(`确认通过“${deliveryTarget?.name ?? '外部渠道'}”向 ${recipientId.trim()} 发送真实外部消息？`)) return
          delivery.mutate()
        }}>
          <label><span>启用的正式渠道</span><select value={deliveryChannelId} onChange={(event) => setDeliveryChannelId(event.target.value)} required><option value="">选择渠道实例</option>{instances.data?.items.filter((item) => item.status === 'enabled' && item.implementation_status === 'ready' && (item.platform === 'web' || item.credential_configured)).map((item) => <option value={item.id} key={item.id}>{item.name} · {channelPlatformLabels[item.platform]}</option>)}</select></label>
          <label><span>收件人 / chat ID</span><input value={recipientId} maxLength={255} onChange={(event) => setRecipientId(event.target.value)} placeholder="Telegram 整数 chat ID 或 @channel_username" required /></label>
          <label><span>话题 ID（可选）</span><input value={deliveryThreadId} inputMode="numeric" maxLength={255} onChange={(event) => setDeliveryThreadId(event.target.value)} placeholder="Forum message_thread_id" /></label>
          <label><span>编辑消息 ID（可选）</span><input value={deliveryEditMessageId} inputMode="numeric" maxLength={255} onChange={(event) => setDeliveryEditMessageId(event.target.value)} placeholder="填写后执行 editMessageText" /></label>
          <label className="channel-json-field"><span>纯文本消息</span><textarea value={deliveryText} maxLength={deliveryTarget?.capabilities.max_text_chars ?? 20_000} onChange={(event) => setDeliveryText(event.target.value)} rows={4} required /></label>
          <button className="primary-button" type="submit" disabled={!canSend || !deliveryChannelId || !recipientId.trim() || !deliveryText.trim() || delivery.isPending}><Send size={14} />确认发送</button>
        </form>
      </section>

      {delivery.data && <div className="notice success"><Send size={17} /><div><strong>发送测试已由渠道适配器接收</strong><span>{channelEventStatusLabels[delivery.data.status]} · {delivery.data.external_message_id} · {degradationLabels(delivery.data.degradations).join('、') || '无降级'}</span></div></div>}

      <section className="panel table-panel">
        <div className="panel-heading task-panel-heading"><div><span>无正文审计</span><h2>事件诊断</h2></div><small>最近 {events.data?.items.length ?? 0} 条</small></div>
        <AdminDataTable rows={events.data?.items ?? []} columns={eventColumns} rowKey={(row) => row.id} searchableText={(row) => `${row.event_type} ${row.status} ${row.error_code ?? ''} ${row.degradations.join(' ')}`} searchPlaceholder="搜索事件类型、结果或错误码" emptyMessage={events.isLoading ? '正在读取诊断事件…' : '暂无诊断事件'} />
      </section>
    </div>
  )
}
