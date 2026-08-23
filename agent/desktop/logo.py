"""品牌 Logo（纯 Pillow 绘制，无外部资源文件）。

统一品牌图形：手机轮廓 + 对勾（靛蓝 #4f46e5 渐变圆角底），
与前端 frontend/public/favicon.svg 同构。

- tray_icon()：pystray 托盘图标（64px RGBA）
- icon(size)：任意尺寸（打包资产 agent.ico 由 scripts/make_logo.py 生成）
"""

from PIL import Image, ImageDraw

PRIMARY = (79, 70, 229)  # #4f46e5 前端 --primary
PRIMARY_LIGHT = (111, 103, 236)  # #6f67ec 渐变亮端
ACCENT = (199, 195, 248)  # #c7c3f8 home 键

# 64×64 设计坐标（与 favicon.svg 保持一致）
_BG_RADIUS = 14
_PHONE = (21, 10, 43, 54)
_SCREEN = (23.5, 13.5, 40.5, 45.5)
_PHONE_RADIUS = 5
_SCREEN_RADIUS = 3
_CHECK = [(27.5, 29), (31.0, 32.5), (37.5, 24)]
_HOME = (29.75, 49.5, 34.25, 51.5)


def icon(size: int = 64) -> Image.Image:
    """绘制品牌图标（RGBA），返回指定尺寸（4x 超采样抗锯齿）。"""
    ss = max(size, 16) * 4
    scale = ss / 64.0
    img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))

    # 1) 渐变底（上亮 → 下深），并裁剪圆角
    draw = ImageDraw.Draw(img)
    for y in range(ss):
        t = y / (ss - 1)
        color = tuple(
            round(PRIMARY_LIGHT[i] + (PRIMARY[i] - PRIMARY_LIGHT[i]) * t) for i in range(3)
        )
        draw.line((0, y, ss, y), fill=(*color, 255))
    mask = Image.new("L", (ss, ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, ss - 1, ss - 1), radius=_BG_RADIUS * scale, fill=255
    )
    img.putalpha(mask)

    # 2) 手机外形（白）
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        tuple(v * scale for v in _PHONE), radius=_PHONE_RADIUS * scale, fill=(255, 255, 255, 255)
    )
    # 3) 屏幕（品牌色）
    draw.rounded_rectangle(
        tuple(v * scale for v in _SCREEN), radius=_SCREEN_RADIUS * scale, fill=(*PRIMARY, 255)
    )
    # 4) 对勾（白，圆角端点/拐点）
    pts = [(x * scale, y * scale) for x, y in _CHECK]
    draw.line(pts, fill=(255, 255, 255, 255), width=round(3 * scale), joint="curve")
    for cx, cy in pts:
        r = 1.5 * scale
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, 255))
    # 5) home 键
    draw.rounded_rectangle(
        tuple(v * scale for v in _HOME), radius=1 * scale, fill=(*ACCENT, 255)
    )

    return img.resize((size, size), Image.LANCZOS)


def tray_icon() -> Image.Image:
    """pystray 托盘图标（64px）。"""
    return icon(64)
