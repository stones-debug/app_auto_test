"""智能元素定位（Step 2）：配置校验、变量渲染、选择器生成与滚动查找。

定位策略：
- Strategy U（UiAutomator selector chain）：无 anchor/path 的普通 target（纯属性条件
  组合，可含 regex），生成 `new UiSelector()...` 链，经 Appium 的 ANDROID_UIAUTOMATOR
  执行。
- Strategy X（XPath）：组合条件（含 UiAutomator 无法表达的场景，如 displayed）、
  anchor+path 相对定位、以及 ends_with 操作符。

显式支持限制（与后端 Pydantic 口径一致，Agent 侧自校验防篡改）：
- regex 条件只允许出现在普通 target（Strategy U，UiAutomator 的 textMatches 等）；
  出现在 anchor/path 相对定位（XPath 无法表达 regex）→ INVALID_SMART_LOCATOR。
- ends_with 与 regex 不能组合（UiAutomator 无 endsWith 方法，XPath 无法表达 regex）。
- displayed 布尔属性 UiAutomator 无对应选择器方法，自动回退 Strategy X。
- resource_id/class_name/package 的 contains/starts_with 无对应 UiAutomator 方法，
  自动回退 Strategy X。

解析流程：自校验 → 渲染变量 → 渲染后重验 → 逐候选查询 → 滚动查找 → 选择策略；
滚动为「当前页全部候选依次检查 → 全未命中才滚动一页 → 新页重查全部候选」；
全程 60s 总超时（monotonic），每次查询/滚动前检查 context.should_stop()。
"""

import logging
import re
import time
from typing import Any

from .appium_driver import _xpath_literal
from .driver import DriverError, ElementNotFound, StopRequested

logger = logging.getLogger("agent.executor.smart_locator")


class SmartLocatorError(DriverError):
    """智能定位错误基类。"""


class InvalidSmartLocator(SmartLocatorError):
    """配置非法 / 不支持的条件组合 / 变量渲染失败。"""


class ElementNotUnique(SmartLocatorError):
    """匹配到多个元素但选择策略要求唯一。"""


class ScrollLimitReached(SmartLocatorError):
    """滚动查找耗尽（达到 max_swipes 或页面指纹连续两次不再变化）。"""


# ---------- 常量与校验 ----------

_ALLOWED_ATTRIBUTES = frozenset(
    {
        "text",
        "content_desc",
        "resource_id",
        "class_name",
        "package",
        "clickable",
        "enabled",
        "selected",
        "displayed",
    }
)
_ALLOWED_OPERATORS = frozenset({"equals", "contains", "starts_with", "ends_with", "regex"})
_BOOL_ATTRIBUTES = frozenset({"clickable", "enabled", "selected", "displayed"})

_MAX_ALTERNATIVES = 10
_MAX_CONDITIONS = 20
_MAX_VALUE_LENGTH = 200
_MAX_PATH_SEGMENTS = 3
_MAX_SWIPES = 20
_MIN_DURATION_MS = 100
_MAX_DURATION_MS = 2000
_MAX_SETTLE_MS = 2000

_AXES = frozenset(
    {"ancestor", "parent", "child", "descendant", "following_sibling", "preceding_sibling"}
)

_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _uiautomator_expressible(cond: dict) -> bool:
    """该条件是否可用 UiAutomator UiSelector 链表达（否则回退 Strategy X）。"""
    attribute = cond["attribute"]
    operator = cond["operator"]
    if attribute == "displayed":
        return False
    if attribute in ("text", "content_desc"):
        return operator != "ends_with"
    if attribute in ("resource_id", "class_name", "package"):
        return operator in ("equals", "regex")
    return operator == "equals"


def _validate_condition(cond: Any) -> dict:
    if not isinstance(cond, dict):
        raise InvalidSmartLocator("condition 必须是对象")
    attribute = cond.get("attribute")
    operator = cond.get("operator")
    if attribute not in _ALLOWED_ATTRIBUTES:
        raise InvalidSmartLocator(f"未知条件属性: {attribute!r}")
    if operator not in _ALLOWED_OPERATORS:
        raise InvalidSmartLocator(f"未知条件操作符: {operator!r}")
    value = cond.get("value")
    if attribute in _BOOL_ATTRIBUTES:
        if operator != "equals":
            raise InvalidSmartLocator(f"布尔属性 {attribute} 仅支持 equals 操作符")
        if not isinstance(value, bool):
            raise InvalidSmartLocator(f"布尔属性 {attribute} 的 value 必须是布尔值")
        return {"attribute": attribute, "operator": operator, "value": bool(value)}
    if isinstance(value, bool) or not isinstance(value, str):
        raise InvalidSmartLocator(f"字符串属性 {attribute} 的 value 必须是字符串")
    if len(value) < 1:
        raise InvalidSmartLocator(f"条件值不能为空字符串: {attribute}")
    if len(value) > _MAX_VALUE_LENGTH:
        raise InvalidSmartLocator(f"条件值超过 {_MAX_VALUE_LENGTH} 字符上限: {attribute}={value[:20]}...")
    if operator == "regex":
        try:
            re.compile(value)
        except re.error as exc:
            raise InvalidSmartLocator(f"非法正则表达式 {value!r}: {exc}") from exc
    return {"attribute": attribute, "operator": operator, "value": value}


def _validate_path_segment(seg: Any) -> dict:
    if not isinstance(seg, dict):
        raise InvalidSmartLocator("path 段必须是对象")
    axis = seg.get("axis")
    if axis not in _AXES:
        raise InvalidSmartLocator(f"未知路径轴: {axis!r}")
    depth = seg.get("depth")
    if axis in ("ancestor", "parent"):
        if not isinstance(depth, int) or isinstance(depth, bool) or not (1 <= depth <= 5):
            raise InvalidSmartLocator(f"轴 {axis} 必须携带 depth(1..5)，收到 {depth!r}")
        return {"axis": axis, "depth": int(depth)}
    if depth is not None:
        raise InvalidSmartLocator(f"轴 {axis} 不允许 depth（仅 ancestor/parent 需要）")
    return {"axis": axis, "depth": None}


def _validate_alternative(alt: Any) -> dict:
    if not isinstance(alt, dict):
        raise InvalidSmartLocator("alternative 必须是对象")
    target_raw = alt.get("target")
    if not isinstance(target_raw, list) or not target_raw:
        raise InvalidSmartLocator("alternative.target 必须是非空条件列表")
    if len(target_raw) > _MAX_CONDITIONS:
        raise InvalidSmartLocator(f"target 条件数超过上限 {_MAX_CONDITIONS}")
    target = [_validate_condition(cond) for cond in target_raw]

    anchor_raw = alt.get("anchor")
    anchor: list[dict] = []
    if anchor_raw is not None:
        if not isinstance(anchor_raw, list) or not (1 <= len(anchor_raw) <= _MAX_CONDITIONS):
            raise InvalidSmartLocator(f"anchor 条件数必须在 1..{_MAX_CONDITIONS}")
        anchor = [_validate_condition(cond) for cond in anchor_raw]

    path_raw = alt.get("path")
    path: list[dict] = []
    if path_raw is not None:
        if not isinstance(path_raw, list) or not (1 <= len(path_raw) <= _MAX_PATH_SEGMENTS):
            raise InvalidSmartLocator(f"path 段数必须在 1..{_MAX_PATH_SEGMENTS}")
        if not anchor:
            raise InvalidSmartLocator("path 相对定位必须同时提供 anchor")
        path = [_validate_path_segment(seg) for seg in path_raw]

    if anchor or path:
        # 显式支持限制：regex 无法在 XPath 中表达，仅普通 target 的 Strategy U 可用
        for cond in [*target, *anchor]:
            if cond["operator"] == "regex":
                raise InvalidSmartLocator(
                    "anchor/path 相对定位不支持 regex 条件（XPath 无法表达 regex，"
                    "regex 仅支持普通 target 的 Strategy U）"
                )
    else:
        # 普通 target：regex 需走 Strategy U，不能与需 XPath 表达的条件共存
        has_regex = any(cond["operator"] == "regex" for cond in target)
        needs_xpath = any(
            cond["operator"] == "ends_with" or not _uiautomator_expressible(cond) for cond in target
        )
        if has_regex and needs_xpath:
            raise InvalidSmartLocator(
                "regex 条件不能与 ends_with/displayed 等需 XPath 表达的条件组合"
            )
    return {"anchor": anchor, "path": path, "target": target}


def _validate_search(search: Any) -> dict:
    if not isinstance(search, dict):
        raise InvalidSmartLocator("search 必须是对象")
    scroll = search.get("scroll", True)
    if not isinstance(scroll, bool):
        raise InvalidSmartLocator("search.scroll 必须是布尔值")
    direction = search.get("direction", "up")
    if direction not in {"up", "down"}:
        raise InvalidSmartLocator(f"search.direction 非法: {direction!r}（仅支持 up/down）")
    max_swipes = search.get("max_swipes", 8)
    if isinstance(max_swipes, bool) or not isinstance(max_swipes, int):
        raise InvalidSmartLocator("search.max_swipes 必须是整数")
    if not (1 <= max_swipes <= _MAX_SWIPES):
        raise InvalidSmartLocator(f"search.max_swipes 必须在 1..{_MAX_SWIPES}")
    duration_ms = search.get("duration_ms", 500)
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int):
        raise InvalidSmartLocator("search.duration_ms 必须是整数")
    if not (_MIN_DURATION_MS <= duration_ms <= _MAX_DURATION_MS):
        raise InvalidSmartLocator(f"search.duration_ms 必须在 {_MIN_DURATION_MS}..{_MAX_DURATION_MS}ms")
    settle_ms = search.get("settle_ms", 300)
    if isinstance(settle_ms, bool) or not isinstance(settle_ms, int):
        raise InvalidSmartLocator("search.settle_ms 必须是整数")
    if not (0 <= settle_ms <= _MAX_SETTLE_MS):
        raise InvalidSmartLocator(f"search.settle_ms 必须在 0..{_MAX_SETTLE_MS}ms")
    return {
        "scroll": scroll,
        "direction": direction,
        "max_swipes": int(max_swipes),
        "duration_ms": int(duration_ms),
        "settle_ms": int(settle_ms),
    }


def _validate_selection(selection: Any) -> dict:
    if not isinstance(selection, dict):
        raise InvalidSmartLocator("selection 必须是对象")
    policy = selection.get("policy", "unique")
    if policy not in {"unique", "index"}:
        raise InvalidSmartLocator(f"selection.policy 非法: {policy!r}")
    index = selection.get("index")
    if policy == "unique":
        if index is not None:
            raise InvalidSmartLocator("selection.policy=unique 时不允许提供 index")
        return {"policy": "unique", "index": None}
    # index 为 1 基序号（第 1 个候选 = index=1），与后端契约一致
    if isinstance(index, bool) or not isinstance(index, int) or index < 1:
        raise InvalidSmartLocator("selection.index 必须是 ≥1 的整数（1 基序号）")
    return {"policy": "index", "index": int(index)}


def validate_config(config: dict) -> dict:
    """校验 locator_config 结构与上限（白名单），返回规范化配置；非法抛 InvalidSmartLocator。"""
    if not isinstance(config, dict):
        raise InvalidSmartLocator("locator_config 必须是对象")
    version = config.get("version")
    if version != 1:
        raise InvalidSmartLocator(f"不支持的 locator_config 版本: {version!r}（仅支持 1）")
    alternatives_raw = config.get("alternatives")
    if not isinstance(alternatives_raw, list) or not (1 <= len(alternatives_raw) <= _MAX_ALTERNATIVES):
        raise InvalidSmartLocator(f"alternatives 必须是 1..{_MAX_ALTERNATIVES} 个候选")
    alternatives = [_validate_alternative(alt) for alt in alternatives_raw]
    search = _validate_search(config.get("search") or {})
    selection = _validate_selection(config.get("selection") or {})
    return {
        "version": 1,
        "alternatives": alternatives,
        "search": search,
        "selection": selection,
    }


# ---------- 变量渲染 ----------


def _render_value(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _render_str(value, variables)
    if isinstance(value, list):
        return [_render_value(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _render_value(item, variables) for key, item in value.items()}
    return value


def _render_str(text: str, variables: dict[str, str]) -> str:
    def repl(match: re.Match) -> str:
        name = match.group(1)
        if name not in variables:
            raise ValueError(f"未定义变量: ${{{name}}}")
        return str(variables[name])

    return _VAR_RE.sub(repl, text)


def render_config(config: dict, variables: dict[str, str]) -> dict:
    """渲染 ${variable} 后重新校验，返回规范化配置；任何失败抛 InvalidSmartLocator。

    渲染失败语义：未定义变量 / 渲染后值超长 / 渲染后 regex 编译失败 / 布尔类型错误。
    """
    try:
        rendered = _render_value(config, dict(variables or {}))
    except ValueError as exc:
        raise InvalidSmartLocator(f"智能定位变量渲染失败: {exc}") from exc
    return validate_config(rendered)


# ---------- 选择器生成 ----------

_UIAUTOMATOR_METHODS = {
    ("text", "equals"): "text",
    ("text", "contains"): "textContains",
    ("text", "starts_with"): "textStartsWith",
    ("text", "regex"): "textMatches",
    ("content_desc", "equals"): "description",
    ("content_desc", "contains"): "descriptionContains",
    ("content_desc", "starts_with"): "descriptionStartsWith",
    ("content_desc", "regex"): "descriptionMatches",
    ("resource_id", "equals"): "resourceId",
    ("resource_id", "regex"): "resourceIdMatches",
    ("class_name", "equals"): "className",
    ("class_name", "regex"): "classNameMatches",
    ("package", "equals"): "packageName",
    ("package", "regex"): "packageNameMatches",
    ("clickable", "equals"): "clickable",
    ("enabled", "equals"): "enabled",
    ("selected", "equals"): "selected",
}

_XPATH_ATTRIBUTE_MAP = {
    "text": "text",
    "content_desc": "content-desc",
    "resource_id": "resource-id",
    "class_name": "class",
    "package": "package",
    "clickable": "clickable",
    "enabled": "enabled",
    "selected": "selected",
    "displayed": "displayed",
}

_AXIS_XPATH = {
    "parent": "parent",
    "ancestor": "ancestor",
    "child": "child",
    "descendant": "descendant",
    "following_sibling": "following-sibling",
    "preceding_sibling": "preceding-sibling",
}


def _java_string_escape(value: str) -> str:
    """Java 字符串字面量转义：转义 \\ 与单双引号，禁止裸引号逃逸（防注入）。"""
    return value.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')


def _build_uiautomator(conditions: list[dict]) -> str:
    parts = ["new UiSelector()"]
    for cond in conditions:
        method = _UIAUTOMATOR_METHODS.get((cond["attribute"], cond["operator"]))
        if method is None:
            raise InvalidSmartLocator(
                f"UiAutomator 无法表达的条件: {cond['attribute']} {cond['operator']}"
            )
        if cond["attribute"] in _BOOL_ATTRIBUTES:
            parts.append(f".{method}({str(cond['value']).lower()})")
        else:
            parts.append(f'.{method}("{_java_string_escape(cond["value"])}")')
    return "".join(parts)


def _xpath_predicate(cond: dict) -> str:
    xattr = f"@{_XPATH_ATTRIBUTE_MAP[cond['attribute']]}"
    value = cond["value"]
    if cond["attribute"] in _BOOL_ATTRIBUTES:
        lit = "true" if value else "false"
        return f"{xattr}='{lit}'"
    lit = _xpath_literal(value)
    operator = cond["operator"]
    if operator == "equals":
        return f"{xattr}={lit}"
    if operator == "contains":
        return f"contains({xattr}, {lit})"
    if operator == "starts_with":
        return f"starts-with({xattr}, {lit})"
    if operator == "ends_with":
        return f"substring({xattr}, string-length({xattr})-string-length({lit})+1)={lit}"
    raise InvalidSmartLocator(f"XPath 无法表达的操作符: {operator!r}（regex 需走 Strategy U）")


def _xpath_axis_segment(seg: dict) -> str:
    axis = _AXIS_XPATH[seg["axis"]]
    depth = seg.get("depth")
    if seg["axis"] == "ancestor":
        return f"/{axis}::*[{depth}]"
    if seg["axis"] == "parent":
        # parent::*[N] 在 XPath 1.0 中 N>1 恒空（父轴至多一个节点）；
        # depth≥2 语义等同「第 N 级祖先」，转译为 ancestor::*[N]（与 Mock _axis_nodes 一致）。
        if depth and depth >= 2:
            return f"/ancestor::*[{depth}]"
        return f"/parent::*[{depth}]" if depth else "/parent::*"
    return f"/{axis}::*"


def _build_xpath(conditions: list[dict]) -> str:
    preds = [_xpath_predicate(cond) for cond in conditions]
    return f"//*[{' and '.join(preds)}]"


def _build_relative_xpath(alt: dict) -> str:
    anchor_preds = [_xpath_predicate(cond) for cond in alt["anchor"]]
    xpath = f"//*[{' and '.join(anchor_preds)}]"
    for seg in alt["path"]:
        xpath += _xpath_axis_segment(seg)
    if alt["target"]:
        target_preds = [_xpath_predicate(cond) for cond in alt["target"]]
        xpath += f"[{' and '.join(target_preds)}]"
    return xpath


def build_selector(alt: dict) -> tuple[str, str]:
    """生成 (locator_type, locator_value)；locator_type ∈ {"xpath", "uiautomator"}。"""
    if alt["anchor"] or alt["path"]:
        return ("xpath", _build_relative_xpath(alt))
    conditions = alt["target"]
    if any(cond["operator"] == "regex" for cond in conditions):
        return ("uiautomator", _build_uiautomator(conditions))
    if any(cond["operator"] == "ends_with" or not _uiautomator_expressible(cond) for cond in conditions):
        return ("xpath", _build_xpath(conditions))
    return ("uiautomator", _build_uiautomator(conditions))


# ---------- 解析器 ----------


class SmartElementResolver:
    """智能定位解析器：校验 → 渲染 → 当前页候选轮询 → 滚动重查 → 选择策略。

    全程 60s 总超时（monotonic 时钟）；每次查询/滚动前检查 context.should_stop()。
    """

    DEFAULT_TOTAL_TIMEOUT = 60.0

    def __init__(self, total_timeout: float = DEFAULT_TOTAL_TIMEOUT) -> None:
        self.total_timeout = total_timeout

    def resolve(self, driver, context, locator_snapshot: dict, disable_scroll: bool = False):
        """从 smart 快照定位元素；找不到/不唯一/滚动耗尽/配置非法均抛对应异常。"""
        # 平台守卫：本阶段智能定位仅支持 Android（平台字段缺省/None 视为不限定）。
        platform = locator_snapshot.get("platform")
        if platform is not None and str(platform).strip().lower() not in ("android", "both"):
            raise InvalidSmartLocator("smart 定位当前仅支持 Android")
        raw_config = locator_snapshot.get("locator_config")
        if not isinstance(raw_config, dict):
            raise InvalidSmartLocator("smart 快照缺少 locator_config")
        # 1. 自校验（原始配置，防篡改）→ 2. 渲染变量 → 3. 渲染后重验
        validate_config(raw_config)
        variables = getattr(context, "variables", None) or {}
        config = render_config(raw_config, variables)

        search = config["search"]
        if disable_scroll:
            # 本动作自行控制滑动，临时关闭智能定位的自动滚动，防止全页面滚动抢占。
            search = dict(search)
            search["scroll"] = False
        selection = config["selection"]
        alternatives = config["alternatives"]
        deadline = time.monotonic() + self.total_timeout
        scroll_enabled = search["scroll"] and search["max_swipes"] > 0
        swipes_done = 0
        scrolled = False
        page_stable = False
        prev_sig: str | None = None
        stable_streak = 0

        # 轮询语义（P1 修复）：当前页面依次检查全部候选 → 全部未命中才滚动一页 →
        # 新页面重新检查全部候选。禁止让单个候选耗尽滚动预算，
        # 否则备用候选若原本位于首页，首候选滚动后就可能永远错过。
        while True:
            for alt_index, alt in enumerate(alternatives, start=1):
                self._check_stop(context)
                self._check_deadline(deadline)
                locator_type, locator_value = build_selector(alt)
                logger.info(
                    "智能定位候选 %s/%s: %s=%s", alt_index, len(alternatives), locator_type, locator_value
                )
                matches = self._find_all(driver, locator_type, locator_value)
                count = len(matches)
                logger.info("候选 %s 匹配 %s 个元素", alt_index, count)
                if count >= 1:
                    return self._select(matches, selection, locator_value)

            # 当前页面全部候选未命中 → 滚动一页后重查（页面指纹连续两次不变提前停止）
            if not scroll_enabled or page_stable:
                break
            if swipes_done >= search["max_swipes"]:
                page_stable = True
                break
            self._check_stop(context)
            self._check_deadline(deadline)
            if prev_sig is None:
                prev_sig = self._page_signature(driver)
            driver.swipe(search["direction"], duration=search["duration_ms"])
            swipes_done += 1
            scrolled = True
            settle = search["settle_ms"] / 1000.0
            if settle > 0:
                time.sleep(settle)
            self._check_stop(context)
            self._check_deadline(deadline)
            cur_sig = self._page_signature(driver)
            sig_changed = cur_sig != prev_sig
            logger.info(
                "滚动 %s/%s 次后全部候选仍未命中（指纹变化=%s）",
                swipes_done,
                search["max_swipes"],
                sig_changed,
            )
            if sig_changed:
                stable_streak = 0
            else:
                stable_streak += 1
                if stable_streak >= 2:
                    page_stable = True
                    logger.info("页面指纹连续两次未变化，提前停止滚动（疑似已滚动到底）")
            prev_sig = cur_sig

        # 全部候选耗尽
        if scrolled:
            raise ScrollLimitReached(
                f"智能定位已滑动 {swipes_done} 次仍未找到元素（共尝试 {len(alternatives)} 个候选）"
            )
        raise ElementNotFound(
            f"智能定位未找到元素（共尝试 {len(alternatives)} 个候选，未发生滚动）"
        )

    @staticmethod
    def _select(matches: list, selection: dict, selector_desc: str):
        count = len(matches)
        policy = selection["policy"]
        if policy == "unique":
            if count > 1:
                raise ElementNotUnique(
                    f"智能定位匹配到 {count} 个元素，期望唯一（候选: {selector_desc}）"
                )
            return matches[0]
        index = selection["index"] or 0
        if count < index:
            raise ElementNotFound(
                f"智能定位匹配到 {count} 个元素，期望第 {index} 个（index 越界，候选: {selector_desc}）"
            )
        return matches[index - 1]

    @staticmethod
    def _check_stop(context) -> None:
        stop = getattr(context, "should_stop", None)
        if stop is not None and stop():
            raise StopRequested("执行被用户停止")

    def _check_deadline(self, deadline: float) -> None:
        if time.monotonic() > deadline:
            raise ElementNotFound(f"智能定位超过 {self.total_timeout:.0f}s 总超时仍未找到元素")

    @staticmethod
    def _find_all(driver, locator_type: str, locator_value: str):
        return driver.find_elements(locator_type, locator_value, wait_timeout=0)

    @staticmethod
    def _page_signature(driver) -> str:
        return driver.page_signature()
