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

    return ConfigurationRegistry(
        (
            ConfigDefinition(
                key="system.default_timezone",
                section="system",
                label="默认时区",
                description="Agent 在没有用户级时区时使用的 IANA 时区。",
                value_kind=ConfigValueKind.STRING,
                default="Asia/Shanghai",
                scopes=system_and_tenant,
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
                key="cognition.reflection.enabled",
                section="cognition",
                label="启用异步反思",
                description="在对话完成后生成 Episode、记忆和关系更新候选。",
                value_kind=ConfigValueKind.BOOLEAN,
                default=True,
                scopes=per_agent,
            ),
            ConfigDefinition(
                key="memory.recall.limit",
                section="memory",
                label="记忆召回数量",
                description="进入重排阶段的长期记忆条目上限。",
                value_kind=ConfigValueKind.INTEGER,
                default=12,
                scopes=per_agent,
                minimum=1,
                maximum=100,
            ),
            ConfigDefinition(
                key="memory.recall.semantic_weight",
                section="memory",
                label="语义相关权重",
                description="混合召回中语义相似度的初始权重。",
                value_kind=ConfigValueKind.NUMBER,
                default=0.45,
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
                scopes=per_agent,
            ),
            ConfigDefinition(
                key="proactive.daily_message_budget",
                section="proactive",
                label="每日主动消息预算",
                description="单个 Agent 对单个用户每日可发送的主动消息上限。",
                value_kind=ConfigValueKind.INTEGER,
                default=2,
                scopes=per_agent,
                minimum=0,
                maximum=20,
            ),
        )
    )
