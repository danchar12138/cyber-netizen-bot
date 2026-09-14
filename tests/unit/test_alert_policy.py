"""渠道告警升级和值班路由策略测试。"""

from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from cnb_application import AlertEscalationPolicy, AlertPolicyValidationError
from cnb_domain import AlertSeverity, JsonValue


def _values(overrides: Mapping[str, JsonValue] | None = None) -> dict[str, JsonValue]:
    values: dict[str, JsonValue] = {
        "alerts.notification.adapter": "webhook",
        "alerts.notification.escalation_enabled": True,
        "alerts.notification.escalation_after_minutes": 30,
        "alerts.notification.escalation_level_2_after_minutes": 120,
        "alerts.notification.escalation_level_3_after_minutes": 360,
        "alerts.notification.escalation_level_1_adapter": "webhook",
        "alerts.notification.escalation_level_2_adapter": "feishu_webhook",
        "alerts.notification.escalation_level_3_adapter": "email",
        "alerts.notification.warning_max_escalation_level": 1,
        "alerts.notification.on_call_timezone": "UTC",
        "alerts.notification.on_call_weekdays": ["mon", "tue", "wed", "thu", "fri"],
        "alerts.notification.on_call_start_hour": 9,
        "alerts.notification.on_call_end_hour": 18,
        "alerts.notification.out_of_hours_adapter": "email",
    }
    if overrides is not None:
        values.update(overrides)
    return values


def test_alert_policy_matches_all_thresholds_but_advances_only_one_level() -> None:
    policy = AlertEscalationPolicy.from_values(_values())
    evaluated_at = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)

    first = policy.evaluate(
        severity=AlertSeverity.CRITICAL,
        duration_minutes=400,
        current_level=0,
        evaluated_at=evaluated_at,
    )
    second = policy.evaluate(
        severity=AlertSeverity.CRITICAL,
        duration_minutes=400,
        current_level=1,
        evaluated_at=evaluated_at,
    )
    third = policy.evaluate(
        severity=AlertSeverity.CRITICAL,
        duration_minutes=400,
        current_level=2,
        evaluated_at=evaluated_at,
    )

    assert first.matched_level == 3
    assert (first.target_level, first.adapter) == (1, "webhook")
    assert (second.target_level, second.adapter) == (2, "feishu_webhook")
    assert (third.target_level, third.adapter) == (3, "email")


def test_alert_policy_honors_warning_maximum_level() -> None:
    policy = AlertEscalationPolicy.from_values(_values())

    decision = policy.evaluate(
        severity=AlertSeverity.WARNING,
        duration_minutes=1_000,
        current_level=1,
        evaluated_at=datetime(2026, 9, 14, 10, 0, tzinfo=UTC),
    )

    assert decision.maximum_level == 1
    assert decision.matched_level == 1
    assert decision.target_level is None
    assert decision.reason_code == "maximum_level_reached"
    assert [step.eligible for step in decision.steps] == [True, False, False]


@pytest.mark.parametrize(
    ("evaluated_at", "expected_on_call", "expected_adapter"),
    (
        (datetime(2026, 9, 14, 23, 0, tzinfo=UTC), True, "webhook"),
        (datetime(2026, 9, 15, 2, 0, tzinfo=UTC), True, "webhook"),
        (datetime(2026, 9, 15, 10, 0, tzinfo=UTC), False, "email"),
        (datetime(2026, 9, 20, 23, 0, tzinfo=UTC), False, "email"),
    ),
)
def test_alert_policy_routes_cross_midnight_and_out_of_hours(
    evaluated_at: datetime,
    expected_on_call: bool,
    expected_adapter: str,
) -> None:
    policy = AlertEscalationPolicy.from_values(
        _values(
            {
                "alerts.notification.on_call_weekdays": ["mon"],
                "alerts.notification.on_call_start_hour": 22,
                "alerts.notification.on_call_end_hour": 6,
            }
        )
    )

    decision = policy.evaluate(
        severity=AlertSeverity.CRITICAL,
        duration_minutes=30,
        current_level=0,
        evaluated_at=evaluated_at,
    )

    assert decision.on_call is expected_on_call
    assert decision.adapter == expected_adapter


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"alerts.notification.on_call_timezone": "Invalid/Timezone"}, "IANA 时区"),
        ({"alerts.notification.on_call_weekdays": ["mon", "mon"]}, "重复或未知"),
        ({"alerts.notification.on_call_weekdays": ["holiday"]}, "重复或未知"),
        (
            {"alerts.notification.escalation_level_2_after_minutes": 30},
            "必须严格递增",
        ),
    ),
)
def test_alert_policy_rejects_invalid_cross_field_configuration(
    overrides: dict[str, JsonValue],
    message: str,
) -> None:
    values = _values(overrides)
    with pytest.raises(AlertPolicyValidationError, match=message):
        AlertEscalationPolicy.from_values(values)
