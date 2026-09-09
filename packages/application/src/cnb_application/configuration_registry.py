"""Built-in configuration definitions exposed to the management plane."""

from collections.abc import Iterable

from cnb_domain import ConfigDefinition, ConfigScope, ConfigValueKind


class ConfigurationRegistry:
    """Immutable registry of typed runtime configuration definitions."""

    def __init__(self, definitions: Iterable[ConfigDefinition]) -> None:
        by_key: dict[str, ConfigDefinition] = {}
        for definition in definitions:
            if definition.key in by_key:
                raise ValueError(f"duplicate configuration key: {definition.key}")
            by_key[definition.key] = definition
        self._definitions = by_key

    def all(self) -> tuple[ConfigDefinition, ...]:
        """Return definitions in deterministic section/key order."""
        return tuple(sorted(self._definitions.values(), key=lambda item: (item.section, item.key)))

    def get(self, key: str) -> ConfigDefinition:
        """Return one definition, preserving KeyError for unknown keys."""
        return self._definitions[key]

    def __len__(self) -> int:
        return len(self._definitions)


def build_default_registry() -> ConfigurationRegistry:
    """Create the safe built-in baseline used before persisted overrides exist."""
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
