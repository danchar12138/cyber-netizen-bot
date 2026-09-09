import { Bot, ImagePlus, Paperclip, SendHorizontal, Sparkles } from 'lucide-react'

export function ChatPage() {
  return (
    <div className="page chat-page">
      <section className="page-heading compact">
        <div>
          <p className="eyebrow">INTERNAL PLAYGROUND</p>
          <h1>内部对话</h1>
          <p>用于 Agent 调试、人格观察、记忆追踪和日常对话。</p>
        </div>
        <span className="phase-tag">UI SHELL</span>
      </section>

      <section className="chat-workspace panel">
        <aside className="conversation-list">
          <button className="new-conversation"><Sparkles size={15} /> 新建测试会话</button>
          <p className="nav-label">最近会话</p>
          <div className="conversation active"><strong>欢迎来到心智工作台</strong><span>刚刚 · 草稿</span></div>
        </aside>
        <div className="conversation-main">
          <div className="conversation-header">
            <div className="agent-avatar"><Bot size={20} /></div>
            <div><strong>未配置 Agent</strong><span>认知链路将在 P1 接通</span></div>
          </div>
          <div className="message-stage">
            <div className="welcome-orb"><Bot size={28} /></div>
            <h2>对话工作台已经就位</h2>
            <p>创建 Agent 并配置模型后，这里将支持流式响应、附件、分支、记忆引用与运行轨迹。</p>
          </div>
          <div className="composer-shell">
            <textarea disabled placeholder="P1 接通消息 API 后即可开始对话…" />
            <div className="composer-actions">
              <div><button disabled aria-label="添加附件"><Paperclip size={17} /></button><button disabled aria-label="添加图片"><ImagePlus size={17} /></button></div>
              <button className="send-button" disabled><SendHorizontal size={16} /></button>
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}

