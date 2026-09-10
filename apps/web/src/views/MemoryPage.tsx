import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BrainCircuit,
  CheckCircle2,
  GitCompareArrows,
  History,
  Link2,
  Plus,
  RefreshCw,
  Search,
  ShieldAlert,
  Trash2,
  UsersRound,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import {
  closeEpisode,
  correctMemory,
  createEpisode,
  createMemory,
  createRelationshipEvent,
  forgetMemory,
  getAdminSession,
  getDevelopmentIdentity,
  getEpisodes,
  getMemories,
  getMemoryDetail,
  getMemoryIndexJobs,
  getRelationship,
  linkMemoryConflict,
  recallMemories,
  rebuildMemoryIndex,
  setMemoryConfirmation,
  type MemoryConfirmation,
  type MemoryKind,
  type MemorySensitivity,
  type MemoryStatus,
  type MemoryVisibility,
} from '../api'
import { useSelectedAgentId } from '../agentSelection'
import {
  displayLabel,
  episodeStatusLabels,
  memoryConfirmationLabels,
  memoryIndexJobStatusLabels,
  memoryKindLabels,
  memoryLinkKindLabels,
  memorySensitivityLabels,
  memorySourceKindLabels,
  memoryStatusLabels,
  memoryVisibilityLabels,
  relationshipEventTypeLabels,
  relationshipStageLabels,
} from '../displayLabels'
import { invalidateAcrossTabs } from '../tabSync'

type MemoryTab = 'memories' | 'relationship' | 'episodes' | 'index'

function nowIso() {
  return new Date().toISOString()
}

export function MemoryPage() {
  const queryClient = useQueryClient()
  const selectedAgentId = useSelectedAgentId()
  const [tab, setTab] = useState<MemoryTab>('memories')
  const [searchText, setSearchText] = useState('')
  const [kind, setKind] = useState<MemoryKind | ''>('')
  const [memoryStatus, setMemoryStatus] = useState<MemoryStatus | ''>('active')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [content, setContent] = useState('')
  const [createKind, setCreateKind] = useState<MemoryKind>('semantic')
  const [visibility, setVisibility] = useState<MemoryVisibility>('user')
  const [sensitivity, setSensitivity] = useState<MemorySensitivity>('normal')
  const [confirmation, setConfirmation] = useState<MemoryConfirmation>('unconfirmed')
  const [importance, setImportance] = useState(0.5)
  const [recallQuery, setRecallQuery] = useState('')
  const [relationshipSummary, setRelationshipSummary] = useState('')
  const [relationshipEventType, setRelationshipEventType] = useState('interaction')
  const [relationshipDelta, setRelationshipDelta] = useState(0.1)
  const [relationshipBoundaries, setRelationshipBoundaries] = useState('')
  const [episodeTitle, setEpisodeTitle] = useState('')
  const [episodeSummary, setEpisodeSummary] = useState('')
  const [episodeConversationId, setEpisodeConversationId] = useState('')
  const [episodeMessageIds, setEpisodeMessageIds] = useState('')

  const identity = useQuery({ queryKey: ['chat-identity', selectedAgentId], queryFn: getDevelopmentIdentity })
  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const canWrite = session.data?.permissions.includes('memory:write') ?? false
  const canRebuild = session.data?.permissions.includes('memory:rebuild') ?? false
  const memories = useQuery({
    queryKey: ['memories', selectedAgentId, searchText, kind, memoryStatus],
    queryFn: () => getMemories({
      query: searchText,
      kind: kind || undefined,
      status: memoryStatus || undefined,
    }),
  })
  const detail = useQuery({
    queryKey: ['memory-detail', selectedAgentId, selectedId],
    queryFn: () => getMemoryDetail(selectedId!),
    enabled: selectedId !== null,
  })
  const relationship = useQuery({
    queryKey: ['relationship', selectedAgentId, identity.data?.user_id],
    queryFn: () => getRelationship(identity.data!.user_id),
    enabled: Boolean(identity.data?.user_id),
  })
  const episodes = useQuery({
    queryKey: ['episodes', selectedAgentId, identity.data?.user_id],
    queryFn: () => getEpisodes(identity.data!.user_id),
    enabled: Boolean(identity.data?.user_id),
  })
  const indexJobs = useQuery({ queryKey: ['memory-index-jobs', selectedAgentId], queryFn: getMemoryIndexJobs })

  useEffect(() => {
    setSelectedId(null)
    setRecallQuery('')
  }, [selectedAgentId])

  const refreshMemories = () => invalidateAcrossTabs(queryClient, ['memories'])
  const createMutation = useMutation({
    mutationFn: () => createMemory({
      user_id: visibility === 'user' ? identity.data!.user_id : null,
      conversation_id: null,
      episode_id: null,
      kind: createKind,
      visibility,
      content,
      event_at: nowIso(),
      confidence: confirmation === 'confirmed' ? 0.9 : 0.7,
      importance,
      emotional_weight: 0,
      sensitivity,
      confirmation,
      sources: [{
        kind: 'import',
        source_id: `admin:${crypto.randomUUID()}`,
        excerpt: null,
        is_verbatim: false,
        occurred_at: nowIso(),
      }],
    }),
    onSuccess: async (created) => {
      setContent('')
      setShowCreate(false)
      setSelectedId(created.memory.id)
      await refreshMemories()
    },
  })
  const confirmationMutation = useMutation({
    mutationFn: ({ memoryId, value }: { memoryId: string; value: MemoryConfirmation }) =>
      setMemoryConfirmation(memoryId, value),
    onSuccess: async () => {
      await refreshMemories()
      await queryClient.invalidateQueries({
        queryKey: ['memory-detail', selectedAgentId, selectedId],
      })
    },
  })
  const correctionMutation = useMutation({
    mutationFn: ({ memoryId, corrected }: { memoryId: string; corrected: string }) =>
      correctMemory(memoryId, { content: corrected, event_at: nowIso(), note: '管理后台纠正' }),
    onSuccess: async (corrected) => {
      setSelectedId(corrected.memory.id)
      await refreshMemories()
    },
  })
  const conflictMutation = useMutation({
    mutationFn: ({ memoryId, targetId }: { memoryId: string; targetId: string }) =>
      linkMemoryConflict(memoryId, targetId, '管理后台标记冲突'),
    onSuccess: () => queryClient.invalidateQueries({
      queryKey: ['memory-detail', selectedAgentId, selectedId],
    }),
  })
  const forgetMutation = useMutation({
    mutationFn: forgetMemory,
    onSuccess: async () => {
      await refreshMemories()
      await queryClient.invalidateQueries({
        queryKey: ['memory-detail', selectedAgentId, selectedId],
      })
    },
  })
  const recallMutation = useMutation({
    mutationFn: () => recallMemories(identity.data!.user_id, recallQuery),
  })
  const relationshipMutation = useMutation({
    mutationFn: () => createRelationshipEvent({
      user_id: identity.data!.user_id,
      event_type: relationshipEventType,
      affinity_delta: relationshipDelta,
      trust_delta: relationshipDelta,
      familiarity_delta: relationshipDelta,
      summary: relationshipSummary,
      boundaries: relationshipBoundaries.split('\n').map((item) => item.trim()).filter(Boolean),
      evidence_memory_id: selectedId,
    }),
    onSuccess: async () => {
      setRelationshipSummary('')
      await invalidateAcrossTabs(queryClient, ['relationship'])
    },
  })
  const episodeMutation = useMutation({
    mutationFn: () => createEpisode({
      user_id: identity.data!.user_id,
      conversation_id: episodeConversationId.trim(),
      title: episodeTitle,
      summary: episodeSummary,
      started_at: nowIso(),
      ended_at: nowIso(),
      source_message_ids: episodeMessageIds.split(',').map((item) => item.trim()).filter(Boolean),
    }),
    onSuccess: async () => {
      setEpisodeTitle('')
      setEpisodeSummary('')
      setEpisodeMessageIds('')
      await invalidateAcrossTabs(queryClient, ['episodes'])
    },
  })
  const closeEpisodeMutation = useMutation({
    mutationFn: ({ episodeId, consolidate }: { episodeId: string; consolidate: boolean }) =>
      closeEpisode(episodeId, consolidate),
    onSuccess: () => invalidateAcrossTabs(queryClient, ['episodes']),
  })
  const rebuildMutation = useMutation({
    mutationFn: () => rebuildMemoryIndex(),
    onSuccess: async () => {
      await refreshMemories()
      await invalidateAcrossTabs(queryClient, ['memory-index-jobs'])
    },
  })

  const operationError = useMemo(() => [
    createMutation.error,
    confirmationMutation.error,
    correctionMutation.error,
    conflictMutation.error,
    forgetMutation.error,
    recallMutation.error,
    relationshipMutation.error,
    episodeMutation.error,
    closeEpisodeMutation.error,
    rebuildMutation.error,
  ].find(Boolean), [
    createMutation.error,
    confirmationMutation.error,
    correctionMutation.error,
    conflictMutation.error,
    forgetMutation.error,
    recallMutation.error,
    relationshipMutation.error,
    episodeMutation.error,
    closeEpisodeMutation.error,
    rebuildMutation.error,
  ])

  const requestCorrection = () => {
    if (!detail.data?.memory.content) return
    const corrected = window.prompt('输入纠正后的记忆；原版本会保留为已替代', detail.data.memory.content)?.trim()
    if (corrected && corrected !== detail.data.memory.content) {
      correctionMutation.mutate({ memoryId: detail.data.memory.id, corrected })
    }
  }
  const requestConflict = () => {
    if (!detail.data) return
    const targetId = window.prompt('输入与当前记忆冲突的记忆 ID')?.trim()
    if (targetId) conflictMutation.mutate({ memoryId: detail.data.memory.id, targetId })
  }

  return (
    <div className="page memory-page">
      <section className="page-heading compact">
        <div>
          <p className="eyebrow">长期连续性控制平面</p>
          <h1>记忆与关系</h1>
          <p>管理六类长期记忆、可追溯来源、纠正与冲突链、关系阶段和向量重建。</p>
        </div>
        {tab === 'memories' && <button className="primary-button" disabled={!canWrite} onClick={() => setShowCreate((value) => !value)}><Plus size={14} /> 新建记忆</button>}
      </section>
      <div className="notice info"><ShieldAlert size={17} /><div><strong>隔离与可追溯默认开启</strong><span>推断不会标成用户原话；遗忘会清除正文、摘录和向量，仅保留最小审计骨架。</span></div></div>
      {operationError && <div className="notice error">{operationError.message}</div>}
      <div className="config-tabs memory-tabs" aria-label="记忆管理功能">
        <button className={tab === 'memories' ? 'active' : ''} onClick={() => setTab('memories')}>记忆与召回</button>
        <button className={tab === 'relationship' ? 'active' : ''} onClick={() => setTab('relationship')}>关系状态</button>
        <button className={tab === 'episodes' ? 'active' : ''} onClick={() => setTab('episodes')}>情景记录</button>
        <button className={tab === 'index' ? 'active' : ''} onClick={() => setTab('index')}>索引任务</button>
      </div>

      {tab === 'memories' && <>
        {showCreate && <section className="panel memory-editor">
          <div className="panel-heading"><h2>创建可追溯记忆</h2><span className="subtle">管理录入默认标记为非逐字导入来源</span></div>
          <label className="payload-editor">记忆内容<textarea rows={5} value={content} onChange={(event) => setContent(event.target.value)} /></label>
          <div className="memory-form-grid">
            <label>类型<select value={createKind} onChange={(event) => setCreateKind(event.target.value as MemoryKind)}>{Object.entries(memoryKindLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>可见范围<select value={visibility} onChange={(event) => setVisibility(event.target.value as MemoryVisibility)}>{Object.entries(memoryVisibilityLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>敏感级别<select value={sensitivity} onChange={(event) => setSensitivity(event.target.value as MemorySensitivity)}>{Object.entries(memorySensitivityLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>确认状态<select value={confirmation} onChange={(event) => setConfirmation(event.target.value as MemoryConfirmation)}>{Object.entries(memoryConfirmationLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>重要性 <span>{importance.toFixed(2)}</span><input type="range" min="0" max="1" step="0.05" value={importance} onChange={(event) => setImportance(Number(event.target.value))} /></label>
          </div>
          <div className="cognition-editor-actions"><button className="primary-button" disabled={!content.trim() || !identity.data || createMutation.isPending} onClick={() => createMutation.mutate()}><CheckCircle2 size={14} /> 保存记忆</button></div>
        </section>}
        <section className="memory-layout">
          <div className="panel memory-list-panel">
            <div className="memory-toolbar">
              <label className="search-box"><Search size={14} /><input aria-label="搜索记忆" placeholder="搜索记忆正文" value={searchText} onChange={(event) => setSearchText(event.target.value)} /></label>
              <select aria-label="筛选记忆类型" value={kind} onChange={(event) => setKind(event.target.value as MemoryKind | '')}><option value="">全部类型</option>{Object.entries(memoryKindLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
              <select aria-label="筛选记忆状态" value={memoryStatus} onChange={(event) => setMemoryStatus(event.target.value as MemoryStatus | '')}><option value="">全部状态</option>{Object.entries(memoryStatusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
            </div>
            <div className="memory-records">
              {memories.data?.items.map((item) => <button key={item.id} className={selectedId === item.id ? 'selected' : ''} onClick={() => setSelectedId(item.id)}>
                <span><strong>{memoryKindLabels[item.kind]}</strong><small>{memoryStatusLabels[item.status]} · {memoryConfirmationLabels[item.confirmation]} · v{item.version}</small></span>
                <p>{item.content ?? '正文已按遗忘请求清除'}</p>
                <code>{new Date(item.event_at).toLocaleString()}</code>
              </button>)}
              {!memories.isLoading && !memories.data?.items.length && <div className="empty-state">没有符合条件的记忆。</div>}
            </div>
          </div>
          <aside className="panel memory-detail-panel" aria-label="记忆详情">
            {!detail.data && <div className="empty-state">选择一条记忆查看来源和关系链。</div>}
            {detail.data && <>
              <div className="panel-heading"><h2>{memoryKindLabels[detail.data.memory.kind]}记忆 · v{detail.data.memory.version}</h2><span className={`entity-status ${detail.data.memory.status}`}>{memoryStatusLabels[detail.data.memory.status]}</span></div>
              <p className="memory-content">{detail.data.memory.content ?? '正文已清除'}</p>
              <dl className="memory-metrics"><div><dt>置信度</dt><dd>{detail.data.memory.confidence.toFixed(2)}</dd></div><div><dt>重要性</dt><dd>{detail.data.memory.importance.toFixed(2)}</dd></div><div><dt>可见范围</dt><dd>{memoryVisibilityLabels[detail.data.memory.visibility]}</dd></div><div><dt>敏感级别</dt><dd>{memorySensitivityLabels[detail.data.memory.sensitivity]}</dd></div></dl>
              {detail.data.memory.status === 'active' && <div className="table-actions memory-actions">
                <button disabled={!canWrite} onClick={() => confirmationMutation.mutate({ memoryId: detail.data.memory.id, value: 'confirmed' })}><CheckCircle2 size={12} /> 确认</button>
                <button disabled={!canWrite} onClick={() => confirmationMutation.mutate({ memoryId: detail.data.memory.id, value: 'disputed' })}><ShieldAlert size={12} /> 争议</button>
                <button disabled={!canWrite} onClick={requestCorrection}><GitCompareArrows size={12} /> 纠正</button>
                <button disabled={!canWrite} onClick={requestConflict}><Link2 size={12} /> 冲突</button>
                <button disabled={!canWrite} onClick={() => { if (window.confirm('遗忘后正文、来源摘录和向量不可恢复，确定继续吗？')) forgetMutation.mutate(detail.data.memory.id) }}><Trash2 size={12} /> 遗忘</button>
              </div>}
              <h3>来源</h3>
              <div className="memory-evidence">{detail.data.sources.map((source) => <article key={source.id}><strong>{memorySourceKindLabels[source.kind]}</strong><span>{source.is_verbatim ? '逐字证据' : '非逐字摘要'}</span><p>{source.excerpt ?? '无可展示摘录'}</p><code>{source.source_id}</code></article>)}</div>
              <h3>关系链</h3>
              <div className="memory-evidence">{detail.data.links.map((link) => <article key={link.id}><strong>{memoryLinkKindLabels[link.kind]}</strong><p>{link.note ?? '无备注'}</p><code>{link.source_memory_id} → {link.target_memory_id}</code></article>)}{!detail.data.links.length && <span className="subtle">暂无冲突或替代关系。</span>}</div>
            </>}
          </aside>
        </section>
        <section className="panel recall-panel">
          <div className="panel-heading"><h2>混合召回试验</h2><span className="subtle">使用当前发布配置，不写入记忆</span></div>
          <div className="trace-search"><label><BrainCircuit size={15} /><input aria-label="召回查询" placeholder="输入自然语言查询" value={recallQuery} onChange={(event) => setRecallQuery(event.target.value)} /></label><button className="secondary-button" disabled={!recallQuery.trim() || !identity.data || recallMutation.isPending} onClick={() => recallMutation.mutate()}>执行召回</button></div>
          <div className="recall-results">{recallMutation.data?.items.map((item) => <article key={item.memory.id}><strong>{item.score.toFixed(3)} · {memoryKindLabels[item.memory.kind]}</strong><p>{item.memory.content}</p><code>{JSON.stringify(item.components)}</code></article>)}</div>
        </section>
      </>}

      {tab === 'relationship' && <section className="relationship-grid">
        <div className="panel relationship-summary">
          <div className="panel-heading"><h2>当前关系</h2><UsersRound size={18} /></div>
          {relationship.data ? <><strong>{relationshipStageLabels[relationship.data.relationship.stage]}</strong><p>{relationship.data.relationship.summary}</p><dl className="memory-metrics"><div><dt>亲和度</dt><dd>{relationship.data.relationship.affinity.toFixed(2)}</dd></div><div><dt>信任度</dt><dd>{relationship.data.relationship.trust.toFixed(2)}</dd></div><div><dt>熟悉度</dt><dd>{relationship.data.relationship.familiarity.toFixed(2)}</dd></div><div><dt>交互次数</dt><dd>{relationship.data.relationship.interaction_count}</dd></div></dl><h3>边界</h3><ul>{relationship.data.relationship.boundaries.map((item) => <li key={item}>{item}</li>)}</ul></> : <div className="empty-state">尚未形成关系记录。</div>}
        </div>
        <div className="panel relationship-editor">
          <div className="panel-heading"><h2>追加关系事件</h2><span className="subtle">事件只保存安全摘要</span></div>
          <div className="memory-form-grid"><label>事件类型<input value={relationshipEventType} onChange={(event) => setRelationshipEventType(event.target.value)} /></label><label>三项变化 <span>{relationshipDelta.toFixed(2)}</span><input type="range" min="-1" max="1" step="0.05" value={relationshipDelta} onChange={(event) => setRelationshipDelta(Number(event.target.value))} /></label></div>
          <label className="payload-editor">事件摘要<textarea rows={4} value={relationshipSummary} onChange={(event) => setRelationshipSummary(event.target.value)} /></label>
          <label className="payload-editor">关系边界（每行一项）<textarea rows={4} value={relationshipBoundaries} onChange={(event) => setRelationshipBoundaries(event.target.value)} /></label>
          <div className="cognition-editor-actions"><button className="primary-button" disabled={!canWrite || !identity.data || !relationshipSummary.trim()} onClick={() => relationshipMutation.mutate()}><Plus size={14} /> 追加事件</button></div>
          <div className="memory-evidence">{relationship.data?.events.map((event) => <article key={event.id}><strong>{displayLabel(relationshipEventTypeLabels, event.event_type)}</strong><span>{new Date(event.created_at).toLocaleString()}</span><p>{event.summary}</p></article>)}</div>
        </div>
      </section>}

      {tab === 'episodes' && <section className="relationship-grid">
        <div className="panel relationship-editor">
          <div className="panel-heading"><h2>创建情景记录</h2><History size={18} /></div>
          <div className="memory-form-grid"><label>标题<input value={episodeTitle} onChange={(event) => setEpisodeTitle(event.target.value)} /></label><label>会话 ID<input value={episodeConversationId} onChange={(event) => setEpisodeConversationId(event.target.value)} /></label></div>
          <label className="payload-editor">摘要<textarea rows={4} value={episodeSummary} onChange={(event) => setEpisodeSummary(event.target.value)} /></label>
          <label className="payload-editor">来源消息 ID（英文逗号分隔）<textarea rows={3} value={episodeMessageIds} onChange={(event) => setEpisodeMessageIds(event.target.value)} /></label>
          <div className="cognition-editor-actions"><button className="primary-button" disabled={!canWrite || !identity.data || !episodeTitle.trim() || !episodeSummary.trim() || !episodeConversationId.trim() || !episodeMessageIds.trim()} onClick={() => episodeMutation.mutate()}><Plus size={14} /> 创建情景记录</button></div>
        </div>
        <div className="panel">
          <div className="panel-heading"><h2>情景记录列表</h2><span className="subtle">{episodes.data?.items.length ?? 0} 项</span></div>
          <div className="memory-evidence">{episodes.data?.items.map((episode) => <article key={episode.id}><strong>{episode.title}</strong><span>{episodeStatusLabels[episode.status]}</span><p>{episode.summary}</p><div className="table-actions"><button disabled={!canWrite || episode.status !== 'open'} onClick={() => closeEpisodeMutation.mutate({ episodeId: episode.id, consolidate: false })}>关闭</button><button disabled={!canWrite || episode.status === 'consolidated'} onClick={() => closeEpisodeMutation.mutate({ episodeId: episode.id, consolidate: true })}>标记已巩固</button></div></article>)}</div>
        </div>
      </section>}

      {tab === 'index' && <section className="panel">
        <div className="panel-heading"><h2>向量索引重建任务</h2><button className="secondary-button" disabled={!canRebuild || rebuildMutation.isPending} onClick={() => { if (window.confirm('确定按当前编码版本重建全部生效记忆吗？')) rebuildMutation.mutate() }}><RefreshCw size={14} /> 重建全部</button></div>
        <div className="memory-evidence">{indexJobs.data?.items.map((job) => <article key={job.id}><strong>{job.target_embedding_version}</strong><span>{memoryIndexJobStatusLabels[job.status]}</span><p>{job.processed_items} / {job.total_items}</p><div className="progress-track"><span style={{ width: `${job.total_items ? job.processed_items / job.total_items * 100 : 100}%` }} /></div>{job.error_code && <code>{job.error_code}</code>}</article>)}{!indexJobs.data?.items.length && <div className="empty-state">尚无索引重建任务。</div>}</div>
      </section>}
    </div>
  )
}
