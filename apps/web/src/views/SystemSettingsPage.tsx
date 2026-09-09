import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Database, KeyRound, RotateCcw, ServerCog, ShieldCheck } from 'lucide-react'

import { getBootstrapSettings } from '../api'

function configuredLabel(configured: boolean) {
  return configured ? '已配置' : '未配置'
}

export function SystemSettingsPage() {
  const settings = useQuery({ queryKey: ['bootstrap-settings'], queryFn: getBootstrapSettings })
  const data = settings.data

  return (
    <div className="page">
      <section className="page-heading compact">
        <div>
          <p className="eyebrow">启动边界与运行配置</p>
          <h1>系统设置</h1>
          <p>安全查看进程启动设置状态；日常运行参数统一前往配置中心管理、校验和发布。</p>
        </div>
        <Link to="/configuration" className="primary-button">打开配置中心</Link>
      </section>

      {settings.isError && <div className="notice error">无法读取系统设置，请检查 API 连接。</div>}
      {data?.config_master_key_status === 'development_placeholder' && (
        <div className="notice warning" role="alert">
          当前仍在使用开发期主密钥占位值。预发布或生产环境必须先配置随机的 32 字节主密钥。
        </div>
      )}

      <section className="settings-grid" aria-label="启动设置摘要">
        <article className="panel settings-card">
          <div className="panel-heading"><div><p className="eyebrow">进程</p><h2>运行环境</h2></div><ServerCog size={20} /></div>
          <dl className="settings-list">
            <div><dt>环境</dt><dd>{data?.environment ?? '读取中'}</dd></div>
            <div><dt>日志级别</dt><dd>{data?.log_level ?? '读取中'}</dd></div>
            <div><dt>深度就绪探测</dt><dd>{data ? (data.readiness_deep_checks ? '已开启' : '未开启') : '读取中'}</dd></div>
            <div><dt>认证模式</dt><dd>{data?.authentication_mode === 'oidc' ? 'OIDC' : '开发身份'}</dd></div>
            <div><dt>变更生效</dt><dd><RotateCcw size={13} /> 需要重启进程</dd></div>
          </dl>
        </article>

        <article className="panel settings-card">
          <div className="panel-heading"><div><p className="eyebrow">依赖</p><h2>基础设施</h2></div><Database size={20} /></div>
          <dl className="settings-list">
            <div><dt>PostgreSQL</dt><dd>{configuredLabel(data?.database_configured ?? false)}</dd></div>
            <div><dt>Redis</dt><dd>{configuredLabel(data?.redis_configured ?? false)}</dd></div>
            <div><dt>对象存储</dt><dd>MinIO</dd></div>
            <div><dt>MinIO 凭证</dt><dd>{configuredLabel(data?.minio_credentials_configured ?? false)}</dd></div>
            <div><dt>MinIO 地址</dt><dd><code>{data?.minio_endpoint_url ?? '读取中'}</code></dd></div>
            <div><dt>私有桶</dt><dd><code>{data?.minio_bucket ?? '读取中'}</code></dd></div>
          </dl>
        </article>

        <article className="panel settings-card">
          <div className="panel-heading"><div><p className="eyebrow">安全</p><h2>凭证保护</h2></div><KeyRound size={20} /></div>
          <dl className="settings-list">
            <div><dt>配置主密钥</dt><dd>{data?.config_master_key_status === 'configured' ? '已安全配置' : '开发占位值'}</dd></div>
            <div><dt>OIDC 引导</dt><dd>{configuredLabel(data?.oidc_configured ?? false)}</dd></div>
            <div><dt>允许的 Web 来源</dt><dd>{data?.cors_origins.join('、') || '读取中'}</dd></div>
          </dl>
          <p className="settings-help"><ShieldCheck size={14} /> 页面永不返回数据库、Redis、MinIO 或配置主密钥明文。</p>
        </article>
      </section>
    </div>
  )
}
