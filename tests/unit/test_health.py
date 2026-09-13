"""基础设施健康详情的中文友好与安全边界测试。"""

from cnb_infrastructure.health import _safe_probe  # pyright: ignore[reportPrivateUsage]


async def test_failed_dependency_probe_returns_localized_safe_detail() -> None:
    async def failed_probe() -> None:
        raise ConnectionError("不得进入健康接口的底层地址")

    result = await _safe_probe("object_storage", failed_probe)

    assert result.status == "degraded"
    assert result.detail == "MinIO 对象存储暂不可用。"
    assert "ConnectionError" not in result.detail
    assert "底层地址" not in result.detail
