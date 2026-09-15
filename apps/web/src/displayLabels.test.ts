import { describe, expect, it } from 'vitest'

import {
  adminRoleLabels,
  auditActionLabels,
  auditResourceLabels,
  backgroundJobStatusLabels,
  channelAlertLifecycleStatusLabels,
  channelCapabilityLabels,
  channelDegradationLabels,
  cognitiveActionLabels,
  cognitiveStageLabels,
  configScopeLabels,
  configurationOptionLabel,
  displayLabel,
  formatEnvironment,
  formatLogLevel,
  formatMetadataEntries,
  memoryKindLabels,
  memorySensitivityLabels,
  memorySourceKindLabels,
  memoryVisibilityLabels,
  notificationDeliveryEventLabels,
  observabilityAlertCodeLabels,
  observabilityAlertSourceTypeLabels,
  observabilityReplayDecisionLabels,
  observabilityReplayReasonLabels,
  observabilityRecommendationActionLabels,
  observabilityRecommendationGuardrailLabels,
  observabilityRecommendationPriorityLabels,
  observabilityRecommendationReasonLabels,
  observabilityUnitLabel,
  permissionLabels,
  relationshipStageLabels,
  secretIntegrityLabels,
} from './displayLabels'

describe('管理后台中文显示文案', () => {
  it('覆盖身份、配置和运行环境协议值', () => {
    expect(adminRoleLabels.admin).toBe('管理员')
    expect(secretIntegrityLabels.valid).toBe('完整性正常')
    expect(formatEnvironment('production')).toBe('生产环境')
    expect(formatLogLevel('INFO')).toBe('信息')
    expect(auditActionLabels['admin_session.revoked']).toBe('撤销管理会话')
    expect(auditActionLabels['user.role_override_expired']).toBe('用户角色覆盖到期')
    expect(auditResourceLabels.admin_session).toBe('管理会话')
    expect(configScopeLabels.agent).toBe('智能体')
    expect(auditActionLabels['agent.created']).toBe('创建智能体')
    expect(auditResourceLabels.prompt).toBe('提示词版本')
    expect(permissionLabels['observability_alert:manage']).toBe('处置通用告警')
    expect(auditActionLabels['observability_alert.suppressed']).toBe('抑制通用告警')
    expect(auditActionLabels['observability_alert.replay_review_blocked']).toBe('阻止通用告警通知重放')
    expect(auditActionLabels['observability_alert.recommendation_feedback_accepted']).toBe(
      '采纳告警处置建议',
    )
    expect(auditActionLabels['observability_alert.recommendation_feedback_rejected']).toBe(
      '驳回告警处置建议',
    )
    expect(auditResourceLabels.observability_alert_disposition).toBe('通用告警处置')
    expect(auditResourceLabels.observability_alert_replay_review).toBe('通用告警通知重放复核')
    expect(auditResourceLabels.observability_alert_recommendation_feedback).toBe(
      '告警处置建议反馈',
    )
  })

  it('覆盖拟人认知与长期记忆协议值', () => {
    expect(cognitiveStageLabels.memory_recall).toBe('记忆召回')
    expect(cognitiveActionLabels.no_reply).toBe('不回复')
    expect(memoryKindLabels.episodic).toBe('情景')
    expect(memorySensitivityLabels.restricted).toBe('受限')
    expect(memorySourceKindLabels.admin_correction).toBe('管理员纠正')
    expect(memoryVisibilityLabels.agent).toBe('智能体共享')
    expect(relationshipStageLabels.trusted).toBe('信任')
  })

  it('覆盖渠道能力与透明降级原因', () => {
    expect(channelCapabilityLabels.streaming).toBe('流式响应')
    expect(channelDegradationLabels.streaming_to_buffered).toBe('流式响应改为缓冲后发送')
    expect(backgroundJobStatusLabels.dead_letter).toBe('死信')
    expect(notificationDeliveryEventLabels.recovery).toBe('恢复通知')
    expect(notificationDeliveryEventLabels.escalation).toBe('升级通知')
    expect(channelAlertLifecycleStatusLabels.resolved).toBe('已恢复')
    expect(observabilityAlertSourceTypeLabels.model_runtime).toBe('模型运行')
    expect(observabilityAlertCodeLabels.model_cost_budget).toBe('模型成本超出窗口预算')
    expect(observabilityReplayDecisionLabels.blocked).toBe('阻止重放')
    expect(observabilityReplayReasonLabels.blocked_active_suppression).toBe('告警仍在抑制期')
    expect(observabilityRecommendationActionLabels.acknowledge).toBe('确认并调查')
    expect(observabilityRecommendationPriorityLabels.urgent).toBe('紧急')
    expect(observabilityRecommendationReasonLabels.baseline_anomaly).toBe('超过稳健基线')
    expect(observabilityRecommendationGuardrailLabels.automatic_execution_forbidden).toBe(
      '禁止自动执行',
    )
    expect(observabilityUnitLabel('jobs')).toBe('项')
  })

  it('未知协议值保留原值以便定位兼容性问题', () => {
    expect(displayLabel({}, 'future_status')).toBe('未识别（future_status）')
  })

  it('将安全元数据键和值格式化为可读中文', () => {
    expect(formatMetadataEntries({ status: 'disabled', objects_deleted: true })).toBe(
      '状态：已停用；对象清理完成：是',
    )
    expect(formatMetadataEntries({ memory_decision: 'skip_small_talk', relationship_signals: ['warmth'] })).toBe(
      '记忆写入决策：跳过寒暄；关系信号：友好或感谢',
    )
    expect(formatMetadataEntries({ compression_applied: true, summary_levels: 3 })).toBe(
      '已应用长对话压缩：是；摘要层级：3',
    )
    expect(formatMetadataEntries({
      observability_recommendation_feedback_purged: 2,
      recommendation_feedback: 3,
    })).toBe('已清理建议反馈：2；建议反馈：3')
  })

  it('将配置协议选项显示为自然中文并保留未知扩展值', () => {
    expect(configurationOptionLabel('high_precision')).toBe('高精度（只保留明确信息）')
    expect(configurationOptionLabel('provider-extension')).toBe('provider-extension')
  })
})
