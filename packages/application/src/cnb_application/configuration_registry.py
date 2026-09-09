"""向管理平面公开的内置配置定义。"""

from collections.abc import Iterable

from cnb_domain import ConfigDefinition, ConfigEntry, ConfigScope, ConfigValueKind, JsonValue


class ConfigurationValidationError(ValueError):
    """配置值不符合已注册定义时抛出。"""


class ConfigurationRegistry:
    """带类型运行配置定义的不可变注册表。"""

    def __init__(self, definitions: Iterable[ConfigDefinition]) -> None:
        by_key: dict[str, ConfigDefinition] = {}
        for definition in definitions:
            if definition.key in by_key:
                raise ValueError(f"存在重复配置键：{definition.key}")
            by_key[definition.key] = definition
        self._definitions = by_key

    def all(self) -> tuple[ConfigDefinition, ...]:
        """按稳定的分区和配置键顺序返回定义。"""
        return tuple(sorted(self._definitions.values(), key=lambda item: (item.section, item.key)))

    def get(self, key: str) -> ConfigDefinition:
        """返回单个定义，未知配置键继续抛出 KeyError。"""
        return self._definitions[key]

    def __len__(self) -> int:
        return len(self._definitions)

    def validate_entry(self, entry: ConfigEntry) -> None:
        """校验值类型、作用域以及数值或选项约束。"""
        try:
            definition = self.get(entry.key)
        except KeyError as error:
            raise ConfigurationValidationError(f"未知配置键：{entry.key}") from error

        if definition.secret:
            raise ConfigurationValidationError(f"密钥配置必须通过密钥存储处理：{entry.key}")
        if entry.scope_type not in definition.scopes:
            raise ConfigurationValidationError(
                f"配置 {entry.key} 不支持作用域 {entry.scope_type.value}"
            )
        if entry.scope_type is ConfigScope.SYSTEM and entry.scope_id is not None:
            raise ConfigurationValidationError("系统作用域不能设置作用域 ID")
        if entry.scope_type is not ConfigScope.SYSTEM and entry.scope_id is None:
            raise ConfigurationValidationError(f"作用域 {entry.scope_type.value} 必须设置作用域 ID")

        self._validate_value(definition, entry.value)

    @staticmethod
    def _validate_value(definition: ConfigDefinition, value: JsonValue) -> None:
        expected = definition.value_kind
        valid = False
        if expected is ConfigValueKind.STRING:
            valid = isinstance(value, str)
        elif expected is ConfigValueKind.BOOLEAN:
            valid = isinstance(value, bool)
        elif expected is ConfigValueKind.INTEGER:
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif expected is ConfigValueKind.NUMBER:
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif expected is ConfigValueKind.STRING_LIST:
            valid = isinstance(value, list) and all(isinstance(item, str) for item in value)

        if not valid:
            raise ConfigurationValidationError(
                f"配置 {definition.key} 的值不符合 {expected.value} 类型"
            )

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if definition.minimum is not None and value < definition.minimum:
                raise ConfigurationValidationError(
                    f"配置 {definition.key} 不能小于 {definition.minimum}"
                )
            if definition.maximum is not None and value > definition.maximum:
                raise ConfigurationValidationError(
                    f"配置 {definition.key} 不能大于 {definition.maximum}"
                )
        if definition.options and value not in definition.options:
            raise ConfigurationValidationError(
                f"配置 {definition.key} 必须是以下选项之一：{', '.join(definition.options)}"
            )


def build_default_registry() -> ConfigurationRegistry:
    """创建持久化覆盖值生效前使用的安全内置基线。"""
    system_and_tenant = (ConfigScope.SYSTEM, ConfigScope.TENANT)
    per_agent = (*system_and_tenant, ConfigScope.AGENT)
    per_channel = (*per_agent, ConfigScope.CHANNEL)
    all_scopes = (*per_channel, ConfigScope.USER)

    return ConfigurationRegistry(
        (
            ConfigDefinition(
                key="system.default_timezone",
                section="system",
                label="默认时区",
                description="Agent 在没有用户级时区时使用的 IANA 时区。",
                value_kind=ConfigValueKind.STRING,
                default="Asia/Shanghai",
                scopes=(*system_and_tenant, ConfigScope.USER),
            ),
            ConfigDefinition(
                key="cognition.context.max_tokens",
                section="cognition",
                label="上下文 Token 上限",
                description="单次认知运行在模型调用前允许组装的最大上下文预算。",
                value_kind=ConfigValueKind.INTEGER,
                default=24000,
                scopes=per_agent,
                minimum=2048,
                maximum=200000,
            ),
            ConfigDefinition(
                key="cognition.affect.half_life_seconds",
                section="cognition",
                label="短期情绪半衰期",
                description="短期情绪状态回归中性基线所需的秒数。",
                value_kind=ConfigValueKind.INTEGER,
                default=21600,
                scopes=per_agent,
                minimum=60,
                maximum=604800,
            ),
            ConfigDefinition(
                key="cognition.reflection.enabled",
                section="cognition",
                label="启用异步反思",
                description="在对话完成后生成 Episode、记忆和关系更新候选。",
                value_kind=ConfigValueKind.BOOLEAN,
                default=True,
                scopes=per_agent,
            ),
            ConfigDefinition(
                key="cognition.reflection.delay_seconds",
                section="cognition",
                label="异步反思延迟",
                description="对话运行结束后等待多少秒再执行反思，避免与前台响应争抢资源。",
                value_kind=ConfigValueKind.INTEGER,
                default=3,
                scopes=per_agent,
                minimum=0,
                maximum=3600,
            ),
            ConfigDefinition(
                key="cognition.reflection.minimum_importance",
                section="cognition",
                label="反思记忆最低重要性",
                description="异步反思候选达到该重要性后才写入长期情景记忆。",
                value_kind=ConfigValueKind.NUMBER,
                default=0.45,
                scopes=per_agent,
                minimum=0,
                maximum=1,
            ),
            ConfigDefinition(
                key="tasks.max_attempts",
                section="tasks",
                label="任务最大尝试次数",
                description="后台任务进入死信前允许的执行尝试总数。",
                value_kind=ConfigValueKind.INTEGER,
                default=5,
                scopes=system_and_tenant,
                minimum=1,
                maximum=20,
            ),
            ConfigDefinition(
                key="tasks.lease_seconds",
                section="tasks",
                label="任务执行租约",
                description="Worker 认领任务后的租约秒数；过期后由恢复器接管。",
                value_kind=ConfigValueKind.INTEGER,
                default=120,
                scopes=system_and_tenant,
                minimum=5,
                maximum=3600,
            ),
            ConfigDefinition(
                key="tasks.retry_base_seconds",
                section="tasks",
                label="失败退避基数",
                description="任务与 Outbox 发布失败后指数退避的起始秒数。",
                value_kind=ConfigValueKind.INTEGER,
                default=5,
                scopes=system_and_tenant,
                minimum=1,
                maximum=600,
            ),
            ConfigDefinition(
                key="memory.recall.limit",
                section="memory",
                label="记忆召回数量",
                description="最终进入认知上下文的长期记忆条目上限。",
                value_kind=ConfigValueKind.INTEGER,
                default=12,
                scopes=per_agent,
                minimum=1,
                maximum=100,
            ),
            ConfigDefinition(
                key="memory.embedding.version",
                section="memory",
                label="记忆向量版本",
                description="长期记忆写入与召回使用的向量编码版本；切换后应执行渐进重建。",
                value_kind=ConfigValueKind.STRING,
                default="local-hash-v1",
                scopes=per_agent,
                options=("local-hash-v1",),
            ),
            ConfigDefinition(
                key="memory.recall.candidate_pool",
                section="memory",
                label="记忆候选池大小",
                description="全文、Trigram 与向量检索进入混合重排的最大候选数量。",
                value_kind=ConfigValueKind.INTEGER,
                default=60,
                scopes=per_agent,
                minimum=1,
                maximum=500,
            ),
            ConfigDefinition(
                key="memory.recall.recency_half_life_days",
                section="memory",
                label="记忆时间半衰期",
                description="时间相关性衰减到一半所需天数。",
                value_kind=ConfigValueKind.NUMBER,
                default=30.0,
                scopes=per_agent,
                minimum=0.1,
                maximum=3650,
            ),
            ConfigDefinition(
                key="memory.recall.maximum_sensitivity",
                section="memory",
                label="召回最高敏感级别",
                description="普通对话运行允许进入上下文的最高记忆敏感级别。",
                value_kind=ConfigValueKind.STRING,
                default="personal",
                scopes=per_agent,
                options=("normal", "personal", "sensitive", "restricted"),
            ),
            ConfigDefinition(
                key="memory.recall.full_text_weight",
                section="memory",
                label="全文相关权重",
                description="混合召回中全文和字符相似度的权重。",
                value_kind=ConfigValueKind.NUMBER,
                default=0.25,
                scopes=per_agent,
                minimum=0,
                maximum=1,
            ),
            ConfigDefinition(
                key="model.chat.max_output_tokens",
                section="model",
                label="对话输出 Token 上限",
                description="内部对话单次模型响应允许生成的最大 Token 数量。",
                value_kind=ConfigValueKind.INTEGER,
                default=1024,
                scopes=all_scopes,
                minimum=64,
                maximum=32768,
            ),
            ConfigDefinition(
                key="model.chat.total_token_budget",
                section="model",
                label="单次总 Token 预算",
                description="上下文估算与最大输出合计不得超过的单次运行预算。",
                value_kind=ConfigValueKind.INTEGER,
                default=26000,
                scopes=all_scopes,
                minimum=2048,
                maximum=240000,
            ),
            ConfigDefinition(
                key="model.chat.timeout_seconds",
                section="model",
                label="模型调用超时",
                description="单次模型流从建立到结束允许的最长秒数。",
                value_kind=ConfigValueKind.INTEGER,
                default=60,
                scopes=per_channel,
                minimum=5,
                maximum=600,
            ),
            ConfigDefinition(
                key="model.chat.max_attempts",
                section="model",
                label="模型最大尝试次数",
                description="仅在尚未输出文本时进行的有限尝试次数，避免流式内容重复。",
                value_kind=ConfigValueKind.INTEGER,
                default=2,
                scopes=per_channel,
                minimum=1,
                maximum=5,
            ),
            ConfigDefinition(
                key="model.chat.fallback_provider",
                section="model",
                label="模型降级 Provider",
                description="主路由在未输出内容且尝试失败后使用的安全降级 Provider。",
                value_kind=ConfigValueKind.STRING,
                default="development",
                scopes=per_channel,
                options=("development", "openai"),
            ),
            ConfigDefinition(
                key="model.chat.circuit_breaker_failures",
                section="model",
                label="熔断失败阈值",
                description="同一模型配置连续失败达到该次数后暂时停止调用。",
                value_kind=ConfigValueKind.INTEGER,
                default=3,
                scopes=per_channel,
                minimum=1,
                maximum=20,
            ),
            ConfigDefinition(
                key="model.chat.circuit_breaker_cooldown_seconds",
                section="model",
                label="熔断恢复时间",
                description="模型配置熔断后再次允许试探调用前等待的秒数。",
                value_kind=ConfigValueKind.INTEGER,
                default=30,
                scopes=per_channel,
                minimum=1,
                maximum=3600,
            ),
            ConfigDefinition(
                key="model.chat.provider",
                section="model",
                label="对话模型 Provider",
                description="对话运行使用的模型 Provider；开发模式无需外部凭证。",
                value_kind=ConfigValueKind.STRING,
                default="development",
                scopes=per_channel,
                options=("development", "openai"),
            ),
            ConfigDefinition(
                key="model.openai.api_key",
                section="model",
                label="OpenAI API 密钥",
                description="调用 OpenAI Responses API 的访问密钥，仅可写入、轮换和检查。",
                value_kind=ConfigValueKind.SECRET,
                default=None,
                scopes=per_agent,
                secret=True,
            ),
            ConfigDefinition(
                key="model.openai.model",
                section="model",
                label="OpenAI 对话模型",
                description="通过 OpenAI Responses API 调用的模型名称。",
                value_kind=ConfigValueKind.STRING,
                default="gpt-5-mini",
                scopes=per_channel,
            ),
            ConfigDefinition(
                key="memory.recall.semantic_weight",
                section="memory",
                label="语义相关权重",
                description="混合召回中语义相似度的初始权重。",
                value_kind=ConfigValueKind.NUMBER,
                default=0.35,
                scopes=per_agent,
                minimum=0,
                maximum=1,
            ),
            ConfigDefinition(
                key="memory.recall.recency_weight",
                section="memory",
                label="时间相关权重",
                description="混合召回中时间衰减分量的权重。",
                value_kind=ConfigValueKind.NUMBER,
                default=0.15,
                scopes=per_agent,
                minimum=0,
                maximum=1,
            ),
            ConfigDefinition(
                key="memory.recall.importance_weight",
                section="memory",
                label="重要性权重",
                description="混合召回中人工或反思标注意义强度的权重。",
                value_kind=ConfigValueKind.NUMBER,
                default=0.15,
                scopes=per_agent,
                minimum=0,
                maximum=1,
            ),
            ConfigDefinition(
                key="memory.recall.relationship_weight",
                section="memory",
                label="关系连续性权重",
                description="混合召回中当前用户关系熟悉度的权重。",
                value_kind=ConfigValueKind.NUMBER,
                default=0.10,
                scopes=per_agent,
                minimum=0,
                maximum=1,
            ),
            ConfigDefinition(
                key="proactive.enabled",
                section="proactive",
                label="允许主动行为",
                description="允许 Agent 创建主动联系候选; 候选仍需通过策略门。",
                value_kind=ConfigValueKind.BOOLEAN,
                default=False,
                scopes=all_scopes,
            ),
            ConfigDefinition(
                key="proactive.minimum_score",
                section="proactive",
                label="主动行为最低评分",
                description="关系、时效、重要性和置信度的综合评分达到该值后才允许分发。",
                value_kind=ConfigValueKind.NUMBER,
                default=0.7,
                scopes=all_scopes,
                minimum=0,
                maximum=1,
            ),
            ConfigDefinition(
                key="proactive.quiet_hours_start",
                section="proactive",
                label="安静时段开始小时",
                description="按用户时区计算的 24 小时制开始小时。",
                value_kind=ConfigValueKind.INTEGER,
                default=22,
                scopes=all_scopes,
                minimum=0,
                maximum=23,
            ),
            ConfigDefinition(
                key="proactive.quiet_hours_end",
                section="proactive",
                label="安静时段结束小时",
                description="按用户时区计算的 24 小时制结束小时。",
                value_kind=ConfigValueKind.INTEGER,
                default=8,
                scopes=all_scopes,
                minimum=0,
                maximum=23,
            ),
            ConfigDefinition(
                key="proactive.require_recent_user_days",
                section="proactive",
                label="用户最近活跃天数",
                description="超过该天数没有用户主动互动时，禁止 Agent 主动联系。",
                value_kind=ConfigValueKind.INTEGER,
                default=30,
                scopes=all_scopes,
                minimum=1,
                maximum=365,
            ),
            ConfigDefinition(
                key="persona.system_prompt",
                section="persona",
                label="基础人格提示",
                description="进入对话模型的基础人格与行为边界；后续将迁移至独立人格版本。",
                value_kind=ConfigValueKind.STRING,
                default=(
                    "你是一个自然、友善且尊重边界的赛博网友。"
                    "请直接回应用户，不要声称执行了未发生的操作。"
                ),
                scopes=per_agent,
            ),
            ConfigDefinition(
                key="proactive.daily_message_budget",
                section="proactive",
                label="每日主动消息预算",
                description="单个 Agent 对单个用户每日可发送的主动消息上限。",
                value_kind=ConfigValueKind.INTEGER,
                default=2,
                scopes=all_scopes,
                minimum=0,
                maximum=20,
            ),
            ConfigDefinition(
                key="attachment.max_size_bytes",
                section="attachment",
                label="附件大小上限",
                description="单个附件允许上传的最大字节数，超出后服务端拒绝预签名申请。",
                value_kind=ConfigValueKind.INTEGER,
                default=25 * 1024 * 1024,
                scopes=per_agent,
                minimum=1,
                maximum=250 * 1024 * 1024,
            ),
            ConfigDefinition(
                key="attachment.allowed_mime_types",
                section="attachment",
                label="允许的附件类型",
                description="上传白名单；对象完成上传时仍会再次校验实际媒体类型。",
                value_kind=ConfigValueKind.STRING_LIST,
                default=[
                    "image/png",
                    "image/jpeg",
                    "image/webp",
                    "image/gif",
                    "text/plain",
                    "text/markdown",
                    "text/csv",
                    "application/json",
                    "application/pdf",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ],
                scopes=per_agent,
            ),
            ConfigDefinition(
                key="attachment.upload_expiry_seconds",
                section="attachment",
                label="上传授权有效期",
                description="预签名 PUT 授权的有效秒数，过期的待上传对象可被清理任务回收。",
                value_kind=ConfigValueKind.INTEGER,
                default=900,
                scopes=per_agent,
                minimum=60,
                maximum=3600,
            ),
            ConfigDefinition(
                key="channel.feishu.bot_credential",
                section="channel",
                label="飞书机器人凭证",
                description="飞书 Adapter 使用的不透明凭证；当前占位实现只保存和检测配置状态。",
                value_kind=ConfigValueKind.SECRET,
                default=None,
                scopes=(ConfigScope.CHANNEL,),
                secret=True,
            ),
            ConfigDefinition(
                key="channel.discord.bot_token",
                section="channel",
                label="Discord Bot Token",
                description="Discord Adapter 使用的不透明 Token；当前占位实现不会访问外部 API。",
                value_kind=ConfigValueKind.SECRET,
                default=None,
                scopes=(ConfigScope.CHANNEL,),
                secret=True,
            ),
            ConfigDefinition(
                key="channel.telegram.bot_token",
                section="channel",
                label="Telegram Bot Token",
                description="Telegram Adapter 使用的不透明 Token；当前占位实现不会访问外部 API。",
                value_kind=ConfigValueKind.SECRET,
                default=None,
                scopes=(ConfigScope.CHANNEL,),
                secret=True,
            ),
        )
    )
