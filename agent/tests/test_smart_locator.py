"""智能元素定位（Step 2）测试：Mock 世界覆盖解析/滚动/stale/选择策略/校验。"""

import pytest

from executor import ElementNotFound, ExecutionContext, MockDriver, StopRequested, TestRunner
from executor.driver import ElementStaleRetryExhausted
from executor.smart_locator import (
    ElementNotUnique,
    InvalidSmartLocator,
    ScrollLimitReached,
    build_selector,
)


def _smart_snapshot(config: dict) -> dict:
    return {"locator_type": "smart", "locator_config": config, "locator_value": None}


def _make_smart_case(config: dict, variables: dict | None = None) -> dict:
    return {
        "execution_case_id": 3001,
        "case_id": 10,
        "case_name": "智能定位用例",
        "elements_snapshot": {"1": _smart_snapshot(config)},
    }


def _context(driver, config, variables=None, should_stop=None):
    case = _make_smart_case(config, variables)
    return ExecutionContext(driver, case, variables, None, should_stop)


def _node(**attrs) -> dict:
    return attrs


def _config(target, *, search=None, selection=None, alternatives=None) -> dict:
    return {
        "version": 1,
        "alternatives": alternatives or [{"target": target}],
        "search": search or {"scroll": False},
        "selection": selection or {"policy": "unique"},
    }


def _scroll_search(max_swipes: int = 5) -> dict:
    return {"scroll": True, "direction": "up", "max_swipes": max_swipes, "duration_ms": 100, "settle_ms": 0}


# ---------- 场景 1：单条件全 operator（ends_with 走 XPath） ----------


@pytest.mark.parametrize(
    ("operator", "condition_value", "screen_text"),
    [
        ("equals", "设置", "设置"),
        ("contains", "设置", "打开设置界面"),
        ("starts_with", "设置", "设置中心"),
        ("ends_with", "中心", "设置中心"),
        ("regex", r"^\d{4}$", "2026"),
    ],
)
async def test_smart_single_condition_operators(operator, condition_value, screen_text):
    driver = MockDriver()
    driver.set_screen([_node(text=screen_text, id="target")])
    config = _config([{"attribute": "text", "operator": operator, "value": condition_value}])
    element = _context(driver, config).find_element("1")
    assert element.locator_value == "target"


# ---------- 场景 2：组合属性 AND ----------


async def test_smart_combined_attributes_strategy_u():
    """纯 UiAutomator 可表达组合 → Strategy U（UiSelector 链 AND）。"""
    driver = MockDriver()
    driver.set_screen(
        [
            _node(
                text="主机列表", content_desc="设备列表", resource_id="com.demo:id/host_list",
                class_name="android.widget.TextView", package="com.demo",
                clickable=False, enabled=True, id="host",
            ),
            _node(
                text="主机列表", content_desc="设备列表", resource_id="com.demo:id/other",
                class_name="android.widget.TextView", package="com.demo",
                clickable=False, enabled=True, id="other",
            ),
        ]
    )
    config = _config(
        [
            {"attribute": "text", "operator": "equals", "value": "主机列表"},
            {"attribute": "content_desc", "operator": "equals", "value": "设备列表"},
            {"attribute": "resource_id", "operator": "equals", "value": "com.demo:id/host_list"},
            {"attribute": "class_name", "operator": "equals", "value": "android.widget.TextView"},
            {"attribute": "package", "operator": "equals", "value": "com.demo"},
            {"attribute": "clickable", "operator": "equals", "value": False},
            {"attribute": "enabled", "operator": "equals", "value": True},
        ]
    )
    element = _context(driver, config).find_element("1")
    assert element.locator_value == "host"


async def test_smart_combined_attributes_strategy_x_displayed():
    """含 displayed（UiAutomator 无方法）→ 自动回退 Strategy X。"""
    driver = MockDriver()
    driver.set_screen(
        [
            _node(
                text="主机列表", content_desc="设备列表", resource_id="com.demo:id/host_list",
                class_name="android.widget.TextView", package="com.demo",
                clickable=True, enabled=True, selected=False, displayed=True, id="host",
            ),
            _node(
                text="主机列表", content_desc="设备列表", resource_id="com.demo:id/host_list",
                class_name="android.widget.TextView", package="com.demo",
                clickable=True, enabled=True, selected=False, displayed=False, id="hidden",
            ),
        ]
    )
    config = _config(
        [
            {"attribute": "text", "operator": "equals", "value": "主机列表"},
            {"attribute": "resource_id", "operator": "equals", "value": "com.demo:id/host_list"},
            {"attribute": "displayed", "operator": "equals", "value": True},
        ]
    )
    element = _context(driver, config).find_element("1")
    assert element.locator_value == "host"


# ---------- 场景 3：运行时变量替换 ----------


async def test_smart_runtime_variable_replacement():
    driver = MockDriver()
    driver.set_screen([_node(text="测试设备B", id="device-b")])
    config = _config([{"attribute": "text", "operator": "equals", "value": "${device_name}"}])
    element = _context(driver, config, variables={"device_name": "测试设备B"}).find_element("1")
    assert element.locator_value == "device-b"


# ---------- 场景 4：候选回退 ----------


async def test_smart_fallback_to_next_alternative():
    driver = MockDriver()
    driver.set_screen([_node(text="实际按钮", id="real")])
    config = _config(
        [],
        alternatives=[
            {"target": [{"attribute": "text", "operator": "equals", "value": "不存在的按钮"}]},
            {"target": [{"attribute": "text", "operator": "equals", "value": "实际按钮"}]},
        ],
    )
    element = _context(driver, config).find_element("1")
    assert element.locator_value == "real"


# ---------- 场景 5：anchor+path 相对定位 ----------


async def test_smart_anchor_path_relative():
    driver = MockDriver()
    driver.set_screen(
        [
            _node(
                text="列表容器",
                children=[
                    _node(
                        text="设备行1",
                        children=[
                            _node(text="设备行1详情"),
                            _node(content_desc="连接", clickable=True, id="connect-btn"),
                        ],
                    )
                ],
            )
        ]
    )
    config = _config(
        [],
        alternatives=[
            {
                "anchor": [{"attribute": "text", "operator": "equals", "value": "设备行1"}],
                "path": [{"axis": "ancestor", "depth": 1}, {"axis": "descendant"}],
                "target": [
                    {"attribute": "content_desc", "operator": "equals", "value": "连接"},
                    {"attribute": "clickable", "operator": "equals", "value": True},
                ],
            }
        ],
    )
    element = _context(driver, config).find_element("1")
    assert element.locator_value == "connect-btn"


# ---------- 场景 6：滚动后找到屏幕外元素 ----------


async def test_smart_scroll_finds_offscreen_element():
    driver = MockDriver()
    driver.set_screen([_node(text="初始内容")])
    driver.set_scroll_callback(
        lambda count: [_node(text="目标元素", id="target")] if count >= 1 else None
    )
    config = _config(
        [{"attribute": "text", "operator": "equals", "value": "目标元素"}],
        search=_scroll_search(max_swipes=5),
    )
    element = _context(driver, config).find_element("1")
    assert element.locator_value == "target"
    assert driver.swipe_count == 1


# ---------- 场景 7：滚动到底（指纹连续两次不变提前停止） ----------


async def test_smart_scroll_bottom_raises_scroll_limit():
    driver = MockDriver()
    driver.set_screen([_node(text="不相关元素")])
    # 未注册 scroll 回调 → 屏幕永不变化，指纹连续两次不变提前停止
    config = _config(
        [{"attribute": "text", "operator": "equals", "value": "永远不存在"}],
        search=_scroll_search(max_swipes=10),
    )
    with pytest.raises(ScrollLimitReached):
        _context(driver, config).find_element("1")
    assert driver.swipe_count == 2  # swipe 次数有界


async def test_smart_scroll_budget_exhausted_raises_scroll_limit():
    """每屏都在变但始终无目标 → max_swipes 耗尽 → SCROLL_LIMIT_REACHED。"""
    driver = MockDriver()
    driver.set_screen([_node(text="第0屏")])
    driver.set_scroll_callback(lambda count: [_node(text=f"第{count}屏")])
    config = _config(
        [{"attribute": "text", "operator": "equals", "value": "永不出现"}],
        search=_scroll_search(max_swipes=3),
    )
    with pytest.raises(ScrollLimitReached):
        _context(driver, config).find_element("1")
    assert driver.swipe_count == 3


# ---------- 场景 8：不唯一 ----------


async def test_smart_not_unique_raises():
    driver = MockDriver()
    driver.set_screen([_node(text="确定", id="a"), _node(text="确定", id="b")])
    config = _config([{"attribute": "text", "operator": "equals", "value": "确定"}])
    with pytest.raises(ElementNotUnique, match="匹配到 2 个元素"):
        _context(driver, config).find_element("1")


async def test_smart_not_unique_click_does_not_click_any():
    """runner 整链：不唯一 → 步骤失败，且不点击任何元素。"""
    driver = MockDriver()
    driver.set_screen([_node(text="确定", id="a"), _node(text="确定", id="b")])
    config = _config([{"attribute": "text", "operator": "equals", "value": "确定"}])
    case = _make_smart_case(config)
    case["steps_snapshot"] = [
        {"execution_step_id": 3101, "order": 1, "action": "click", "element_id": 1, "params": {}}
    ]
    sent: list[dict] = []

    async def fake_send(payload: dict):
        sent.append(payload)

    status = await TestRunner(driver, fake_send, 100).run_case(case)
    assert status == "failed"
    step_msg = next(m for m in sent if m["type"] == "step_result")
    assert "匹配到 2 个元素" in (step_msg["error_message"] or "")


# ---------- 场景 9/10：refresh 后 stale → 重定位 / 重试耗尽 ----------


class _RefreshingDriver(MockDriver):
    """首次 click 时页面刷新一次，使已定位元素失效。"""

    def __init__(self):
        super().__init__()
        self._refreshed = False

    def click(self, element):
        if not self._refreshed:
            self._refreshed = True
            self.refresh()
        super().click(element)


async def test_smart_stale_refresh_relocates_and_click_succeeds(monkeypatch):
    from executor.actions import ClickAction

    driver = _RefreshingDriver()
    driver.set_screen([_node(text="提交", clickable=True)])
    config = _config([{"attribute": "text", "operator": "equals", "value": "提交"}])
    context = _context(driver, config)
    monkeypatch.setattr(ClickAction, "_STALE_RETRY_DELAYS", (0, 0))

    result = await ClickAction().execute(driver, context, {"element_id": 1})

    assert result["status"] == "passed"
    assert driver._refreshed is True


class _AlwaysRefreshingDriver(MockDriver):
    """每次 click 都刷新页面 → 元素永远失效。"""

    def click(self, element):
        self.refresh()
        super().click(element)


async def test_smart_stale_retry_exhausted(monkeypatch):
    from executor.actions import ClickAction

    driver = _AlwaysRefreshingDriver()
    driver.set_screen([_node(text="提交", clickable=True)])
    config = _config([{"attribute": "text", "operator": "equals", "value": "提交"}])
    context = _context(driver, config)
    monkeypatch.setattr(ClickAction, "_STALE_RETRY_DELAYS", (0, 0))

    with pytest.raises(ElementStaleRetryExhausted, match="重新定位并重试 2 次"):
        await ClickAction().execute(driver, context, {"element_id": 1})


# ---------- 场景 11：滚动循环中停止 ----------


async def test_smart_scroll_respects_stop():
    checks = {"n": 0}

    def should_stop() -> bool:
        checks["n"] += 1
        return checks["n"] >= 4

    driver = MockDriver()
    driver.set_screen([_node(text="x")])
    config = _config(
        [{"attribute": "text", "operator": "equals", "value": "永不出现"}],
        search=_scroll_search(max_swipes=5),
    )
    context = _context(driver, config, should_stop=should_stop)

    with pytest.raises(StopRequested):
        context.find_element("1")
    assert driver.swipe_count == 1  # 第二次滚动前即停止


# ---------- 场景 12：普通定位回归（sanitiy） ----------


async def test_smart_context_ordinary_locator_unchanged():
    driver = MockDriver()
    context = ExecutionContext(
        driver,
        {
            "execution_case_id": 1,
            "elements_snapshot": {
                "1": {"locator_type": "id", "locator_value": "username"},
                "2": {"locator_type": "accessibility_id", "locator_value": "登录按钮"},
                "3": {"locator_type": "xpath", "locator_value": "//*[@resource-id='x']"},
                "4": {"locator_type": "resource_id", "locator_value": "com.demo:id/btn"},
            },
        },
    )
    assert context.find_element("1").locator_value == "username"
    assert context.find_element("2").locator_value == "登录按钮"
    assert context.find_element("3").locator_value == "//*[@resource-id='x']"
    assert context.find_element("4").locator_value == "com.demo:id/btn"


# ---------- 场景 13：index 选择策略 ----------


async def test_smart_index_selection():
    driver = MockDriver()
    driver.set_screen(
        [_node(text="选项", id="o1"), _node(text="选项", id="o2"), _node(text="选项", id="o3")]
    )
    config = _config(
        [{"attribute": "text", "operator": "equals", "value": "选项"}],
        selection={"policy": "index", "index": 2},
    )
    element = _context(driver, config).find_element("1")
    assert element.locator_value == "o2"  # index 为 1 基序号：第 2 个


async def test_smart_index_out_of_range():
    driver = MockDriver()
    driver.set_screen(
        [_node(text="选项", id="o1"), _node(text="选项", id="o2"), _node(text="选项", id="o3")]
    )
    config = _config(
        [{"attribute": "text", "operator": "equals", "value": "选项"}],
        selection={"policy": "index", "index": 5},
    )
    with pytest.raises(ElementNotFound, match="匹配到 3 个元素，期望第 5 个"):
        _context(driver, config).find_element("1")


async def test_smart_index_exceeds_match_count():
    driver = MockDriver()
    driver.set_screen([_node(text="唯一", id="only")])
    config = _config(
        [{"attribute": "text", "operator": "equals", "value": "唯一"}],
        selection={"policy": "index", "index": 2},
    )
    with pytest.raises(ElementNotFound, match="匹配到 1 个元素，期望第 2 个"):
        _context(driver, config).find_element("1")


# ---------- 场景 14：非法配置 → INVALID_SMART_LOCATOR ----------


async def test_smart_invalid_path_segments_too_many():
    config = {
        "version": 1,
        "alternatives": [
            {
                "anchor": [{"attribute": "text", "operator": "equals", "value": "a"}],
                "path": [{"axis": "child"}, {"axis": "child"}, {"axis": "child"}, {"axis": "child"}],
                "target": [{"attribute": "text", "operator": "equals", "value": "b"}],
            }
        ],
        "search": {"scroll": False},
        "selection": {"policy": "unique"},
    }
    with pytest.raises(InvalidSmartLocator, match="path 段数"):
        _context(MockDriver(), config).find_element("1")


async def test_smart_invalid_ancestor_depth():
    config = {
        "version": 1,
        "alternatives": [
            {
                "anchor": [{"attribute": "text", "operator": "equals", "value": "a"}],
                "path": [{"axis": "ancestor", "depth": 6}],
                "target": [{"attribute": "text", "operator": "equals", "value": "b"}],
            }
        ],
        "search": {"scroll": False},
        "selection": {"policy": "unique"},
    }
    with pytest.raises(InvalidSmartLocator, match="depth"):
        _context(MockDriver(), config).find_element("1")


async def test_smart_invalid_attribute():
    config = _config([{"attribute": "foo", "operator": "equals", "value": "x"}])
    with pytest.raises(InvalidSmartLocator, match="未知条件属性"):
        _context(MockDriver(), config).find_element("1")


async def test_smart_invalid_operator():
    config = _config([{"attribute": "text", "operator": "fuzzy", "value": "x"}])
    with pytest.raises(InvalidSmartLocator, match="未知条件操作符"):
        _context(MockDriver(), config).find_element("1")


async def test_smart_rendered_value_too_long():
    config = _config([{"attribute": "text", "operator": "equals", "value": "${long}"}])
    context = _context(MockDriver(), config, variables={"long": "长" * 300})
    with pytest.raises(InvalidSmartLocator, match="200"):
        context.find_element("1")


async def test_smart_undefined_variable():
    config = _config([{"attribute": "text", "operator": "equals", "value": "${missing}"}])
    with pytest.raises(InvalidSmartLocator, match="未定义变量"):
        _context(MockDriver(), config).find_element("1")


async def test_smart_invalid_regex():
    config = _config([{"attribute": "text", "operator": "regex", "value": "([unclosed"}])
    with pytest.raises(InvalidSmartLocator, match="非法正则表达式"):
        _context(MockDriver(), config).find_element("1")


async def test_smart_path_without_anchor_invalid():
    config = {
        "version": 1,
        "alternatives": [
            {
                "path": [{"axis": "child"}],
                "target": [{"attribute": "text", "operator": "equals", "value": "b"}],
            }
        ],
        "search": {"scroll": False},
        "selection": {"policy": "unique"},
    }
    with pytest.raises(InvalidSmartLocator, match="anchor"):
        _context(MockDriver(), config).find_element("1")


async def test_smart_version_invalid():
    config = {
        "version": 2,
        "alternatives": [{"target": [{"attribute": "text", "operator": "equals", "value": "x"}]}],
        "search": {"scroll": False},
        "selection": {"policy": "unique"},
    }
    with pytest.raises(InvalidSmartLocator, match="版本"):
        _context(MockDriver(), config).find_element("1")


async def test_smart_bool_value_type_invalid():
    config = _config([{"attribute": "clickable", "operator": "equals", "value": "true"}])
    with pytest.raises(InvalidSmartLocator, match="布尔值"):
        _context(MockDriver(), config).find_element("1")


# ---------- 场景 15：regex 在 anchor/path 场景 → INVALID（限制文档化） ----------


async def test_smart_regex_in_anchor_path_invalid():
    config = {
        "version": 1,
        "alternatives": [
            {
                "anchor": [{"attribute": "text", "operator": "regex", "value": ".*"}],
                "path": [{"axis": "child"}],
                "target": [{"attribute": "text", "operator": "equals", "value": "b"}],
            }
        ],
        "search": {"scroll": False},
        "selection": {"policy": "unique"},
    }
    with pytest.raises(InvalidSmartLocator, match="regex"):
        _context(MockDriver(), config).find_element("1")


async def test_smart_regex_plus_ends_with_invalid():
    config = _config(
        [
            {"attribute": "text", "operator": "regex", "value": ".*"},
            {"attribute": "text", "operator": "ends_with", "value": "x"},
        ]
    )
    with pytest.raises(InvalidSmartLocator, match="regex"):
        _context(MockDriver(), config).find_element("1")


# ---------- 引号安全：XPath concat() 字面量 ----------


async def test_smart_xpath_concat_literal_with_both_quote_types():
    """值同时含单双引号 → _xpath_literal 产出 concat()，Mock 正确解析且无注入逃逸。"""
    driver = MockDriver()
    driver.set_screen([_node(text='xxa"b\'c', displayed=True, id="quoted")])
    config = _config(
        [
            {"attribute": "text", "operator": "ends_with", "value": 'a"b\'c'},
            {"attribute": "displayed", "operator": "equals", "value": True},
        ]
    )
    element = _context(driver, config).find_element("1")
    assert element.locator_value == "quoted"


# ---------- 选择器生成（安全转义与策略选择） ----------


def test_smart_selector_generation_strategies():
    # Strategy U：Java 转义（\\ 与单双引号均转义，禁止裸引号）
    locator_type, locator_value = build_selector(
        {
            "anchor": [],
            "path": [],
            "target": [
                {"attribute": "text", "operator": "equals", "value": 'a"b'},
                {"attribute": "clickable", "operator": "equals", "value": True},
            ],
        }
    )
    assert locator_type == "uiautomator"
    assert locator_value == 'new UiSelector().text("a\\"b").clickable(true)'

    # Strategy X：ends_with 走 XPath substring
    locator_type, locator_value = build_selector(
        {"anchor": [], "path": [], "target": [{"attribute": "text", "operator": "ends_with", "value": "中心"}]}
    )
    assert locator_type == "xpath"
    assert locator_value == '//*[substring(@text, string-length(@text)-string-length("中心")+1)="中心"]'

    # Strategy X：anchor+path 相对定位
    locator_type, locator_value = build_selector(
        {
            "anchor": [{"attribute": "text", "operator": "equals", "value": "行"}],
            "path": [{"axis": "ancestor", "depth": 1}, {"axis": "descendant"}],
            "target": [{"attribute": "content_desc", "operator": "equals", "value": "连接"}],
        }
    )
    assert locator_type == "xpath"
    assert locator_value == '//*[@text="行"]/ancestor::*[1]/descendant::*[@content-desc="连接"]'


# ---------- G2 门禁：parent 轴 depth≥2（转译 ancestor） ----------


def test_smart_parent_depth_two_generates_ancestor():
    """parent depth≥2 转译为 ancestor::*[N]（parent::*[2] 在 XPath 1.0 恒空）。"""
    locator_type, locator_value = build_selector(
        {
            "anchor": [{"attribute": "text", "operator": "equals", "value": "行"}],
            "path": [{"axis": "parent", "depth": 2}, {"axis": "descendant"}],
            "target": [{"attribute": "content_desc", "operator": "equals", "value": "目标"}],
        }
    )
    assert locator_type == "xpath"
    assert locator_value == '//*[@text="行"]/ancestor::*[2]/descendant::*[@content-desc="目标"]'


def test_smart_parent_depth_one_keeps_parent_axis():
    """parent depth=1 保持 parent::*[1]。"""
    locator_type, locator_value = build_selector(
        {
            "anchor": [{"attribute": "text", "operator": "equals", "value": "行"}],
            "path": [{"axis": "parent", "depth": 1}],
            "target": [{"attribute": "text", "operator": "equals", "value": "容器"}],
        }
    )
    assert locator_type == "xpath"
    assert locator_value == '//*[@text="行"]/parent::*[1][@text="容器"]'


async def test_smart_parent_depth_two_matching():
    """parent depth=2（第 2 级祖先=祖父）下的 descendant 目标命中（Mock 三层树）。"""
    driver = MockDriver()
    driver.set_screen(
        [
            _node(
                text="祖父容器",
                children=[
                    _node(
                        text="父容器",
                        children=[
                            _node(text="叶子行", children=[_node(content_desc="目标", id="goal")]),
                        ],
                    )
                ],
            )
        ]
    )
    config = _config(
        [],
        alternatives=[
            {
                "anchor": [{"attribute": "text", "operator": "equals", "value": "叶子行"}],
                "path": [{"axis": "parent", "depth": 2}, {"axis": "descendant"}],
                "target": [{"attribute": "content_desc", "operator": "equals", "value": "目标"}],
            }
        ],
    )
    element = _context(driver, config).find_element("1")
    assert element.locator_value == "goal"


# ---------- G2 门禁：平台守卫（仅 Android） ----------


async def test_smart_platform_guard_rejects_ios():
    driver = MockDriver()
    driver.set_screen([_node(text="确定")])
    config = _config([{"attribute": "text", "operator": "equals", "value": "确定"}])
    snapshot = _smart_snapshot(config)
    snapshot["platform"] = "ios"
    context = ExecutionContext(
        driver, {"execution_case_id": 5001, "elements_snapshot": {"1": snapshot}}
    )
    with pytest.raises(InvalidSmartLocator, match="仅支持 Android"):
        context.find_element("1")


async def test_smart_platform_android_both_and_none_allowed():
    driver = MockDriver()
    driver.set_screen([_node(text="确定", id="ok")])
    config = _config([{"attribute": "text", "operator": "equals", "value": "确定"}])
    for platform in ("android", "both", None):
        snapshot = _smart_snapshot(config)
        if platform is not None:
            snapshot["platform"] = platform
        context = ExecutionContext(
            driver, {"execution_case_id": 5001, "elements_snapshot": {"1": snapshot}}
        )
        element = context.find_element("1")
        assert element.locator_value == "ok"


# ---------- G2 门禁：校验对齐 Phase A ----------


async def test_smart_invalid_empty_value():
    config = _config([{"attribute": "text", "operator": "equals", "value": ""}])
    with pytest.raises(InvalidSmartLocator, match="空"):
        _context(MockDriver(), config).find_element("1")


async def test_smart_invalid_direction_left():
    config = _config(
        [{"attribute": "text", "operator": "equals", "value": "x"}],
        search={"scroll": True, "direction": "left", "max_swipes": 3, "duration_ms": 100, "settle_ms": 0},
    )
    with pytest.raises(InvalidSmartLocator, match="direction"):
        _context(MockDriver(), config).find_element("1")


async def test_smart_invalid_max_swipes_zero():
    config = _config(
        [{"attribute": "text", "operator": "equals", "value": "x"}],
        search={"scroll": True, "direction": "up", "max_swipes": 0, "duration_ms": 100, "settle_ms": 0},
    )
    with pytest.raises(InvalidSmartLocator, match="max_swipes"):
        _context(MockDriver(), config).find_element("1")


# ---------- G2 门禁：element_exists 放行 StopRequested / ScrollAction stale 重试 ----------


async def test_element_exists_re_raises_stop_requested():
    from executor.assertions import ElementExistsAssertion

    driver = MockDriver()
    context = ExecutionContext(
        driver,
        {"execution_case_id": 6001, "elements_snapshot": {"1": {"locator_type": "id", "locator_value": "btn"}}},
        should_stop=lambda: True,
    )
    with pytest.raises(StopRequested):
        await ElementExistsAssertion().verify(driver, context, {"element_id": 1, "expected": "exists"})


async def test_scroll_action_wrapped_in_stale_retry():
    from executor.actions import ScrollAction

    driver = MockDriver()
    context = ExecutionContext(
        driver,
        {"execution_case_id": 6002, "elements_snapshot": {"1": {"locator_type": "id", "locator_value": "btn"}}},
    )
    result = await ScrollAction().execute(driver, context, {"element_id": 1})
    assert result["status"] == "passed"
