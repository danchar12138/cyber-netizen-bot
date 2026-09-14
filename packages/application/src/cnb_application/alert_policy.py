"""无框架依赖的渠道告警升级和值班路由策略。"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cnb_domain import AlertSeverity, JsonValue

ALERT_NOTIFICATION_ADAPTERS = ("webhook", "feishu_webhook", "email")
ON_CALL_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class AlertPolicyValidationError(ValueError):
    """生效配置或模拟输入无法形成确定的升级策略。"""


@dataclass(frozen=True, slots=True)
class AlertEscalationPolicyStep:
    """单个升级等级的阈值、值班路由和模拟状态。"""

    level: int
    threshold_minutes: int
    adapter: str
    eligible: bool
    reached: bool
    completed: bool


@dataclass(frozen=True, slots=True)
class AlertEscalationPolicyDecision:
    """一次无副作用策略评估的完整、安全结果。"""

    enabled: bool
    severity: AlertSeverity
    duration_minutes: int
    current_level: int
    maximum_level: int
    matched_level: int
    target_level: int | None
    adapter: str | None
    on_call: bool
    evaluated_at: datetime
    local_time: datetime
    timezone: str
    reason_code: str
    reason: str
    steps: tuple[AlertEscalationPolicyStep, ...]


@dataclass(frozen=True, slots=True)
class AlertEscalationPolicy:
    """从已解析运行配置得到的三级升级和值班路由规则。"""

    enabled: bool
    thresholds: tuple[int, int, int]
    adapters: tuple[str, str, str]
    warning_maximum_level: int
    timezone: str
    weekdays: frozenset[str]
    start_hour: int
    end_hour: int
    out_of_hours_adapter: str

    @classmethod
    def from_values(cls, values: Mapping[str, JsonValue]) -> "AlertEscalationPolicy":
        """解析生效配置，并拒绝无法安全执行的跨字段组合。"""
        default_adapter = _adapter(values, "alerts.notification.adapter")
        thresholds = (
            _integer(values, "alerts.notification.escalation_after_minutes", 1, 10_080),
            _integer(
                values,
                "alerts.notification.escalation_level_2_after_minutes",
                1,
                10_080,
            ),
            _integer(
                values,
                "alerts.notification.escalation_level_3_after_minutes",
                1,
                10_080,
            ),
        )
        if not thresholds[0] < thresholds[1] < thresholds[2]:
            raise AlertPolicyValidationError("告警升级三级等待时间必须严格递增")
        timezone = _string(values, "alerts.notification.on_call_timezone")
        try:
            ZoneInfo(timezone)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise AlertPolicyValidationError("告警值班时区必须是有效的 IANA 时区") from error
        weekdays_value = values.get("alerts.notification.on_call_weekdays")
        if not isinstance(weekdays_value, list):
            raise AlertPolicyValidationError("告警值班工作日必须是字符串列表")
        normalized_weekdays_values: list[str] = []
        for item in weekdays_value:
            if not isinstance(item, str):
                raise AlertPolicyValidationError("告警值班工作日必须是字符串列表")
            normalized_weekdays_values.append(item.strip().casefold())
        normalized_weekdays = tuple(normalized_weekdays_values)
        if len(set(normalized_weekdays)) != len(normalized_weekdays) or any(
            item not in ON_CALL_WEEKDAYS for item in normalized_weekdays
        ):
            raise AlertPolicyValidationError("告警值班工作日包含重复或未知值")
        return cls(
            enabled=_boolean(values, "alerts.notification.escalation_enabled"),
            thresholds=thresholds,
            adapters=(
                _adapter(
                    values,
                    "alerts.notification.escalation_level_1_adapter",
                    inherited=default_adapter,
                ),
                _adapter(
                    values,
                    "alerts.notification.escalation_level_2_adapter",
                    inherited=default_adapter,
                ),
                _adapter(
                    values,
                    "alerts.notification.escalation_level_3_adapter",
                    inherited=default_adapter,
                ),
            ),
            warning_maximum_level=_integer(
                values,
                "alerts.notification.warning_max_escalation_level",
                0,
                3,
            ),
            timezone=timezone,
            weekdays=frozenset(normalized_weekdays),
            start_hour=_integer(values, "alerts.notification.on_call_start_hour", 0, 23),
            end_hour=_integer(values, "alerts.notification.on_call_end_hour", 0, 23),
            out_of_hours_adapter=_adapter(
                values,
                "alerts.notification.out_of_hours_adapter",
                inherited=default_adapter,
            ),
        )

    def evaluate(
        self,
        *,
        severity: AlertSeverity,
        duration_minutes: int,
        current_level: int,
        evaluated_at: datetime,
    ) -> AlertEscalationPolicyDecision:
        """最多推进一级，避免探测延迟跳过通知等级。"""
        if duration_minutes < 0:
            raise AlertPolicyValidationError("告警持续分钟数不能小于 0")
        if not 0 <= current_level <= 3:
            raise AlertPolicyValidationError("当前升级等级必须位于 0 到 3 之间")
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            raise AlertPolicyValidationError("策略评估时间必须包含时区")
        local_time = evaluated_at.astimezone(ZoneInfo(self.timezone))
        on_call = self._is_on_call(local_time)
        maximum_level = self.warning_maximum_level if severity is AlertSeverity.WARNING else 3
        matched_level = max(
            (
                level
                for level, threshold in enumerate(self.thresholds, start=1)
                if level <= maximum_level and duration_minutes >= threshold
            ),
            default=0,
        )
        steps = tuple(
            AlertEscalationPolicyStep(
                level=level,
                threshold_minutes=threshold,
                adapter=adapter if on_call else self.out_of_hours_adapter,
                eligible=level <= maximum_level,
                reached=duration_minutes >= threshold,
                completed=current_level >= level,
            )
            for level, (threshold, adapter) in enumerate(
                zip(self.thresholds, self.adapters, strict=True), start=1
            )
        )
        target_level: int | None = None
        adapter: str | None = None
        if not self.enabled:
            reason_code = "disabled"
            reason = "持续告警升级当前已关闭"
        elif current_level >= maximum_level:
            reason_code = "maximum_level_reached"
            reason = "当前严重级别已达到允许的最高升级等级"
        else:
            next_level = current_level + 1
            if duration_minutes < self.thresholds[next_level - 1]:
                reason_code = "waiting_for_threshold"
                reason = "下一升级等级尚未达到持续时间阈值"
            else:
                target_level = next_level
                adapter = self.adapters[target_level - 1] if on_call else self.out_of_hours_adapter
                reason_code = "eligible_for_escalation"
                reason = "已达到下一升级等级并命中通知路由"
        return AlertEscalationPolicyDecision(
            enabled=self.enabled,
            severity=severity,
            duration_minutes=duration_minutes,
            current_level=current_level,
            maximum_level=maximum_level,
            matched_level=matched_level,
            target_level=target_level,
            adapter=adapter,
            on_call=on_call,
            evaluated_at=evaluated_at,
            local_time=local_time,
            timezone=self.timezone,
            reason_code=reason_code,
            reason=reason,
            steps=steps,
        )

    def _is_on_call(self, local_time: datetime) -> bool:
        weekday = ON_CALL_WEEKDAYS[local_time.weekday()]
        if self.start_hour == self.end_hour:
            return weekday in self.weekdays
        if self.start_hour < self.end_hour:
            return weekday in self.weekdays and self.start_hour <= local_time.hour < self.end_hour
        if local_time.hour >= self.start_hour:
            return weekday in self.weekdays
        previous_weekday = ON_CALL_WEEKDAYS[(local_time.weekday() - 1) % 7]
        return local_time.hour < self.end_hour and previous_weekday in self.weekdays


def _boolean(values: Mapping[str, JsonValue], key: str) -> bool:
    value = values.get(key)
    if not isinstance(value, bool):
        raise AlertPolicyValidationError(f"配置 {key} 必须是布尔值")
    return value


def _integer(values: Mapping[str, JsonValue], key: str, minimum: int, maximum: int) -> int:
    value = values.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise AlertPolicyValidationError(f"配置 {key} 必须位于 {minimum} 到 {maximum} 之间")
    return value


def _string(values: Mapping[str, JsonValue], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AlertPolicyValidationError(f"配置 {key} 必须是非空字符串")
    return value.strip()


def _adapter(
    values: Mapping[str, JsonValue],
    key: str,
    *,
    inherited: str | None = None,
) -> str:
    value = _string(values, key).casefold()
    if value == "default" and inherited is not None:
        return inherited
    if value not in ALERT_NOTIFICATION_ADAPTERS:
        raise AlertPolicyValidationError(f"配置 {key} 不是受支持的通知适配器")
    return value
