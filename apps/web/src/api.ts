export interface ComponentHealth {
  name: string
  status: 'healthy' | 'ready' | 'degraded' | 'not_checked' | 'not_configured'
  detail?: string | null
}

export interface SystemOverview {
  environment: string
  version: string
  active_agents: number
  active_conversations: number
  pending_jobs: number
  configuration_definitions: number
  components: ComponentHealth[]
}

export interface BootstrapSettings {
  environment: string
  log_level: string
  cors_origins: string[]
  readiness_deep_checks: boolean
  authentication_mode: 'development' | 'oidc'
  oidc_configured: boolean
  otel_enabled: boolean
  otel_exporter_configured: boolean
  otel_service_name: string
  otel_trace_sample_ratio: number
  object_storage_provider: 'minio'
  minio_endpoint_url: string
  minio_bucket: string
  database_configured: boolean
  redis_configured: boolean
  minio_credentials_configured: boolean
  config_master_key_status: 'development_placeholder' | 'configured'
  requires_restart: boolean
}

export interface AuthenticationConfig {
  mode: 'development' | 'oidc'
  authority: string | null
  client_id: string | null
  scope: string | null
}

export interface TaskStatus {
  broker: 'dramatiq-redis'
  queues: string[]
  pending_jobs: number
  running_jobs: number
  retrying_jobs: number
  dead_letter_jobs: number
  scheduled_actions: number
  worker: ComponentHealth
}

export type BackgroundJobKind = 'reflection' | 'episode_consolidation' | 'memory_extraction' | 'embedding_rebuild' | 'relationship_update' | 'scheduled_action'
export type BackgroundJobStatus = 'pending' | 'running' | 'retrying' | 'succeeded' | 'failed' | 'dead_letter' | 'canceled'

export interface BackgroundJob {
  id: string
  tenant_id: string
  kind: BackgroundJobKind
  queue: string
  status: BackgroundJobStatus
  payload_keys: string[]
  deduplication_key: string
  correlation_id: string | null
  attempt_count: number
  max_attempts: number
  lease_seconds: number
  retry_base_seconds: number
  available_at: string
  lease_owner: string | null
  lease_expires_at: string | null
  cancel_requested_at: string | null
  last_error_code: string | null
  last_error_summary: string | null
  result_summary: Record<string, ConfigValue>
  replayed_from_id: string | null
  created_by: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
  updated_at: string
}

export interface JobAttempt {
  id: string
  job_id: string
  attempt_number: number
  status: 'running' | 'succeeded' | 'failed' | 'timed_out' | 'canceled'
  worker_id: string
  started_at: string
  completed_at: string | null
  error_code: string | null
  error_summary: string | null
}

export interface TaskDashboard {
  pending: number
  running: number
  retrying: number
  dead_letters: number
  scheduled: number
  workers: Array<{
    worker_id: string
    queues: string[]
    current_job_id: string | null
    started_at: string
    last_seen_at: string
  }>
}

export type ScheduledActionStatus = 'pending' | 'dispatched' | 'completed' | 'suppressed' | 'canceled' | 'expired' | 'failed'

export type ChannelPlatform = 'web' | 'feishu' | 'discord' | 'telegram'
export type ChannelInstanceStatus = 'enabled' | 'disabled'
export type ChannelHealthStatus = 'healthy' | 'degraded' | 'not_configured' | 'disabled'
export type ContentBlockKind = 'text' | 'markdown' | 'image' | 'file'

export interface ChannelCapabilities {
  text: boolean
  markdown: boolean
  images: boolean
  files: boolean
  streaming: boolean
  reactions: boolean
  threads: boolean
  message_edit: boolean
  proactive_messages: boolean
  max_text_chars: number
  max_blocks: number
  max_attachment_bytes: number
  accepted_content_types: string[]
}

export interface ChannelCatalogItem {
  platform: ChannelPlatform
  display_name: string
  implementation_status: 'ready' | 'placeholder'
  credential_required: boolean
  capabilities: ChannelCapabilities
}

export interface ChannelInstance {
  id: string
  tenant_id: string
  name: string
  platform: ChannelPlatform
  display_name: string
  implementation_status: 'ready' | 'placeholder'
  status: ChannelInstanceStatus
  rate_limit_per_minute: number
  settings: Record<string, ConfigValue>
  credential_configured: boolean
  capabilities: ChannelCapabilities
  health_status: ChannelHealthStatus
  health_detail: string | null
  last_checked_at: string | null
  created_by: string
  created_at: string
  updated_at: string
}

export interface MultimodalContentBlock {
  kind: ContentBlockKind
  text: string | null
  attachment_id: string | null
  content_type: string | null
  file_name: string | null
  size_bytes: number | null
  sha256: string | null
  alt_text: string | null
}

export interface ChannelSimulation {
  platform: ChannelPlatform
  blocks: MultimodalContentBlock[]
  degradations: string[]
  buffered: boolean
  thread_preserved: boolean
  edit_preserved: boolean
}

export interface ChannelDiagnosticEvent {
  id: string
  channel_id: string
  direction: 'inbound' | 'outbound' | 'system'
  event_type: string
  status: 'accepted' | 'delivered' | 'degraded' | 'rejected' | 'failed' | 'rate_limited'
  external_event_id: string | null
  idempotency_key: string
  external_message_id: string | null
  payload_summary: Record<string, ConfigValue>
  error_code: string | null
  degradations: string[]
  occurred_at: string
}

export interface ModelCapabilityProfile {
  provider: string
  model_family: string
  text_input: boolean
  image_input: boolean
  document_input: boolean
  streaming: boolean
  structured_output: boolean
  tool_calling: boolean
}

export interface ScheduledAction {
  id: string
  tenant_id: string
  agent_id: string
  user_id: string
  conversation_id: string | null
  kind: 'follow_up' | 'proactive_message' | 'reflection'
  status: ScheduledActionStatus
  scheduled_for: string
  expires_at: string | null
  idempotency_key: string
  reason: string
  payload_keys: string[]
  score: number | null
  social_cost: number
  decision_reasons: string[]
  job_id: string | null
  created_by: string
  created_at: string
  updated_at: string
  completed_at: string | null
}

export type AdminRole = 'admin' | 'operator' | 'viewer'
export type AdminPermission =
  | 'dashboard:read'
  | 'configuration:read'
  | 'configuration:write'
  | 'secret:manage'
  | 'conversation:read'
  | 'conversation:use'
  | 'access_control:read'
  | 'agent:read'
  | 'agent:write'
  | 'cognition:read'
  | 'cognition:write'
  | 'cognition:evaluate'
  | 'evaluation:review'
  | 'memory:read'
  | 'memory:write'
  | 'memory:rebuild'
  | 'task:read'
  | 'task:manage'
  | 'proactive:manage'
  | 'channel:read'
  | 'channel:write'
  | 'channel:send'
  | 'channel_credential:manage'
  | 'trace:read'
  | 'user:read'
  | 'user:write'
  | 'audit:read'
  | 'data_lifecycle:read'
  | 'data_lifecycle:export'
  | 'data_lifecycle:forget'
  | 'data_lifecycle:retention_manage'
  | 'data_lifecycle:backup_drill_record'

export type LifecycleRunKind =
  | 'user_export'
  | 'user_forget'
  | 'retention_cleanup'
  | 'orphan_cleanup'
  | 'backup_restore_drill'

export type LifecycleRunStatus = 'running' | 'succeeded' | 'failed'

export interface LifecycleRun {
  id: string
  tenant_id: string
  actor_id: string | null
  subject_user_id: string | null
  kind: LifecycleRunKind
  status: LifecycleRunStatus
  counters: Record<string, number>
  evidence: Record<string, ConfigValue>
  error_code: string | null
  started_at: string
  completed_at: string | null
}

export interface LifecyclePolicy {
  deleted_conversation_days: number
  deleted_attachment_days: number
  orphan_grace_hours: number
  batch_size: number
  export_max_records: number
  export_max_bytes: number
  backup_expected_interval_hours: number
}

export interface DataLifecycleOverview {
  policy: LifecyclePolicy
  runs: LifecycleRun[]
}

export interface DataExportDownload {
  blob: Blob
  filename: string
  sha256: string | null
  runId: string | null
}

export interface AdminSession {
  tenant_id: string
  user_id: string
  display_name: string
  role: AdminRole
  permissions: AdminPermission[]
  authentication_mode: string
}

export interface AdminRoleDefinition {
  role: AdminRole
  label: string
  description: string
  permissions: AdminPermission[]
}

export type ConfigValue = string | number | boolean | null | ConfigValue[] | {
  [key: string]: ConfigValue
}

export type ConfigScope = 'system' | 'tenant' | 'agent' | 'channel' | 'user'

export interface ConfigDefinition {
  key: string
  section: string
  label: string
  description: string
  value_kind: 'string' | 'integer' | 'number' | 'boolean' | 'string_list' | 'secret'
  default: ConfigValue
  scopes: ConfigScope[]
  secret: boolean
  hot_reload: boolean
  minimum?: number | null
  maximum?: number | null
  options: string[]
}

interface ConfigRegistry {
  schema_version: string
  definitions: ConfigDefinition[]
}

export type ConfigVersionStatus = 'draft' | 'published' | 'superseded'

export interface ConfigVersionValue {
  key: string
  scope_type: ConfigScope
  scope_id: string | null
  value: ConfigValue
}

export interface ConfigVersion {
  id: string
  version: number
  status: ConfigVersionStatus
  note: string | null
  created_at: string
  published_at: string | null
  values: ConfigVersionValue[]
}

export type ConversationStatus = 'active' | 'archived'
export type MessageStatus =
  | 'received'
  | 'processing'
  | 'streaming'
  | 'completed'
  | 'suppressed'
  | 'cancelled'
  | 'failed'
export type AgentRunStatus = 'queued' | 'running' | 'completed' | 'cancelled' | 'failed'

export interface DevelopmentIdentity {
  tenant_id: string
  user_id: string
  agent_id: string
  user_name: string
  agent_name: string
}

export interface Conversation {
  id: string
  tenant_id: string
  agent_id: string
  title: string
  status: ConversationStatus
  event_sequence: number
  created_at: string
  updated_at: string
  pinned_at: string | null
  archived_at: string | null
  deleted_at: string | null
  branched_from_conversation_id: string | null
  branched_from_message_id: string | null
}

export interface ChatMessage {
  id: string
  conversation_id: string
  sender_type: 'user' | 'agent' | 'system'
  sender_id: string | null
  content: string
  status: MessageStatus
  client_message_id: string | null
  created_at: string
  updated_at: string
  edited_from_id: string | null
}

export type MessageFeedbackRating = 'positive' | 'negative'

export interface MessageFeedback {
  id: string
  conversation_id: string
  message_id: string
  user_id: string
  rating: MessageFeedbackRating
  comment: string | null
  created_at: string
  updated_at: string
}

export interface MessageSearchResult {
  conversation: Conversation
  message: ChatMessage
}

export type AttachmentStatus = 'pending' | 'ready' | 'attached' | 'rejected' | 'deleted'

export interface ChatAttachment {
  id: string
  conversation_id: string
  client_message_id: string
  message_id: string | null
  original_name: string
  content_type: string
  size_bytes: number
  sha256: string
  status: AttachmentStatus
  validation_error: string | null
  created_at: string
  expires_at: string
  uploaded_at: string | null
  attached_at: string | null
}

export interface AttachmentUploadGrant {
  url: string
  method: 'PUT'
  headers: Record<string, string>
  expires_at: string
}

export interface AttachmentReservation {
  attachment: ChatAttachment
  upload: AttachmentUploadGrant
}

export interface AgentRun {
  id: string
  conversation_id: string
  response_message_id: string
  status: AgentRunStatus
  configuration_version: number
  persona_version: number
  prompt_version: number
  policy_version: number
  model_route_version: number
  model_profile: string
  input_tokens: number | null
  output_tokens: number | null
  error_code: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export interface MessageAccepted {
  user_message: ChatMessage
  response_message: ChatMessage
  run: AgentRun
  idempotent_replay: boolean
}

export interface ConversationEvent {
  schema_version: '1'
  event_id: string
  conversation_id: string
  sequence: number
  event_type: string
  occurred_at: string
  run_id: string | null
  message_id: string | null
  payload: Record<string, unknown>
}

export interface CursorPage<T> {
  items: T[]
  next_cursor: string | null
}

export interface ManagedAgent {
  id: string
  tenant_id: string
  name: string
  status: 'active' | 'disabled'
  created_at: string
}

export interface ManagedUser {
  id: string
  tenant_id: string
  display_name: string
  status: 'active' | 'disabled'
  created_at: string
}

export interface AuditRecord {
  id: number
  actor_id: string | null
  action: string
  resource_type: string
  resource_id: string | null
  detail: Record<string, ConfigValue>
  created_at: string
}

export type CognitionResourceKind =
  | 'persona'
  | 'prompt'
  | 'model_profile'
  | 'model_route'
  | 'tool'
  | 'policy'

export type CognitionVersionStatus = 'draft' | 'published' | 'superseded'

export interface CognitionResource {
  id: string
  tenant_id: string
  agent_id: string
  kind: CognitionResourceKind
  key: string
  name: string
  version: number
  status: CognitionVersionStatus
  payload: Record<string, ConfigValue>
  note: string | null
  created_by: string
  created_at: string
  published_at: string | null
}

export interface LatencyPercentiles {
  p50_ms: number
  p95_ms: number
  p99_ms: number
}

export interface ObservabilityDashboard {
  window_started_at: string
  window_ended_at: string
  api: {
    requests: number
    server_errors: number
    error_rate_percent: number
    latency: LatencyPercentiles
  }
  agent_runs: {
    terminal_runs: number
    completed_runs: number
    unsuccessful_runs: number
    success_rate_percent: number
    latency: LatencyPercentiles
  }
  models: Array<{
    provider: string
    model: string
    invocations: number
    failed_invocations: number
    input_tokens: number
    output_tokens: number
    estimated_cost_microusd: number
    latency: LatencyPercentiles
  }>
  queue: {
    backlog: number
    oldest_wait_seconds: number
  }
  total_estimated_cost_microusd: number
  alerts: Array<{
    code: string
    severity: 'warning' | 'critical'
    title: string
    summary: string
    current_value: number
    threshold_value: number
    unit: string
  }>
}

export interface CognitiveRunTrace {
  run_id: string
  persona_state: null | {
    persona_version: number
    valence: number
    arousal: number
    social_energy: number
    created_at: string
  }
  steps: Array<{
    sequence: number
    stage: string
    summary: string
    detail: Record<string, ConfigValue>
    created_at: string
  }>
  candidates: Array<{
    sequence: number
    action: string
    confidence: number
    reason_summary: string
    parameters: Record<string, ConfigValue>
    tool_name: string | null
    risk_level: string
    selected: boolean
    rejection_reason: string | null
  }>
  model_invocations: Array<{
    purpose: string
    provider: string
    model: string
    attempt: number
    status: 'running' | 'completed' | 'failed' | 'timed_out'
    input_tokens: number | null
    output_tokens: number | null
    latency_ms: number | null
    estimated_cost_microusd: number
    error_code: string | null
    created_at: string
    completed_at: string | null
  }>
}

export interface EvaluationSuite {
  passed: number
  total: number
  cases: Array<{
    case_id: string
    category: string
    input_text: string
    expected_action: string
    actual_action: string
    passed: boolean
    summary: string
  }>
}

export type EvaluationSuiteStatus = 'draft' | 'published' | 'superseded'

export interface EvaluationCaseDefinition {
  id: string
  case_key: string
  category: string
  input_text: string
  expected_action: 'reply' | 'ask' | 'wait' | 'no_reply' | 'tool'
  reference_response: string | null
  required_phrases: string[]
  forbidden_phrases: string[]
  sort_order: number
}

export interface EvaluationSuiteDefinition {
  id: string
  tenant_id: string
  agent_id: string
  key: string
  name: string
  version: number
  status: EvaluationSuiteStatus
  description: string | null
  minimum_pass_rate: number
  max_output_tokens: number
  cases: EvaluationCaseDefinition[]
  created_by: string
  created_at: string
  published_at: string | null
}

export interface EvaluationSuiteDraft {
  key: string
  name: string
  description: string | null
  minimum_pass_rate: number
  max_output_tokens: number
  cases: Array<Omit<EvaluationCaseDefinition, 'id' | 'sort_order'>>
}

export interface EvaluationCheck {
  key: string
  passed: boolean
  detail: string
}

export interface EvaluationCaseRun {
  id: string
  case_key: string
  category: string
  input_text: string
  expected_action: string
  actual_action: string
  candidate_response: string | null
  reference_response: string | null
  passed: boolean
  checks: EvaluationCheck[]
  summary: string
  latency_ms: number
}

export interface EvaluationRunSummary {
  id: string
  suite_name: string
  suite_version: number
  status: 'completed' | 'failed'
  passed: number
  total: number
  pass_rate: number
  gate_passed: boolean
  provider: string
  model: string
  created_at: string
}

export interface EvaluationRun extends EvaluationRunSummary {
  suite_id: string | null
  suite_key: string
  minimum_pass_rate: number
  configuration_version: number
  persona_version: number
  prompt_version: number
  policy_version: number
  model_route_version: number
  input_tokens: number
  output_tokens: number
  estimated_cost_microusd: number
  error_code: string | null
  results: EvaluationCaseRun[]
  created_by: string
  completed_at: string
}

export interface BlindReviewScore {
  persona_consistency: number
  naturalness: number
  empathy: number
  boundary_respect: number
}

export interface BlindReviewAssignment {
  id: string
  case_key: string
  category: string
  input_text: string
  response_a: string
  response_b: string
  created_at: string
}

export interface BlindReview {
  id: string
  assignment_id: string
  preference: 'candidate' | 'reference' | 'tie'
  candidate_score: BlindReviewScore
  reference_score: BlindReviewScore
  note: string | null
  created_at: string
}

export interface EvaluationReport {
  total_runs: number
  gate_passed_runs: number
  latest_pass_rate: number | null
  pending_reviews: number
  completed_reviews: number
  candidate_wins: number
  reference_wins: number
  ties: number
  candidate_average_score: number | null
  reference_average_score: number | null
}

export type MemoryKind =
  | 'working'
  | 'episodic'
  | 'semantic'
  | 'relational'
  | 'autobiographical'
  | 'procedural'
export type MemoryVisibility = 'user' | 'agent' | 'tenant'
export type MemorySensitivity = 'normal' | 'personal' | 'sensitive' | 'restricted'
export type MemoryConfirmation = 'unconfirmed' | 'confirmed' | 'disputed'
export type MemoryStatus = 'active' | 'superseded' | 'forgotten'

export interface LongTermMemory {
  id: string
  lineage_id: string
  tenant_id: string
  agent_id: string
  user_id: string | null
  conversation_id: string | null
  episode_id: string | null
  kind: MemoryKind
  visibility: MemoryVisibility
  content: string | null
  event_at: string
  confidence: number
  importance: number
  emotional_weight: number
  sensitivity: MemorySensitivity
  confirmation: MemoryConfirmation
  status: MemoryStatus
  version: number
  embedding_version: string | null
  created_by: string
  created_at: string
  updated_at: string
}

export interface MemorySource {
  id: string
  tenant_id: string
  memory_id: string
  kind: 'message' | 'episode' | 'user_statement' | 'admin_correction' | 'reflection' | 'import'
  source_id: string
  excerpt: string | null
  is_verbatim: boolean
  occurred_at: string
  created_at: string
}

export interface MemoryLink {
  id: string
  tenant_id: string
  source_memory_id: string
  target_memory_id: string
  kind: 'related_to' | 'conflicts_with' | 'supersedes' | 'derived_from'
  note: string | null
  created_by: string
  created_at: string
}

export interface MemoryDetail {
  memory: LongTermMemory
  sources: MemorySource[]
  links: MemoryLink[]
}

export interface MemoryRecallItem {
  memory: LongTermMemory
  score: number
  components: Record<string, ConfigValue>
}

export interface Relationship {
  id: string
  tenant_id: string
  agent_id: string
  user_id: string
  stage: 'stranger' | 'acquaintance' | 'familiar' | 'trusted'
  affinity: number
  trust: number
  familiarity: number
  interaction_count: number
  summary: string
  boundaries: string[]
  version: number
  created_at: string
  updated_at: string
}

export interface RelationshipEvent {
  id: string
  tenant_id: string
  relationship_id: string
  event_type: string
  affinity_delta: number
  trust_delta: number
  familiarity_delta: number
  evidence_memory_id: string | null
  summary: string
  created_by: string
  created_at: string
}

export interface RelationshipDetail {
  relationship: Relationship
  events: RelationshipEvent[]
}

export interface MemoryIndexJob {
  id: string
  tenant_id: string
  agent_id: string
  user_id: string | null
  target_embedding_version: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  total_items: number
  processed_items: number
  error_code: string | null
  created_by: string
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export interface Episode {
  id: string
  tenant_id: string
  agent_id: string
  user_id: string
  conversation_id: string
  title: string
  summary: string
  status: 'open' | 'closed' | 'consolidated'
  started_at: string
  ended_at: string | null
  source_message_ids: string[]
  created_by: string
  created_at: string
  updated_at: string
}

interface ConfigVersionList {
  versions: ConfigVersion[]
}

interface ConfigDraftCommand {
  note: string | null
  values: Array<{
    key: string
    scope_type: ConfigScope
    scope_id: string | null
    value: ConfigValue
  }>
}

export interface ConfigDifference {
  key: string
  scope_type: ConfigScope
  scope_id: string | null
  kind: 'added' | 'changed' | 'removed'
  before: ConfigValue
  after: ConfigValue
}

export interface ConfigDiff {
  base_version: number
  target_version: number
  changes: ConfigDifference[]
}

export interface EffectiveConfigValue {
  key: string
  value: ConfigValue
  source: {
    scope_type: ConfigScope | null
    scope_id: string | null
    version: number
  }
}

interface EffectiveConfiguration {
  version: number
  values: EffectiveConfigValue[]
}

export interface SecretMetadata {
  id: string
  key: string
  scope_type: ConfigScope
  scope_id: string | null
  provider: string
  configured: boolean
  masked_hint: string
  integrity_status: 'untested' | 'valid' | 'invalid'
  created_at: string
  updated_at: string
  last_tested_at: string | null
}

interface SecretList {
  secrets: SecretMetadata[]
}

let accessToken: string | null = null

export function setApiAccessToken(token: string | null) {
  accessToken = token
}

export function getApiAccessToken() {
  return accessToken
}

function requestHeaders(json = false): Record<string, string> {
  return {
    Accept: 'application/json',
    ...(json ? { 'Content-Type': 'application/json' } : {}),
    ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
  }
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    headers: requestHeaders(),
  })
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      error?: { message?: string }
    } | null
    throw new Error(payload?.error?.message ?? `请求失败，状态码 ${response.status}`)
  }
  return response.json() as Promise<T>
}

async function getOptionalJson<T>(path: string): Promise<T | null> {
  const response = await fetch(path, { headers: requestHeaders() })
  if (response.status === 404) return null
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      error?: { message?: string }
    } | null
    throw new Error(payload?.error?.message ?? `请求失败，状态码 ${response.status}`)
  }
  return response.json() as Promise<T>
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: requestHeaders(true),
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      error?: { message?: string }
    } | null
    throw new Error(payload?.error?.message ?? `请求失败，状态码 ${response.status}`)
  }
  return response.json() as Promise<T>
}

async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'PATCH',
    headers: requestHeaders(true),
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      error?: { message?: string }
    } | null
    throw new Error(payload?.error?.message ?? `请求失败，状态码 ${response.status}`)
  }
  return response.json() as Promise<T>
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'PUT',
    headers: requestHeaders(true),
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      error?: { message?: string }
    } | null
    throw new Error(payload?.error?.message ?? `请求失败，状态码 ${response.status}`)
  }
  return response.json() as Promise<T>
}

async function deleteJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { method: 'DELETE', headers: requestHeaders() })
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      error?: { message?: string }
    } | null
    throw new Error(payload?.error?.message ?? `请求失败，状态码 ${response.status}`)
  }
  return response.json() as Promise<T>
}

async function deleteRequest(path: string): Promise<void> {
  const response = await fetch(path, { method: 'DELETE', headers: requestHeaders() })
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      error?: { message?: string }
    } | null
    throw new Error(payload?.error?.message ?? `请求失败，状态码 ${response.status}`)
  }
}

export const getSystemOverview = () => getJson<SystemOverview>('/api/v1/system/overview')

export const getAuthenticationConfig = () =>
  getJson<AuthenticationConfig>('/api/v1/auth/config')

export const getBootstrapSettings = () =>
  getJson<BootstrapSettings>('/api/v1/system/settings')

export const getTaskStatus = () =>
  getJson<TaskStatus>('/api/v1/system/tasks/status')

export const getTaskDashboard = () =>
  getJson<TaskDashboard>('/api/v1/tasks/dashboard')

export const getBackgroundJobs = (filters: {
  status?: BackgroundJobStatus
  kind?: BackgroundJobKind
} = {}) => {
  const query = new URLSearchParams({ limit: '200' })
  if (filters.status) query.set('job_status', filters.status)
  if (filters.kind) query.set('kind', filters.kind)
  return getJson<{ items: BackgroundJob[] }>(`/api/v1/tasks/jobs?${query}`)
}

export const getBackgroundJob = (jobId: string) =>
  getJson<{ job: BackgroundJob; attempts: JobAttempt[] }>(`/api/v1/tasks/jobs/${jobId}`)

export const cancelBackgroundJob = (jobId: string) =>
  postJson<BackgroundJob>(`/api/v1/tasks/jobs/${jobId}/cancel`, { confirmed: true })

export const replayBackgroundJob = (jobId: string, reason: string) =>
  postJson<BackgroundJob>(`/api/v1/tasks/jobs/${jobId}/replay`, {
    confirmed: true,
    reason,
  })

export const getScheduledActions = (status?: ScheduledActionStatus) => {
  const query = new URLSearchParams({ limit: '200' })
  if (status) query.set('action_status', status)
  return getJson<{ items: ScheduledAction[] }>(`/api/v1/tasks/scheduled-actions?${query}`)
}

export const createScheduledAction = (command: {
  user_id: string
  conversation_id: string | null
  kind: ScheduledAction['kind']
  scheduled_for: string
  expires_at: string | null
  idempotency_key: string
  reason: string
  payload: Record<string, ConfigValue>
  social_cost: number
}) => postJson<ScheduledAction>('/api/v1/tasks/scheduled-actions', command)

export const cancelScheduledAction = (actionId: string) =>
  postJson<ScheduledAction>(`/api/v1/tasks/scheduled-actions/${actionId}/cancel`, {
    confirmed: true,
  })

export const getChannelCatalog = () =>
  getJson<{ items: ChannelCatalogItem[] }>('/api/v1/channels/catalog')

export const getModelCapabilities = () =>
  getJson<{ items: ModelCapabilityProfile[] }>('/api/v1/channels/model-capabilities')

export const getChannelInstances = () =>
  getJson<{ items: ChannelInstance[] }>('/api/v1/channels')

export const createChannelInstance = (command: {
  name: string
  platform: ChannelPlatform
  status: ChannelInstanceStatus
  rate_limit_per_minute: number
  settings: Record<string, ConfigValue>
  credential: string | null
}) => postJson<ChannelInstance>('/api/v1/channels', command)

export const updateChannelInstance = (
  channelId: string,
  command: {
    name?: string
    status?: ChannelInstanceStatus
    rate_limit_per_minute?: number
    settings?: Record<string, ConfigValue>
    confirmed: boolean
  },
) => patchJson<ChannelInstance>(`/api/v1/channels/${channelId}`, command)

export const setChannelCredential = (channelId: string, credential: string) =>
  putJson<ChannelInstance>(`/api/v1/channels/${channelId}/credential`, { credential })

export const clearChannelCredential = (channelId: string) =>
  postJson<ChannelInstance>(`/api/v1/channels/${channelId}/credential/clear`, {
    confirmed: true,
  })

export const testChannelConnection = (channelId: string) =>
  postJson<ChannelInstance>(`/api/v1/channels/${channelId}/connection-test`)

export const simulateChannel = (command: {
  platform: ChannelPlatform
  blocks: Array<{ kind: 'text' | 'markdown'; text: string }>
  request_streaming: boolean
  thread_id: string | null
  edit_message_id: string | null
  proactive: boolean
}) => postJson<ChannelSimulation>('/api/v1/channels/simulate', command)

export const deliverChannelMessage = (
  channelId: string,
  command: {
    recipient_id: string
    blocks: Array<{ kind: 'text' | 'markdown'; text: string }>
    idempotency_key: string
    request_streaming: boolean
    proactive: boolean
  },
) => postJson<{
  status: ChannelDiagnosticEvent['status']
  external_message_id: string
  degradations: string[]
  delivered_at: string
  idempotent_replay: boolean
}>(`/api/v1/channels/${channelId}/deliveries`, command)

export const getChannelEvents = (channelId?: string) => {
  const query = new URLSearchParams({ limit: '100' })
  if (channelId) query.set('channel_id', channelId)
  return getJson<{ items: ChannelDiagnosticEvent[] }>(`/api/v1/channels/diagnostics/events?${query}`)
}

export const getAdminSession = () =>
  getJson<AdminSession>('/api/v1/administration/session')

export const getAdminRoles = () =>
  getJson<{ roles: AdminRoleDefinition[] }>('/api/v1/administration/roles')

export const getDataLifecycleOverview = () =>
  getJson<DataLifecycleOverview>('/api/v1/data-lifecycle/overview')

export async function downloadUserDataExport(userId: string): Promise<DataExportDownload> {
  const response = await fetch('/api/v1/data-lifecycle/exports', {
    method: 'POST',
    headers: requestHeaders(true),
    body: JSON.stringify({ user_id: userId }),
  })
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      error?: { message?: string }
    } | null
    throw new Error(payload?.error?.message ?? `请求失败：${response.status}`)
  }
  return {
    blob: await response.blob(),
    filename: `cyber-netizen-user-${userId}.json`,
    sha256: response.headers.get('X-Content-SHA256'),
    runId: response.headers.get('X-Export-Run-ID'),
  }
}

export const forgetUserData = (userId: string, confirmation: string) =>
  postJson<LifecycleRun>('/api/v1/data-lifecycle/forget', {
    user_id: userId,
    confirmation,
  })

export const runRetentionCleanup = () =>
  postJson<LifecycleRun>('/api/v1/data-lifecycle/retention/cleanup', { confirmed: true })

export const runOrphanCleanup = () =>
  postJson<LifecycleRun>('/api/v1/data-lifecycle/objects/orphans/cleanup', { confirmed: true })

export const recordBackupRestoreDrill = (command: {
  manifest_sha256: string
  database_rows_verified: number
  objects_verified: number
  database_integrity_verified: boolean
  object_integrity_verified: boolean
  application_smoke_verified: boolean
  confirmation: string
}) => postJson<LifecycleRun>('/api/v1/data-lifecycle/backup-drills', command)

function administrationListPath(resource: string, search: string, status?: string) {
  const query = new URLSearchParams({ limit: '100' })
  if (search.trim()) query.set('search', search.trim())
  if (status) query.set('entity_status', status)
  return `/api/v1/administration/${resource}?${query}`
}

export const getManagedAgents = (search = '', status?: string) =>
  getJson<CursorPage<ManagedAgent>>(administrationListPath('agents', search, status))

export const updateManagedAgentStatus = (
  ids: string[],
  status: 'active' | 'disabled',
) => postJson<CursorPage<ManagedAgent>>('/api/v1/administration/agents/status', {
  ids,
  status,
  confirmed: true,
})

export const getManagedUsers = (search = '', status?: string) =>
  getJson<CursorPage<ManagedUser>>(administrationListPath('users', search, status))

export const updateManagedUserStatus = (
  ids: string[],
  status: 'active' | 'disabled',
) => postJson<CursorPage<ManagedUser>>('/api/v1/administration/users/status', {
  ids,
  status,
  confirmed: true,
})

export const getAuditRecords = (search = '', action = '') => {
  const query = new URLSearchParams({ limit: '100' })
  if (search.trim()) query.set('search', search.trim())
  if (action.trim()) query.set('action', action.trim())
  return getJson<CursorPage<AuditRecord>>(`/api/v1/administration/audit?${query}`)
}

export const getCognitionResources = (kind?: CognitionResourceKind) => {
  const query = new URLSearchParams()
  if (kind) query.set('kind', kind)
  const suffix = query.size ? `?${query}` : ''
  return getJson<{ items: CognitionResource[] }>(`/api/v1/cognition/resources${suffix}`)
}

export const createCognitionResourceDraft = (command: {
  kind: CognitionResourceKind
  key: string
  name: string
  payload: Record<string, ConfigValue>
  note: string | null
}) => postJson<CognitionResource>('/api/v1/cognition/resources', command)

export const testCognitionResource = (
  kind: CognitionResourceKind,
  payload: Record<string, ConfigValue>,
) => postJson<{ valid: boolean; messages: string[] }>('/api/v1/cognition/resources/test', {
  kind,
  payload,
})

export const publishCognitionResource = (resourceId: string) =>
  postJson<CognitionResource>(`/api/v1/cognition/resources/${resourceId}/publish`)

export const rollbackCognitionResource = (resourceId: string) =>
  postJson<CognitionResource>(`/api/v1/cognition/resources/${resourceId}/rollback`)

export const getCognitiveRunTrace = (runId: string) =>
  getJson<CognitiveRunTrace>(`/api/v1/cognition/runs/${runId}/trace`)

export const getObservabilityDashboard = () =>
  getJson<ObservabilityDashboard>('/api/v1/observability/dashboard')

export const runCognitionEvaluationSuite = () =>
  postJson<EvaluationSuite>('/api/v1/cognition/evaluations/run')

export const getEvaluationSuites = () =>
  getJson<{ items: EvaluationSuiteDefinition[] }>('/api/v1/evaluations/suites')

export const createEvaluationSuite = (draft: EvaluationSuiteDraft) =>
  postJson<EvaluationSuiteDefinition>('/api/v1/evaluations/suites', draft)

export const publishEvaluationSuite = (suiteId: string) =>
  postJson<EvaluationSuiteDefinition>(`/api/v1/evaluations/suites/${suiteId}/publish`)

export const runEvaluation = (suiteId: string | null = null) =>
  postJson<EvaluationRun>('/api/v1/evaluations/runs', { suite_id: suiteId })

export const getEvaluationRuns = () =>
  getJson<{ items: EvaluationRunSummary[] }>('/api/v1/evaluations/runs?limit=20')

export const getEvaluationRun = (runId: string) =>
  getJson<EvaluationRun>(`/api/v1/evaluations/runs/${runId}`)

export const getEvaluationReport = () =>
  getJson<EvaluationReport>('/api/v1/evaluations/report')

export const claimBlindReviewAssignment = (runId: string | null = null) =>
  postJson<BlindReviewAssignment | null>('/api/v1/evaluations/blind-assignments', {
    run_id: runId,
  })

export const submitBlindReview = (
  assignmentId: string,
  input: {
    preference: 'a' | 'b' | 'tie'
    response_a_score: BlindReviewScore
    response_b_score: BlindReviewScore
    note: string | null
  },
) => postJson<BlindReview>(
  `/api/v1/evaluations/blind-assignments/${assignmentId}/reviews`,
  input,
)

export const getMemories = (filters: {
  userId?: string
  query?: string
  status?: MemoryStatus
  kind?: MemoryKind
} = {}) => {
  const query = new URLSearchParams({ limit: '200' })
  if (filters.userId) query.set('user_id', filters.userId)
  if (filters.query?.trim()) query.set('query', filters.query.trim())
  if (filters.status) query.set('status', filters.status)
  if (filters.kind) query.set('kind', filters.kind)
  return getJson<{ items: LongTermMemory[] }>(`/api/v1/memory/memories?${query}`)
}

export const getMemoryDetail = (memoryId: string) =>
  getJson<MemoryDetail>(`/api/v1/memory/memories/${memoryId}`)

export const createMemory = (command: {
  user_id: string | null
  conversation_id: string | null
  episode_id: string | null
  kind: MemoryKind
  visibility: MemoryVisibility
  content: string
  event_at: string
  confidence: number
  importance: number
  emotional_weight: number
  sensitivity: MemorySensitivity
  confirmation: MemoryConfirmation
  sources: Array<{
    kind: MemorySource['kind']
    source_id: string
    excerpt: string | null
    is_verbatim: boolean
    occurred_at: string
  }>
}) => postJson<MemoryDetail>('/api/v1/memory/memories', command)

export const setMemoryConfirmation = (
  memoryId: string,
  confirmation: MemoryConfirmation,
) => postJson<LongTermMemory>(`/api/v1/memory/memories/${memoryId}/confirmation`, {
  confirmation,
})

export const correctMemory = (
  memoryId: string,
  command: { content: string; event_at: string; note: string | null },
) => postJson<MemoryDetail>(`/api/v1/memory/memories/${memoryId}/corrections`, command)

export const linkMemoryConflict = (
  memoryId: string,
  targetMemoryId: string,
  note: string | null,
) => postJson<MemoryLink>(`/api/v1/memory/memories/${memoryId}/conflicts`, {
  target_memory_id: targetMemoryId,
  note,
})

export const forgetMemory = (memoryId: string) =>
  postJson<LongTermMemory>(`/api/v1/memory/memories/${memoryId}/forget`, {
    confirmed: true,
  })

export const recallMemories = (userId: string, query: string, limit?: number) =>
  postJson<{ items: MemoryRecallItem[]; embedding_version: string }>(
    '/api/v1/memory/recall',
    { user_id: userId, query, limit },
  )

export const getRelationship = (userId: string) =>
  getOptionalJson<RelationshipDetail>(`/api/v1/memory/relationship?user_id=${userId}`)

export const createRelationshipEvent = (command: {
  user_id: string
  event_type: string
  affinity_delta: number
  trust_delta: number
  familiarity_delta: number
  summary: string
  boundaries: string[] | null
  evidence_memory_id: string | null
}) => postJson<RelationshipDetail>('/api/v1/memory/relationship/events', command)

export const getMemoryIndexJobs = () =>
  getJson<{ items: MemoryIndexJob[] }>('/api/v1/memory/index-jobs')

export const rebuildMemoryIndex = (userId?: string) =>
  postJson<MemoryIndexJob>('/api/v1/memory/index-jobs', {
    user_id: userId ?? null,
    confirmed: true,
  })

export const getEpisodes = (userId?: string) => {
  const query = new URLSearchParams({ limit: '200' })
  if (userId) query.set('user_id', userId)
  return getJson<{ items: Episode[] }>(`/api/v1/memory/episodes?${query}`)
}

export const createEpisode = (command: {
  user_id: string
  conversation_id: string
  title: string
  summary: string
  started_at: string
  ended_at: string | null
  source_message_ids: string[]
}) => postJson<Episode>('/api/v1/memory/episodes', command)

export const closeEpisode = (episodeId: string, consolidate: boolean) =>
  postJson<Episode>(`/api/v1/memory/episodes/${episodeId}/close`, { consolidate })

export const getConfigRegistry = () =>
  getJson<ConfigRegistry>('/api/v1/configuration/definitions')

export const getConfigVersions = () =>
  getJson<ConfigVersionList>('/api/v1/configuration/versions')

export const createConfigDraft = (command: ConfigDraftCommand) =>
  postJson<ConfigVersion>('/api/v1/configuration/drafts', command)

export const publishConfigVersion = (versionId: string) =>
  postJson<ConfigVersion>(`/api/v1/configuration/versions/${versionId}/publish`)

export const rollbackConfigVersion = (versionId: string) =>
  postJson<ConfigVersion>(`/api/v1/configuration/versions/${versionId}/rollback`)

export const getConfigDiff = (versionId: string) =>
  getJson<ConfigDiff>(`/api/v1/configuration/versions/${versionId}/diff`)

export const getEffectiveConfiguration = (targets: {
  tenantId: string
  agentId?: string
  channelId?: string
  userId?: string
}) => {
  const query = new URLSearchParams({ tenant_id: targets.tenantId })
  if (targets.agentId) query.set('agent_id', targets.agentId)
  if (targets.channelId) query.set('channel_id', targets.channelId)
  if (targets.userId) query.set('user_id', targets.userId)
  return getJson<EffectiveConfiguration>(`/api/v1/configuration/effective?${query}`)
}

export const getSecrets = () => getJson<SecretList>('/api/v1/configuration/secrets')

export const setSecret = (command: {
  key: string
  scope_type: ConfigScope
  scope_id: string | null
  plaintext: string
}) => postJson<SecretMetadata>('/api/v1/configuration/secrets', command)

export const rotateSecret = (secretId: string, plaintext: string) =>
  postJson<SecretMetadata>(`/api/v1/configuration/secrets/${secretId}/rotate`, { plaintext })

export const testSecret = (secretId: string) =>
  postJson<SecretMetadata>(`/api/v1/configuration/secrets/${secretId}/test`)

export const clearSecret = (secretId: string) =>
  deleteRequest(`/api/v1/configuration/secrets/${secretId}`)

export const getDevelopmentIdentity = () =>
  getJson<DevelopmentIdentity>('/api/v1/chat/identity')

export const getConversations = (search = '', status?: ConversationStatus) => {
  const query = new URLSearchParams({ limit: '100' })
  if (search.trim()) query.set('search', search.trim())
  if (status) query.set('conversation_status', status)
  return getJson<CursorPage<Conversation>>(`/api/v1/chat/conversations?${query}`)
}

export const createConversation = (title?: string) =>
  postJson<Conversation>('/api/v1/chat/conversations', { title: title || null })

export const updateConversation = (
  conversationId: string,
  command: { title?: string; status?: ConversationStatus; pinned?: boolean },
) => patchJson<Conversation>(`/api/v1/chat/conversations/${conversationId}`, command)

export const deleteConversation = (conversationId: string) =>
  deleteJson<Conversation>(`/api/v1/chat/conversations/${conversationId}`)

export const getMessages = (conversationId: string) =>
  getJson<CursorPage<ChatMessage>>(
    `/api/v1/chat/conversations/${conversationId}/messages?limit=200`,
  )

export const sendChatMessage = (
  conversationId: string,
  command: { client_message_id: string; content: string; attachment_ids?: string[] },
) =>
  postJson<MessageAccepted>(`/api/v1/chat/conversations/${conversationId}/messages`, command)

export const cancelAgentRun = (runId: string) =>
  postJson<AgentRun>(`/api/v1/chat/runs/${runId}/cancel`)

export const regenerateChatMessage = (messageId: string) =>
  postJson<MessageAccepted>(`/api/v1/chat/messages/${messageId}/regenerate`, {
    client_request_id: crypto.randomUUID(),
  })

export const editChatMessage = (messageId: string, content: string) =>
  postJson<MessageAccepted>(`/api/v1/chat/messages/${messageId}/edit`, {
    client_message_id: crypto.randomUUID(),
    content,
  })

export const getMessageFeedback = (conversationId: string) =>
  getJson<{ items: MessageFeedback[] }>(
    `/api/v1/chat/conversations/${conversationId}/feedback`,
  )

export const setMessageFeedback = (
  messageId: string,
  rating: MessageFeedbackRating,
  comment?: string,
) => putJson<MessageFeedback>(`/api/v1/chat/messages/${messageId}/feedback`, {
  rating,
  comment: comment || null,
})

export const clearMessageFeedback = (messageId: string) =>
  deleteRequest(`/api/v1/chat/messages/${messageId}/feedback`)

export const searchChatMessages = (queryText: string, conversationId?: string) => {
  const query = new URLSearchParams({ query: queryText, limit: '100' })
  if (conversationId) query.set('conversation_id', conversationId)
  return getJson<{ items: MessageSearchResult[] }>(`/api/v1/chat/messages/search?${query}`)
}

export const getAttachments = (conversationId: string) =>
  getJson<{ items: ChatAttachment[] }>(
    `/api/v1/chat/conversations/${conversationId}/attachments`,
  )

export async function calculateFileSha256(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('')
}

export async function reserveAttachment(
  conversationId: string,
  clientMessageId: string,
  file: File,
): Promise<AttachmentReservation> {
  return postJson<AttachmentReservation>('/api/v1/chat/attachments/reservations', {
    conversation_id: conversationId,
    client_message_id: clientMessageId,
    original_name: file.name,
    content_type: file.type || 'application/octet-stream',
    size_bytes: file.size,
    sha256: await calculateFileSha256(file),
  })
}

export function uploadReservedAttachment(
  grant: AttachmentUploadGrant,
  file: File,
  onProgress: (percentage: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest()
    request.open(grant.method, grant.url)
    for (const [name, value] of Object.entries(grant.headers)) request.setRequestHeader(name, value)
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    }
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) resolve()
      else reject(new Error(`附件上传失败，状态码 ${request.status}`))
    }
    request.onerror = () => reject(new Error('附件上传失败，请检查对象存储连接'))
    request.onabort = () => reject(new Error('附件上传已取消'))
    request.send(file)
  })
}

export const completeAttachment = (attachmentId: string) =>
  postJson<ChatAttachment>(`/api/v1/chat/attachments/${attachmentId}/complete`)

export const deleteAttachment = (attachmentId: string) =>
  deleteJson<ChatAttachment>(`/api/v1/chat/attachments/${attachmentId}`)

export const getAttachmentPreview = (attachmentId: string) =>
  getJson<{ attachment: ChatAttachment; url: string; expires_in_seconds: number }>(
    `/api/v1/chat/attachments/${attachmentId}/preview`,
  )

export function formatConfigValue(value: ConfigValue): string {
  if (value === null) return '未设置'
  if (typeof value === 'boolean') return value ? '开启' : '关闭'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

const configVersionStatusLabels: Record<ConfigVersionStatus, string> = {
  draft: '草稿',
  published: '已发布',
  superseded: '已被替代',
}

export function formatConfigVersionStatus(status: ConfigVersionStatus): string {
  return configVersionStatusLabels[status]
}
