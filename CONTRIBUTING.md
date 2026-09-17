# 参与开发

感谢你参与赛博网友机器人的开发。提交代码前，请先搜索[现有 Issue](https://github.com/danchar12138/cyber-netizen-bot/issues)；较大的功能或架构变更建议先创建 Issue 说明目标、边界和兼容性影响。

## 开发流程

1. 开始前阅读 `AGENTS.md` 和 `docs/plans/` 中的最新计划。
2. 从 `main` 创建短生命周期分支。
3. 始终保持 API Schema、数据库迁移、生成的客户端类型、测试和文档一致。
4. 修改路由或契约后运行 `corepack pnpm api:generate`，不要手工编辑 `apps/web/src/api-client/generated/`。
5. 发起拉取请求前运行后端和 Web 的全部检查。
6. 删除临时文件，并在拉取请求中说明迁移或运维影响。
7. 从仓库的 [Pull Requests](https://github.com/danchar12138/cyber-netizen-bot/pulls) 页面提交变更，并确保 CI 通过。

## 提交规范

使用 Conventional Commits，类型和作用域保留行业通用英文，描述使用中文，例如 `feat(config): 增加最终生效值解析器`。

不得提交密钥、本地数据库、上传文件、生成日志、构建产物或编辑器状态。

## 安全问题

安全漏洞请遵循 [SECURITY.md](SECURITY.md) 的私下披露流程，不要创建包含利用细节、令牌、密钥或个人数据的公开 Issue。
