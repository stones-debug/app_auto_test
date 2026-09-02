import re
import time
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from .driver import ElementNotFound
from .smart_locator import SmartElementResolver

_VAR_RE = re.compile(r"\$\{(\w+)\}")
_EDITABLE_SUFFIX = "//android.widget.EditText"
_MISSING_ELEMENT_NAMES = frozenset({"NoSuchElementException", "TimeoutException"})

StopPredicate = Callable[[], bool]


def _is_missing_element_error(error: BaseException) -> bool:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, ElementNotFound):
            return True
        if current.__class__.__name__ in _MISSING_ELEMENT_NAMES:
            return True
        if "no such element" in str(current).lower():
            return True
        current = current.__cause__ or current.__context__
    return False


class ExecutionContext:
    """执行上下文：从元素快照解析定位信息（§10.3），支持运行时变量。"""

    def __init__(
        self,
        driver,
        case: dict,
        variables: dict | None = None,
        screenshots_dir: Path | None = None,
        should_stop: StopPredicate | None = None,
    ) -> None:
        self.driver = driver
        self.case = case
        self.elements_snapshot = case.get("elements_snapshot") or {}
        self.variables = dict(variables or {})
        self.screenshots_dir = screenshots_dir or Path(".")
        self.should_stop = should_stop

    def render(self, text: str) -> str:
        def repl(match: re.Match) -> str:
            name = match.group(1)
            if name not in self.variables:
                raise ValueError(f"未定义变量: ${{{name}}}")
            return str(self.variables[name])

        return _VAR_RE.sub(repl, text)

    def find_element(
        self,
        element_id,
        wait_timeout: float | None = None,
        *,
        deadline: float | None = None,
        allow_immediate: bool = False,
        editable: bool = False,
        editable_suffix_only: bool = False,
        disable_smart_scroll: bool = False,
    ):
        key = str(element_id)
        data = self.elements_snapshot.get(key)
        if data is None:
            raise ElementNotFound(f"元素快照缺失: element_id={element_id}")
        locator_type = data.get("locator_type") or "id"
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0 and not allow_immediate:
                raise ElementNotFound(f"元素定位达到最大等待时间: element_id={element_id}")
            if remaining <= 0:
                wait_timeout = 0.0
            else:
                wait_timeout = remaining if wait_timeout is None else min(float(wait_timeout), remaining)
        if locator_type == "smart":
            # smart 快照不支持 editable 概念（resource_id 追加 EditText 后缀仅适用普通定位）；
            # 若 step 参数携带 editable 一并忽略。
            return SmartElementResolver().resolve(
                self.driver,
                self,
                data,
                disable_scroll=disable_smart_scroll,
                deadline=deadline,
                allow_immediate=allow_immediate,
            )
        locator_value = self.render(data.get("locator_value") or "")
        if editable and locator_type == "resource_id":
            suffix_locator = (
                locator_value
                if locator_value.endswith(_EDITABLE_SUFFIX)
                else f"{locator_value}{_EDITABLE_SUFFIX}"
            )
            if editable_suffix_only:
                locator_value = suffix_locator
            else:
                try:
                    # resource_id 可能直接指向 EditText，优先使用原始定位值。
                    return self.driver.find_element(
                        locator_type, locator_value, wait_timeout=wait_timeout
                    )
                except Exception as exc:
                    if not _is_missing_element_error(exc):
                        raise
                    locator_value = suffix_locator
        return self.driver.find_element(locator_type, locator_value, wait_timeout=wait_timeout)

    def invalidate_element(self, element_id) -> None:
        """丢弃失效元素（视图类缓存扩展点）。

        当前实现每次 find_element 都重新向驱动查询，无元素句柄缓存，
        此处为空实现；若未来引入缓存，需按 element_id 清除。
        """
        _ = element_id

    def save_screenshot(self, filename: str = "screenshot.png") -> str:
        # 忽略用户提供的文件名，使用服务端安全文件名，避免任意路径写入（CR-13）
        _ = filename
        safe_name = f"{uuid4().hex}.png"
        if self.screenshots_dir is None:
            raise ValueError("未配置隔离的截图目录")
        root = self.screenshots_dir.resolve()
        target = (root / safe_name).resolve()
        if not target.is_relative_to(root):
            raise ValueError("截图路径越界")
        target.parent.mkdir(parents=True, exist_ok=True)
        self.driver.screenshot(str(target))
        return str(target)
