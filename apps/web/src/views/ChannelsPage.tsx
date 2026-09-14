import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, BellRing, Cable, CheckCircle2, FlaskConical, HeartPulse, KeyRound, Plus, RadioTower, RefreshCw, Send, ShieldCheck, TriangleAlert, Unplug } from 'lucide-react'
import { useCallback, useMemo, useState, type FormEvent } from 'react'

import {
  clearChannelCredential,
  acknowledgeChannelAlert,
  createChannelInstance,
  deliverChannelMessage,
  getAdminSession,
  getChannelCatalog,
  getChannelEvents,
  getChannelAlerts,
  getChannelAlertLifecycles,
  getChannelErrorMetrics,
  getChannelHealthTrend,
  getChannelInstances,
  getChannelNotificationTimeline,
  getChannelOperationMetrics,
  getModelCapabilities,
  notifyChannelAlerts,
  queueChannelAlertsNotification,
  getTelegramWebhookStatus,
  clearTelegramWebhook,
  registerTelegramWebhook,
  setChannelCredential,
  simulateChannelAlertPolicy,
  suppressChannelAlert,
  simulateChannel,
  testChannelConnection,
  unsuppressChannelAlert,
  updateChannelInstance,
  type ChannelDiagnosticEvent,
  type ChannelAlert,
  type AlertPolicySimulation,
  type ChannelAlertLifecycle,
  type ChannelAlertLifecycleStatus,
  type ChannelErrorMetric,
  type ChannelHealthSnapshot,
  type ChannelInstance,
  type ChannelOperationMetrics,
  type ChannelPlatform,
  type ModelCapabilityProfile,
  type BackgroundJobStatus,
  type NotificationDeliveryEvent,
  type NotificationDeliveryTimelineItem,
} from '../api'
import { useSelectedAgentId } from '../agentSelection'
import { AdminDataTable, type AdminTableColumn } from '../components/AdminDataTable'
import {
  channelCapabilityLabels,
  channelAlertLifecycleStatusLabels,
  channelDegradationLabels,
  channelEventDirectionLabels,
  channelEventStatusLabels,
  channelEventTypeLabels,
  channelHealthLabels,
  channelPlatformLabels,
  backgroundJobStatusLabels,
  displayLabel,
  formatMetadataEntries,
  notificationDeliveryEventLabels,
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

function formatMetricTime(value: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN') : '无'
}

const alertSeverityLabels = { warning: '警告', critical: '严重' } as const
const healthSnapshotLabels = {
  healthy: '健康',
  degraded: '降级',
  not_configured: '未配置',
  disabled: '已停用',
} as const
const notificationAdapterLabels = {
  webhook: '通用 Webhook',
  feishu_webhook: '飞书 Webhook',
  email: '电子邮件',
} as const

function localDateTimeInputValue() {
  const now = new Date()
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 16)
}

function notificationAdapterLabel(value?: string | null) {
  if (!value) return '无通知路由'
  return notificationAdapterLabels[value as keyof typeof notificationAdapterLabels] ?? value
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
  const [telegramChannelId, setTelegramChannelId] = useState('')
  const [webhookUrl, setWebhookUrl] = useState('')
  const [dropPendingUpdates, setDropPendingUpdates] = useState(false)
  const [webhookConfirmed, setWebhookConfirmed] = useState(false)
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
  const [metricsWindow, setMetricsWindow] = useState(60)
  const [alertNotificationWindow, setAlertNotificationWindow] = useState(60)
  const [policySeverity, setPolicySeverity] = useState<'warning' | 'critical'>('critical')
  const [policyDuration, setPolicyDuration] = useState(30)
  const [policyCurrentLevel, setPolicyCurrentLevel] = useState(0)
  const [policyEvaluatedAt, setPolicyEvaluatedAt] = useState(localDateTimeInputValue)
  const [lifecycleStatus, setLifecycleStatus] = useState<ChannelAlertLifecycleStatus | ''>('')
  const [lifecycleSeverity, setLifecycleSeverity] = useState<'warning' | 'critical' | ''>('')
  const [lifecycleChannelId, setLifecycleChannelId] = useState('')
  const [notificationStatus, setNotificationStatus] = useState<BackgroundJobStatus | ''>('')
  const [notificationAdapter, setNotificationAdapter] = useState('')
  const [notificationEvent, setNotificationEvent] = useState<NotificationDeliveryEvent | ''>('')
  const [formError, setFormError] = useState('')

  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const catalog = useQuery({ queryKey: ['channel-catalog'], queryFn: getChannelCatalog })
  const models = useQuery({ queryKey: ['model-capabilities'], queryFn: getModelCapabilities })
  const instances = useQuery({
    queryKey: ['channel-instances', selectedAgentId],
    queryFn: getChannelInstances,
  })
  const telegramChannels = instances.data?.items.filter((item) => item.platform === 'telegram') ?? []
  const selectedTelegramChannelId = telegramChannelId || telegramChannels[0]?.id || ''
  const webhook = useQuery({
    queryKey: ['telegram-webhook', selectedTelegramChannelId],
    queryFn: () => getTelegramWebhookStatus(selectedTelegramChannelId),
    enabled: Boolean(selectedTelegramChannelId),
  })
  const events = useQuery({
    queryKey: ['channel-events', selectedAgentId],
    queryFn: () => getChannelEvents(),
  })
  const connectionTests = useQuery({
    queryKey: ['channel-connection-tests', selectedAgentId],
    queryFn: () => getChannelEvents(undefined, 'connection.tested'),
  })
  const operationMetrics = useQuery({
    queryKey: ['channel-operation-metrics', selectedAgentId, metricsWindow],
    queryFn: () => getChannelOperationMetrics(undefined, metricsWindow),
  })
  const errorMetrics = useQuery({
    queryKey: ['channel-error-metrics', selectedAgentId, metricsWindow],
    queryFn: () => getChannelErrorMetrics(undefined, metricsWindow),
  })
  const healthTrend = useQuery({
    queryKey: ['channel-health-trend', selectedAgentId, metricsWindow],
    queryFn: () => getChannelHealthTrend(undefined, metricsWindow, 200),
  })
  const alerts = useQuery({
    queryKey: ['channel-alerts', selectedAgentId, metricsWindow],
    queryFn: () => getChannelAlerts(undefined, metricsWindow),
  })
  const alertLifecycles = useQuery({
    queryKey: [
      'channel-alert-lifecycles',
      selectedAgentId,
      lifecycleStatus,
      lifecycleSeverity,
      lifecycleChannelId,
    ],
    queryFn: () => getChannelAlertLifecycles({
      status: lifecycleStatus || undefined,
      severity: lifecycleSeverity || undefined,
      channel_id: lifecycleChannelId || undefined,
      limit: 200,
    }),
  })
  const notificationTimeline = useQuery({
    queryKey: [
      'channel-alert-notifications',
      selectedAgentId,
      notificationStatus,
      notificationAdapter,
      notificationEvent,
    ],
    queryFn: () => getChannelNotificationTimeline({
      status: notificationStatus || undefined,
      adapter: notificationAdapter || undefined,
      event: notificationEvent || undefined,
      limit: 200,
    }),
  })
  const canWrite = session.data?.permissions.includes('channel:write') ?? false
  const canSend = session.data?.permissions.includes('channel:send') ?? false
  const canManageCredential = session.data?.permissions.includes('channel_credential:manage') ?? false
  const canManageAlerts = session.data?.permissions.includes('channel_alert:manage') ?? false
  const canManageNotifications = session.data?.permissions.includes('channel_notification:manage') ?? false
  const deliveryTarget = instances.data?.items.find((item) => item.id === deliveryChannelId)

  const refresh = async () => {
    await Promise.all([
      invalidateAcrossTabs(queryClient, ['channel-instances', selectedAgentId]),
      invalidateAcrossTabs(queryClient, ['channel-events', selectedAgentId]),
      invalidateAcrossTabs(queryClient, ['channel-connection-tests', selectedAgentId]),
      invalidateAcrossTabs(queryClient, ['channel-operation-metrics', selectedAgentId, metricsWindow]),
      invalidateAcrossTabs(queryClient, ['channel-error-metrics', selectedAgentId, metricsWindow]),
      invalidateAcrossTabs(queryClient, ['channel-health-trend', selectedAgentId, metricsWindow]),
      invalidateAcrossTabs(queryClient, ['channel-alerts', selectedAgentId, metricsWindow]),
      invalidateAcrossTabs(queryClient, ['channel-alert-lifecycles', selectedAgentId]),
      invalidateAcrossTabs(queryClient, ['channel-alert-notifications', selectedAgentId]),
      ...(selectedTelegramChannelId
        ? [invalidateAcrossTabs(queryClient, ['telegram-webhook', selectedTelegramChannelId])]
        : []),
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
  const webhookStatusMutation = useMutation({
    mutationFn: getTelegramWebhookStatus,
    onSuccess: (result) => queryClient.setQueryData(['telegram-webhook', result.channel_id], result),
  })
  const registerWebhookMutation = useMutation({
    mutationFn: () => registerTelegramWebhook(selectedTelegramChannelId, {
      webhook_url: webhookUrl.trim(),
      drop_pending_updates: dropPendingUpdates,
      confirmed: webhookConfirmed,
    }),
    onSuccess: async () => {
      setWebhookConfirmed(false)
      await refresh()
    },
  })
  const clearWebhookMutation = useMutation({
    mutationFn: () => clearTelegramWebhook(selectedTelegramChannelId, {
      drop_pending_updates: dropPendingUpdates,
      confirmed: webhookConfirmed,
    }),
    onSuccess: async () => {
      setWebhookConfirmed(false)
      await refresh()
    },
  })
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
  const alertDispositionMutation = useMutation({
    mutationFn: ({ action, channelId, alertKey, reason, expiresAt }: {
      action: 'acknowledge' | 'suppress'
      channelId: string
      alertKey: string
      reason: string
      expiresAt?: string | null
    }) => action === 'acknowledge'
      ? acknowledgeChannelAlert(channelId, { alert_key: alertKey, reason, expires_at: expiresAt, confirmed: true })
      : suppressChannelAlert(channelId, { alert_key: alertKey, reason, expires_at: expiresAt ?? '', confirmed: true }),
    onSuccess: refresh,
  })
  const unsuppressMutation = useMutation({
    mutationFn: ({ channelId, alertKey }: { channelId: string; alertKey: string }) =>
      unsuppressChannelAlert(channelId, { alert_key: alertKey, confirmed: true }),
    onSuccess: refresh,
  })
  const alertNotificationMutation = useMutation({
    mutationFn: () => notifyChannelAlerts({ window_minutes: alertNotificationWindow, confirmed: true }),
    onSuccess: refresh,
  })
  const alertNotificationQueueMutation = useMutation({
    mutationFn: () => queueChannelAlertsNotification({ window_minutes: alertNotificationWindow, confirmed: true }),
    onSuccess: refresh,
  })
  const policySimulation = useMutation<AlertPolicySimulation>({
    mutationFn: () => simulateChannelAlertPolicy({
      severity: policySeverity,
      duration_minutes: policyDuration,
      current_level: policyCurrentLevel,
      evaluated_at: new Date(policyEvaluatedAt).toISOString(),
    }),
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
    { key: 'inbound', label: '入站 Webhook', render: (row) => row.platform === 'telegram' ? row.inbound_webhook_configured ? '密钥已加密配置' : '尚未配置密钥' : '不适用' },
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
  const instanceNames = useMemo(
    () => new Map((instances.data?.items ?? []).map((item) => [item.id, item.name])),
    [instances.data?.items],
  )
  const operationMetricColumns = useMemo<Array<AdminTableColumn<ChannelOperationMetrics>>>(() => [
    {
      key: 'channel', label: '渠道实例',
      render: (row) => <div className="table-primary"><strong>{instanceNames.get(row.channel_id) ?? '未知渠道'}</strong><code>{row.channel_id}</code></div>,
    },
    { key: 'traffic', label: '入站 / 出站', render: (row) => `${row.inbound_events} / ${row.outbound_events}` },
    { key: 'outcomes', label: '送达 / 降级', render: (row) => `${row.outbound_delivered} / ${row.outbound_degraded}` },
    { key: 'failures', label: '失败 / 限流', render: (row) => `${row.outbound_failed} / ${row.outbound_rate_limited}` },
    { key: 'failure-rate', label: '失败率', render: (row) => `${row.outbound_failure_rate_percent.toFixed(2)}%` },
    { key: 'last-failure', label: '最近失败', render: (row) => formatMetricTime(row.last_failure_at) },
  ], [instanceNames])
  const errorMetricColumns = useMemo<Array<AdminTableColumn<ChannelErrorMetric>>>(() => [
    {
      key: 'channel', label: '渠道实例',
      render: (row) => <div className="table-primary"><strong>{instanceNames.get(row.channel_id) ?? '未知渠道'}</strong><code>{row.channel_id}</code></div>,
    },
    { key: 'error-code', label: '安全错误码', render: (row) => <code>{row.error_code}</code> },
    { key: 'occurrences', label: '发生次数', render: (row) => row.occurrences.toLocaleString('zh-CN') },
    { key: 'last-occurred', label: '最近发生', render: (row) => new Date(row.last_occurred_at).toLocaleString('zh-CN') },
  ], [instanceNames])
  const healthSnapshotColumns = useMemo<Array<AdminTableColumn<ChannelHealthSnapshot>>>(() => [
    {
      key: 'channel', label: '渠道实例',
      render: (row) => <div className="table-primary"><strong>{instanceNames.get(row.channel_id) ?? '未知渠道'}</strong><code>{row.platform} · {row.channel_id}</code></div>,
    },
    { key: 'status', label: '健康状态', render: (row) => <span className={`entity-status task-${row.status}`}>{healthSnapshotLabels[row.status]}</span> },
    { key: 'configured', label: '配置', render: (row) => row.configured ? '已配置' : '未配置' },
    { key: 'pending', label: '待处理更新', render: (row) => row.pending_update_count.toLocaleString('zh-CN') },
    { key: 'remote-error', label: '远端错误', render: (row) => row.remote_error_present ? '有记录' : '无记录' },
    { key: 'sampled', label: '采样时间', render: (row) => new Date(row.sampled_at).toLocaleString('zh-CN') },
  ], [instanceNames])
  const connectionTestColumns = useMemo<Array<AdminTableColumn<ChannelDiagnosticEvent>>>(() => [
    {
      key: 'channel', label: '渠道实例',
      render: (row) => <div className="table-primary"><strong>{instanceNames.get(row.channel_id) ?? '未知渠道'}</strong><code>{row.channel_id}</code></div>,
    },
    { key: 'status', label: '探测结果', render: (row) => <span className={`entity-status task-${row.status}`}>{channelEventStatusLabels[row.status]}</span> },
    { key: 'error', label: '安全错误码', render: (row) => row.error_code ? <code>{row.error_code}</code> : '无' },
    { key: 'checked', label: '探测时间', render: (row) => new Date(row.occurred_at).toLocaleString('zh-CN') },
  ], [instanceNames])
  const runAlertDisposition = useCallback((row: ChannelAlert, action: 'acknowledge' | 'suppress') => {
    const reason = window.prompt(action === 'suppress' ? '请输入抑制原因' : '请输入确认备注', row.disposition_reason ?? '')?.trim()
    if (!reason) return
    let expiresAt: string | null = null
    if (action === 'suppress') {
      const input = window.prompt('请输入抑制到期时间（ISO 8601，例如 2026-09-14T12:00:00+08:00）')?.trim()
      if (!input) return
      const parsed = new Date(input)
      if (Number.isNaN(parsed.getTime()) || parsed.getTime() <= Date.now()) {
        setFormError('抑制到期时间必须是未来的有效时间')
        return
      }
      expiresAt = parsed.toISOString()
    }
    if (!window.confirm(`确认${action === 'suppress' ? '抑制' : '确认'}告警“${row.title}”？`)) return
    alertDispositionMutation.mutate({ action, channelId: row.channel_id, alertKey: row.alert_key, reason, expiresAt })
  }, [alertDispositionMutation])
  const runUnsuppress = useCallback((row: ChannelAlert) => {
    if (window.confirm(`确认解除告警“${row.title}”的抑制？`)) {
      unsuppressMutation.mutate({ channelId: row.channel_id, alertKey: row.alert_key })
    }
  }, [unsuppressMutation])
  const alertColumns = useMemo<Array<AdminTableColumn<ChannelAlert>>>(() => [
    {
      key: 'alert', label: '告警',
      render: (row) => <div className="table-primary"><strong>{row.title}</strong><code>{instanceNames.get(row.channel_id) ?? row.channel_id}</code><span className="audit-detail">{row.summary}</span></div>,
    },
    { key: 'severity', label: '级别', render: (row) => <span className={`entity-status task-${row.severity}`}>{alertSeverityLabels[row.severity]}</span> },
    { key: 'code', label: '规则 / 错误码', render: (row) => <span><code>{row.code}</code>{row.error_code ? ` · ${row.error_code}` : ''}</span> },
    { key: 'value', label: '当前 / 阈值', render: (row) => `${row.current_value.toFixed(2)} / ${row.threshold_value.toFixed(2)} ${row.unit}` },
    { key: 'occurrences', label: '聚合次数', render: (row) => row.occurrences.toLocaleString('zh-CN') },
    { key: 'cooldown', label: '冷却至', render: (row) => new Date(row.cooldown_until).toLocaleString('zh-CN') },
    {
      key: 'disposition', label: '处置',
      render: (row) => row.disposition_status
        ? <div className="table-primary"><span className={`entity-status task-${row.disposition_status}`}>{row.disposition_status === 'suppressed' ? '已抑制' : '已确认'}</span><small>{row.disposition_reason ?? ''}{row.disposition_expires_at ? ` · ${new Date(row.disposition_expires_at).toLocaleString('zh-CN')}` : ''}</small></div>
        : '未处置',
    },
    {
      key: 'actions', label: '操作',
      render: (row) => <div className="table-actions">
        {!row.disposition_status && <>
          <button disabled={!canManageAlerts || alertDispositionMutation.isPending} onClick={() => runAlertDisposition(row, 'acknowledge')}><CheckCircle2 size={12} />确认</button>
          <button disabled={!canManageAlerts || alertDispositionMutation.isPending} onClick={() => runAlertDisposition(row, 'suppress')}><ShieldCheck size={12} />抑制</button>
        </>}
        {row.disposition_status === 'suppressed' && <button disabled={!canManageAlerts || unsuppressMutation.isPending} onClick={() => runUnsuppress(row)}><RefreshCw size={12} />解除抑制</button>}
      </div>,
    },
  ], [alertDispositionMutation.isPending, canManageAlerts, instanceNames, runAlertDisposition, runUnsuppress, unsuppressMutation.isPending])
  const lifecycleColumns = useMemo<Array<AdminTableColumn<ChannelAlertLifecycle>>>(() => [
    {
      key: 'alert', label: '规则 / 渠道',
      render: (row) => <div className="table-primary"><strong><code>{row.code}</code></strong><span>{instanceNames.get(row.channel_id) ?? row.channel_id}</span></div>,
    },
    { key: 'status', label: '状态', render: (row) => <span className={`entity-status task-${row.status}`}>{channelAlertLifecycleStatusLabels[row.status]}</span> },
    { key: 'severity', label: '级别', render: (row) => <span className={`entity-status task-${row.severity}`}>{alertSeverityLabels[row.severity]}</span> },
    { key: 'error-code', label: '安全错误码', render: (row) => row.error_code ? <code>{row.error_code}</code> : '无' },
    { key: 'occurrences', label: '次数', render: (row) => row.occurrences.toLocaleString('zh-CN') },
    { key: 'first', label: '首次发生', render: (row) => new Date(row.first_occurred_at).toLocaleString('zh-CN') },
    { key: 'latest', label: '最近发生', render: (row) => new Date(row.last_occurred_at).toLocaleString('zh-CN') },
    {
      key: 'duration', label: '持续 / 恢复',
      render: (row) => {
        const seconds = row.recovery_duration_seconds
          ?? Math.max(0, (Date.now() - new Date(row.first_occurred_at).getTime()) / 1_000)
        const minutes = Math.round(seconds / 60)
        return `${row.status === 'resolved' ? '恢复耗时' : '已持续'} ${minutes.toLocaleString('zh-CN')} 分钟`
      },
    },
    {
      key: 'escalation', label: '升级',
      render: (row) => <div className="table-primary">
        <strong>L{row.escalation_level}</strong>
        <small>{row.escalated_at ? `首次 ${new Date(row.escalated_at).toLocaleString('zh-CN')}` : '尚未升级'}</small>
        {row.last_escalated_at && <small>最近 {new Date(row.last_escalated_at).toLocaleString('zh-CN')}</small>}
      </div>,
    },
  ], [instanceNames])
  const notificationColumns = useMemo<Array<AdminTableColumn<NotificationDeliveryTimelineItem>>>(() => [
    {
      key: 'event', label: '事件',
      render: (row) => <div className="table-primary"><strong>{notificationDeliveryEventLabels[row.event]}</strong><code>{notificationAdapterLabels[row.adapter as keyof typeof notificationAdapterLabels] ?? row.adapter}</code></div>,
    },
    { key: 'status', label: '状态', render: (row) => <span className={`entity-status task-${row.status}`}>{backgroundJobStatusLabels[row.status]}</span> },
    { key: 'alerts', label: '告警数', render: (row) => row.alert_count.toLocaleString('zh-CN') },
    { key: 'attempts', label: '尝试次数', render: (row) => `${row.attempt_count} / ${row.max_attempts}` },
    { key: 'failures', label: '连续失败', render: (row) => row.consecutive_failures.toLocaleString('zh-CN') },
    { key: 'result', label: '结果', render: (row) => row.delivered === true ? `已送达${row.status_code ? ` · ${row.status_code}` : ''}` : row.last_error_code ? <code>{row.last_error_code}</code> : '等待结果' },
    { key: 'elapsed', label: '耗时', render: (row) => row.elapsed_ms === null || row.elapsed_ms === undefined ? '—' : `${row.elapsed_ms} ms` },
    { key: 'created', label: '创建时间', render: (row) => new Date(row.created_at).toLocaleString('zh-CN') },
  ], [])
  const metricTotals = useMemo(() => {
    const totals = (operationMetrics.data?.items ?? []).reduce(
      (result, row) => ({
        inbound_events: result.inbound_events + row.inbound_events,
        outbound_events: result.outbound_events + row.outbound_events,
        outbound_delivered: result.outbound_delivered + row.outbound_delivered,
        outbound_degraded: result.outbound_degraded + row.outbound_degraded,
        outbound_failed: result.outbound_failed + row.outbound_failed,
        outbound_rate_limited: result.outbound_rate_limited + row.outbound_rate_limited,
        last_failure_at: result.last_failure_at && row.last_failure_at
          ? (result.last_failure_at > row.last_failure_at ? result.last_failure_at : row.last_failure_at)
          : result.last_failure_at ?? row.last_failure_at,
      }),
      {
        inbound_events: 0,
        outbound_events: 0,
        outbound_delivered: 0,
        outbound_degraded: 0,
        outbound_failed: 0,
        outbound_rate_limited: 0,
        last_failure_at: null as string | null,
      },
    )
    const attempts = totals.outbound_delivered + totals.outbound_degraded + totals.outbound_failed + totals.outbound_rate_limited
    return {
      ...totals,
      failure_rate: attempts ? ((totals.outbound_failed + totals.outbound_rate_limited) * 100) / attempts : 0,
    }
  }, [operationMetrics.data?.items])
  const hasOperationFailure = (operationMetrics.data?.items ?? []).some((item) => item.outbound_failure_rate_percent > 0)
  const eventColumns = useMemo<Array<AdminTableColumn<ChannelDiagnosticEvent>>>(() => [
    { key: 'event', label: '事件', render: (row) => <div className="table-primary"><strong>{displayLabel(channelEventTypeLabels, row.event_type)}</strong><code>{channelEventDirectionLabels[row.direction]} · {row.id}</code></div> },
    { key: 'status', label: '结果', render: (row) => <span className={`entity-status task-${row.status}`}>{channelEventStatusLabels[row.status]}</span> },
    { key: 'summary', label: '安全摘要', render: (row) => <span className="audit-detail">{formatMetadataEntries(row.payload_summary)}</span> },
    { key: 'degradation', label: '降级 / 错误', render: (row) => degradationLabels(row.degradations).join('、') || row.error_code || '无' },
    { key: 'time', label: '时间', render: (row) => new Date(row.occurred_at).toLocaleString('zh-CN') },
  ], [])
  const notifyAlerts = useCallback(() => {
    if (window.confirm(`确认投递最近 ${alertNotificationWindow} 分钟的活动告警？`)) {
      alertNotificationQueueMutation.mutate()
    }
  }, [alertNotificationQueueMutation, alertNotificationWindow])
  const operationError = createMutation.error ?? testMutation.error ?? updateMutation.error ?? credentialMutation.error ?? clearCredentialMutation.error ?? webhookStatusMutation.error ?? registerWebhookMutation.error ?? clearWebhookMutation.error ?? simulation.error ?? delivery.error ?? alertDispositionMutation.error ?? unsuppressMutation.error ?? alertNotificationMutation.error ?? alertNotificationQueueMutation.error ?? policySimulation.error

  return (
    <div className="page">
      <section className="page-heading compact">
        <div><p className="eyebrow">多模态与平台接入控制平面</p><h1>渠道与适配器</h1><p>统一管理能力协商、实例、凭证、健康、限流和安全诊断；Telegram 已支持安全出站与文本入站，飞书与 Discord 仍为零副作用占位。</p></div>
        <span className="phase-tag">Telegram 入站已接通</span>
      </section>

      {(catalog.isError || models.isError || instances.isError || events.isError || operationMetrics.isError || errorMetrics.isError || healthTrend.isError || alerts.isError || alertLifecycles.isError || notificationTimeline.isError) && <div className="notice error">渠道数据读取失败，请检查 API 与迁移状态。</div>}
      {(formError || operationError) && <div className="notice error">{formError || operationError?.message}</div>}
      <div className="notice info"><ShieldCheck size={17} /><div><strong>凭证只写入信封加密存储</strong><span>API、页面、诊断事件和审计记录只展示是否已配置；能力降级会明确列出，不会静默丢弃图片、文件、线程或流式语义。</span></div></div>
      <div className="notice info"><RadioTower size={17} /><div><strong>渠道严格归属当前智能体</strong><span>创建、凭证、连接测试、收发模拟和诊断事件均按全局选择隔离；当前标识：{selectedAgentId ?? '默认智能体'}。</span></div></div>
      <div className="notice info"><Send size={17} /><div><strong>Telegram 已接通安全文本入站</strong><span>出站支持纯文本主动消息、Forum 话题和消息编辑；入站仅接受配置了渠道级 Webhook 密钥的文本 message Update，并沿用身份、会话映射和 Inbox 异步处理。</span></div></div>

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

      {telegramChannels.length > 0 && <section className="panel channel-form-panel">
        <div className="panel-heading"><div><span>Telegram 运营</span><h2>Webhook 状态与管理</h2></div><RadioTower size={19} /></div>
        <div className="channel-create-form">
          <label><span>Telegram 渠道实例</span><select value={selectedTelegramChannelId} onChange={(event) => setTelegramChannelId(event.target.value)}><option value="">选择 Telegram 渠道</option>{telegramChannels.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
          <div className="channel-simulation-result" aria-live="polite">
            <strong>{webhook.isLoading ? '正在探测 Webhook' : webhook.data ? channelHealthLabels[webhook.data.status] : '尚未探测'}</strong>
            <span>{webhook.data ? `${webhook.data.configured ? '已注册' : '未注册'} · 待处理更新 ${webhook.data.pending_update_count}` : '选择渠道后可读取 Telegram 安全状态摘要'}</span>
            {webhook.data && <small>{webhook.data.last_error_present ? `最近错误：${webhook.data.last_error_at ? new Date(webhook.data.last_error_at).toLocaleString('zh-CN') : '有记录'}` : '最近探测未发现远端错误'} · 更新类型：{webhook.data.allowed_updates.join('、') || '未配置'}</small>}
          </div>
          <button className="secondary-button" type="button" disabled={!canManageCredential || !selectedTelegramChannelId || webhookStatusMutation.isPending} onClick={() => webhookStatusMutation.mutate(selectedTelegramChannelId)}><RefreshCw size={14} />刷新探测</button>
        </div>
        <form className="channel-create-form" onSubmit={(event) => { event.preventDefault(); registerWebhookMutation.mutate() }}>
          <label><span>HTTPS Webhook URL</span><input type="url" inputMode="url" value={webhookUrl} onChange={(event) => setWebhookUrl(event.target.value)} placeholder="https://example.com/api/v1/webhooks/telegram/..." pattern="https://.*" maxLength={2048} required /></label>
          <label className="channel-options"><span>注册选项</span><span><input type="checkbox" checked={dropPendingUpdates} onChange={(event) => setDropPendingUpdates(event.target.checked)} />丢弃积压更新</span></label>
          <label className="channel-options"><span>明确确认</span><span><input type="checkbox" checked={webhookConfirmed} onChange={(event) => setWebhookConfirmed(event.target.checked)} required />我确认将修改 Telegram Webhook</span></label>
          <button className="primary-button" type="submit" disabled={!canManageCredential || !selectedTelegramChannelId || !webhookUrl.trim() || !webhookConfirmed || registerWebhookMutation.isPending}><RadioTower size={14} />注册 Webhook</button>
          <button className="secondary-button" type="button" disabled={!canManageCredential || !selectedTelegramChannelId || !webhookConfirmed || clearWebhookMutation.isPending} onClick={() => clearWebhookMutation.mutate()}><Unplug size={14} />清理 Webhook</button>
        </form>
      </section>}

      <section className="panel table-panel">
        <div className="panel-heading task-panel-heading"><div><span>运行实例</span><h2>渠道实例</h2></div><small>{instances.data?.items.length ?? 0} 个实例</small></div>
        <AdminDataTable rows={instances.data?.items ?? []} columns={instanceColumns} rowKey={(row) => row.id} searchableText={(row) => `${row.name} ${row.platform} ${row.status} ${row.health_status}`} searchPlaceholder="搜索渠道、平台或健康状态" emptyMessage={instances.isLoading ? '正在读取渠道…' : '尚未创建渠道实例'} />
      </section>

      <section className="channel-operation-section" aria-label="渠道运营指标">
        <div className="panel-heading channel-operation-heading"><div><span>时间窗聚合 · 不含正文</span><h2>运营指标</h2></div><label className="status-filter"><span>统计窗口</span><select value={metricsWindow} onChange={(event) => setMetricsWindow(Number(event.target.value))}><option value={15}>最近 15 分钟</option><option value={60}>最近 1 小时</option><option value={360}>最近 6 小时</option><option value={1440}>最近 24 小时</option></select></label></div>
        <div className="metric-grid channel-operation-metrics">
          <article className="metric-card"><div className="metric-icon"><Activity size={18} /></div><p>入站事件</p><strong>{operationMetrics.data ? metricTotals.inbound_events : '—'}</strong><span>当前 Agent 全部渠道</span></article>
          <article className="metric-card"><div className="metric-icon"><Send size={18} /></div><p>出站事件</p><strong>{operationMetrics.data ? metricTotals.outbound_events : '—'}</strong><span>发送尝试已聚合</span></article>
          <article className="metric-card"><div className="metric-icon"><CheckCircle2 size={18} /></div><p>送达</p><strong>{operationMetrics.data ? metricTotals.outbound_delivered : '—'}</strong><span>适配器确认送达</span></article>
          <article className="metric-card"><div className="metric-icon"><ShieldCheck size={18} /></div><p>降级</p><strong>{operationMetrics.data ? metricTotals.outbound_degraded : '—'}</strong><span>透明能力降级</span></article>
          <article className="metric-card"><div className="metric-icon"><TriangleAlert size={18} /></div><p>失败</p><strong>{operationMetrics.data ? metricTotals.outbound_failed : '—'}</strong><span>失败与拒绝</span></article>
          <article className="metric-card"><div className="metric-icon"><RadioTower size={18} /></div><p>限流</p><strong>{operationMetrics.data ? metricTotals.outbound_rate_limited : '—'}</strong><span>渠道速率限制</span></article>
          <article className="metric-card"><div className="metric-icon"><Activity size={18} /></div><p>出站失败率</p><strong>{operationMetrics.data ? `${metricTotals.failure_rate.toFixed(2)}%` : '—'}</strong><span>失败与限流 / 出站尝试</span></article>
          <article className="metric-card"><div className="metric-icon"><RefreshCw size={18} /></div><p>最近失败</p><strong className="metric-text">{operationMetrics.data ? formatMetricTime(metricTotals.last_failure_at) : '—'}</strong><span>{operationMetrics.data ? `窗口 ${new Date(operationMetrics.data.window_started_at).toLocaleString('zh-CN')} 起` : '等待指标返回'}</span></article>
        </div>
        {hasOperationFailure && <div className="notice warning"><TriangleAlert size={17} /><div><strong>检测到失败出站</strong><span>失败率大于 0。请前往<a href="/tasks">任务与主动行为</a>，逐项确认问题已处理后再显式重放；本页不提供自动重试。</span></div></div>}
        <div className="panel table-panel channel-operation-table">
          <div className="panel-heading task-panel-heading"><div><span>{operationMetrics.data ? `${new Date(operationMetrics.data.window_started_at).toLocaleString('zh-CN')} 至 ${new Date(operationMetrics.data.window_ended_at).toLocaleString('zh-CN')}` : '等待查询'}</span><h2>按渠道明细</h2></div><small>{operationMetrics.data?.items.length ?? 0} 个渠道</small></div>
          <AdminDataTable rows={operationMetrics.data?.items ?? []} columns={operationMetricColumns} rowKey={(row) => row.channel_id} searchableText={(row) => `${instanceNames.get(row.channel_id) ?? ''} ${row.channel_id}`} searchPlaceholder="搜索渠道实例" emptyMessage={operationMetrics.isLoading ? '正在读取运营指标…' : '当前窗口暂无渠道指标'} />
        </div>
      </section>

      <section className="panel table-panel" aria-label="渠道告警">
        <div className="panel-heading task-panel-heading"><div><span>策略聚合 · 不含远端错误正文</span><h2><BellRing size={18} />活动告警</h2></div><div className="table-actions"><select value={alertNotificationWindow} onChange={(event) => setAlertNotificationWindow(Number(event.target.value))} aria-label="告警通知时间窗"><option value={15}>最近 15 分钟</option><option value={60}>最近 1 小时</option><option value={360}>最近 6 小时</option><option value={1440}>最近 24 小时</option></select><button disabled={!canManageNotifications || alertNotificationQueueMutation.isPending} onClick={notifyAlerts}><Send size={12} />加入通知队列</button><small>{alerts.data?.items.length ?? 0} 条</small></div></div>
        <AdminDataTable rows={alerts.data?.items ?? []} columns={alertColumns} rowKey={(row) => `${row.channel_id}:${row.code}:${row.error_code ?? ''}`} searchableText={(row) => `${row.title} ${row.code} ${row.error_code ?? ''} ${row.severity}`} searchPlaceholder="搜索告警规则、错误码或级别" emptyMessage={alerts.isLoading ? '正在读取告警…' : '当前窗口没有活动告警'} />
      </section>

      <section className="panel channel-policy-panel" aria-label="告警升级策略模拟器">
        <div className="panel-heading"><div><span>当前 Agent 生效配置 · 无外部副作用</span><h2><FlaskConical size={18} />告警升级策略模拟器</h2></div></div>
        <form className="channel-policy-form" onSubmit={(event) => { event.preventDefault(); policySimulation.mutate() }}>
          <label><span>严重级别</span><select value={policySeverity} onChange={(event) => setPolicySeverity(event.target.value as 'warning' | 'critical')}><option value="critical">严重</option><option value="warning">警告</option></select></label>
          <label><span>持续分钟数</span><input type="number" min={0} max={525_600} value={policyDuration} onChange={(event) => setPolicyDuration(Number(event.target.value))} required /></label>
          <label><span>当前升级等级</span><select value={policyCurrentLevel} onChange={(event) => setPolicyCurrentLevel(Number(event.target.value))}><option value={0}>L0 未升级</option><option value={1}>L1</option><option value={2}>L2</option><option value={3}>L3</option></select></label>
          <label><span>模拟时间</span><input type="datetime-local" value={policyEvaluatedAt} onChange={(event) => setPolicyEvaluatedAt(event.target.value)} required /></label>
          <button className="secondary-button" type="submit" disabled={!policyEvaluatedAt || policySimulation.isPending}><FlaskConical size={14} />评估策略</button>
        </form>
        {policySimulation.data && <div className="channel-policy-result" aria-live="polite">
          <div className="channel-policy-decision">
            <strong>{policySimulation.data.target_level ? `升级至 L${policySimulation.data.target_level}` : '本次不升级'}</strong>
            <span>{policySimulation.data.reason}</span>
            <small>{policySimulation.data.on_call ? '值班时段' : '非值班时段'} · {notificationAdapterLabel(policySimulation.data.adapter)} · {new Date(policySimulation.data.local_time).toLocaleString('zh-CN')} ({policySimulation.data.timezone})</small>
          </div>
          <div className="channel-policy-steps">
            {policySimulation.data.steps.map((step) => <div key={step.level}>
              <strong>L{step.level}</strong>
              <span>{step.threshold_minutes} 分钟 · {notificationAdapterLabel(step.adapter)}</span>
              <small>{step.completed ? '已完成' : !step.eligible ? '不适用于当前级别' : step.reached ? '已达到阈值' : '等待阈值'}</small>
            </div>)}
          </div>
        </div>}
      </section>

      <section className="panel table-panel" aria-label="告警生命周期">
        <div className="panel-heading task-panel-heading"><div><span>PostgreSQL 持久化历史 · 不含业务正文</span><h2><Activity size={18} />告警生命周期</h2></div><div className="table-actions lifecycle-filters"><label className="status-filter"><span>状态</span><select value={lifecycleStatus} onChange={(event) => setLifecycleStatus(event.target.value as ChannelAlertLifecycleStatus | '')}><option value="">全部状态</option>{Object.entries(channelAlertLifecycleStatusLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label className="status-filter"><span>级别</span><select value={lifecycleSeverity} onChange={(event) => setLifecycleSeverity(event.target.value as 'warning' | 'critical' | '')}><option value="">全部级别</option>{Object.entries(alertSeverityLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label className="status-filter"><span>渠道</span><select value={lifecycleChannelId} onChange={(event) => setLifecycleChannelId(event.target.value)}><option value="">全部渠道</option>{instances.data?.items.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label><small>{alertLifecycles.data?.items.length ?? 0} 条</small></div></div>
        <AdminDataTable rows={alertLifecycles.data?.items ?? []} columns={lifecycleColumns} rowKey={(row) => row.id} searchableText={(row) => `${row.code} ${row.error_code ?? ''} ${row.status} ${row.severity} ${instanceNames.get(row.channel_id) ?? row.channel_id}`} searchPlaceholder="搜索规则、渠道、错误码或状态" emptyMessage={alertLifecycles.isLoading ? '正在读取告警生命周期…' : '暂无匹配的告警生命周期'} />
      </section>

      <section className="channel-operation-section" aria-label="告警通知投递">
        <div className="panel-heading channel-operation-heading"><div><span>background_jobs 安全投影 · 不含目标、密钥和正文</span><h2><Send size={18} />通知投递</h2></div><label className="status-filter"><span>事件</span><select value={notificationEvent} onChange={(event) => setNotificationEvent(event.target.value as NotificationDeliveryEvent | '')}><option value="">全部事件</option>{Object.entries(notificationDeliveryEventLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label></div>
        <div className="metric-grid channel-notification-metrics">
          <article className="metric-card"><div className="metric-icon"><Send size={18} /></div><p>通知总量</p><strong>{notificationTimeline.data?.total ?? '—'}</strong><span>当前 Agent</span></article>
          <article className="metric-card"><div className="metric-icon"><CheckCircle2 size={18} /></div><p>已完成</p><strong>{notificationTimeline.data?.succeeded ?? '—'}</strong><span>成功投递</span></article>
          <article className="metric-card"><div className="metric-icon"><Activity size={18} /></div><p>处理中</p><strong>{notificationTimeline.data ? notificationTimeline.data.pending + notificationTimeline.data.running + notificationTimeline.data.retrying : '—'}</strong><span>等待、执行或重试</span></article>
          <article className="metric-card"><div className="metric-icon"><TriangleAlert size={18} /></div><p>失败 / 死信</p><strong>{notificationTimeline.data ? notificationTimeline.data.failed + notificationTimeline.data.dead_letters : '—'}</strong><span>需人工关注</span></article>
          <article className="metric-card"><div className="metric-icon"><RefreshCw size={18} /></div><p>当前连续失败</p><strong>{notificationTimeline.data?.current_consecutive_failures ?? '—'}</strong><span>{notificationTimeline.data?.last_succeeded_at ? `最近成功 ${new Date(notificationTimeline.data.last_succeeded_at).toLocaleString('zh-CN')}` : '尚无成功记录'}</span></article>
        </div>
        <div className="panel table-panel channel-operation-table">
          <div className="panel-heading task-panel-heading"><div><span>可按安全状态、适配器和事件筛选</span><h2>投递时间线</h2></div><div className="table-actions"><label className="status-filter"><span>状态</span><select value={notificationStatus} onChange={(event) => setNotificationStatus(event.target.value as BackgroundJobStatus | '')}><option value="">全部状态</option>{Object.entries(backgroundJobStatusLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label className="status-filter"><span>适配器</span><select value={notificationAdapter} onChange={(event) => setNotificationAdapter(event.target.value)}><option value="">全部适配器</option>{Object.entries(notificationAdapterLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><small>{notificationTimeline.data?.items.length ?? 0} 条</small></div></div>
          <AdminDataTable rows={notificationTimeline.data?.items ?? []} columns={notificationColumns} rowKey={(row) => row.job_id} searchableText={(row) => `${row.adapter} ${row.event} ${row.status} ${row.last_error_code ?? ''}`} searchPlaceholder="搜索适配器、事件或安全错误码" emptyMessage={notificationTimeline.isLoading ? '正在读取通知投递…' : '暂无通知投递记录'} />
        </div>
      </section>

      <section className="panel table-panel" aria-label="渠道错误指标">
        <div className="panel-heading task-panel-heading"><div><span>错误码聚合 · 时间窗包含边界</span><h2><TriangleAlert size={18} />错误码指标</h2></div><small>{errorMetrics.data?.items.length ?? 0} 个错误码</small></div>
        <AdminDataTable rows={errorMetrics.data?.items ?? []} columns={errorMetricColumns} rowKey={(row) => `${row.channel_id}:${row.error_code}`} searchableText={(row) => `${instanceNames.get(row.channel_id) ?? ''} ${row.channel_id} ${row.error_code}`} searchPlaceholder="搜索渠道或安全错误码" emptyMessage={errorMetrics.isLoading ? '正在读取错误指标…' : '当前窗口暂无错误码指标'} />
      </section>

      <section className="panel table-panel" aria-label="渠道健康趋势">
        <div className="panel-heading task-panel-heading"><div><span>安全快照 · 不含 URL、凭证或错误原文</span><h2><HeartPulse size={18} />健康趋势</h2></div><small>{healthTrend.data?.items.length ?? 0} 条快照</small></div>
        <AdminDataTable rows={healthTrend.data?.items ?? []} columns={healthSnapshotColumns} rowKey={(row) => row.id} searchableText={(row) => `${instanceNames.get(row.channel_id) ?? ''} ${row.channel_id} ${row.platform} ${row.status}`} searchPlaceholder="搜索渠道、平台或健康状态" emptyMessage={healthTrend.isLoading ? '正在读取健康快照…' : '当前窗口暂无健康快照'} />
      </section>

      <section className="panel table-panel" aria-label="连接探测记录">
        <div className="panel-heading task-panel-heading"><div><span>Telegram 与其他正式适配器</span><h2><Cable size={18} />最近连接探测</h2></div><small>{connectionTests.data?.items.length ?? 0} 条</small></div>
        <AdminDataTable rows={connectionTests.data?.items ?? []} columns={connectionTestColumns} rowKey={(row) => row.id} searchableText={(row) => `${instanceNames.get(row.channel_id) ?? ''} ${row.channel_id} ${row.status} ${row.error_code ?? ''}`} searchPlaceholder="搜索渠道、结果或错误码" emptyMessage={connectionTests.isLoading ? '正在读取连接探测…' : '暂无连接探测记录'} />
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
