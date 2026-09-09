import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, CircleDashed, RotateCcw, Save, Search } from 'lucide-react'
import { useMemo, useState } from 'react'

import { formatConfigValue, getConfigRegistry } from '../api'

export function ConfigurationPage() {
  const [search, setSearch] = useState('')
  const registry = useQuery({ queryKey: ['config-registry'], queryFn: getConfigRegistry })
  const definitions = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    if (!query) return registry.data?.definitions ?? []
    return (registry.data?.definitions ?? []).filter((item) =>
      [item.key, item.label, item.description, item.section].some((value) =>
        value.toLocaleLowerCase().includes(query),
      ),
    )
  }, [registry.data, search])

  return (
    <div className="page">
      <section className="page-heading compact">
        <div>
          <p className="eyebrow">CONFIGURATION REGISTRY</p>
          <h1>配置中心</h1>
          <p>由后端 Schema 驱动。当前展示安全默认值，发布与回滚能力将在下一切片开放。</p>
        </div>
        <div className="heading-actions">
          <button className="secondary-button" disabled><RotateCcw size={15} /> 版本历史</button>
          <button className="primary-button" disabled><Save size={15} /> 发布草稿</button>
        </div>
      </section>

      <div className="notice info">
        <CheckCircle2 size={17} />
        <div><strong>Schema 驱动已接通</strong><span>字段定义来自 API，密钥不会通过该接口回显。</span></div>
      </div>

      <section className="configuration-layout">
        <aside className="config-sections panel">
          <p className="nav-label">配置分区</p>
          <button className="config-section active">全部配置 <span>{registry.data?.definitions.length ?? 0}</span></button>
          {Array.from(new Set((registry.data?.definitions ?? []).map((item) => item.section))).map(
            (section) => <button className="config-section" key={section}>{section}</button>,
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
            <span className="subtle">Schema v{registry.data?.schema_version ?? '—'}</span>
          </div>

          {registry.isLoading && <div className="empty-state"><CircleDashed className="spin" /> 正在读取配置注册表</div>}
          {registry.isError && <div className="empty-state error">无法连接配置 API。请先启动后端服务。</div>}
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
                    <span>{definition.hot_reload ? '热更新' : '需重启'}</span>
                  </div>
                </div>
                <div className="definition-value">
                  <small>内置默认值</small>
                  <strong>{definition.secret ? '••••••••' : formatConfigValue(definition.default)}</strong>
                </div>
              </article>
            ))}
          </div>
        </section>
      </section>
    </div>
  )
}

