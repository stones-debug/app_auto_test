import re
from pathlib import Path

from .driver import ElementNotFound

_VAR_RE = re.compile(r"\$\{(\w+)\}")


class ExecutionContext:
    """执行上下文：从元素快照解析定位信息（§10.3），支持运行时变量。"""

    def __init__(self, driver, case: dict, variables: dict | None = None, screenshots_dir: Path | None = None) -> None:
        self.driver = driver
        self.case = case
        self.elements_snapshot = case.get("elements_snapshot") or {}
        self.variables = dict(variables or {})
        self.screenshots_dir = screenshots_dir or Path(".")

    def render(self, text: str) -> str:
        def repl(match: re.Match) -> str:
            name = match.group(1)
            if name not in self.variables:
                raise ValueError(f"未定义变量: ${{{name}}}")
            return str(self.variables[name])

        return _VAR_RE.sub(repl, text)

    def find_element(self, element_id):
        key = str(element_id)
        data = self.elements_snapshot.get(key)
        if data is None:
            raise ElementNotFound(f"元素快照缺失: element_id={element_id}")
        locator_type = data.get("locator_type") or "id"
        locator_value = self.render(data.get("locator_value") or "")
        return self.driver.find_element(locator_type, locator_value)

    def save_screenshot(self, filename: str = "screenshot.png") -> str:
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        path = self.screenshots_dir / filename
        self.driver.screenshot(str(path))
        return str(path)
