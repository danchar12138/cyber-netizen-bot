import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  ArchiveRestore,
  DatabaseBackup,
  Download,
  Eraser,
  HardDrive,
  ShieldAlert,
  Trash2,
} from 'lucide-react'
import { useState, type FormEvent } from 'react'

import {
  downloadUserDataExport,
  forgetUserData,
  getAdminSession,
  getDataLifecycleOverview,
  recordBackupRestoreDrill,
  runOrphanCleanup,
  runRetentionCleanup,
  type AdminPermission,
  type LifecycleRun,
  type LifecycleRunKind,
} from '../api'
import { displayLabel, metadataKeyLabels } from '../displayLabels'

const USER_FORGET_CONFIRMATION_PREFIX = '确认永久遗忘 '
const BACKUP_RESTORE_CONFIRMATION = '确认备份恢复演练已验证'

const runKindLabels: Record<LifecycleRunKind, string> = {
  user_export: '用户数据导出',
  user_forget: '用户数据遗忘',
  retention_cleanup: '保留期清理',
  orphan_cleanup: 'MinIO 孤儿清理',
  backup_restore_drill: '备份恢复演练',
}

const runStatusLabels: Record<LifecycleRun['status'], string> = {
  running: '执行中',
  succeeded: '已成功',
  failed: '已失败',
}

type Notice = { tone: 'info' | 'error'; message: string } | null

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '操作失败，请稍后重试。'
}

function formatTime(value: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '尚未完成'
}

function formatEvidence(value: unknown) {
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') return value.toLocaleString('zh-CN')
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

function RunHistory({ runs }: { runs: LifecycleRun[] }) {
  if (runs.length === 0) {
    return <p className="empty-state">尚无数据生命周期运行记录。</p>
  }

  return (
    <div className="lifecycle-run-list">
      {runs.map((run) => (
        <article className="lifecycle-run" key={run.id}>
          <div className="lifecycle-run-heading">
            <div>
              <strong>{runKindLabels[run.kind]}</strong>
              <span>{formatTime(run.completed_at ?? run.started_at)}</span>
            </div>
            <span className={`entity-status task-${run.status}`}>
              {runStatusLabels[run.status]}
            </span>
          </div>
          {run.subject_user_id && <p>目标用户：<code>{run.subject_user_id}</code></p>}
          <dl className="lifecycle-run-values">
            {Object.entries(run.counters).map(([key, value]) => (
              <div key={key}><dt>{displayLabel(metadataKeyLabels, key)}</dt><dd>{value.toLocaleString('zh-CN')}</dd></div>
            ))}
            {Object.entries(run.evidence).map(([key, value]) => (
              <div key={key}><dt>{displayLabel(metadataKeyLabels, key)}</dt><dd>{formatEvidence(value)}</dd></div>
            ))}
          </dl>
          {run.error_code && <small>固定错误码：<code>{run.error_code}</code></small>}
        </article>
      ))}
    </div>
  )
}

export function DataLifecyclePage() {
  const queryClient = useQueryClient()
  const overview = useQuery({
    queryKey: ['data-lifecycle-overview'],
    queryFn: getDataLifecycleOverview,
  })
  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const [userId, setUserId] = useState('')
  const [forgetConfirmation, setForgetConfirmation] = useState('')
  const [retentionConfirmed, setRetentionConfirmed] = useState(false)
  const [orphanConfirmed, setOrphanConfirmed] = useState(false)
  const [notice, setNotice] = useState<Notice>(null)
  const [manifestSha256, setManifestSha256] = useState('')
  const [databaseRows, setDatabaseRows] = useState('0')
  const [objectsVerified, setObjectsVerified] = useState('0')
  const [databaseVerified, setDatabaseVerified] = useState(false)
  const [objectVerified, setObjectVerified] = useState(false)
  const [smokeVerified, setSmokeVerified] = useState(false)
  const [drillConfirmation, setDrillConfirmation] = useState('')

  const can = (permission: AdminPermission) =>
    session.data?.permissions.includes(permission) ?? false
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['data-lifecycle-overview'] })
  }
  const failed = (error: unknown) => setNotice({ tone: 'error', message: errorMessage(error) })

  const exportMutation = useMutation({
    mutationFn: () => downloadUserDataExport(userId.trim()),
    onSuccess: async (download) => {
      const url = URL.createObjectURL(download.blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = download.filename
      anchor.click()
      URL.revokeObjectURL(url)
      setNotice({
        tone: 'info',
        message: `导出已生成并开始下载${download.sha256 ? `，SHA-256：${download.sha256}` : ''}。`,
      })
      await refresh()
    },
    onError: failed,
  })
  const forgetMutation = useMutation({
    mutationFn: () => forgetUserData(userId.trim(), forgetConfirmation),
    onSuccess: async () => {
      setNotice({ tone: 'info', message: '用户正文、身份绑定、记忆、关系与私有对象已按策略处理。' })
      setForgetConfirmation('')
      await refresh()
    },
    onError: failed,
  })
  const retentionMutation = useMutation({
    mutationFn: runRetentionCleanup,
    onSuccess: async () => {
      setNotice({ tone: 'info', message: '保留期清理已完成，结果已登记到安全运行证据。' })
      setRetentionConfirmed(false)
      await refresh()
    },
    onError: failed,
  })
  const orphanMutation = useMutation({
    mutationFn: runOrphanCleanup,
    onSuccess: async () => {
      setNotice({ tone: 'info', message: 'MinIO 孤儿扫描已完成，新对象和已引用对象均受保护。' })
      setOrphanConfirmed(false)
      await refresh()
    },
    onError: failed,
  })
  const drillMutation = useMutation({
    mutationFn: () => recordBackupRestoreDrill({
      manifest_sha256: manifestSha256,
      database_rows_verified: Number(databaseRows),
      objects_verified: Number(objectsVerified),
      database_integrity_verified: databaseVerified,
      object_integrity_verified: objectVerified,
      application_smoke_verified: smokeVerified,
      confirmation: drillConfirmation,
    }),
    onSuccess: async () => {
      setNotice({ tone: 'info', message: '隔离恢复演练证据已登记。' })
      setDrillConfirmation('')
      await refresh()
    },
    onError: failed,
  })

  const submitDrill = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    drillMutation.mutate()
  }
  const latestDrill = overview.data?.runs.find((run) => run.kind === 'backup_restore_drill')
  const drillExpired = !latestDrill?.completed_at || (
    Date.now() - new Date(latestDrill.completed_at).getTime()
      > (overview.data?.policy.backup_expected_interval_hours ?? 0) * 60 * 60 * 1000
  )
  const validUserId = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(userId.trim())

  return (
    <div className="page">
      <section className="page-heading compact">
        <div>
          <p className="eyebrow">隐私、保留期与灾难恢复</p>
          <h1>数据生命周期</h1>
          <p>统一执行白名单导出、可验证遗忘、保留期和 MinIO 清理，并登记隔离恢复演练证据。</p>
        </div>
        <Link to="/configuration" className="secondary-button">管理生命周期配置</Link>
      </section>

      {overview.isError && <div className="notice error" role="alert">无法读取数据生命周期状态，请检查 API 连接和权限。</div>}
      {notice && <div className={`notice ${notice.tone}`} role="status" aria-live="polite">{notice.message}</div>}
      {drillExpired && overview.data && (
        <div className="notice warning" role="alert">
          <ShieldAlert size={17} /> 尚无有效期内的隔离恢复演练，请按运维手册实际恢复后登记证据。
        </div>
      )}

      <section className="lifecycle-policy-grid" aria-label="生效策略">
        <article className="metric-card"><span>已删除会话保留</span><strong>{overview.data?.policy.deleted_conversation_days ?? '—'} 天</strong><small>超过截止时间后才物理清理</small></article>
        <article className="metric-card"><span>孤儿对象宽限</span><strong>{overview.data?.policy.orphan_grace_hours ?? '—'} 小时</strong><small>保护在途上传与新对象</small></article>
        <article className="metric-card"><span>单批上限</span><strong>{overview.data?.policy.batch_size ?? '—'} 项</strong><small>限制同步管理请求负载</small></article>
        <article className="metric-card"><span>导出大小上限</span><strong>{overview.data ? Math.round(overview.data.policy.export_max_bytes / 1024 / 1024) : '—'} MiB</strong><small>服务端不保存导出文件</small></article>
      </section>

      <section className="lifecycle-grid">
        <article className="panel lifecycle-card">
          <div className="panel-heading"><div><p className="eyebrow">用户权利</p><h2>数据导出与遗忘</h2></div><Eraser size={20} /></div>
          <p className="lifecycle-help">导出仅包含显式白名单字段，不含密钥、令牌、对象键、Prompt 或隐藏推理。遗忘是不可逆操作。</p>
          <label className="lifecycle-field">
            目标用户 UUID
            <input value={userId} onChange={(event) => setUserId(event.target.value)} placeholder="00000000-0000-4000-8000-000000000000" />
          </label>
          <div className="lifecycle-actions">
            <button className="secondary-button" type="button" disabled={!validUserId || !can('data_lifecycle:export') || exportMutation.isPending} onClick={() => exportMutation.mutate()}>
              <Download size={15} /> {exportMutation.isPending ? '正在生成' : '下载 JSON 导出'}
            </button>
          </div>
          <div className="lifecycle-danger-zone">
            <label className="lifecycle-field">
              输入 <code>{validUserId ? `${USER_FORGET_CONFIRMATION_PREFIX}${userId.trim()}` : `${USER_FORGET_CONFIRMATION_PREFIX}<目标用户 UUID>`}</code>
              <input value={forgetConfirmation} onChange={(event) => setForgetConfirmation(event.target.value)} autoComplete="off" />
            </label>
            <button className="danger-button" type="button" disabled={!validUserId || forgetConfirmation !== `${USER_FORGET_CONFIRMATION_PREFIX}${userId.trim()}` || !can('data_lifecycle:forget') || forgetMutation.isPending} onClick={() => forgetMutation.mutate()}>
              <Trash2 size={15} /> {forgetMutation.isPending ? '正在遗忘' : '永久遗忘用户数据'}
            </button>
          </div>
        </article>

        <article className="panel lifecycle-card">
          <div className="panel-heading"><div><p className="eyebrow">受控清理</p><h2>保留期与 MinIO</h2></div><HardDrive size={20} /></div>
          <p className="lifecycle-help">每次只处理当前租户和配置批量上限。对象删除失败时不会删除对应会话数据库记录。</p>
          <div className="lifecycle-cleanup-action">
            <div><strong>保留期清理</strong><span>清理到期软删除会话、对象和过期附件元数据。</span></div>
            <label><input type="checkbox" checked={retentionConfirmed} onChange={(event) => setRetentionConfirmed(event.target.checked)} /> 我确认执行</label>
            <button className="danger-button" type="button" disabled={!retentionConfirmed || !can('data_lifecycle:retention_manage') || retentionMutation.isPending} onClick={() => retentionMutation.mutate()}>
              <ArchiveRestore size={15} /> 执行保留期清理
            </button>
          </div>
          <div className="lifecycle-cleanup-action">
            <div><strong>MinIO 孤儿清理</strong><span>只清理超过宽限期且数据库没有引用的租户对象。</span></div>
            <label><input type="checkbox" checked={orphanConfirmed} onChange={(event) => setOrphanConfirmed(event.target.checked)} /> 我确认执行</label>
            <button className="danger-button" type="button" disabled={!orphanConfirmed || !can('data_lifecycle:retention_manage') || orphanMutation.isPending} onClick={() => orphanMutation.mutate()}>
              <Trash2 size={15} /> 扫描并清理孤儿
            </button>
          </div>
        </article>
      </section>

      <section className="panel lifecycle-drill-panel">
        <div className="panel-heading"><div><p className="eyebrow">灾难恢复</p><h2>登记备份恢复演练</h2></div><DatabaseBackup size={20} /></div>
        <p className="lifecycle-help">此表单不会执行恢复。必须先在隔离环境按运维手册完成 PostgreSQL、MinIO 和应用冒烟验证，再登记清单摘要。</p>
        <form className="lifecycle-drill-form" onSubmit={submitDrill}>
          <label className="lifecycle-field lifecycle-manifest">备份清单 SHA-256<input required minLength={64} maxLength={64} pattern="[0-9a-fA-F]{64}" value={manifestSha256} onChange={(event) => setManifestSha256(event.target.value)} /></label>
          <label className="lifecycle-field">数据库校验行数<input required type="number" min="0" value={databaseRows} onChange={(event) => setDatabaseRows(event.target.value)} /></label>
          <label className="lifecycle-field">对象校验数量<input required type="number" min="0" value={objectsVerified} onChange={(event) => setObjectsVerified(event.target.value)} /></label>
          <fieldset className="lifecycle-verifications">
            <legend>实际验证项</legend>
            <label><input type="checkbox" checked={databaseVerified} onChange={(event) => setDatabaseVerified(event.target.checked)} /> PostgreSQL 完整性</label>
            <label><input type="checkbox" checked={objectVerified} onChange={(event) => setObjectVerified(event.target.checked)} /> MinIO 对象完整性</label>
            <label><input type="checkbox" checked={smokeVerified} onChange={(event) => setSmokeVerified(event.target.checked)} /> 应用冒烟</label>
          </fieldset>
          <label className="lifecycle-field lifecycle-confirmation">输入 <code>{BACKUP_RESTORE_CONFIRMATION}</code><input required value={drillConfirmation} onChange={(event) => setDrillConfirmation(event.target.value)} autoComplete="off" /></label>
          <button className="primary-button" type="submit" disabled={!can('data_lifecycle:backup_drill_record') || drillMutation.isPending || !databaseVerified || !objectVerified || !smokeVerified || drillConfirmation !== BACKUP_RESTORE_CONFIRMATION}>
            <DatabaseBackup size={15} /> {drillMutation.isPending ? '正在登记' : '登记演练证据'}
          </button>
        </form>
      </section>

      <section className="panel lifecycle-history-panel">
        <div className="panel-heading"><div><p className="eyebrow">安全证据</p><h2>最近运行记录</h2></div><span>{overview.data?.runs.length ?? 0} 条</span></div>
        <p className="lifecycle-help">证据只保留计数、摘要、固定错误码和时间，不保存被遗忘正文、对象路径或底层异常。</p>
        <RunHistory runs={overview.data?.runs ?? []} />
      </section>
    </div>
  )
}
