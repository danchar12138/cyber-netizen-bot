import * as apiSdk from './api-client/generated/sdk.gen'
import type {
  AgentRunResponse,
  AttachmentReservationResponse,
  ConfigDefinitionResponse,
  ConversationResponse,
  MessageAcceptedResponse,
  MessagePartResponse,
  MessageResponse,
} from './api-client/generated/types.gen'
import {
  ApiClientError,
  apiClient,
  getGeneratedApiAccessToken,
  setGeneratedApiAccessToken,
} from './api-client/client'

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

export interface ConfigPackageDocument {
  format: string
  schema_version: string
  source: {
    version: number
    status: ConfigVersionStatus
    note: string | null
    created_at: string
    published_at: string | null
  }
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
  parts: MessagePart[]
}

export type MessagePart = Omit<MessagePartResponse, 'size_bytes'> & {
  size_bytes: number | null
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

export function setApiAccessToken(token: string | null) {
  setGeneratedApiAccessToken(token)
}

export function getApiAccessToken() {
  return getGeneratedApiAccessToken()
}

function normalizeConfigDefinition(definition: ConfigDefinitionResponse): ConfigDefinition {
  return {
    ...definition,
    default: definition.default,
    options: definition.options ?? [],
  }
}

function normalizeConversation(conversation: ConversationResponse): Conversation {
  return {
    ...conversation,
    pinned_at: conversation.pinned_at ?? null,
    archived_at: conversation.archived_at ?? null,
    deleted_at: conversation.deleted_at ?? null,
    branched_from_conversation_id: conversation.branched_from_conversation_id ?? null,
    branched_from_message_id: conversation.branched_from_message_id ?? null,
  }
}

function normalizeMessage(message: MessageResponse): ChatMessage {
  const parts = message.parts?.map((part) => ({
    ...part,
    size_bytes: part.size_bytes ?? null,
  })) ?? [{
    id: `legacy-${message.id}`,
    position: 0,
    kind: 'markdown' as const,
    text: message.content,
    attachment_id: null,
    content_type: null,
    file_name: null,
    size_bytes: null,
    sha256: null,
    alt_text: null,
    created_at: message.created_at,
    updated_at: message.updated_at,
  }]
  return { ...message, edited_from_id: message.edited_from_id ?? null, parts }
}

function normalizeAgentRun(run: AgentRunResponse): AgentRun {
  return {
    ...run,
    input_tokens: run.input_tokens ?? null,
    output_tokens: run.output_tokens ?? null,
    error_code: run.error_code ?? null,
  }
}

function normalizeMessageAccepted(response: MessageAcceptedResponse): MessageAccepted {
  return {
    ...response,
    user_message: normalizeMessage(response.user_message),
    response_message: normalizeMessage(response.response_message),
    run: normalizeAgentRun(response.run),
  }
}

function normalizeAttachmentReservation(
  reservation: AttachmentReservationResponse,
): AttachmentReservation {
  return {
    ...reservation,
    upload: { ...reservation.upload, method: reservation.upload.method ?? 'PUT' },
  }
}

export const getSystemOverview = () => apiSdk.getApiV1SystemOverview()

export const getAuthenticationConfig = () =>
  apiSdk.getApiV1AuthConfig()

export const getBootstrapSettings = () =>
  apiSdk.getApiV1SystemSettings()

export const getTaskStatus = () =>
  apiSdk.getApiV1SystemTasksStatus()

export const getTaskDashboard = () =>
  apiSdk.getApiV1TasksDashboard()

export const getBackgroundJobs = (filters: {
  status?: BackgroundJobStatus
  kind?: BackgroundJobKind
} = {}) => {
  return apiSdk.getApiV1TasksJobs({
    query: { limit: 200, job_status: filters.status, kind: filters.kind },
  })
}

export const getBackgroundJob = (jobId: string) =>
  apiSdk.getApiV1TasksJobsByJobId({ path: { job_id: jobId } })

export const cancelBackgroundJob = (jobId: string) =>
  apiSdk.postApiV1TasksJobsByJobIdCancel({
    path: { job_id: jobId },
    body: { confirmed: true },
  })

export const replayBackgroundJob = (jobId: string, reason: string) =>
  apiSdk.postApiV1TasksJobsByJobIdReplay({
    path: { job_id: jobId },
    body: { confirmed: true, reason },
  })

export const getScheduledActions = (status?: ScheduledActionStatus) => {
  return apiSdk.getApiV1TasksScheduledActions({
    query: { limit: 200, action_status: status },
  })
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
}) => apiSdk.postApiV1TasksScheduledActions({ body: command })

export const cancelScheduledAction = (actionId: string) =>
  apiSdk.postApiV1TasksScheduledActionsByActionIdCancel({
    path: { action_id: actionId },
    body: { confirmed: true },
  })

export const getChannelCatalog = () =>
  apiSdk.getApiV1ChannelsCatalog()

export const getModelCapabilities = () =>
  apiSdk.getApiV1ChannelsModelCapabilities()

export const getChannelInstances = () =>
  apiSdk.getApiV1Channels()

export const createChannelInstance = (command: {
  name: string
  platform: ChannelPlatform
  status: ChannelInstanceStatus
  rate_limit_per_minute: number
  settings: Record<string, ConfigValue>
  credential: string | null
}) => apiSdk.postApiV1Channels({ body: command })

export const updateChannelInstance = (
  channelId: string,
  command: {
    name?: string
    status?: ChannelInstanceStatus
    rate_limit_per_minute?: number
    settings?: Record<string, ConfigValue>
    confirmed: boolean
  },
) => apiSdk.patchApiV1ChannelsByChannelId({ path: { channel_id: channelId }, body: command })

export const setChannelCredential = (channelId: string, credential: string) =>
  apiSdk.putApiV1ChannelsByChannelIdCredential({
    path: { channel_id: channelId },
    body: { credential },
  })

export const clearChannelCredential = (channelId: string) =>
  apiSdk.postApiV1ChannelsByChannelIdCredentialClear({
    path: { channel_id: channelId },
    body: { confirmed: true },
  })

export const testChannelConnection = (channelId: string) =>
  apiSdk.postApiV1ChannelsByChannelIdConnectionTest({ path: { channel_id: channelId } })

export const simulateChannel = (command: {
  platform: ChannelPlatform
  blocks: Array<{ kind: 'text' | 'markdown'; text: string }>
  request_streaming: boolean
  thread_id: string | null
  edit_message_id: string | null
  proactive: boolean
}) => apiSdk.postApiV1ChannelsSimulate({ body: command })

export const deliverChannelMessage = (
  channelId: string,
  command: {
    recipient_id: string
    blocks: Array<{ kind: 'text' | 'markdown'; text: string }>
    idempotency_key: string
    request_streaming: boolean
    proactive: boolean
  },
) => apiSdk.postApiV1ChannelsByChannelIdDeliveries({
  path: { channel_id: channelId },
  body: command,
})

export const getChannelEvents = (channelId?: string) => {
  return apiSdk.getApiV1ChannelsDiagnosticsEvents({
    query: { limit: 100, channel_id: channelId },
  })
}

export const getAdminSession = () =>
  apiSdk.getApiV1AdministrationSession()

export const getAdminRoles = () =>
  apiSdk.getApiV1AdministrationRoles()

export const getDataLifecycleOverview = () =>
  apiSdk.getApiV1DataLifecycleOverview()

export async function downloadUserDataExport(userId: string): Promise<DataExportDownload> {
  const { data, response } = await apiClient.post<{ 200: Blob }, unknown, true, 'fields'>({
    url: '/api/v1/data-lifecycle/exports',
    body: { user_id: userId },
    headers: { 'Content-Type': 'application/json' },
    parseAs: 'blob',
    responseStyle: 'fields',
    throwOnError: true,
  })
  return {
    blob: data,
    filename: `cyber-netizen-user-${userId}.json`,
    sha256: response.headers.get('X-Content-SHA256'),
    runId: response.headers.get('X-Export-Run-ID'),
  }
}

export const forgetUserData = (userId: string, confirmation: string) =>
  apiSdk.postApiV1DataLifecycleForget({ body: { user_id: userId, confirmation } })

export const runRetentionCleanup = () =>
  apiSdk.postApiV1DataLifecycleRetentionCleanup({ body: { confirmed: true } })

export const runOrphanCleanup = () =>
  apiSdk.postApiV1DataLifecycleObjectsOrphansCleanup({ body: { confirmed: true } })

export const recordBackupRestoreDrill = (command: {
  manifest_sha256: string
  database_rows_verified: number
  objects_verified: number
  database_integrity_verified: boolean
  object_integrity_verified: boolean
  application_smoke_verified: boolean
  confirmation: string
}) => apiSdk.postApiV1DataLifecycleBackupDrills({ body: command })

export const getManagedAgents = (search = '', status?: string) =>
  apiSdk.getApiV1AdministrationAgents({
    query: {
      limit: 100,
      search: search.trim() || undefined,
      entity_status: status === 'active' || status === 'disabled' ? status : undefined,
    },
  })

export const updateManagedAgentStatus = (
  ids: string[],
  status: 'active' | 'disabled',
) => apiSdk.postApiV1AdministrationAgentsStatus({ body: { ids, status, confirmed: true } })

export const getManagedUsers = (search = '', status?: string) =>
  apiSdk.getApiV1AdministrationUsers({
    query: {
      limit: 100,
      search: search.trim() || undefined,
      entity_status: status === 'active' || status === 'disabled' ? status : undefined,
    },
  })

export const updateManagedUserStatus = (
  ids: string[],
  status: 'active' | 'disabled',
) => apiSdk.postApiV1AdministrationUsersStatus({ body: { ids, status, confirmed: true } })

export const getAuditRecords = (search = '', action = '') => {
  return apiSdk.getApiV1AdministrationAudit({
    query: {
      limit: 100,
      search: search.trim() || undefined,
      action: action.trim() || undefined,
    },
  })
}

export const getCognitionResources = (kind?: CognitionResourceKind) => {
  return apiSdk.getApiV1CognitionResources({ query: { kind } })
}

export const createCognitionResourceDraft = (command: {
  kind: CognitionResourceKind
  key: string
  name: string
  payload: Record<string, ConfigValue>
  note: string | null
}) => apiSdk.postApiV1CognitionResources({ body: command })

export const testCognitionResource = (
  kind: CognitionResourceKind,
  payload: Record<string, ConfigValue>,
) => apiSdk.postApiV1CognitionResourcesTest({ body: { kind, payload } })

export const publishCognitionResource = (resourceId: string) =>
  apiSdk.postApiV1CognitionResourcesByResourceIdPublish({
    path: { resource_id: resourceId },
  })

export const rollbackCognitionResource = (resourceId: string) =>
  apiSdk.postApiV1CognitionResourcesByResourceIdRollback({
    path: { resource_id: resourceId },
  })

export const getCognitiveRunTrace = (runId: string) =>
  apiSdk.getApiV1CognitionRunsByRunIdTrace({ path: { run_id: runId } })

export const getObservabilityDashboard = () =>
  apiSdk.getApiV1ObservabilityDashboard()

export const runCognitionEvaluationSuite = () =>
  apiSdk.postApiV1CognitionEvaluationsRun()

export const getEvaluationSuites = () =>
  apiSdk.getApiV1EvaluationsSuites()

export const createEvaluationSuite = (draft: EvaluationSuiteDraft) =>
  apiSdk.postApiV1EvaluationsSuites({ body: draft })

export const publishEvaluationSuite = (suiteId: string) =>
  apiSdk.postApiV1EvaluationsSuitesBySuiteIdPublish({ path: { suite_id: suiteId } })

export const runEvaluation = (suiteId: string | null = null) =>
  apiSdk.postApiV1EvaluationsRuns({ body: { suite_id: suiteId } })

export const getEvaluationRuns = () =>
  apiSdk.getApiV1EvaluationsRuns({ query: { limit: 20 } })

export const getEvaluationRun = (runId: string) =>
  apiSdk.getApiV1EvaluationsRunsByRunId({ path: { run_id: runId } })

export const getEvaluationReport = () =>
  apiSdk.getApiV1EvaluationsReport()

export const claimBlindReviewAssignment = (runId: string | null = null) =>
  apiSdk.postApiV1EvaluationsBlindAssignments({ body: { run_id: runId } })

export const submitBlindReview = (
  assignmentId: string,
  input: {
    preference: 'a' | 'b' | 'tie'
    response_a_score: BlindReviewScore
    response_b_score: BlindReviewScore
    note: string | null
  },
) => apiSdk.postApiV1EvaluationsBlindAssignmentsByAssignmentIdReviews({
  path: { assignment_id: assignmentId },
  body: input,
})

export const getMemories = (filters: {
  userId?: string
  query?: string
  status?: MemoryStatus
  kind?: MemoryKind
} = {}) => {
  return apiSdk.getApiV1MemoryMemories({
    query: {
      limit: 200,
      user_id: filters.userId,
      query: filters.query?.trim() || undefined,
      status: filters.status,
      kind: filters.kind,
    },
  })
}

export const getMemoryDetail = (memoryId: string) =>
  apiSdk.getApiV1MemoryMemoriesByMemoryId({ path: { memory_id: memoryId } })

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
}) => apiSdk.postApiV1MemoryMemories({ body: command })

export const setMemoryConfirmation = (
  memoryId: string,
  confirmation: MemoryConfirmation,
) => apiSdk.postApiV1MemoryMemoriesByMemoryIdConfirmation({
  path: { memory_id: memoryId },
  body: { confirmation },
})

export const correctMemory = (
  memoryId: string,
  command: { content: string; event_at: string; note: string | null },
) => apiSdk.postApiV1MemoryMemoriesByMemoryIdCorrections({
  path: { memory_id: memoryId },
  body: command,
})

export const linkMemoryConflict = (
  memoryId: string,
  targetMemoryId: string,
  note: string | null,
) => apiSdk.postApiV1MemoryMemoriesByMemoryIdConflicts({
  path: { memory_id: memoryId },
  body: { target_memory_id: targetMemoryId, note },
})

export const forgetMemory = (memoryId: string) =>
  apiSdk.postApiV1MemoryMemoriesByMemoryIdForget({
    path: { memory_id: memoryId },
    body: { confirmed: true },
  })

export const recallMemories = (userId: string, query: string, limit?: number) =>
  apiSdk.postApiV1MemoryRecall({ body: { user_id: userId, query, limit } })

export async function getRelationship(userId: string): Promise<RelationshipDetail | null> {
  try {
    return await apiSdk.getApiV1MemoryRelationship({ query: { user_id: userId } })
  } catch (error) {
    if (error instanceof ApiClientError && error.status === 404) return null
    throw error
  }
}

export const createRelationshipEvent = (command: {
  user_id: string
  event_type: string
  affinity_delta: number
  trust_delta: number
  familiarity_delta: number
  summary: string
  boundaries: string[] | null
  evidence_memory_id: string | null
}) => apiSdk.postApiV1MemoryRelationshipEvents({ body: command })

export const getMemoryIndexJobs = () =>
  apiSdk.getApiV1MemoryIndexJobs()

export const rebuildMemoryIndex = (userId?: string) =>
  apiSdk.postApiV1MemoryIndexJobs({
    body: { user_id: userId ?? null, confirmed: true },
  })

export const getEpisodes = (userId?: string) => {
  return apiSdk.getApiV1MemoryEpisodes({ query: { limit: 200, user_id: userId } })
}

export const createEpisode = (command: {
  user_id: string
  conversation_id: string
  title: string
  summary: string
  started_at: string
  ended_at: string | null
  source_message_ids: string[]
}) => apiSdk.postApiV1MemoryEpisodes({ body: command })

export const closeEpisode = (episodeId: string, consolidate: boolean) =>
  apiSdk.postApiV1MemoryEpisodesByEpisodeIdClose({
    path: { episode_id: episodeId },
    body: { consolidate },
  })

export const getConfigRegistry = () =>
  apiSdk.getApiV1ConfigurationDefinitions().then((registry) => ({
    ...registry,
    schema_version: registry.schema_version ?? '1',
    definitions: registry.definitions.map(normalizeConfigDefinition),
  }))

export const getConfigVersions = () =>
  apiSdk.getApiV1ConfigurationVersions()

export const createConfigDraft = (command: ConfigDraftCommand) =>
  apiSdk.postApiV1ConfigurationDrafts({ body: command })

export const exportConfigPackage = (versionId: string) =>
  apiSdk.getApiV1ConfigurationVersionsByVersionIdExport({ path: { version_id: versionId } })

export const importConfigPackage = (document: ConfigPackageDocument) =>
  apiSdk.postApiV1ConfigurationImports({ body: document }) as Promise<ConfigVersion>

export const publishConfigVersion = (versionId: string) =>
  apiSdk.postApiV1ConfigurationVersionsByVersionIdPublish({
    path: { version_id: versionId },
  })

export const rollbackConfigVersion = (versionId: string) =>
  apiSdk.postApiV1ConfigurationVersionsByVersionIdRollback({
    path: { version_id: versionId },
  })

export const getConfigDiff = (versionId: string) =>
  apiSdk.getApiV1ConfigurationVersionsByVersionIdDiff({ path: { version_id: versionId } })

export const getEffectiveConfiguration = (targets: {
  tenantId: string
  agentId?: string
  channelId?: string
  userId?: string
}) => {
  return apiSdk.getApiV1ConfigurationEffective({
    query: {
      tenant_id: targets.tenantId,
      agent_id: targets.agentId,
      channel_id: targets.channelId,
      user_id: targets.userId,
    },
  })
}

export const getSecrets = () => apiSdk.getApiV1ConfigurationSecrets()

export const setSecret = (command: {
  key: string
  scope_type: ConfigScope
  scope_id: string | null
  plaintext: string
}) => apiSdk.postApiV1ConfigurationSecrets({ body: command })

export const rotateSecret = (secretId: string, plaintext: string) =>
  apiSdk.postApiV1ConfigurationSecretsBySecretIdRotate({
    path: { secret_id: secretId },
    body: { plaintext },
  })

export const testSecret = (secretId: string) =>
  apiSdk.postApiV1ConfigurationSecretsBySecretIdTest({ path: { secret_id: secretId } })

export const clearSecret = (secretId: string) =>
  apiSdk.deleteApiV1ConfigurationSecretsBySecretId({ path: { secret_id: secretId } })

export const getDevelopmentIdentity = () =>
  apiSdk.getApiV1ChatIdentity()

export const getConversations = (search = '', status?: ConversationStatus) => {
  return apiSdk.getApiV1ChatConversations({
    query: {
      limit: 100,
      search: search.trim() || undefined,
      conversation_status: status,
    },
  }).then((page) => ({
    items: page.items.map(normalizeConversation),
    next_cursor: page.next_cursor ?? null,
  }))
}

export const createConversation = (title?: string) =>
  apiSdk.postApiV1ChatConversations({ body: { title: title || null } }).then(normalizeConversation)

export const updateConversation = (
  conversationId: string,
  command: { title?: string; status?: ConversationStatus; pinned?: boolean },
) => apiSdk.patchApiV1ChatConversationsByConversationId({
  path: { conversation_id: conversationId },
  body: command,
}).then(normalizeConversation)

export const deleteConversation = (conversationId: string) =>
  apiSdk.deleteApiV1ChatConversationsByConversationId({
    path: { conversation_id: conversationId },
  }).then(normalizeConversation)

export const getMessages = (conversationId: string) =>
  apiSdk.getApiV1ChatConversationsByConversationIdMessages({
    path: { conversation_id: conversationId },
    query: { limit: 200 },
  }).then((page) => ({
    items: page.items.map(normalizeMessage),
    next_cursor: page.next_cursor ?? null,
  }))

export const sendChatMessage = (
  conversationId: string,
  command: { client_message_id: string; content: string; attachment_ids?: string[] },
) =>
  apiSdk.postApiV1ChatConversationsByConversationIdMessages({
    path: { conversation_id: conversationId },
    body: command,
  }).then(normalizeMessageAccepted)

export const cancelAgentRun = (runId: string) =>
  apiSdk.postApiV1ChatRunsByRunIdCancel({ path: { run_id: runId } })

export const regenerateChatMessage = (messageId: string) =>
  apiSdk.postApiV1ChatMessagesByMessageIdRegenerate({
    path: { message_id: messageId },
    body: { client_request_id: crypto.randomUUID() },
  }).then(normalizeMessageAccepted)

export const editChatMessage = (messageId: string, content: string) =>
  apiSdk.postApiV1ChatMessagesByMessageIdEdit({
    path: { message_id: messageId },
    body: { client_message_id: crypto.randomUUID(), content },
  }).then(normalizeMessageAccepted)

export const getMessageFeedback = (conversationId: string) =>
  apiSdk.getApiV1ChatConversationsByConversationIdFeedback({
    path: { conversation_id: conversationId },
  })

export const setMessageFeedback = (
  messageId: string,
  rating: MessageFeedbackRating,
  comment?: string,
) => apiSdk.putApiV1ChatMessagesByMessageIdFeedback({
  path: { message_id: messageId },
  body: { rating, comment: comment || null },
})

export const clearMessageFeedback = (messageId: string) =>
  apiSdk.deleteApiV1ChatMessagesByMessageIdFeedback({ path: { message_id: messageId } })

export const searchChatMessages = (queryText: string, conversationId?: string) => {
  return apiSdk.getApiV1ChatMessagesSearch({
    query: { query: queryText, limit: 100, conversation_id: conversationId },
  }).then((response) => ({
    items: response.items.map((item) => ({
      conversation: normalizeConversation(item.conversation),
      message: normalizeMessage(item.message),
    })),
  }))
}

export const getAttachments = (conversationId: string) =>
  apiSdk.getApiV1ChatConversationsByConversationIdAttachments({
    path: { conversation_id: conversationId },
  })

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
  return apiSdk.postApiV1ChatAttachmentsReservations({
    body: {
      conversation_id: conversationId,
      client_message_id: clientMessageId,
      original_name: file.name,
      content_type: file.type || 'application/octet-stream',
      size_bytes: file.size,
      sha256: await calculateFileSha256(file),
    },
  }).then(normalizeAttachmentReservation)
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
  apiSdk.postApiV1ChatAttachmentsByAttachmentIdComplete({
    path: { attachment_id: attachmentId },
  })

export const deleteAttachment = (attachmentId: string) =>
  apiSdk.deleteApiV1ChatAttachmentsByAttachmentId({ path: { attachment_id: attachmentId } })

export const getAttachmentPreview = (attachmentId: string) =>
  apiSdk.getApiV1ChatAttachmentsByAttachmentIdPreview({
    path: { attachment_id: attachmentId },
  })

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
