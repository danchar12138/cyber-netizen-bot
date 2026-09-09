import { LogIn, ShieldCheck } from 'lucide-react'
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { UserManager, WebStorageStateStore, type User } from 'oidc-client-ts'

import {
  getAuthenticationConfig,
  setApiAccessToken,
} from '../api'
import {
  AuthenticationContext,
  type AuthenticationContextValue,
} from './authentication-context'

type AuthenticationState =
  | { status: 'loading' }
  | { status: 'development' }
  | { status: 'anonymous'; manager: UserManager }
  | { status: 'authenticated'; manager: UserManager; user: User }
  | { status: 'error' }

let bootstrapPromise: Promise<AuthenticationState> | null = null

function safeReturnUrl(user: User) {
  const raw = user.state as { returnUrl?: unknown } | undefined
  const value = raw?.returnUrl
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//')
    ? value
    : '/'
}

async function bootstrapAuthentication(): Promise<AuthenticationState> {
  const config = await getAuthenticationConfig()
  if (config.mode === 'development') {
    setApiAccessToken(null)
    return { status: 'development' }
  }
  if (!config.authority || !config.client_id || !config.scope) {
    return { status: 'error' }
  }
  const manager = new UserManager({
    authority: config.authority,
    client_id: config.client_id,
    redirect_uri: `${window.location.origin}/auth/callback`,
    silent_redirect_uri: `${window.location.origin}/auth/silent-callback`,
    post_logout_redirect_uri: window.location.origin,
    response_type: 'code',
    scope: config.scope,
    automaticSilentRenew: true,
    monitorSession: true,
    loadUserInfo: true,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  })
  let user: User | null
  const silentCallback = window.location.pathname === '/auth/silent-callback'
    && new URLSearchParams(window.location.search).has('code')
  const callback = window.location.pathname === '/auth/callback'
    && new URLSearchParams(window.location.search).has('code')
  if (silentCallback) {
    await manager.signinSilentCallback()
    user = await manager.getUser()
  } else if (callback) {
    user = await manager.signinRedirectCallback()
    window.history.replaceState({}, document.title, safeReturnUrl(user))
  } else {
    user = await manager.getUser()
  }
  if (!user || user.expired) {
    setApiAccessToken(null)
    return { status: 'anonymous', manager }
  }
  setApiAccessToken(user.access_token)
  return { status: 'authenticated', manager, user }
}

export function AuthenticationBoundary({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthenticationState>({ status: 'loading' })

  useEffect(() => {
    let active = true
    bootstrapPromise ??= bootstrapAuthentication().catch(() => ({ status: 'error' }))
    void bootstrapPromise.then((result) => {
      if (active) setState(result)
    })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (state.status !== 'authenticated') return
    const onLoaded = (user: User) => {
      setApiAccessToken(user.access_token)
      setState({ status: 'authenticated', manager: state.manager, user })
    }
    const onExpired = () => {
      setApiAccessToken(null)
      setState({ status: 'anonymous', manager: state.manager })
    }
    state.manager.events.addUserLoaded(onLoaded)
    state.manager.events.addAccessTokenExpired(onExpired)
    return () => {
      state.manager.events.removeUserLoaded(onLoaded)
      state.manager.events.removeAccessTokenExpired(onExpired)
    }
  }, [state])

  const signOut = useCallback(async () => {
    if (state.status !== 'authenticated') return
    setApiAccessToken(null)
    await state.manager.signoutRedirect()
  }, [state])
  const context = useMemo<AuthenticationContextValue>(
    () => ({ mode: state.status === 'development' ? 'development' : 'oidc', signOut }),
    [signOut, state.status],
  )

  if (state.status === 'loading') {
    return <AuthenticationStatus title="正在验证登录状态" detail="正在读取安全认证引导配置…" />
  }
  if (state.status === 'error') {
    return <AuthenticationStatus title="认证配置不可用" detail="请检查 API、OIDC issuer 与浏览器来源设置。" />
  }
  if (state.status === 'anonymous') {
    return (
      <AuthenticationStatus
        title="登录赛博网友控制平面"
        detail="使用组织 OIDC 身份登录。授权码流程启用 PKCE，浏览器不会持有 client secret。"
        action={(
          <button
            className="primary-button"
            onClick={() => void state.manager.signinRedirect({
              state: { returnUrl: `${window.location.pathname}${window.location.search}` },
            })}
          >
            <LogIn size={16} /> 使用 OIDC 登录
          </button>
        )}
      />
    )
  }
  return (
    <AuthenticationContext.Provider value={context}>
      {children}
    </AuthenticationContext.Provider>
  )
}

function AuthenticationStatus({
  title,
  detail,
  action,
}: {
  title: string
  detail: string
  action?: ReactNode
}) {
  return (
    <main className="authentication-screen">
      <section className="authentication-card" aria-live="polite">
        <div className="brand-mark"><ShieldCheck size={24} /></div>
        <p className="eyebrow">安全身份边界</p>
        <h1>{title}</h1>
        <p>{detail}</p>
        {action}
      </section>
    </main>
  )
}
