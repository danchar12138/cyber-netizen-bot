import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CircleDashed,
  Download,
  Eye,
  FileUp,
  History,
  KeyRound,
  RotateCcw,
  Save,
  Search,
  ShieldCheck,
  Trash2,
  UploadCloud,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import {
  type ConfigDefinition,
  type ConfigScope,
  type ConfigValue,
  type ConfigVersion,
  clearSecret,
  createConfigDraft,
  exportConfigPackage,
  formatConfigValue,
  formatConfigVersionStatus,
  getAdminSession,
  getConfigDiff,
  getConfigRegistry,
  getConfigVersions,
  getDevelopmentIdentity,
  getEffectiveConfiguration,
  getSecrets,
  importConfigPackage,
  publishConfigVersion,
  rollbackConfigVersion,
  rotateSecret,
  setSecret,
  testSecret,
} from '../api'
import { invalidateAcrossTabs } from '../tabSync'

const scopeLabels: Record<ConfigScope, string> = {
  system: '系统',
  tenant: '租户',
  agent: 'Agent',
  channel: '渠道',
  user: '用户',
}

const diffLabels = { added: '新增', changed: '修改', removed: '移除' } as const

function scopeIdFor(
  scope: ConfigScope,
  identity: Awaited<ReturnType<typeof getDevelopmentIdentity>> | undefined,
  customScopeId: string,
) {
  if (scope === 'system') return null
  if (scope === 'tenant') return identity?.tenant_id ?? null
  if (scope === 'agent') return identity?.agent_id ?? null
  if (scope === 'user') return identity?.user_id ?? null
  return customScopeId.trim() || null
}

function valuesForScope(
  definitions: ConfigDefinition[],
  published: ConfigVersion | undefined,
  scope: ConfigScope,
  scopeId: string | null,
): Record<string, ConfigValue> {
  const values = Object.fromEntries(definitions.map((item) => [item.key, item.default]))
  for (const item of published?.values ?? []) {
    if (item.scope_type === scope && item.scope_id === scopeId) values[item.key] = item.value
  }
  return values
}

function ConfigInput({
  definition,
  value,
  onChange,
  disabled = false,
}: {
  definition: ConfigDefinition
  value: ConfigValue
  onChange: (value: ConfigValue) => void
  disabled?: boolean
}) {
  if (definition.value_kind === 'boolean') {
    const enabled = value === true
    return (
      <button
        type="button"
        className={`toggle ${enabled ? 'enabled' : ''}`}
        onClick={() => onChange(!enabled)}
        aria-pressed={enabled}
        disabled={disabled}
      >
        <span /> {enabled ? '已开启' : '已关闭'}
      </button>
    )
  }

  if (definition.options.length > 0) {
    return (
      <select
        className="config-input"
        value={typeof value === 'string' ? value : ''}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      >
        {definition.options.map((option) => <option key={option}>{option}</option>)}
      </select>
    )
  }

  if (definition.value_kind === 'integer' || definition.value_kind === 'number') {
    return (
      <input
        className="config-input"
        type="number"
        min={definition.minimum ?? undefined}
        max={definition.maximum ?? undefined}
        step={definition.value_kind === 'integer' ? 1 : 'any'}
        value={typeof value === 'number' ? value : ''}
        onChange={(event) => {
          const parsed = event.target.valueAsNumber
          onChange(Number.isNaN(parsed) ? definition.default : parsed)
        }}
        disabled={disabled}
      />
    )
  }

  if (definition.value_kind === 'string_list') {
    return (
      <input
        className="config-input"
        value={Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string').join(', ') : ''}
        onChange={(event) =>
          onChange(event.target.value.split(',').map((item) => item.trim()).filter(Boolean))
        }
        placeholder="使用逗号分隔"
        disabled={disabled}
      />
    )
  }

  return (
    <input
      className="config-input"
      value={typeof value === 'string' ? value : ''}
      onChange={(event) => onChange(event.target.value)}
      disabled={disabled}
    />
  )
}

export function ConfigurationPage() {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<'runtime' | 'secrets'>('runtime')
  const [scope, setScope] = useState<ConfigScope>('system')
  const [customScopeId, setCustomScopeId] = useState('')
  const [search, setSearch] = useState('')
  const [note, setNote] = useState('')
  const [values, setValues] = useState<Record<string, ConfigValue>>({})
  const [currentDraft, setCurrentDraft] = useState<ConfigVersion | null>(null)
  const [secretValues, setSecretValues] = useState<Record<string, string>>({})
  const [transferMessage, setTransferMessage] = useState<string | null>(null)
  const importInput = useRef<HTMLInputElement>(null)
  const registry = useQuery({ queryKey: ['config-registry'], queryFn: getConfigRegistry })
  const history = useQuery({ queryKey: ['config-versions'], queryFn: getConfigVersions })
  const identity = useQuery({ queryKey: ['development-identity'], queryFn: getDevelopmentIdentity })
  const session = useQuery({ queryKey: ['admin-session'], queryFn: getAdminSession })
  const canWrite = session.data?.permissions.includes('configuration:write') ?? false
  const canManageSecrets = session.data?.permissions.includes('secret:manage') ?? false
  const secrets = useQuery({
    queryKey: ['config-secrets'],
    queryFn: getSecrets,
    enabled: canManageSecrets,
  })
  const published = history.data?.versions.find((item) => item.status === 'published')
  const scopeId = scopeIdFor(scope, identity.data, customScopeId)
  const effective = useQuery({
    queryKey: ['effective-configuration', identity.data, customScopeId],
    queryFn: () => getEffectiveConfiguration({
      tenantId: identity.data!.tenant_id,
      agentId: identity.data!.agent_id,
      channelId: customScopeId.trim() || undefined,
      userId: identity.data!.user_id,
    }),
    enabled: Boolean(identity.data),
  })
  const diff = useQuery({
    queryKey: ['config-diff', currentDraft?.id],
    queryFn: () => getConfigDiff(currentDraft!.id),
    enabled: Boolean(currentDraft),
  })

  useEffect(() => {
    if (!registry.data) return
    setValues(valuesForScope(registry.data.definitions, published, scope, scopeId))
    setCurrentDraft(null)
  }, [registry.data, published, scope, scopeId])

  const refreshHistory = async () => {
    await Promise.all([
      invalidateAcrossTabs(queryClient, ['config-versions']),
      invalidateAcrossTabs(queryClient, ['effective-configuration']),
    ])
  }

  const createDraft = useMutation({
    mutationFn: () => {
      if (scope !== 'system' && !scopeId) throw new Error(`${scopeLabels[scope]}作用域需要有效 UUID`)
      const untouched = (published?.values ?? []).filter(
        (item) => !(item.scope_type === scope && item.scope_id === scopeId),
      )
      const edited = (registry.data?.definitions ?? [])
        .filter((item) => !item.secret && item.scopes.includes(scope))
        .map((item) => ({
          key: item.key,
          scope_type: scope,
          scope_id: scopeId,
          value: values[item.key] ?? item.default,
        }))
      return createConfigDraft({ note: note.trim() || null, values: [...untouched, ...edited] })
    },
    onSuccess: async (draft) => {
      setCurrentDraft(draft)
      await refreshHistory()
    },
  })

  const publishDraft = useMutation({
    mutationFn: (versionId: string) => publishConfigVersion(versionId),
    onSuccess: async () => {
      setCurrentDraft(null)
      setNote('')
      await refreshHistory()
    },
  })

  const rollbackVersion = useMutation({
    mutationFn: (versionId: string) => rollbackConfigVersion(versionId),
    onSuccess: refreshHistory,
  })

  const exportPackage = useMutation({
    mutationFn: exportConfigPackage,
    onSuccess: (packageDocument) => {
      const blob = new Blob([JSON.stringify(packageDocument, null, 2)], {
        type: 'application/json;charset=utf-8',
      })
      const downloadUrl = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = downloadUrl
      link.download = `cnb-configuration-v${packageDocument.source.version}.json`
      link.click()
      URL.revokeObjectURL(downloadUrl)
      setTransferMessage(`配置 v${packageDocument.source.version} 已安全导出，文件不包含密钥。`)
    },
  })

  const importPackage = useMutation({
    mutationFn: async (file: File) => {
      if (file.size > 2 * 1024 * 1024) throw new Error('配置包不能超过 2 MiB')
      let packageDocument: unknown
      try {
        packageDocument = JSON.parse(await file.text())
      } catch {
        throw new Error('配置包不是有效的 JSON 文件')
      }
      return importConfigPackage(packageDocument as Parameters<typeof importConfigPackage>[0])
    },
    onSuccess: async (draft) => {
      setCurrentDraft(draft)
      setTransferMessage(`配置包已导入为草稿 v${draft.version}，请检查差异后再发布。`)
      await refreshHistory()
    },
  })

  const writeSecret = useMutation({
    mutationFn: async (definition: ConfigDefinition) => {
      if (scope !== 'system' && !scopeId) throw new Error(`${scopeLabels[scope]}作用域需要有效 UUID`)
      const plaintext = secretValues[definition.key] ?? ''
      const existing = secrets.data?.secrets.find(
        (item) => item.key === definition.key && item.scope_type === scope && item.scope_id === scopeId,
      )
      return existing
        ? rotateSecret(existing.id, plaintext)
        : setSecret({ key: definition.key, scope_type: scope, scope_id: scopeId, plaintext })
    },
    onSuccess: async (_, definition) => {
      setSecretValues((current) => ({ ...current, [definition.key]: '' }))
      await invalidateAcrossTabs(queryClient, ['config-secrets'])
    },
  })

  const verifySecret = useMutation({
    mutationFn: testSecret,
    onSuccess: async () => invalidateAcrossTabs(queryClient, ['config-secrets']),
  })
  const removeSecret = useMutation({
    mutationFn: clearSecret,
    onSuccess: async () => invalidateAcrossTabs(queryClient, ['config-secrets']),
  })

  const definitions = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    return (registry.data?.definitions ?? []).filter((item) => {
      const matchesTab = tab === 'secrets' ? item.secret : !item.secret
      const matchesScope = item.scopes.includes(scope)
      const matchesSearch = !query || [item.key, item.label, item.description, item.section].some(
        (candidate) => candidate.toLocaleLowerCase().includes(query),
      )
      return matchesTab && matchesScope && matchesSearch
    })
  }, [registry.data, scope, search, tab])

  const effectiveByKey = Object.fromEntries(
    (effective.data?.values ?? []).map((item) => [item.key, item]),
  )
  const operationError = createDraft.error ?? publishDraft.error ?? rollbackVersion.error
    ?? exportPackage.error ?? importPackage.error ?? writeSecret.error ?? verifySecret.error
    ?? removeSecret.error

  return (
    <div className="page">
      <section className="page-heading compact">
        <div>
          <p className="eyebrow">配置注册表与安全凭证</p>
          <h1>配置中心</h1>
          <p>统一管理全作用域运行参数、发布差异、最终生效来源和加密密钥。</p>
        </div>
        {tab === 'runtime' && (
          <div className="heading-actions">
            <input
              ref={importInput}
              type="file"
              accept="application/json,.json"
              hidden
              onChange={(event) => {
                const file = event.currentTarget.files?.[0]
                event.currentTarget.value = ''
                if (file) importPackage.mutate(file)
              }}
            />
            <button className="secondary-button" disabled={!canWrite || importPackage.isPending} onClick={() => importInput.current?.click()}>
              <FileUp size={15} /> 导入配置包
            </button>
            <button className="secondary-button" disabled={!canWrite || createDraft.isPending} onClick={() => createDraft.mutate()}>
              <Save size={15} /> 保存新草稿
            </button>
            <button
              className="primary-button"
              disabled={!canWrite || !currentDraft || publishDraft.isPending}
              onClick={() => currentDraft && publishDraft.mutate(currentDraft.id)}
            >
              <UploadCloud size={15} /> 校验并发布
            </button>
          </div>
        )}
      </section>

      <div className="notice info">
        <ShieldCheck size={17} />
        <div>
          <strong>普通配置版本与密钥材料已完全分离</strong>
          <span>密钥只显示掩码；差异、日志、配置快照和浏览器响应均不包含明文。</span>
        </div>
      </div>
      {currentDraft && (
        <div className="notice warning">
          草稿 v{currentDraft.version} 已保存，相对 v{diff.data?.base_version ?? '—'} 有 {diff.data?.changes.length ?? '…'} 项变更。
        </div>
      )}
      {operationError && <div className="notice error">{operationError.message}</div>}
      {transferMessage && <div className="notice info">{transferMessage}</div>}

      <section className="config-context panel">
        <div className="config-tabs" role="tablist" aria-label="配置类型">
          <button className={tab === 'runtime' ? 'active' : ''} onClick={() => setTab('runtime')}><Eye size={14} /> 运行配置</button>
          <button disabled={!canManageSecrets} className={tab === 'secrets' ? 'active' : ''} onClick={() => setTab('secrets')}><KeyRound size={14} /> 密钥管理</button>
        </div>
        <label>编辑作用域
          <select value={scope} onChange={(event) => setScope(event.target.value as ConfigScope)}>
            {Object.entries(scopeLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
          </select>
        </label>
        {scope === 'channel' ? (
          <label>渠道 UUID
            <input value={customScopeId} onChange={(event) => setCustomScopeId(event.target.value)} placeholder="输入渠道实例 UUID" />
          </label>
        ) : <span className="scope-identity">{scopeId ?? '全局，无需作用域 ID'}</span>}
      </section>

      <section className="configuration-layout">
        <aside className="config-sections panel">
          <div className="history-heading"><History size={14} /> 配置版本</div>
          {(history.data?.versions ?? []).map((version) => (
            <div className="version-row" key={version.id}>
              <div><strong>v{version.version}</strong><span className={`version-status ${version.status}`}>{formatConfigVersionStatus(version.status)}</span></div>
              <small>{version.note || '无版本说明'}</small>
              <button type="button" onClick={() => exportPackage.mutate(version.id)} disabled={exportPackage.isPending}>
                <Download size={12} /> 安全导出
              </button>
              {version.status !== 'draft' && (
                <button type="button" onClick={() => rollbackVersion.mutate(version.id)} disabled={!canWrite || rollbackVersion.isPending}>
                  <RotateCcw size={12} /> 回滚到此版本
                </button>
              )}
            </div>
          ))}
          {!history.isLoading && !history.data?.versions.length && <p className="empty-copy">还没有配置版本。</p>}
          {currentDraft && diff.data && (
            <div className="diff-preview">
              <strong>发布差异</strong>
              {diff.data.changes.map((item) => (
                <span key={`${item.key}-${item.scope_type}-${item.scope_id}`}>
                  <b>{diffLabels[item.kind]}</b> {item.key}<small>{scopeLabels[item.scope_type]} · {formatConfigValue(item.before)} → {formatConfigValue(item.after)}</small>
                </span>
              ))}
              {diff.data.changes.length === 0 && <small>没有值变化</small>}
            </div>
          )}
        </aside>

        <section className="panel config-panel">
          <div className="config-toolbar">
            <label className="search-box"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索配置键或说明" /></label>
            {tab === 'runtime' && <input className="version-note" value={note} maxLength={1000} onChange={(event) => setNote(event.target.value)} placeholder="本次修改说明（可选）" />}
            <span className="subtle">Schema v{registry.data?.schema_version ?? '—'}</span>
          </div>

          {registry.isLoading && <div className="empty-state"><CircleDashed className="spin" /> 正在读取配置注册表</div>}
          {registry.isError && <div className="empty-state error">无法连接配置 API，请先启动后端服务。</div>}
          {!registry.isLoading && !registry.isError && definitions.length === 0 && <div className="empty-state">当前作用域没有匹配的配置项。</div>}
          <div className="definition-list">
            {definitions.map((definition) => {
              const configuredSecret = secrets.data?.secrets.find(
                (item) => item.key === definition.key && item.scope_type === scope && item.scope_id === scopeId,
              )
              const effectiveValue = effectiveByKey[definition.key]
              return (
                <article className="definition-row" key={definition.key}>
                  <div className="definition-main">
                    <div className="definition-title"><strong>{definition.label}</strong><code>{definition.key}</code></div>
                    <p>{definition.description}</p>
                    <div className="tag-row">
                      <span>{definition.value_kind}</span><span>{scopeLabels[scope]}作用域</span>
                      <span>{definition.hot_reload ? '支持热更新' : '需要重启'}</span>
                    </div>
                  </div>
                  <div className="definition-value editor">
                    {definition.secret ? (
                      <>
                        <small>{configuredSecret ? `${configuredSecret.masked_hint} · ${configuredSecret.integrity_status}` : '尚未配置'}</small>
                        <input
                          className="config-input"
                          type="password"
                          autoComplete="new-password"
                          value={secretValues[definition.key] ?? ''}
                          onChange={(event) => setSecretValues((current) => ({ ...current, [definition.key]: event.target.value }))}
                          placeholder={configuredSecret ? '输入新值以轮换' : '输入密钥'}
                        />
                        <div className="secret-actions">
                          <button disabled={!secretValues[definition.key] || writeSecret.isPending} onClick={() => writeSecret.mutate(definition)}>{configuredSecret ? '轮换' : '写入'}</button>
                          {configuredSecret && <button disabled={verifySecret.isPending} onClick={() => verifySecret.mutate(configuredSecret.id)}>完整性测试</button>}
                          {configuredSecret && <button className="danger" disabled={removeSecret.isPending} onClick={() => removeSecret.mutate(configuredSecret.id)}><Trash2 size={11} /> 清除</button>}
                        </div>
                      </>
                    ) : (
                      <>
                        <small>
                          最终生效：{formatConfigValue(effectiveValue?.value ?? definition.default)} · 来源 {effectiveValue?.source.scope_type ? scopeLabels[effectiveValue.source.scope_type] : '内置默认'} v{effectiveValue?.source.version ?? 0}
                        </small>
                        <ConfigInput disabled={!canWrite} definition={definition} value={values[definition.key] ?? definition.default} onChange={(value) => setValues((current) => ({ ...current, [definition.key]: value }))} />
                      </>
                    )}
                  </div>
                </article>
              )
            })}
          </div>
        </section>
      </section>
    </div>
  )
}
