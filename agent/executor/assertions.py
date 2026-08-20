import re


class BaseAssertion:
    async def verify(self, driver, context, params: dict) -> dict:
        raise NotImplementedError


ASSERTION_REGISTRY: dict[str, type[BaseAssertion]] = {}


def register_assertion(name: str):
    def decorator(cls: type[BaseAssertion]):
        ASSERTION_REGISTRY[name] = cls
        return cls

    return decorator


@register_assertion("element_exists")
class ElementExistsAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict) -> dict:
        expected = params.get("expected", "exists")
        try:
            context.find_element(params.get("element_id"))
            found = True
        except Exception:
            found = False
        passed = found if expected == "exists" else not found
        return {
            "status": "passed" if passed else "failed",
            "expected": expected,
            "actual": "found" if found else "not_found",
        }


@register_assertion("text_equals")
class TextEqualsAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict) -> dict:
        element = context.find_element(params.get("element_id"))
        actual = driver.get_text(element)
        if params.get("trim"):
            actual = actual.strip()
        expected = str(params.get("expected", ""))
        passed = actual == expected
        return {"status": "passed" if passed else "failed", "expected": expected, "actual": actual}


@register_assertion("text_contains")
class TextContainsAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict) -> dict:
        element = context.find_element(params.get("element_id"))
        actual = driver.get_text(element)
        expected = str(params.get("expected", ""))
        passed = expected in actual
        return {"status": "passed" if passed else "failed", "expected": expected, "actual": actual}


@register_assertion("text_not_contains")
class TextNotContainsAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict) -> dict:
        element = context.find_element(params.get("element_id"))
        actual = driver.get_text(element)
        expected = str(params.get("expected", ""))
        passed = expected not in actual
        return {"status": "passed" if passed else "failed", "expected": expected, "actual": actual}


@register_assertion("attribute_equals")
class AttributeEqualsAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict) -> dict:
        element = context.find_element(params.get("element_id"))
        actual = driver.get_attribute(element, params.get("attribute", ""))
        expected = str(params.get("expected", ""))
        passed = actual == expected
        return {"status": "passed" if passed else "failed", "expected": expected, "actual": actual}


@register_assertion("attribute_contains")
class AttributeContainsAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict) -> dict:
        element = context.find_element(params.get("element_id"))
        actual = driver.get_attribute(element, params.get("attribute", ""))
        expected = str(params.get("expected", ""))
        passed = expected in actual
        return {"status": "passed" if passed else "failed", "expected": expected, "actual": actual}


@register_assertion("value_equals")
class ValueEqualsAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict) -> dict:
        element = context.find_element(params.get("element_id"))
        actual = driver.get_text(element)
        expected = str(params.get("expected", ""))
        passed = actual == expected
        return {"status": "passed" if passed else "failed", "expected": expected, "actual": actual}


@register_assertion("regex_match")
class RegexMatchAssertion(BaseAssertion):
    async def verify(self, driver, context, params: dict) -> dict:
        element = context.find_element(params.get("element_id"))
        actual = driver.get_text(element)
        pattern = str(params.get("pattern", ""))
        passed = re.search(pattern, actual) is not None
        return {"status": "passed" if passed else "failed", "expected": pattern, "actual": actual}
