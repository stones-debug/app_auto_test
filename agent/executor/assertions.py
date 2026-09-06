import re
from decimal import Decimal, InvalidOperation

from .driver import ElementNotFound, StopRequested, _coerce_bool
from .stale_guard import with_stale_retry


class BaseAssertion:
    async def verify(self, driver, context, params: dict, *, deadline: float | None = None) -> dict:
        raise NotImplementedError


ASSERTION_REGISTRY: dict[str, type[BaseAssertion]] = {}


def register_assertion(name: str):
    def decorator(cls: type[BaseAssertion]):
        ASSERTION_REGISTRY[name] = cls
        return cls

    return decorator


@register_assertion("element_exists")
class ElementExistsAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict, *, deadline: float | None = None) -> dict:
        expected = params.get("expected", "exists")
        try:
            await with_stale_retry(
                driver,
                context,
                params.get("element_id"),
                lambda element: None,
                deadline=deadline,
                label="存在性检查",
            )
            found = True
        except StopRequested:
            # 停止信号不能被存在性检查吞掉，必须上抛（由 Runner 收敛为 stopped）
            raise
        except ElementNotFound:
            # 仅将明确的元素不存在识别为 not_found；
            # 其余异常（配置非法 InvalidSmartLocator / 匹配不唯一 ElementNotUnique /
            # 滚动超限 ScrollLimitReached / Appium 故障 / stale 重试耗尽）一律上抛，
            # 避免 expected=not_exists 时错误通过。
            found = False
        passed = found if expected == "exists" else not found
        return {
            "status": "passed" if passed else "failed",
            "expected": expected,
            "actual": "found" if found else "not_found",
        }


@register_assertion("checked")
class CheckedAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict, *, deadline: float | None = None) -> dict:
        expected = _coerce_bool(params.get("checked", True))
        actual = await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: driver.is_checked(element),
            deadline=deadline,
            label="断言-读取勾选状态",
        )
        return {
            "status": "passed" if actual == expected else "failed",
            "expected": "checked" if expected else "unchecked",
            "actual": "checked" if actual else "unchecked",
        }


@register_assertion("text_equals")
class TextEqualsAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict, *, deadline: float | None = None) -> dict:
        actual = await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: driver.get_text(element),
            deadline=deadline,
            label="断言-读取文本",
        )
        if params.get("trim"):
            actual = actual.strip()
        expected = str(params.get("expected", ""))
        passed = actual == expected
        return {"status": "passed" if passed else "failed", "expected": expected, "actual": actual}


@register_assertion("text_not_equals")
class TextNotEqualsAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict, *, deadline: float | None = None) -> dict:
        actual = await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: driver.get_text(element),
            deadline=deadline,
            label="断言-读取文本",
        )
        if params.get("trim"):
            actual = actual.strip()
        expected = str(params.get("expected", ""))
        passed = actual != expected
        return {"status": "passed" if passed else "failed", "expected": expected, "actual": actual}


@register_assertion("number_compare")
class NumberCompareAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict, *, deadline: float | None = None) -> dict:
        operator = str(params.get("operator", "")).strip()
        if operator not in {">", ">=", "<", "<=", "==", "!="}:
            raise ValueError(f"不支持的数字比较符: {operator or '空'}")

        actual_raw = await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: driver.get_text(element),
            deadline=deadline,
            label="断言-读取数字文本",
        )
        actual_text = str(actual_raw)
        try:
            actual_value = Decimal(actual_text.strip())
        except (InvalidOperation, ValueError):
            raise ValueError(f"实际文本无法解析为数字: {actual_text!r}") from None
        if not actual_value.is_finite():
            raise ValueError(f"实际文本无法解析为数字: {actual_text!r}")

        expected_text = str(params.get("expected", "")).strip()
        try:
            expected_value = Decimal(expected_text)
        except (InvalidOperation, ValueError):
            raise ValueError(f"目标值无法解析为数字: {expected_text!r}") from None
        if not expected_value.is_finite():
            raise ValueError(f"目标值无法解析为数字: {expected_text!r}")

        passed = {
            ">": actual_value > expected_value,
            ">=": actual_value >= expected_value,
            "<": actual_value < expected_value,
            "<=": actual_value <= expected_value,
            "==": actual_value == expected_value,
            "!=": actual_value != expected_value,
        }[operator]
        return {
            "status": "passed" if passed else "failed",
            "expected": f"{operator} {expected_text}",
            "actual": actual_text,
        }


@register_assertion("text_contains")
class TextContainsAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict, *, deadline: float | None = None) -> dict:
        actual = await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: driver.get_text(element),
            deadline=deadline,
            label="断言-读取文本",
        )
        expected = str(params.get("expected", ""))
        passed = expected in actual
        return {"status": "passed" if passed else "failed", "expected": expected, "actual": actual}


@register_assertion("text_not_contains")
class TextNotContainsAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict, *, deadline: float | None = None) -> dict:
        actual = await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: driver.get_text(element),
            deadline=deadline,
            label="断言-读取文本",
        )
        expected = str(params.get("expected", ""))
        passed = expected not in actual
        return {"status": "passed" if passed else "failed", "expected": expected, "actual": actual}


@register_assertion("attribute_equals")
class AttributeEqualsAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict, *, deadline: float | None = None) -> dict:
        attribute = params.get("attribute", "")
        actual = await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: driver.get_attribute(element, attribute),
            deadline=deadline,
            label="断言-读取属性",
        )
        expected = str(params.get("expected", ""))
        passed = actual == expected
        return {"status": "passed" if passed else "failed", "expected": expected, "actual": actual}


@register_assertion("attribute_contains")
class AttributeContainsAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict, *, deadline: float | None = None) -> dict:
        attribute = params.get("attribute", "")
        actual = await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: driver.get_attribute(element, attribute),
            deadline=deadline,
            label="断言-读取属性",
        )
        expected = str(params.get("expected", ""))
        passed = expected in actual
        return {"status": "passed" if passed else "failed", "expected": expected, "actual": actual}


@register_assertion("value_equals")
class ValueEqualsAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict, *, deadline: float | None = None) -> dict:
        actual = await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: driver.get_text(element),
            deadline=deadline,
            label="断言-读取文本",
        )
        expected = str(params.get("expected", ""))
        passed = actual == expected
        return {"status": "passed" if passed else "failed", "expected": expected, "actual": actual}


@register_assertion("regex_match")
class RegexMatchAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict, *, deadline: float | None = None) -> dict:
        actual = await with_stale_retry(
            driver,
            context,
            params.get("element_id"),
            lambda element: driver.get_text(element),
            deadline=deadline,
            label="断言-读取文本",
        )
        pattern = str(params.get("pattern", ""))
        passed = re.search(pattern, actual) is not None
        return {"status": "passed" if passed else "failed", "expected": pattern, "actual": actual}
