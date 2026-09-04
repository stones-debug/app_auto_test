import hashlib
import re


def _coerce_bool(value) -> bool:
    """将 Appium/Mock 返回的 checked/selected 值统一转换为布尔值。"""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "checked", "selected"}


class DriverError(Exception):
    pass


class ElementNotFound(DriverError):
    pass


class StopRequested(DriverError):
    pass


class StaleObjectException(DriverError):
    """元素失效异常（Mock 世界模拟 Appium 的 StaleObjectException）。

    类名与 Appium/Selenium 的 StaleElementReferenceException 家族保持一致，
    便于 stale_guard.is_stale_element_error 按类名识别后触发重定位重试。
    """


class ElementStaleRetryExhausted(DriverError):
    """元素操作连续失效、重新定位并重试耗尽。"""


class MockElement:
    def __init__(self, locator_value: str, attrs: dict | None = None, generation: int = 0) -> None:
        self.locator_value = locator_value
        self._attributes = dict(attrs or {})
        self.text = str(self._attributes.get("text", ""))
        self._generation = generation
        self.children: list[MockElement] = []
        self.parent: MockElement | None = None
        # 节点几何信息（bounds/rect 由 _build_mock_node 从快照字段提取）
        self._bounds: dict[str, int] | None = None

    def get_attribute(self, name: str) -> str:
        return str(self._attributes.get(name, ""))

    def click(self) -> None:
        pass

    def clear(self) -> None:
        pass


class BaseDriver:
    """驱动抽象：MockDriver 与 AppiumDriver 共用接口。"""

    def attach_to_current_app(self) -> None:
        """建立会话但不启动应用，直接操作设备当前前台界面。"""
        raise NotImplementedError

    def launch_app(self, package: str, activity: str | None = None, no_reset: bool = True) -> None:
        raise NotImplementedError

    def close_app(self, package: str | None = None) -> None:
        raise NotImplementedError

    def find_element(self, locator_type: str, locator_value: str, wait_timeout: int = 10):
        raise NotImplementedError

    def get_parent_element(self, element, wait_timeout: float | None = 10):
        """返回元素的直接父节点。

        父节点不是元素库中的定位对象，因此每次需要视口时都应通过此方法
        从当前轮次重新定位的 container 获取，不能跨页面重绘缓存句柄。
        """
        raise NotImplementedError

    def find_elements(self, locator_type: str, locator_value: str, wait_timeout: int = 10):
        """多匹配查询（智能定位用）：返回当前页面全部匹配元素。

        与 find_element 的单元素等待不同，本方法为快照式多匹配查询，
        滚动/重试节奏由智能定位解析器自行控制。
        """
        raise NotImplementedError

    def find_elements_in_element(
        self, element, locator_type: str, locator_value: str, wait_timeout: int = 0
    ):
        """在指定元素后代中查找匹配节点，不扩大到整个页面。"""
        raise NotImplementedError

    def click(self, element) -> None:
        raise NotImplementedError

    def input(self, element, value: str, clear_first: bool = True) -> None:
        raise NotImplementedError

    def clear(self, element) -> None:
        raise NotImplementedError

    def get_text(self, element) -> str:
        raise NotImplementedError

    def get_attribute(self, element, attribute: str) -> str:
        raise NotImplementedError

    def is_checked(self, element) -> bool:
        """返回复选框当前是否处于勾选状态。"""
        raise NotImplementedError

    def swipe(self, direction: str, duration: int = 500) -> None:
        raise NotImplementedError

    def swipe_in_element(
        self, element, direction: str, percent: float, speed: int | None = None
    ) -> None:
        """在指定原生控件可滚动范围内执行滑动。"""
        raise NotImplementedError

    def swipe_in_region(
        self,
        left: int,
        top: int,
        width: int,
        height: int,
        direction: str,
        percent: float,
        speed: int | None = None,
    ) -> None:
        """在屏幕像素区域内执行滑动。"""
        raise NotImplementedError

    def get_window_size(self) -> dict[str, int]:
        """返回当前可操作窗口的像素尺寸。"""
        raise NotImplementedError

    def scroll_to(self, element) -> None:
        raise NotImplementedError

    def scroll_in_element(self, element, direction: str, percent: float) -> bool:
        """在元素边界内滚动，返回当前方向是否还能继续滚动。

        Appium mock 实现为 UiAutomator2 mobile: scrollGesture 的结果；
        Mock 走确定性回退（可编程 scroll_can_continue）。
        """
        raise NotImplementedError

    def get_element_rect(self, element) -> dict[str, int]:
        """返回元素矩形 x、y、width、height（像素）。"""
        raise NotImplementedError

    def back(self) -> None:
        raise NotImplementedError

    def tap_coordinate(self, x: int, y: int) -> None:
        raise NotImplementedError

    def drag_coordinate(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration_ms: int
    ) -> None:
        """从指定屏幕坐标拖动到目标坐标。"""
        raise NotImplementedError

    def screenshot(self, path: str) -> None:
        raise NotImplementedError

    def page_signature(self) -> str:
        """当前页面指纹（智能定位滚动循环去重用，禁止打印完整 page_source）。"""
        raise NotImplementedError

    def quit(self) -> None:
        raise NotImplementedError

    def interrupt(self) -> None:
        """CR-06：stop_test 时尝试终止阻塞中的操作（Appium 终止 app 会话；Mock 无操作）。"""
        pass

    def set_command_timeout(self, timeout: float | None) -> None:
        """临时设置驱动 HTTP 命令超时；不支持的驱动实现为空操作。"""
        _ = timeout

    def run_with_http_timeout(self, remaining: float | None, operation, *, immediate: bool = False):
        """在指定的 HTTP 超时策略下执行一次驱动请求。"""
        _ = remaining
        _ = immediate
        return operation()


class MockDriver(BaseDriver):
    """确定性模拟驱动：以 locator_value 为 key 维护应用状态，供本地联调/测试。

    智能定位仿真（Mock 世界，可编程）：
    - set_screen(elements)：放置当前屏幕节点树（嵌套 children），generation+1 使旧元素失效；
    - find_elements：按 Strategy U/X 语义匹配当前屏幕节点（xpath / uiautomator 选择器）；
    - set_scroll_callback(cb)：每次 swipe 调用 cb(swipe_count)，返回新屏幕列表则替换；
    - refresh(elements=None)：模拟页面刷新，旧元素全部失效；
    - page_signature()：当前屏幕内容指纹（sha256 前 16 位 + 节点数）。
    """

    def __init__(self, initial_state: dict | None = None) -> None:
        self.state: dict[str, str] = dict(initial_state or {})
        self.screenshots: list[str] = []
        self.launched = False
        self._screen_roots: list[MockElement] = []
        self._screen_snapshot: list[dict] = []
        self._screen_generation = 0
        self._next_node_id = 0
        self._scroll_callback = None
        self.swipes: list[tuple[str, int]] = []
        self.element_swipes: list[tuple] = []
        self.region_swipes: list[tuple] = []
        self.element_swipe_speeds: list[int | None] = []
        self.region_swipe_speeds: list[int | None] = []
        self.coordinate_drags: list[tuple[int, int, int, int, int]] = []
        self.coordinate_taps: list[tuple[int, int]] = []
        self.element_scrolls: list[tuple[str, str, float, bool]] = []
        self.scroll_can_continue = True
        self.swipe_count = 0

    def attach_to_current_app(self) -> None:
        self.launched = True

    def launch_app(self, package: str, activity: str | None = None, no_reset: bool = True) -> None:
        self.launched = True

    def close_app(self, package: str | None = None) -> None:
        self.launched = False

    def find_element(self, locator_type: str, locator_value: str, wait_timeout: int = 10):
        # 携带当前屏幕 generation：set_screen 之后普通定位的元素与智能定位一致受 stale 校验，
        # 避免「set_screen 后普通定位元素恒 stale」的潜伏陷阱（未 set_screen 时 generation=0）。
        if locator_type == "id":
            for node in _flatten_mock_nodes(self):
                if node.locator_value == locator_value:
                    return node
        return MockElement(locator_value, generation=self._screen_generation)

    def get_parent_element(self, element, wait_timeout: float | None = 10):
        """返回 MockElement 的直接父节点，模拟 Appium 相对 XPath 查询。"""
        _ = wait_timeout
        self._assert_fresh(element)
        parent = getattr(element, "parent", None)
        if parent is None:
            raise ElementNotFound(
                f"元素 {getattr(element, 'locator_value', '<unknown>')} 没有直接父节点"
            )
        return parent

    def find_elements(self, locator_type: str, locator_value: str, wait_timeout: int = 10):
        """多匹配查询：按 Strategy U/X 语义匹配当前屏幕节点树。"""
        if locator_type not in ("xpath", "uiautomator"):
            return []
        return _mock_find_elements(self, locator_type, locator_value)

    def find_elements_in_element(
        self, element, locator_type: str, locator_value: str, wait_timeout: int = 0
    ):
        self._assert_fresh(element)
        descendants = _all_descendants(element)
        if not descendants and element not in _flatten_mock_nodes(self):
            # 兼容旧测试/旧 mock：未把容器放入屏幕树时无法建立父子关系。
            return _mock_find_elements(self, locator_type, locator_value)
        return _mock_find_elements(self, locator_type, locator_value, roots=descendants)

    def set_screen(self, elements: list[dict]) -> None:
        """放置当前屏幕节点；generation+1 使此前返回的元素全部失效。"""
        self._screen_generation += 1
        self._screen_snapshot = [dict(item) for item in (elements or [])]
        self._screen_roots = [
            self._build_mock_node(item, self._screen_generation) for item in self._screen_snapshot
        ]

    def refresh(self, elements: list[dict] | None = None) -> None:
        """模拟页面刷新：不传 elements 仅失效旧元素并重建节点；传入则同时替换屏幕。"""
        if elements is None:
            self._screen_generation += 1
            self._screen_roots = [
                self._build_mock_node(item, self._screen_generation) for item in self._screen_snapshot
            ]
        else:
            self.set_screen(elements)

    def set_scroll_callback(self, callback) -> None:
        """注册滚动回调：每次 swipe 后调用 callback(swipe_count)（返回新屏幕列表则替换屏幕）。"""
        self._scroll_callback = callback

    def _build_mock_node(self, item: dict, generation: int) -> MockElement:
        attrs = {key: value for key, value in item.items() if key not in ("children", "id", "bounds", "rect")}
        node_id = str(item.get("id") or f"mock-node-{self._next_node_id}")
        if "id" not in item:
            self._next_node_id += 1
        node = MockElement(node_id, attrs, generation)
        bounds = item.get("bounds") or item.get("rect")
        if isinstance(bounds, dict):
            node._bounds = {
                key: int(bounds[key])
                for key in ("x", "y", "width", "height")
                if key in bounds
            }
        for child_item in item.get("children") or []:
            child = self._build_mock_node(child_item, generation)
            child.parent = node
            node.children.append(child)
        return node

    def _assert_fresh(self, element) -> None:
        """Mock 世界元素失效检查：refresh 后旧元素任何操作抛 StaleObjectException。"""
        generation = getattr(element, "_generation", None)
        if generation is not None and generation != self._screen_generation:
            raise StaleObjectException(
                f"元素已失效（页面 generation={self._screen_generation}，元素 generation={generation}）"
            )

    def click(self, element) -> None:
        self._assert_fresh(element)
        if "checked" in element._attributes:
            element._attributes["checked"] = not _coerce_bool(element._attributes["checked"])
        elif "selected" in element._attributes:
            # Mock 中没有 Android 原生控件行为，selected 作为 checked 的兼容状态。
            element._attributes["selected"] = not _coerce_bool(element._attributes["selected"])

    def input(self, element, value: str, clear_first: bool = True) -> None:
        self._assert_fresh(element)
        self.state[element.locator_value] = value

    def clear(self, element) -> None:
        self._assert_fresh(element)
        self.state[element.locator_value] = ""

    def get_text(self, element) -> str:
        self._assert_fresh(element)
        return self.state.get(element.locator_value, element.text or "")

    def get_attribute(self, element, attribute: str) -> str:
        self._assert_fresh(element)
        return element.get_attribute(attribute)

    def is_checked(self, element) -> bool:
        self._assert_fresh(element)
        if "checked" in element._attributes:
            return _coerce_bool(element._attributes["checked"])
        return _coerce_bool(element._attributes.get("selected", False))

    def swipe(self, direction: str, duration: int = 500) -> None:
        self.swipes.append((direction, duration))
        self.swipe_count += 1
        if self._scroll_callback is not None:
            new_screen = self._scroll_callback(self.swipe_count)
            if new_screen is not None:
                self.set_screen(new_screen)

    def swipe_in_element(
        self, element, direction: str, percent: float, speed: int | None = None
    ) -> None:
        self._assert_fresh(element)
        swipe = (element.locator_value, direction, percent)
        self.element_swipes.append(swipe)
        self.element_swipe_speeds.append(speed)
        self.swipe(direction)

    def swipe_in_region(
        self,
        left: int,
        top: int,
        width: int,
        height: int,
        direction: str,
        percent: float,
        speed: int | None = None,
    ) -> None:
        swipe = (left, top, width, height, direction, percent)
        self.region_swipes.append(swipe)
        self.region_swipe_speeds.append(speed)
        self.swipe(direction)

    def get_window_size(self) -> dict[str, int]:
        # 固定尺寸使区域百分比换算在单元测试中可断言。
        return {"width": 1000, "height": 2000}

    def scroll_to(self, element) -> None:
        pass

    def scroll_in_element(self, element, direction: str, percent: float) -> bool:
        self._assert_fresh(element)
        result = self.scroll_can_continue
        self.element_scrolls.append((element.locator_value, direction, percent, result))
        self.swipe(direction)
        return result

    def get_element_rect(self, element) -> dict[str, int]:
        self._assert_fresh(element)
        if element._bounds is not None:
            return dict(element._bounds)
        # 未声明几何信息时回退到窗口尺寸的容器默认矩形，保证可确定性断言。
        size = self.get_window_size()
        return {"x": 0, "y": 0, "width": size["width"], "height": size["height"]}

    def back(self) -> None:
        pass

    def tap_coordinate(self, x: int, y: int) -> None:
        self.coordinate_taps.append((x, y))

    def drag_coordinate(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration_ms: int
    ) -> None:
        self.coordinate_drags.append((start_x, start_y, end_x, end_y, duration_ms))

    def screenshot(self, path: str) -> None:
        self.screenshots.append(path)

    def page_signature(self) -> str:
        """当前屏幕指纹：全部节点属性摘要的 sha256 前 16 位 + 节点数。"""
        nodes = _flatten_mock_nodes(self)
        digest = hashlib.sha256()
        for node in nodes:
            digest.update(repr(sorted(node._attributes.items())).encode("utf-8"))
        return f"{digest.hexdigest()[:16]}:{len(nodes)}"

    def quit(self) -> None:
        pass


# ---------- Mock 世界：智能定位选择器解析与匹配 ----------
# 解析 smart_locator 生成的受限 XPath / UiAutomator 链，按属性语义匹配屏幕节点。
# 真实语义由 Appium 执行；此处仅为可编程的确定性仿真。

_XPATH_PRED_EQUALS = re.compile(r"^@([\w-]+)=(.*)$")
_XPATH_PRED_CONTAINS = re.compile(r"^contains\(@([\w-]+),\s*(.*)\)$")
_XPATH_PRED_STARTS_WITH = re.compile(r"^starts-with\(@([\w-]+),\s*(.*)\)$")
_XPATH_PRED_ENDS_WITH = re.compile(
    r"^substring\(@([\w-]+),\s*string-length\(@[\w-]+\)-string-length\((.*)\)\+1\)=(.*)$"
)

_UISELECTOR_PREFIX = "new UiSelector()"

_UIS_METHOD_MAP = {
    "text": ("text", "equals"),
    "textContains": ("text", "contains"),
    "textStartsWith": ("text", "starts_with"),
    "textMatches": ("text", "regex"),
    "description": ("content_desc", "equals"),
    "descriptionContains": ("content_desc", "contains"),
    "descriptionStartsWith": ("content_desc", "starts_with"),
    "descriptionMatches": ("content_desc", "regex"),
    "resourceId": ("resource_id", "equals"),
    "resourceIdMatches": ("resource_id", "regex"),
    "className": ("class_name", "equals"),
    "classNameMatches": ("class_name", "regex"),
    "packageName": ("package", "equals"),
    "packageNameMatches": ("package", "regex"),
    "clickable": ("clickable", "equals"),
    "enabled": ("enabled", "equals"),
    "selected": ("selected", "equals"),
}

_MOCK_BOOL_ATTRIBUTES = frozenset({"clickable", "enabled", "selected", "checked", "displayed"})

# XPath 属性名 → Mock 节点属性键（content-desc/resource-id/class 与快照字段名的映射）
_XPATH_ATTR_TO_NODE = {
    "text": "text",
    "content-desc": "content_desc",
    "resource-id": "resource_id",
    "class": "class_name",
    "package": "package",
    "clickable": "clickable",
    "enabled": "enabled",
    "selected": "selected",
    "checked": "checked",
    "displayed": "displayed",
}


def _flatten_mock_nodes(driver) -> list[MockElement]:
    nodes: list[MockElement] = []

    def walk(node: MockElement) -> None:
        nodes.append(node)
        for child in node.children:
            walk(child)

    for root in driver._screen_roots:
        walk(root)
    return nodes


def _all_descendants(node: MockElement) -> list[MockElement]:
    out: list[MockElement] = []

    def walk(n: MockElement) -> None:
        for child in n.children:
            out.append(child)
            walk(child)

    walk(node)
    return out


def _dedupe_nodes(nodes: list[MockElement]) -> list[MockElement]:
    seen: set[int] = set()
    out: list[MockElement] = []
    for node in nodes:
        if id(node) not in seen:
            seen.add(id(node))
            out.append(node)
    return out


def _axis_nodes(node: MockElement, axis: str, depth: int | None) -> list[MockElement]:
    if axis in ("parent", "ancestor"):
        # parent 与 ancestor 统一为「向上第 depth 级祖先」（parent::*[1] 即 ancestor::*[1]）
        current = node
        for _ in range(depth or 1):
            current = current.parent
            if current is None:
                return []
        return [current]
    if axis == "child":
        return list(node.children)
    if axis == "descendant":
        return _all_descendants(node)
    if axis == "following_sibling":
        if node.parent is None:
            return []
        siblings = node.parent.children
        try:
            idx = siblings.index(node)
        except ValueError:
            return []
        return siblings[idx + 1 :]
    if axis == "preceding_sibling":
        if node.parent is None:
            return []
        siblings = node.parent.children
        try:
            idx = siblings.index(node)
        except ValueError:
            return []
        return siblings[:idx]
    return []


def _match_bracketed(
    text: str,
    start_idx: int = 0,
    open_ch: str = "[",
    close_ch: str = "]",
    java_escape: bool = False,
) -> tuple[str, int]:
    """从 text[start_idx] 匹配成对括号，返回 (内部内容, 结束索引)。引号内忽略括号。"""
    depth = 0
    in_str: str | None = None
    i = start_idx
    content_start = 0
    while i < len(text):
        ch = text[i]
        if in_str is not None:
            if java_escape and ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        else:
            if ch in "\"'":
                in_str = ch
            elif ch == open_ch:
                depth += 1
                if depth == 1:
                    content_start = i + 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return text[content_start:i], i + 1
        i += 1
    raise ValueError(f"未匹配的括号: {text[start_idx:]!r}")


def _split_top_level(text: str, sep: str) -> list[str]:
    """按 sep 在括号深度 0（且不在引号内）处切分。"""
    parts: list[str] = []
    depth = 0
    in_str: str | None = None
    buf: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if in_str is not None:
            buf.append(ch)
            if ch == in_str:
                in_str = None
        elif ch in "\"'":
            in_str = ch
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif depth == 0 and text.startswith(sep, i):
            parts.append("".join(buf).strip())
            buf = []
            i += len(sep)
            continue
        else:
            buf.append(ch)
        i += 1
    if buf:
        parts.append("".join(buf).strip())
    return [part for part in parts if part]


def _eval_xpath_literal(text: str) -> str:
    """解析 `"..."` / `'...'` / `concat(...)` 形式的 XPath 字面量。"""
    text = text.strip()
    if text.startswith("concat(") and text.endswith(")"):
        inner = text[len("concat(") : -1]
        return "".join(_eval_xpath_literal(part) for part in _split_top_level(inner, ","))
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1]
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1]
    raise ValueError(f"无法解析 XPath 字面量: {text!r}")


def _decode_xpath_predicate(pred: str) -> dict:
    """把生成器产出的受限 XPath 谓词反解为条件。"""
    pred = pred.strip()
    match = _XPATH_PRED_ENDS_WITH.match(pred)
    if match:
        return {
            "attribute": _XPATH_ATTR_TO_NODE.get(match.group(1), match.group(1)),
            "operator": "ends_with",
            "value": _eval_xpath_literal(match.group(3)),
        }
    match = _XPATH_PRED_CONTAINS.match(pred)
    if match:
        return {
            "attribute": _XPATH_ATTR_TO_NODE.get(match.group(1), match.group(1)),
            "operator": "contains",
            "value": _eval_xpath_literal(match.group(2)),
        }
    match = _XPATH_PRED_STARTS_WITH.match(pred)
    if match:
        return {
            "attribute": _XPATH_ATTR_TO_NODE.get(match.group(1), match.group(1)),
            "operator": "starts_with",
            "value": _eval_xpath_literal(match.group(2)),
        }
    match = _XPATH_PRED_EQUALS.match(pred)
    if match:
        attribute = _XPATH_ATTR_TO_NODE.get(match.group(1), match.group(1))
        value = _eval_xpath_literal(match.group(2))
        if attribute in _MOCK_BOOL_ATTRIBUTES:
            return {"attribute": attribute, "operator": "equals", "value": value == "true"}
        return {"attribute": attribute, "operator": "equals", "value": value}
    raise ValueError(f"无法解析 XPath 谓词: {pred!r}")


def _parse_xpath_query(value: str) -> tuple[list[dict], list[tuple[str, int | None]], list[dict]]:
    """解析 smart_locator 生成的受限 XPath，返回 (root_preds, steps, target_preds)。

    steps 为 (axis, depth) 列表；root_preds 为锚点/根条件，target_preds 为末端条件。
    """
    if not value.startswith("//*"):
        raise ValueError(f"不支持的 XPath: {value!r}")
    rest = value[3:]
    root_text, end = _match_bracketed(rest)
    rest = rest[end:]
    root_preds = [_decode_xpath_predicate(p) for p in _split_top_level(root_text, " and ")]
    steps: list[tuple[str, int | None]] = []
    while rest.startswith("/"):
        axis_match = re.match(r"/([a-z-]+)::\*(\[\d+\])?", rest)
        if not axis_match:
            raise ValueError(f"不支持的 XPath 轴段: {rest!r}")
        axis = axis_match.group(1)
        depth = int(axis_match.group(2)[1:-1]) if axis_match.group(2) else None
        steps.append((axis, depth))
        rest = rest[axis_match.end() :]
    target_preds: list[dict] = []
    if rest.startswith("["):
        target_text, end = _match_bracketed(rest)
        rest = rest[end:]
        target_preds = [_decode_xpath_predicate(p) for p in _split_top_level(target_text, " and ")]
    if rest:
        raise ValueError(f"XPath 尾部多余内容: {rest!r}")
    return root_preds, steps, target_preds


def _java_unescape(text: str) -> str:
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _parse_uiautomator_query(value: str) -> list[dict]:
    """解析 `new UiSelector().method(args)...` 链为条件列表。"""
    if not value.startswith(_UISELECTOR_PREFIX):
        raise ValueError(f"不支持的 UiAutomator 选择器: {value!r}")
    rest = value[len(_UISELECTOR_PREFIX) :]
    conditions: list[dict] = []
    i = 0
    while i < len(rest):
        if rest[i] != ".":
            raise ValueError(f"UiAutomator 链格式错误: {value!r}")
        open_idx = rest.index("(", i)
        method = rest[i + 1 : open_idx]
        arg_text, end = _match_bracketed(rest, open_idx, "(", ")", java_escape=True)
        attr_op = _UIS_METHOD_MAP.get(method)
        if attr_op is None:
            raise ValueError(f"未知 UiAutomator 方法: {method!r}")
        attribute, operator = attr_op
        arg = arg_text.strip()
        if attribute in _MOCK_BOOL_ATTRIBUTES:
            conditions.append(
                {"attribute": attribute, "operator": operator, "value": arg.lower() == "true"}
            )
        else:
            conditions.append({"attribute": attribute, "operator": operator, "value": _java_unescape(arg)})
        i = end
    return conditions


def _mock_node_value(node: MockElement, attribute: str) -> str:
    raw = node._attributes.get(attribute, False)
    if attribute in _MOCK_BOOL_ATTRIBUTES:
        if isinstance(raw, str):
            return raw
        return "true" if raw else "false"
    return str(raw)


def _mock_condition_matches(node: MockElement, cond: dict) -> bool:
    attribute = cond["attribute"]
    operator = cond["operator"]
    value = cond["value"]
    node_value = _mock_node_value(node, attribute)
    if attribute in _MOCK_BOOL_ATTRIBUTES:
        return (str(node_value).lower() == "true") == bool(value)
    if operator == "equals":
        return node_value == value
    if operator == "contains":
        return value in node_value
    if operator == "starts_with":
        return node_value.startswith(value)
    if operator == "ends_with":
        return node_value.endswith(value)
    if operator == "regex":
        try:
            return re.search(value, node_value) is not None
        except re.error:
            return False
    return False


def _mock_find_elements(
    driver, locator_type: str, locator_value: str, roots: list[MockElement] | None = None
) -> list[MockElement]:
    nodes = _flatten_mock_nodes(driver) if roots is None else roots
    if not nodes:
        return []
    if locator_type == "xpath":
        root_preds, steps, target_preds = _parse_xpath_query(locator_value)
        matches = [n for n in nodes if all(_mock_condition_matches(n, p) for p in root_preds)]
        for axis, depth in steps:
            expanded: list[MockElement] = []
            for node in matches:
                expanded.extend(_axis_nodes(node, axis, depth))
            matches = expanded
        if target_preds:
            matches = [n for n in matches if all(_mock_condition_matches(n, p) for p in target_preds)]
        return _dedupe_nodes(matches)
    if locator_type == "uiautomator":
        conditions = _parse_uiautomator_query(locator_value)
        return [n for n in nodes if all(_mock_condition_matches(n, c) for c in conditions)]
    return []


def create_driver(mode: str, config: dict | None = None, device: dict | None = None, initial_state: dict | None = None):
    if mode == "appium":
        from .appium_driver import AppiumDriver

        config = config or {}
        return AppiumDriver(
            host=config.get("appium_host", "127.0.0.1"),
            port=int(config.get("appium_port", 4723)),
            capabilities=config.get("appium_capabilities") or {},
            device=device,
            command_timeout=config.get("appium_command_timeout"),
            http_request_timeout=config.get("appium_http_request_timeout"),
        )
    return MockDriver(initial_state)
