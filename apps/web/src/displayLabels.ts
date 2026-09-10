import type {
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
  agent: 'Agent',
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
  agent: 'Agent 共享',
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
  'agent.status_updated': '更新 Agent 状态',
  'user.status_updated': '更新用户状态',
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
  'background_job.cancel_requested': '请求取消后台任务',
  'background_job.replayed': '重放后台任务',
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
  agent: 'Agent',
  user: '用户',
  configuration_version: '配置版本',
  secret_reference: '密钥引用',
  channel_instance: '渠道实例',
  evaluation: '评测',
  persona: '人格版本',
  prompt: 'Prompt 版本',
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
}

export const metadataKeyLabels: Readonly<Record<string, string>> = {
  ids: '资源 ID',
  status: '状态',
  health_status: '健康状态',
  version: '版本',
  source_version: '来源版本',
  value_count: '配置项数',
  key: '资源键',
  case_count: '用例数',
  preference: '偏好',
  source_job_id: '来源任务 ID',
  reason: '原因',
  confirmation: '确认状态',
  stage: '关系阶段',
  content_removed: '正文已清除',
  embedding_removed: '向量已清除',
  target_memory_id: '目标记忆 ID',
  target_embedding_version: '目标向量版本',
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
  grace_cutoff: '宽限截止时间',
  records: '记录数',
  bytes: '字节数',
  candidates: '候选会话数',
  candidates_failed: '候选处理失败数',
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
