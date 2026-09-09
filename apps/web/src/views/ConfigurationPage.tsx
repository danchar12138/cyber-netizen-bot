import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle2,
  CircleDashed,
  History,
  RotateCcw,
  Save,
  Search,
  UploadCloud,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import {
  type ConfigDefinition,
  type ConfigValue,
  type ConfigVersion,
  createConfigDraft,
  formatConfigValue,
  formatConfigVersionStatus,
  getConfigRegistry,
  getConfigVersions,
  publishConfigVersion,
  rollbackConfigVersion,
} from '../api'

function defaultValues(
  definitions: ConfigDefinition[],
  published: ConfigVersion | undefined,
): Record<string, ConfigValue> {
  const values = Object.fromEntries(definitions.map((item) => [item.key, item.default]))
  for (const item of published?.values ?? []) {
    if (item.scope_type === 'system') values[item.key] = item.value
  }
  return values
}

function ConfigInput({
  definition,
  value,
  onChange,
}: {
  definition: ConfigDefinition
  value: ConfigValue
  onChange: (value: ConfigValue) => void
}) {
  if (definition.value_kind === 'boolean') {
    const enabled = value === true
    return (
      <button
        type="button"
        className={`toggle ${enabled ? 'enabled' : ''}`}
        onClick={() => onChange(!enabled)}
        aria-pressed={enabled}
      >
        <span /> {enabled ? '已开启' : '已关闭'}
      </button>
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
      />
    )
  }

  return (
    <input
      className="config-input"
      type={definition.secret ? 'password' : 'text'}
      value={typeof value === 'string' ? value : ''}
      onChange={(event) => onChange(event.target.value)}
      disabled={definition.secret}
      placeholder={definition.secret ? '请在密钥管理中设置' : undefined}
    />
  )
}

export function ConfigurationPage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [note, setNote] = useState('')
  const [values, setValues] = useState<Record<string, ConfigValue>>({})
  const [currentDraft, setCurrentDraft] = useState<ConfigVersion | null>(null)
  const registry = useQuery({ queryKey: ['config-registry'], queryFn: getConfigRegistry })
  const history = useQuery({ queryKey: ['config-versions'], queryFn: getConfigVersions })
  const published = history.data?.versions.find((item) => item.status === 'published')

  useEffect(() => {
    if (!registry.data) return
    setValues(defaultValues(registry.data.definitions, published))
  }, [registry.data, published])

  const refreshHistory = async () => {
    await queryClient.invalidateQueries({ queryKey: ['config-versions'] })
  }

  const createDraft = useMutation({
    mutationFn: () =>
      createConfigDraft({
        note: note.trim() || null,
        values: (registry.data?.definitions ?? [])
          .filter((item) => !item.secret && item.scopes.includes('system'))
          .map((item) => ({
            key: item.key,
            scope_type: 'system' as const,
            scope_id: null,
            value: values[item.key] ?? item.default,
          })),
      }),
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

  const definitions = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    if (!query) return registry.data?.definitions ?? []
    return (registry.data?.definitions ?? []).filter((item) =>
      [item.key, item.label, item.description, item.section].some((candidate) =>
        candidate.toLocaleLowerCase().includes(query),
      ),
    )
  }, [registry.data, search])

  const operationError = createDraft.error ?? publishDraft.error ?? rollbackVersion.error

  return (
    <div className="page">
      <section className="page-heading compact">
        <div>
          <p className="eyebrow">配置注册表</p>
          <h1>配置中心</h1>
          <p>从安全默认值创建不可变草稿，经校验后发布；回滚也会保留为一个新的版本。</p>
        </div>
        <div className="heading-actions">
          <button
            className="secondary-button"
            disabled={createDraft.isPending}
            onClick={() => createDraft.mutate()}
          >
            <Save size={15} /> 保存新草稿
          </button>
          <button
            className="primary-button"
            disabled={!currentDraft || publishDraft.isPending}
            onClick={() => currentDraft && publishDraft.mutate(currentDraft.id)}
          >
            <UploadCloud size={15} /> 发布草稿
          </button>
        </div>
      </section>

      <div className="notice info">
        <CheckCircle2 size={17} />
        <div>
          <strong>Schema 驱动与版本管理已接通</strong>
          <span>密钥不会进入普通版本值，也不会通过接口回显。</span>
        </div>
      </div>
      {currentDraft && (
        <div className="notice warning">草稿 v{currentDraft.version} 已保存，确认后即可发布。</div>
      )}
      {operationError && <div className="notice error">{operationError.message}</div>}

      <section className="configuration-layout">
        <aside className="config-sections panel">
          <div className="history-heading"><History size={14} /> 配置版本</div>
          {history.isLoading && <span className="subtle">正在读取…</span>}
          {(history.data?.versions ?? []).map((version) => (
            <div className="version-row" key={version.id}>
              <div>
                <strong>v{version.version}</strong>
                <span className={`version-status ${version.status}`}>
                  {formatConfigVersionStatus(version.status)}
                </span>
              </div>
              <small>{version.note || '无版本说明'}</small>
              {version.status !== 'draft' && (
                <button
                  type="button"
                  onClick={() => rollbackVersion.mutate(version.id)}
                  disabled={rollbackVersion.isPending}
                >
                  <RotateCcw size={12} /> 回滚到此版本
                </button>
              )}
            </div>
          ))}
          {!history.isLoading && !history.data?.versions.length && (
            <p className="empty-copy">还没有配置版本，保存第一份草稿吧。</p>
          )}
        </aside>

        <section className="panel config-panel">
          <div className="config-toolbar">
            <label className="search-box">
              <Search size={16} />
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜索配置键或说明"
              />
            </label>
            <input
              className="version-note"
              value={note}
              maxLength={1000}
              onChange={(event) => setNote(event.target.value)}
              placeholder="本次修改说明（可选）"
            />
            <span className="subtle">Schema v{registry.data?.schema_version ?? '—'}</span>
          </div>

          {registry.isLoading && (
            <div className="empty-state"><CircleDashed className="spin" /> 正在读取配置注册表</div>
          )}
          {registry.isError && <div className="empty-state error">无法连接配置 API，请先启动后端服务。</div>}
          {!registry.isLoading && !registry.isError && definitions.length === 0 && (
            <div className="empty-state">没有匹配的配置项。</div>
          )}
          <div className="definition-list">
            {definitions.map((definition) => (
              <article className="definition-row" key={definition.key}>
                <div className="definition-main">
                  <div className="definition-title">
                    <strong>{definition.label}</strong>
                    <code>{definition.key}</code>
                  </div>
                  <p>{definition.description}</p>
                  <div className="tag-row">
                    <span>{definition.value_kind}</span>
                    {definition.scopes.map((scope) => <span key={scope}>{scope}</span>)}
                    <span>{definition.hot_reload ? '支持热更新' : '需要重启'}</span>
                  </div>
                </div>
                <div className="definition-value editor">
                  <small>系统作用域 · 默认 {formatConfigValue(definition.default)}</small>
                  <ConfigInput
                    definition={definition}
                    value={values[definition.key] ?? definition.default}
                    onChange={(value) => setValues((current) => ({ ...current, [definition.key]: value }))}
                  />
                </div>
              </article>
            ))}
          </div>
        </section>
      </section>
    </div>
  )
}
