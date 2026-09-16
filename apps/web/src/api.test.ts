import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  acknowledgeObservabilityAlert,
  batchDisposeObservabilityAlerts,
  clearObservabilityAlertDisposition,
  createObservabilityAlertRecommendationCalibrationDraft,
  type ConfigPackageDocument,
  downloadObservabilityAlertHistory,
  exportConfigPackage,
  formatConfigValue,
  formatConfigVersionStatus,
  getAuditRecords,
  getObservabilityAlertLifecycles,
  getObservabilityAlertDispositionEvents,
  getObservabilityAlertLifecycleMetrics,
  getObservabilityAlertLifecyclePage,
  getObservabilityAlertOperationsSummary,
  getObservabilityAlertRecommendationCalibration,
  getObservabilityAlertRecommendationCalibrationReplay,
  getObservabilityAlertRecommendationEvidence,
  getObservabilityAlertRecommendationQuality,
  getObservabilityAlertRecommendations,
  getUnifiedQualityOverview,
  getObservabilityAlertReplayMetrics,
  getObservabilityAlertReplayReviews,
  importConfigPackage,
  setApiAccessToken,
  submitObservabilityAlertRecommendationFeedback,
  suppressObservabilityAlert,
} from './api'
import { setSelectedAgentId } from './agentSelection'

afterEach(() => {
  setApiAccessToken(null)
  setSelectedAgentId(null)
  vi.unstubAllGlobals()
})

describe('formatConfigValue', () => {
  it('为中文管理界面格式化布尔值', () => {
    expect(formatConfigValue(true)).toBe('开启')
    expect(formatConfigValue(false)).toBe('关闭')
  })

  it('不会混淆未设置值与空字符串', () => {
    expect(formatConfigValue(null)).toBe('未设置')
    expect(formatConfigValue('')).toBe('')
  })
})

describe('formatConfigVersionStatus', () => {
  it('以中文展示所有配置版本状态', () => {
    expect(formatConfigVersionStatus('draft')).toBe('草稿')
    expect(formatConfigVersionStatus('published')).toBe('已发布')
    expect(formatConfigVersionStatus('superseded')).toBe('已被替代')
  })
})

describe('通用告警客户端', () => {
  it('使用当前 Agent 的告警运营摘要端点', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      generated_at: '2026-09-15T12:00:00Z',
      baseline: {
        window_started_at: '2026-09-15T04:00:00Z',
        window_ended_at: '2026-09-15T12:00:00Z',
        window_minutes: 480,
        periods: 7,
        sensitivity: 3,
        minimum_current_count: 3,
        signals: [],
      },
      handoff: {
        window_started_at: '2026-09-15T04:00:00Z',
        window_ended_at: '2026-09-15T12:00:00Z',
        active: 0,
        critical_active: 0,
        unacknowledged_active: 0,
        acknowledged_active: 0,
        suppressed_active: 0,
        opened: 0,
        resolved: 0,
        escalated: 0,
        blocked_replays: 0,
        sources: [],
        priority_items: [],
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await getObservabilityAlertOperationsSummary()

    const request = fetchMock.mock.calls[0]?.[0] as Request
    expect(new URL(request.url).pathname).toBe(
      '/api/v1/observability/alert-operations-summary',
    )
  })

  it('使用只读端点完整传递告警建议筛选', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([]), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await getObservabilityAlertRecommendations({
      source_type: 'model_runtime',
      severity: 'critical',
      action: 'acknowledge',
      limit: 20,
    })

    const request = fetchMock.mock.calls[0]?.[0] as Request
    const url = new URL(request.url)
    expect(url.pathname).toBe('/api/v1/observability/alert-recommendations')
    expect(Object.fromEntries(url.searchParams)).toEqual({
      source_type: 'model_runtime',
      severity: 'critical',
      action: 'acknowledge',
      limit: '20',
    })
    expect(request.method).toBe('GET')
  })

  it('查询单条告警建议的安全证据摘要', async () => {
    const lifecycleId = '11111111-1111-4111-8111-111111111111'
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      lifecycle_id: lifecycleId,
      source_type: 'api',
      source_key: 'global',
      code: 'api_error_rate',
      evaluated_at: '2026-09-15T12:00:00Z',
      current_value: 20,
      threshold_value: 10,
      unit: '%',
      active_minutes: 30,
      occurrences: 2,
      escalation_level: 1,
      baseline_anomalous: true,
      baseline_median: 1,
      baseline_mad: 0,
      baseline_threshold: 3,
      baseline_samples: [0, 1, 0],
      action: 'acknowledge',
      priority: 'urgent',
      confidence: 0.9,
      reason_codes: ['critical'],
      guardrail_codes: ['manual_confirmation_required', 'automatic_execution_forbidden', 'current_scope_only'],
      evidence_fingerprint: 'a'.repeat(64),
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await getObservabilityAlertRecommendationEvidence(lifecycleId)

    const request = fetchMock.mock.calls[0]?.[0] as Request
    expect(new URL(request.url).pathname).toBe(
      `/api/v1/observability/alert-recommendations/${lifecycleId}/evidence`,
    )
    expect(request.method).toBe('GET')
  })

  it('查询建议质量并仅提交服务端可验证的反馈结论', async () => {
    const lifecycleId = '11111111-1111-4111-8111-111111111111'
    const fetchMock = vi.fn().mockImplementation((request: Request) => {
      const url = new URL(request.url)
      const body = url.pathname.endsWith('/quality')
        ? {
            window_started_at: '2026-09-08T12:00:00Z',
            window_ended_at: '2026-09-15T12:00:00Z',
            total: 0,
            accepted: 0,
            rejected: 0,
            acceptance_rate_percent: 0,
            accepted_resolved: 0,
            accepted_active: 0,
            replay_total: 0,
            replay_allowed: 0,
            replay_blocked: 0,
            actions: [],
            sources: [],
          }
        : {
            id: '22222222-2222-4222-8222-222222222222',
            lifecycle_id: lifecycleId,
            source_type: 'api',
            source_key: 'global',
            code: 'api_error_rate',
            recommendation_action: 'acknowledge',
            priority: 'urgent',
            reason_codes: ['critical'],
            decision: 'accepted',
            actor_id: '33333333-3333-4333-8333-333333333333',
            feedback_at: '2026-09-15T12:00:00Z',
          }
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    })
    vi.stubGlobal('fetch', fetchMock)

    await getObservabilityAlertRecommendationQuality({
      window_minutes: 720,
      source_type: 'api',
    })
    await submitObservabilityAlertRecommendationFeedback(lifecycleId, 'accepted')
    await submitObservabilityAlertRecommendationFeedback(lifecycleId, 'rejected', 'observe')

    const qualityRequest = fetchMock.mock.calls[0]?.[0] as Request
    const qualityUrl = new URL(qualityRequest.url)
    expect(qualityUrl.pathname).toBe('/api/v1/observability/alert-recommendations/quality')
    expect(Object.fromEntries(qualityUrl.searchParams)).toEqual({
      window_minutes: '720',
      source_type: 'api',
    })
    expect(qualityRequest.method).toBe('GET')

    const feedbackRequest = fetchMock.mock.calls[1]?.[0] as Request
    expect(new URL(feedbackRequest.url).pathname).toBe(
      `/api/v1/observability/alert-recommendations/${lifecycleId}/feedback`,
    )
    expect(feedbackRequest.method).toBe('POST')
    expect(await feedbackRequest.json()).toEqual({ decision: 'accepted', confirmed: true })

    const alternativeFeedbackRequest = fetchMock.mock.calls[2]?.[0] as Request
    expect(new URL(alternativeFeedbackRequest.url).pathname).toBe(
      `/api/v1/observability/alert-recommendations/${lifecycleId}/feedback`,
    )
    expect(alternativeFeedbackRequest.method).toBe('POST')
    expect(await alternativeFeedbackRequest.json()).toEqual({
      decision: 'rejected',
      confirmed: true,
      alternative_action: 'observe',
    })
  })

  it('读取服务端校准结果并只提交版本与显式确认创建草稿', async () => {
    const fetchMock = vi.fn().mockImplementation((request: Request) => {
      const body = request.method === 'GET'
        ? {
            window_started_at: '2026-08-16T12:00:00Z',
            window_ended_at: '2026-09-15T12:00:00Z',
            configuration_version: 7,
            minimum_samples_per_group: 20,
            target_acceptance_rate_percent: 70,
            confidence_level_percent: 95,
            total_feedback: 20,
            eligible_feedback: 20,
            groups: [],
            proposals: [],
            replay_fingerprint: 'a'.repeat(64),
            automatic_tuning_allowed: false,
          }
        : {
            id: '22222222-2222-4222-8222-222222222222',
            version: 8,
            status: 'draft',
            note: '告警建议离线校准',
            created_at: '2026-09-15T12:00:00Z',
            published_at: null,
            values: [],
          }
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: request.method === 'GET' ? 200 : 201,
        headers: { 'Content-Type': 'application/json' },
      }))
    })
    vi.stubGlobal('fetch', fetchMock)

    await getObservabilityAlertRecommendationCalibration()
    await createObservabilityAlertRecommendationCalibrationDraft(
      7,
      'a'.repeat(64),
      '2026-09-15T12:00:00Z',
    )

    const analysisRequest = fetchMock.mock.calls[0]?.[0] as Request
    expect(new URL(analysisRequest.url).pathname).toBe(
      '/api/v1/observability/alert-recommendations/calibration',
    )
    expect(analysisRequest.method).toBe('GET')
    const draftRequest = fetchMock.mock.calls[1]?.[0] as Request
    expect(new URL(draftRequest.url).pathname).toBe(
      '/api/v1/observability/alert-recommendations/calibration/drafts',
    )
    expect(draftRequest.method).toBe('POST')
    expect(await draftRequest.json()).toEqual({
      configuration_version: 7,
      replay_fingerprint: 'a'.repeat(64),
      replay_window_ended_at: '2026-09-15T12:00:00Z',
      confirmed: true,
    })
  })

  it('读取候选阈值场景回放结果并使用默认窗口', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      window_started_at: '2026-08-16T12:00:00Z',
      window_ended_at: '2026-09-15T12:00:00Z',
      configuration_version: 7,
      total_feedback: 20,
      eligible_feedback: 20,
      proposals: [],
      replay_fingerprint: 'a'.repeat(64),
      scenario_only: true,
      automatic_tuning_allowed: false,
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await getObservabilityAlertRecommendationCalibrationReplay()

    const request = fetchMock.mock.calls[0]?.[0] as Request
    expect(new URL(request.url).pathname).toBe(
      '/api/v1/observability/alert-recommendations/calibration/replay',
    )
    expect(request.method).toBe('GET')
  })

  it('完整传递生命周期组合筛选参数', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([]), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await getObservabilityAlertLifecycles({
      status: 'active',
      source_type: 'model_runtime',
      severity: 'critical',
      minimum_duration_minutes: 30,
    })

    const request = fetchMock.mock.calls[0]?.[0] as Request
    const url = new URL(request.url)
    expect(url.pathname).toBe('/api/v1/observability/alert-lifecycles')
    expect(Object.fromEntries(url.searchParams)).toEqual({
      status: 'active',
      source_type: 'model_runtime',
      severity: 'critical',
      minimum_duration_minutes: '30',
      limit: '100',
    })
  })

  it('通过生成客户端传递生命周期和复核历史游标', async () => {
    const fetchMock = vi.fn().mockImplementation((request: Request) => {
      const pathname = new URL(request.url).pathname
      const body = pathname.endsWith('/metrics')
        ? {
            window_started_at: '2026-09-14T12:00:00Z',
            window_ended_at: '2026-09-15T12:00:00Z',
            total: 0,
            allowed: 0,
            blocked: 0,
            allowed_rate_percent: 0,
            reasons: [],
            sources: [],
          }
        : { items: [], next_cursor: null }
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    })
    vi.stubGlobal('fetch', fetchMock)

    await getObservabilityAlertLifecyclePage({ source_type: 'api', cursor: 'next-life', limit: 25 })
    await getObservabilityAlertReplayReviews({
      decision: 'blocked',
      reason_code: 'blocked_active_suppression',
      cursor: 'next-review',
      limit: 20,
    })
    await getObservabilityAlertReplayMetrics({ window_minutes: 720, source_type: 'api' })

    const urls = fetchMock.mock.calls.map((call) => new URL((call[0] as Request).url))
    expect(urls[0]!.pathname).toBe('/api/v1/observability/alert-lifecycles/page')
    expect(Object.fromEntries(urls[0]!.searchParams)).toEqual({
      source_type: 'api', cursor: 'next-life', limit: '25',
    })
    expect(urls[1]!.pathname).toBe('/api/v1/observability/alert-replay-reviews')
    expect(Object.fromEntries(urls[1]!.searchParams)).toEqual({
      decision: 'blocked',
      reason_code: 'blocked_active_suppression',
      cursor: 'next-review',
      limit: '20',
    })
    expect(urls[2]!.pathname).toBe('/api/v1/observability/alert-replay-reviews/metrics')
    expect(Object.fromEntries(urls[2]!.searchParams)).toEqual({
      window_minutes: '720', source_type: 'api',
    })
  })

  it('使用专用路径和显式确认请求体执行三种处置', async () => {
    const lifecycleId = '11111111-1111-4111-8111-111111111111'
    const response = {
      lifecycle_id: lifecycleId,
      code: 'model_failure_rate',
      source_type: 'model_runtime',
      source_key: 'openai:gpt-5',
      status: 'acknowledged',
      reason: '值班人员正在处理',
      expires_at: null,
      updated_at: '2026-09-14T04:00:00Z',
    }
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(
      JSON.stringify(response),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )))
    vi.stubGlobal('fetch', fetchMock)

    await acknowledgeObservabilityAlert(lifecycleId, {
      confirmed: true,
      reason: '值班人员正在处理',
    })
    await suppressObservabilityAlert(lifecycleId, {
      confirmed: true,
      reason: '计划内维护',
      expires_at: '2026-09-14T06:00:00Z',
    })
    await clearObservabilityAlertDisposition(lifecycleId, { confirmed: true })

    const requests = fetchMock.mock.calls.map((call) => call[0] as Request)
    expect(requests.map((request) => new URL(request.url).pathname)).toEqual([
      `/api/v1/observability/alert-lifecycles/${lifecycleId}/acknowledge`,
      `/api/v1/observability/alert-lifecycles/${lifecycleId}/suppress`,
      `/api/v1/observability/alert-lifecycles/${lifecycleId}/clear-disposition`,
    ])
    expect(await requests[0]!.json()).toEqual({ confirmed: true, reason: '值班人员正在处理' })
    expect(await requests[1]!.json()).toEqual({
      confirmed: true,
      reason: '计划内维护',
      expires_at: '2026-09-14T06:00:00Z',
    })
    expect(await requests[2]!.json()).toEqual({ confirmed: true })
  })

  it('使用通用告警专用路径传递趋势筛选', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      window_started_at: '2026-09-14T12:00:00Z',
      window_ended_at: '2026-09-15T12:00:00Z',
      active: 0,
      opened: 0,
      resolved: 0,
      escalated: 0,
      mean_recovery_seconds: 0,
      p95_recovery_seconds: 0,
      sources: [],
      trend: [],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await getObservabilityAlertLifecycleMetrics({
      window_minutes: 720,
      bucket_minutes: 30,
      source_type: 'api',
      severity: 'warning',
    })

    const request = fetchMock.mock.calls[0]?.[0] as Request
    const url = new URL(request.url)
    expect(url.pathname).toBe('/api/v1/observability/alert-lifecycles/metrics')
    expect(Object.fromEntries(url.searchParams)).toEqual({
      window_minutes: '720',
      bucket_minutes: '30',
      source_type: 'api',
      severity: 'warning',
    })
  })

  it('通过生成客户端查询历史并执行批量处置', async () => {
    const lifecycleId = '11111111-1111-4111-8111-111111111111'
    const fetchMock = vi.fn().mockImplementation((request: Request) => {
      const url = new URL(request.url)
      const body = url.pathname.endsWith('batch-disposition') ? { items: [] } : []
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    })
    vi.stubGlobal('fetch', fetchMock)

    await getObservabilityAlertDispositionEvents({
      lifecycle_id: lifecycleId,
      action: 'suppressed',
    })
    await batchDisposeObservabilityAlerts({
      lifecycle_ids: [lifecycleId],
      action: 'clear',
      reason: '维护窗口已结束',
      confirmed: true,
    })

    const historyRequest = fetchMock.mock.calls[0]?.[0] as Request
    const historyUrl = new URL(historyRequest.url)
    expect(historyUrl.pathname).toBe('/api/v1/observability/alert-disposition-events')
    expect(Object.fromEntries(historyUrl.searchParams)).toEqual({
      lifecycle_id: lifecycleId,
      action: 'suppressed',
      limit: '100',
    })
    const batchRequest = fetchMock.mock.calls[1]?.[0] as Request
    expect(new URL(batchRequest.url).pathname).toBe(
      '/api/v1/observability/alert-lifecycles/batch-disposition',
    )
    expect(await batchRequest.json()).toEqual({
      lifecycle_ids: [lifecycleId],
      action: 'clear',
      reason: '维护窗口已结束',
      confirmed: true,
    })
  })
})

describe('统一质量概览客户端', () => {
  it('使用当前 Agent 和显式窗口读取统一质量事实', async () => {
    setSelectedAgentId('33333333-3333-4333-8333-333333333333')
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      generated_at: '2026-09-16T08:30:00Z',
      evaluation_scope: 'current_agent_all_history',
      operations_window_minutes: 2880,
      coverage: 'empty',
      evaluation: {
        total_runs: 0,
        gate_passed_runs: 0,
        latest_pass_rate: null,
        pending_reviews: 0,
        completed_reviews: 0,
        candidate_wins: 0,
        reference_wins: 0,
        ties: 0,
        candidate_average_score: null,
        reference_average_score: null,
      },
      alert_recommendations: {
        window_started_at: '2026-09-14T08:30:00Z',
        window_ended_at: '2026-09-16T08:30:00Z',
        total: 0,
        accepted: 0,
        rejected: 0,
        acceptance_rate_percent: 0,
        accepted_resolved: 0,
        accepted_active: 0,
        replay_total: 0,
        replay_allowed: 0,
        replay_blocked: 0,
        actions: [],
        sources: [],
      },
      automatic_actions_allowed: false,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await getUnifiedQualityOverview(2_880)

    const request = fetchMock.mock.calls[0]?.[0] as Request
    const url = new URL(request.url)
    expect(url.pathname).toBe('/api/v1/evaluations/quality-overview')
    expect(url.searchParams.get('window_minutes')).toBe('2880')
    expect(request.headers.get('X-CNB-Agent-ID')).toBe(
      '33333333-3333-4333-8333-333333333333',
    )
  })
})

describe('审计客户端', () => {
  it('传递资源类型和资源 ID 的精确筛选', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], next_cursor: null }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await getAuditRecords(
      '',
      'observability.alert_calibration_draft_created',
      'configuration_version',
      'draft-123',
    )

    const request = fetchMock.mock.calls[0]?.[0] as Request
    const url = new URL(request.url)
    expect(url.pathname).toBe('/api/v1/administration/audit')
    expect(Object.fromEntries(url.searchParams)).toEqual({
      limit: '100',
      action: 'observability.alert_calibration_draft_created',
      resource_type: 'configuration_version',
      resource_id: 'draft-123',
    })
  })
})

describe('配置包客户端', () => {
  it('使用专用安全导入导出端点', async () => {
    const packageDocument: ConfigPackageDocument = {
      format: 'cnb-runtime-configuration',
      schema_version: '1',
      source: {
        version: 3,
        status: 'published',
        note: '中文配置基线',
        created_at: '2026-09-10T00:00:00Z',
        published_at: '2026-09-10T00:01:00Z',
      },
      values: [{
        key: 'memory.recall.limit',
        scope_type: 'system',
        scope_id: null,
        value: 16,
      }],
    }
    const imported = {
      id: '00000000-0000-0000-0000-000000000001',
      version: 4,
      status: 'draft',
      note: '从配置包 v3 导入：中文配置基线',
      created_at: '2026-09-10T00:02:00Z',
      published_at: null,
      values: packageDocument.values,
    }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(packageDocument), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(imported), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }))
    vi.stubGlobal('fetch', fetchMock)

    expect(await exportConfigPackage('version-id')).toEqual(packageDocument)
    expect(await importConfigPackage(packageDocument)).toEqual(imported)
    const exportRequest = fetchMock.mock.calls[0]?.[0] as Request
    const importRequest = fetchMock.mock.calls[1]?.[0] as Request
    expect(exportRequest.url).toBe('http://localhost:3000/api/v1/configuration/versions/version-id/export')
    expect(exportRequest.method).toBe('GET')
    expect(exportRequest.headers.get('Accept')).toBe('application/json')
    expect(importRequest.url).toBe('http://localhost:3000/api/v1/configuration/imports')
    expect(importRequest.method).toBe('POST')
    expect(importRequest.headers.get('Content-Type')).toBe('application/json')
    expect(await importRequest.json()).toEqual(packageDocument)
  })

  it('为生成客户端注入仅驻留内存的访问令牌并转换安全错误', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      schema_version: '1',
      error: { code: 'forbidden', message: '没有查看配置包的权限', request_id: 'request-id' },
    }), {
      status: 403,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)
    setApiAccessToken('signed-access-token')
    setSelectedAgentId('33333333-3333-4333-8333-333333333333')

    await expect(exportConfigPackage('version-id')).rejects.toThrow('没有查看配置包的权限')

    const request = fetchMock.mock.calls[0]?.[0] as Request
    expect(request.headers.get('Authorization')).toBe('Bearer signed-access-token')
    expect(request.headers.get('X-CNB-Agent-ID')).toBe(
      '33333333-3333-4333-8333-333333333333',
    )
  })

  it('将网络故障转换为自然中文提示', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(exportConfigPackage('version-id')).rejects.toThrow(
      '无法连接 API 服务，请检查网络或服务状态',
    )
  })
})

describe('告警运营历史导出客户端', () => {
  it('传递窗口、内存令牌与当前 Agent，并解析安全下载响应头', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"data":{}}', {
      status: 200,
      headers: {
        'Content-Disposition': 'attachment; filename="alert-history.json"',
        'Content-Type': 'application/json',
        'X-Content-SHA256': 'a'.repeat(64),
        'X-Export-Run-ID': '00000000-0000-4000-8000-000000000001',
      },
    }))
    vi.stubGlobal('fetch', fetchMock)
    setApiAccessToken('signed-access-token')
    setSelectedAgentId('33333333-3333-4333-8333-333333333333')

    const download = await downloadObservabilityAlertHistory(10_080)
    const request = fetchMock.mock.calls[0]?.[0] as Request

    expect(download.filename).toBe('alert-history.json')
    expect(download.sha256).toBe('a'.repeat(64))
    expect(download.runId).toBe('00000000-0000-4000-8000-000000000001')
    expect(request.url).toBe(
      'http://localhost:3000/api/v1/data-lifecycle/observability-alert-history/exports',
    )
    expect(request.method).toBe('POST')
    expect(request.headers.get('Authorization')).toBe('Bearer signed-access-token')
    expect(request.headers.get('X-CNB-Agent-ID')).toBe(
      '33333333-3333-4333-8333-333333333333',
    )
    expect(await request.json()).toEqual({ window_minutes: 10_080 })
  })
})
