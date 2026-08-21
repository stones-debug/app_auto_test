import re
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from .driver import ElementNotFound

_VAR_RE = re.compile(r"\$\{(\w+)\}")

StopPredicate = Callable[[], bool]


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

    def find_element(self, element_id, wait_timeout: float | None = None):
        key = str(element_id)
        data = self.elements_snapshot.get(key)
        if data is None:
            raise ElementNotFound(f"元素快照缺失: element_id={element_id}")
        locator_type = data.get("locator_type") or "id"
        locator_value = self.render(data.get("locator_value") or "")
        return self.driver.find_element(locator_type, locator_value, wait_timeout=wait_timeout)

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
