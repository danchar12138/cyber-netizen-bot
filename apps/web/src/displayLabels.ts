import type {
  AdminPermission,
  AdminRole,
  BackgroundJobKind,
  BackgroundJobStatus,
  ChannelCapabilities,
  ChannelDiagnosticEvent,
  ChannelHealthStatus,
  ChannelInstanceStatus,
  ChannelPlatform,
  ConfigDefinition,
  ConfigScope,
  CognitiveRunTrace,
  Episode,
  MemoryConfirmation,
  MemoryIndexJob,
  MemoryKind,
  MemoryLink,
  MemorySensitivity,
  MemorySource,
  MemoryStatus,
  MemoryVisibility,
  Relationship,
  ScheduledAction,
  ScheduledActionStatus,
  SecretMetadata,
} from './api'

type Labels<Value extends string> = Readonly<Record<Value, string>>

export function displayLabel(
  labels: Readonly<Record<string, string>>,
  value: string,
): string {
  return labels[value] ?? `未识别（${value}）`
}

export const adminRoleLabels = {
  admin: '管理员',
  operator: '运营者',
  viewer: '只读访客',
} satisfies Labels<AdminRole>

export const permissionLabels = {
  'dashboard:read': '查看总览',
  'configuration:read': '查看配置',
  'configuration:write': '发布配置',
  'secret:manage': '管理密钥',
  'conversation:read': '查看对话',
  'conversation:use': '操作对话',
  'access_control:read': '查看权限',
  'agent:read': '查看智能体',
  'agent:write': '管理智能体状态',
  'cognition:read': '查看认知资源',
  'cognition:write': '管理认知资源',
  'cognition:evaluate': '运行认知评估',
  'evaluation:review': '复核评估结果',
  'memory:read': '查看长期记忆',
  'memory:write': '管理长期记忆',
  'memory:rebuild': '重建记忆索引',
  'task:read': '查看异步任务',
  'task:manage': '管理异步任务',
  'proactive:manage': '管理主动行为',
  'channel:read': '查看渠道',
  'channel:write': '管理渠道',
  'channel:send': '发送渠道消息',
  'channel_credential:manage': '管理渠道凭证',
  'channel_alert:manage': '处置渠道告警',
  'channel_notification:manage': '管理告警通知',
  'observability_alert:manage': '处置通用告警',
  'integration:read': '查看外部映射与入站箱',
  'integration:manage': '管理外部身份与会话映射',
  'inbox:replay': '重放入站箱事件',
  'trace:read': '查看运行轨迹',
  'user:read': '查看用户',
  'user:write': '管理用户状态与访问策略',
  'user:role_write': '覆盖用户可信角色',
  'audit:read': '查看审计',
  'data_lifecycle:read': '查看数据生命周期',
  'data_lifecycle:export': '导出用户数据',
  'data_lifecycle:forget': '执行用户遗忘',
  'data_lifecycle:retention_manage': '管理数据保留',
  'data_lifecycle:backup_drill_record': '记录备份恢复演练',
} satisfies Labels<AdminPermission>

export const authenticationModeLabels = {
  development: '开发身份',
  oidc: 'OIDC 单点登录',
} as const

export const environmentLabels: Readonly<Record<string, string>> = {
  development: '开发环境',
  test: '测试环境',
  staging: '预发布环境',
  production: '生产环境',
}

export const logLevelLabels: Readonly<Record<string, string>> = {
  debug: '调试',
  info: '信息',
  warning: '警告',
  error: '错误',
  critical: '严重',
}

export function formatEnvironment(value: string): string {
  return displayLabel(environmentLabels, value.toLocaleLowerCase())
}

export function formatLogLevel(value: string): string {
  return displayLabel(logLevelLabels, value.toLocaleLowerCase())
}

export const componentLabels: Readonly<Record<string, string>> = {
  api: '应用接口',
  worker: '后台任务进程',
  postgresql: 'PostgreSQL 数据库',
  redis: 'Redis 任务服务',
  object_storage: 'MinIO 对象存储',
}

export const componentHealthLabels: Readonly<Record<string, string>> = {
  healthy: '健康',
  ready: '就绪',
  degraded: '异常',
  not_checked: '待探测',
  not_configured: '未配置',
  disabled: '已停用',
}

export const configScopeLabels = {
  system: '系统',
  tenant: '租户',
  agent: '智能体',
  channel: '渠道',
  user: '用户',
} satisfies Labels<ConfigScope>

export const configValueKindLabels = {
  string: '文本',
  integer: '整数',
  number: '数值',
  boolean: '开关',
  string_list: '文本列表',
  secret: '密钥',
} satisfies Labels<ConfigDefinition['value_kind']>

export const configurationOptionLabels: Readonly<Record<string, string>> = {
  high_precision: '高精度（只保留明确信息）',
  balanced: '均衡（包含完整自我披露）',
  'local-hash-v1': '本地确定性向量 v1',
  normal: '普通',
  personal: '个人',
  sensitive: '敏感',
  restricted: '严格受限',
  development: '内置开发模型',
  openai: 'OpenAI',
}

export function configurationOptionLabel(value: string): string {
  return configurationOptionLabels[value] ?? value
}

export const secretIntegrityLabels = {
  untested: '未测试',
  valid: '完整性正常',
  invalid: '完整性异常',
} satisfies Labels<SecretMetadata['integrity_status']>

export const backgroundJobKindLabels = {
  reflection: '异步反思',
  episode_consolidation: '情景记录巩固',
  memory_extraction: '记忆提取',
  embedding_rebuild: '向量索引重建',
  relationship_update: '关系更新',
  scheduled_action: '主动行为',
  inbound_message: '入站消息路由',
  notification_delivery: '通知投递',
  channel_connection_test: '渠道连接探测',
} satisfies Labels<BackgroundJobKind>

export const backgroundJobStatusLabels = {
  pending: '等待中',
  running: '执行中',
  retrying: '重试中',
  succeeded: '已完成',
  failed: '失败',
  dead_letter: '死信',
  canceled: '已取消',
} satisfies Labels<BackgroundJobStatus>

export const notificationDeliveryEventLabels = {
  active: '活动告警',
  escalation: '升级通知',
  recovery: '恢复通知',
  unknown: '未知事件',
} as const

export const channelAlertLifecycleStatusLabels = {
  active: '活动中',
  resolved: '已恢复',
} as const

export const observabilityAlertSourceTypeLabels: Readonly<Record<string, string>> = {
  api: '应用接口',
  agent_runtime: '智能体运行',
  model_runtime: '模型运行',
  task_queue: '后台任务队列',
  notification: '通知投递',
}

export const observabilityAlertCodeLabels: Readonly<Record<string, string>> = {
  api_error_rate: '应用接口错误率超标',
  api_p95_latency: '应用接口响应偏慢',
  agent_success_rate: '智能体运行成功率偏低',
  agent_p95_latency: '智能体运行耗时偏高',
  model_failure_rate: '模型调用失败率超标',
  model_cost_budget: '模型成本超出窗口预算',
  queue_backlog: '后台任务积压',
  queue_oldest_wait: '后台任务等待过久',
  notification_delivery_dead_letters: '通知投递出现死信',
  channel_delivery_failure_rate: '渠道出站失败率超标',
}

export const observabilityReplayDecisionLabels = {
  allowed: '允许重放',
  blocked: '阻止重放',
} as const

export const observabilityReplayReasonLabels = {
  allowed_no_suppression: '当前无有效抑制',
  allowed_suppression_expired: '抑制已过期',
  blocked_active_suppression: '告警仍在抑制期',
  blocked_missing_source: '通知缺少告警来源',
  blocked_invalid_agent: '通知缺少有效 Agent 归属',
  blocked_agent_mismatch: '任务与通知 Agent 归属冲突',
} as const

const observabilityUnitLabels: Readonly<Record<string, string>> = {
  '%': '%',
  ms: '毫秒',
  s: '秒',
  jobs: '项',
  USD: '美元',
}

export function observabilityUnitLabel(value: string): string {
  return observabilityUnitLabels[value] ?? value
}

export const scheduledActionStatusLabels = {
  pending: '待调度',
  dispatched: '已分发',
  completed: '已完成',
  suppressed: '已抑制',
  canceled: '已取消',
  expired: '已过期',
  failed: '失败',
} satisfies Labels<ScheduledActionStatus>

export const scheduledActionKindLabels = {
  follow_up: '后续跟进',
  proactive_message: '主动消息',
  reflection: '异步反思',
} satisfies Labels<ScheduledAction['kind']>

export const channelPlatformLabels = {
  web: '内部 Web',
  feishu: '飞书',
  discord: 'Discord',
  telegram: 'Telegram',
} satisfies Labels<ChannelPlatform>

export const channelInstanceStatusLabels = {
  enabled: '已启用',
  disabled: '已停用',
} satisfies Labels<ChannelInstanceStatus>

export const channelHealthLabels = {
  healthy: '健康',
  degraded: '降级',
  not_configured: '未配置',
  disabled: '已停用',
} satisfies Labels<ChannelHealthStatus>

export const channelEventDirectionLabels = {
  inbound: '入站',
  outbound: '出站',
  system: '系统',
} satisfies Labels<ChannelDiagnosticEvent['direction']>

export const channelEventStatusLabels = {
  accepted: '已接收',
  delivered: '已送达',
  degraded: '降级送达',
  rejected: '已拒绝',
  failed: '失败',
  rate_limited: '已限流',
} satisfies Labels<ChannelDiagnosticEvent['status']>

export const channelCapabilityLabels = {
  text: '文本',
  markdown: 'Markdown',
  images: '图片',
  files: '文件',
  streaming: '流式响应',
  reactions: '表情回应',
  threads: '话题线程',
  message_edit: '消息编辑',
  proactive_messages: '主动消息',
  max_text_chars: '文本字符上限',
  max_blocks: '内容块上限',
  max_attachment_bytes: '附件大小上限',
  accepted_content_types: '允许的内容类型',
} satisfies Labels<keyof ChannelCapabilities>

export const channelEventTypeLabels: Readonly<Record<string, string>> = {
  'connection.tested': '连接测试',
  'message.created': '消息已创建',
  'message.delivery_started': '消息发送中',
  'message.delivered': '消息已送达',
  'message.delivery_failed': '消息发送失败',
  'message.received': '收到消息',
  'reaction.added': '添加回应',
}

export const channelDegradationLabels: Readonly<Record<string, string>> = {
  streaming_to_buffered: '流式响应改为缓冲后发送',
  thread_to_root_message: '线程回复改为主消息',
  edit_to_new_message: '编辑操作改为发送新消息',
  markdown_to_text: 'Markdown 改为纯文本',
  image_to_file: '图片改为文件发送',
  image_to_text_reference: '图片改为文本引用',
  file_to_text_reference: '文件改为文本引用',
  long_text_split: '长文本已拆分',
}

export const cognitiveStageLabels: Readonly<Record<string, string>> = {
  perception: '输入感知',
  context_assembly: '上下文组装',
  memory_recall: '记忆召回',
  social_mind: '社交心智',
  deliberation: '行动决策',
  policy_gate: '策略门校验',
  realizer: '自然表达',
}

export const cognitiveActionLabels: Readonly<Record<string, string>> = {
  reply: '回复',
  ask: '追问',
  wait: '等待',
  no_reply: '不回复',
  tool: '调用工具',
}

export const modelInvocationStatusLabels = {
  running: '调用中',
  completed: '已完成',
  failed: '失败',
  timed_out: '已超时',
} satisfies Labels<CognitiveRunTrace['model_invocations'][number]['status']>

export const modelPurposeLabels: Readonly<Record<string, string>> = {
  chat: '对话',
  'chat.realizer': '对话自然表达',
}

export const memoryKindLabels = {
  working: '工作',
  episodic: '情景',
  semantic: '语义',
  relational: '关系',
  autobiographical: '自传',
  procedural: '程序性',
} satisfies Labels<MemoryKind>

export const memoryVisibilityLabels = {
  user: '用户私有',
  agent: '智能体共享',
  tenant: '租户共享',
} satisfies Labels<MemoryVisibility>

export const memorySensitivityLabels = {
  normal: '普通',
  personal: '个人',
  sensitive: '敏感',
  restricted: '受限',
} satisfies Labels<MemorySensitivity>

export const memoryConfirmationLabels = {
  unconfirmed: '未确认',
  confirmed: '已确认',
  disputed: '有争议',
} satisfies Labels<MemoryConfirmation>

export const memoryStatusLabels = {
  active: '生效',
  superseded: '已替代',
  forgotten: '已遗忘',
} satisfies Labels<MemoryStatus>

export const memorySourceKindLabels = {
  message: '对话消息',
  episode: '情景记录',
  user_statement: '用户陈述',
  admin_correction: '管理员纠正',
  reflection: '异步反思',
  import: '管理导入',
} satisfies Labels<MemorySource['kind']>

export const memoryLinkKindLabels = {
  related_to: '相关',
  conflicts_with: '冲突',
  supersedes: '替代',
  derived_from: '派生自',
} satisfies Labels<MemoryLink['kind']>

export const relationshipStageLabels = {
  stranger: '陌生',
  acquaintance: '相识',
  familiar: '熟悉',
  trusted: '信任',
} satisfies Labels<Relationship['stage']>

export const relationshipEventTypeLabels: Readonly<Record<string, string>> = {
  interaction: '日常互动',
  user_confirmed_memory: '用户确认记忆',
  conversation_reflected: '对话反思',
  'conversation.reflected.neutral': '普通对话反思',
  'conversation.reflected.warmth': '友好互动反思',
  'conversation.reflected.self_disclosure': '自我披露反思',
  'conversation.reflected.correction': '用户纠错反思',
  'conversation.reflected.boundary': '互动边界反思',
  'conversation.reflected.hostility': '拒绝或敌意反思',
}

export const episodeStatusLabels = {
  open: '进行中',
  closed: '已关闭',
  consolidated: '已巩固',
} satisfies Labels<Episode['status']>

export const memoryIndexJobStatusLabels = {
  pending: '等待中',
  running: '重建中',
  completed: '已完成',
  failed: '失败',
} satisfies Labels<MemoryIndexJob['status']>

export const auditActionLabels: Readonly<Record<string, string>> = {
  'agent.created': '创建智能体',
  'agent.copied': '复制智能体',
  'agent.renamed': '重命名智能体',
  'agent.status_updated': '更新智能体状态',
  'agent.archived': '归档智能体',
  'agent.soft_deleted': '软删除智能体',
  'user.status_updated': '更新用户状态',
  'user.access_policy_updated': '更新用户访问策略',
  'user.role_overridden': '覆盖用户角色',
  'user.role_override_revoked': '撤销用户角色覆盖',
  'user.role_override_expired': '用户角色覆盖到期',
  'admin_session.revoked': '撤销管理会话',
  'user.data_forgotten': '永久遗忘用户数据',
  'configuration.draft_created': '创建配置草稿',
  'configuration.published': '发布配置版本',
  'configuration.rolled_back': '回滚配置版本',
  'secret.created': '写入密钥',
  'secret.updated': '更新密钥',
  'secret.rotated': '轮换密钥',
  'secret.integrity_tested': '测试密钥完整性',
  'secret.cleared': '清除密钥',
  'cognition.resource_draft_created': '创建认知资源草稿',
  'cognition.resource_published': '发布认知资源',
  'cognition.resource_rolled_back': '回滚认知资源',
  'evaluation.suite_created': '创建评测集',
  'evaluation.suite_published': '发布评测集',
  'evaluation.run_completed': '完成自动评测',
  'evaluation.blind_review_submitted': '提交匿名盲评',
  'channel_instance.created': '创建渠道实例',
  'channel_instance.updated': '更新渠道实例',
  'channel_instance.connection_tested': '测试渠道连接',
  'external_identity.created': '创建外部身份映射',
  'external_identity.status_updated': '更新外部身份映射状态',
  'external_conversation.created': '创建外部会话映射',
  'external_conversation.status_updated': '更新外部会话映射状态',
  'background_job.cancel_requested': '请求取消后台任务',
  'background_job.replayed': '重放后台任务',
  'observability_alert.acknowledged': '确认通用告警',
  'observability_alert.suppressed': '抑制通用告警',
  'observability_alert.disposition_cleared': '解除通用告警处置',
  'observability_alert.replay_review_allowed': '允许通用告警通知重放',
  'observability_alert.replay_review_blocked': '阻止通用告警通知重放',
  'scheduled_action.created': '创建定时行为',
  'scheduled_action.canceled': '取消定时行为',
  'episode.created': '创建情景记录',
  'episode.status_updated': '更新情景记录状态',
  'memory.created': '创建记忆',
  'memory.confirmation_updated': '更新记忆确认状态',
  'memory.corrected': '纠正记忆',
  'memory.forgotten': '遗忘记忆',
  'memory.conflict_linked': '标记记忆冲突',
  'memory.index_rebuild_created': '创建向量索引重建任务',
  'relationship.updated': '更新关系状态',
}

export const auditResourceLabels: Readonly<Record<string, string>> = {
  agent: '智能体',
  user: '用户',
  admin_session: '管理会话',
  configuration_version: '配置版本',
  secret_reference: '密钥引用',
  channel_instance: '渠道实例',
  external_identity_mapping: '外部身份映射',
  external_conversation_mapping: '外部会话映射',
  evaluation: '评测',
  persona: '人格版本',
  prompt: '提示词版本',
  model_profile: '模型档案',
  model_route: '模型路由',
  tool: '工具定义',
  policy: '策略版本',
  background_job: '后台任务',
  scheduled_action: '定时行为',
  episode: '情景记录',
  memory: '记忆',
  memory_link: '记忆关系',
  relationship: '关系状态',
  memory_index_job: '向量索引任务',
  observability_alert_disposition: '通用告警处置',
  observability_alert_replay_review: '通用告警通知重放复核',
}

export const metadataKeyLabels: Readonly<Record<string, string>> = {
  ids: '资源 ID',
  status: '状态',
  name: '名称',
  previous_name: '原名称',
  previous_status: '原状态',
  source_agent_id: '来源智能体 ID',
  agent_id: '智能体 ID',
  source_type: '告警来源类型',
  source_key: '告警来源键',
  decision: '复核结论',
  reason_code: '复核原因',
  suppression_expires_at: '抑制到期时间',
  channel_id: '渠道实例 ID',
  user_id: '用户 ID',
  conversation_id: '会话 ID',
  platform: '平台',
  kind: '类型',
  copied_resource_count: '已复制认知版本数',
  archived_at: '归档时间',
  deleted_at: '软删除时间',
  revoked_at: '撤销时间',
  purge_after: '最早物理清理时间',
  retention_days: '保留天数',
  runtime_intake_blocked: '已阻止新运行',
  physical_delete_performed: '已执行物理删除',
  health_status: '健康状态',
  version: '版本',
  source_version: '来源版本',
  value_count: '配置项数',
  key: '资源键',
  case_count: '用例数',
  preference: '偏好',
  source_job_id: '来源任务 ID',
  reason: '原因',
  request_rate_limit_per_minute: '每分钟请求上限',
  suspended_until: '临时停用至',
  suspended: '临时停用',
  role: '生效角色',
  trusted_role: '可信角色',
  override_expires_at: '角色覆盖到期时间',
  restored_role: '恢复角色',
  confirmation: '确认状态',
  stage: '关系阶段',
  content_removed: '正文已清除',
  embedding_removed: '向量已清除',
  target_memory_id: '目标记忆 ID',
  target_embedding_version: '目标向量版本',
  episode_id: '情景记录 ID',
  memory_id: '记忆 ID',
  memory_created: '已创建记忆',
  memory_decision: '记忆写入决策',
  memory_kind: '记忆类型',
  memory_sensitivity: '记忆敏感级别',
  memory_reason_codes: '记忆判别依据',
  relationship_signals: '关系信号',
  relationship_version: '关系版本',
  compression_applied: '已应用长对话压缩',
  source_message_count: '读取消息数',
  recent_message_count: '逐条保留消息数',
  summarized_message_count: '进入摘要的消息数',
  summary_covered_message_count: '摘要实际覆盖消息数',
  summary_fragment_count: '摘要片段数',
  summary_levels: '摘要层级',
  estimated_summary_tokens: '摘要估算 Token',
  omitted_summary_fragment_count: '因摘要预算省略片段数',
  selected_count: '已选上下文片段数',
  omitted_count: '省略上下文片段数',
  truncated_count: '截断上下文片段数',
  estimated_tokens: '上下文估算 Token',
  token_budget: '上下文 Token 预算',
  database_redaction_completed: '数据库脱敏完成',
  objects_deleted: '对象清理完成',
  database_integrity_verified: '数据库完整性通过',
  object_integrity_verified: '对象完整性通过',
  application_smoke_verified: '应用冒烟通过',
  isolated_restore_required: '已隔离恢复',
  schema_version: 'Schema 版本',
  sha256: 'SHA-256',
  manifest_sha256: '备份清单 SHA-256',
  cutoff: '清理截止时间',
  conversation_cutoff: '会话清理截止时间',
  agent_purge_before: '智能体清理截止时间',
  grace_cutoff: '宽限截止时间',
  records: '记录数',
  bytes: '字节数',
  candidates: '候选会话数',
  candidates_failed: '候选处理失败数',
  agent_candidates: '候选智能体数',
  agent_candidates_failed: '智能体候选处理失败数',
  agents_purged: '已物理清理智能体数',
  users_redacted: '已脱敏用户数',
  conversations: '会话数',
  conversations_redacted: '已脱敏会话数',
  memories: '记忆数',
  memories_forgotten: '已遗忘记忆数',
  relationships_deleted: '已清理关系数',
  objects: '对象数',
  objects_scheduled: '待清理对象数',
  objects_failed: '对象清理失败数',
  conversations_purged: '已清理会话数',
  attachment_metadata_purged: '已清理附件元数据数',
  objects_scanned: '已扫描对象数',
  database_rows_verified: '数据库校验行数',
  objects_verified: '对象校验数量',
}

const commonValueLabels: Readonly<Record<string, string>> = {
  active: '已启用',
  disabled: '已停用',
  archived: '已归档',
  deleted: '等待清理',
  draft: '草稿',
  published: '已发布',
  superseded: '已替代',
  unconfirmed: '未确认',
  confirmed: '已确认',
  disputed: '有争议',
  candidate: '候选回答',
  reference: '参考回答',
  tie: '难分高下',
  healthy: '健康',
  ready: '就绪',
  degraded: '降级',
  not_checked: '待探测',
  not_configured: '未配置',
  write: '写入长期记忆',
  skip_credential: '跳过凭据内容',
  skip_small_talk: '跳过寒暄',
  skip_question: '跳过普通提问',
  skip_low_signal: '跳过低信息内容',
  explicit_memory_request: '用户明确要求记住',
  interaction_boundary: '互动边界',
  stable_preference: '稳定偏好',
  personal_fact: '个人事实',
  future_plan: '未来计划',
  important_event: '重要经历',
  self_disclosure: '自我披露',
  small_talk: '寒暄',
  question_only: '仅包含提问',
  low_signal: '信息量不足',
  credential_content: '疑似凭据内容',
  neutral: '普通互动',
  warmth: '友好或感谢',
  correction: '用户纠错',
  boundary: '互动边界',
  hostility: '拒绝或敌意',
  oidc: 'OIDC 可信同步',
  development: '本地开发环境',
  manual: '管理员手工覆盖',
  ...adminRoleLabels,
  ...memoryKindLabels,
  ...memorySensitivityLabels,
  ...observabilityAlertSourceTypeLabels,
  ...observabilityReplayDecisionLabels,
  ...observabilityReplayReasonLabels,
  ...relationshipStageLabels,
}

function formatMetadataValue(value: unknown): string {
  if (value === null) return '未设置'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') return value.toLocaleString('zh-CN')
  if (typeof value === 'string') return commonValueLabels[value] ?? value
  if (Array.isArray(value)) return value.map(formatMetadataValue).join('、')
  return JSON.stringify(value)
}

export function formatMetadataEntries(value: Record<string, unknown>): string {
  return Object.entries(value)
    .map(([key, item]) => `${metadataKeyLabels[key] ?? key}：${formatMetadataValue(item)}`)
    .join('；')
}
