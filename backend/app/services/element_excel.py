"""元素库 Excel 模板、导出与导入解析。"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
from typing import Any
from zipfile import BadZipFile, ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

ELEMENT_HEADERS = (
    "元素ID(element_id)",
    "项目ID(project_id)",
    "项目名称(project_name)",
    "名称(name)",
    "页面(page_name)",
    "平台(platform)",
    "适用范围(scope)",
    "定位方式(locator_type)",
    "定位值(locator_value)",
    "智能定位配置(locator_config)",
    "描述(description)",
)
ELEMENT_HEADER_FIELDS = {
    "元素ID(element_id)": "element_id",
    "项目ID(project_id)": "project_id",
    "项目名称(project_name)": "project_name",
    "名称(name)": "name",
    "页面(page_name)": "page_name",
    "平台(platform)": "platform",
    "适用范围(scope)": "scope",
    "定位方式(locator_type)": "locator_type",
    "定位值(locator_value)": "locator_value",
    "智能定位配置(locator_config)": "locator_config",
    "描述(description)": "description",
}
ELEMENT_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 2000
MAX_EXPORT_ROWS = 20000
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_ZIP_ENTRIES = 1000


@dataclass(slots=True)
class ElementImportRow:
    row_number: int
    element_id: int | None
    project_id: int | None
    data: dict[str, Any]


def _error(row: int, field: str, message: str) -> dict[str, Any]:
    return {"row": row, "field": field, "message": message}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_integer(value: Any, row: int, field: str, errors: list[dict[str, Any]]) -> int | None:
    if value is None or _text(value) == "":
        return None
    try:
        number = float(value) if isinstance(value, (int, float)) else float(_text(value))
        if not number.is_integer() or number < 1:
            raise ValueError
        return int(number)
    except (TypeError, ValueError):
        errors.append(_error(row, field, "必须是大于 0 的整数"))
        return None


def _validate_archive(content: bytes) -> None:
    if len(content) > MAX_IMPORT_BYTES:
        raise ValueError("文件大小不能超过 5 MB")
    try:
        with ZipFile(BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise ValueError("Excel 压缩包文件项过多")
            uncompressed = sum(info.file_size for info in infos)
            if uncompressed > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("Excel 解压后大小超过限制")
    except BadZipFile as exc:
        raise ValueError("文件不是有效的 XLSX 工作簿") from exc


def _apply_data_validation(sheet) -> None:
    platform = DataValidation(type="list", formula1='"android,ios,both"', allow_blank=True)
    locator = DataValidation(
        type="list",
        formula1='"id,resource_id,xpath,accessibility_id,class_name,uiautomator,predicate,coordinate,custom,smart"',
        allow_blank=False,
    )
    sheet.add_data_validation(platform)
    sheet.add_data_validation(locator)
    platform.add(f"F2:F{MAX_IMPORT_ROWS + 1}")
    locator.add(f"H2:H{MAX_IMPORT_ROWS + 1}")


def _style_data_rows(sheet) -> None:
    # 只设置已写入的数据行；不要为了预留导入范围创建 2,000 行空单元格。
    for row in sheet.iter_rows(min_row=2, max_row=sheet.max_row):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if cell.column in (1, 2):
                cell.number_format = "0"
            else:
                cell.number_format = "@"


def _style_data_sheet(sheet) -> None:
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:K{MAX_IMPORT_ROWS + 1}"
    header_fill = PatternFill("solid", fgColor="4F46E5")
    header_font = Font(color="FFFFFF", bold=True)
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    widths = [16, 14, 20, 24, 18, 12, 16, 22, 38, 52, 36]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index)].width = width
    sheet.row_dimensions[1].height = 32
    _style_data_rows(sheet)
    _apply_data_validation(sheet)


def _write_instructions(sheet, project_id: int | None, project_name: str | None) -> None:
    sheet.sheet_view.showGridLines = False
    sheet.column_dimensions["A"].width = 28
    sheet.column_dimensions["B"].width = 90
    sheet.append(["元素 Excel 填写说明", ""])
    sheet.merge_cells("A1:B1")
    sheet["A1"].fill = PatternFill("solid", fgColor="4F46E5")
    sheet["A1"].font = Font(color="FFFFFF", bold=True, size=14)
    sheet["A1"].alignment = Alignment(horizontal="center")
    target = f"当前项目：{project_name or '项目内导入'}（ID: {project_id or '由接口确定'}）"
    sheet.append(["导入目标", target])
    rows = [
        ["导入规则", "元素ID为空时创建；填写元素ID时更新同项目且由当前用户创建的元素。"],
        ["项目列", "项目ID/项目名称用于导出识别；项目内导入以 URL 中的项目为准，项目ID若填写必须一致。"],
        ["平台", "可填写 android、ios、both；留空默认 both。"],
        ["定位方式", "普通定位填写定位值；smart 定位填写智能定位配置 JSON，定位值留空。"],
        ["智能定位配置", '必须是合法 JSON 对象，例如 {"version":1,"alternatives":[...]}。'],
        ["事务规则", "文件任意一行失败时整批不写入，请根据错误行修正后重新导入。"],
        ["空白行", "完全空白的数据行会被忽略；最多导入 2,000 条数据。"],
    ]
    for row in rows:
        sheet.append(row)
    for row in sheet.iter_rows(min_row=2, max_col=2):
        row[0].font = Font(bold=True)
        row[0].alignment = Alignment(vertical="top")
        row[1].alignment = Alignment(vertical="top", wrap_text=True)
    sheet.freeze_panes = "A3"


def _new_workbook(project_id: int | None = None, project_name: str | None = None) -> Workbook:
    workbook = Workbook()
    data = workbook.active
    data.title = "元素"
    data.append(list(ELEMENT_HEADERS))
    _style_data_sheet(data)
    instructions = workbook.create_sheet("填写说明")
    _write_instructions(instructions, project_id, project_name)
    return workbook


def _save_workbook(workbook: Workbook) -> bytes:
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def build_template(project_id: int, project_name: str) -> bytes:
    return _save_workbook(_new_workbook(project_id, project_name))


def _safe_text(value: Any) -> str:
    # 让导出的用户字段始终是文本，避免 Excel 将其解释为公式。
    return "" if value is None else str(value)


def build_export(rows: Iterable[tuple[Any, Any]]) -> bytes:
    workbook = _new_workbook()
    sheet = workbook["元素"]
    for element, project in rows:
        values = [
            element.id,
            project.id if project else None,
            _safe_text(project.name if project else None),
            _safe_text(element.name),
            _safe_text(element.page_name),
            _safe_text(element.platform),
            _safe_text(element.scope),
            _safe_text(element.locator_type),
            _safe_text(element.locator_value),
            json.dumps(element.locator_config, ensure_ascii=False, separators=(",", ":"))
            if element.locator_config is not None
            else "",
            _safe_text(element.description),
        ]
        sheet.append(values)
        for cell in sheet[sheet.max_row]:
            if cell.column >= 3:
                cell.number_format = "@"
                cell.data_type = "s"
    _style_data_rows(sheet)
    sheet.auto_filter.ref = f"A1:K{max(sheet.max_row, 2)}"
    return _save_workbook(workbook)


def parse_import(content: bytes, project_id: int) -> tuple[list[ElementImportRow], list[dict[str, Any]]]:
    _validate_archive(content)
    errors: list[dict[str, Any]] = []
    try:
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
    except Exception as exc:
        raise ValueError("无法读取 XLSX 工作簿") from exc
    if "元素" not in workbook.sheetnames:
        return [], [_error(1, "工作表", "必须包含名为“元素”的工作表")]
    sheet = workbook["元素"]
    header_values = [_text(cell.value) for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
    if header_values != list(ELEMENT_HEADERS):
        return [], [_error(1, "表头", "表头与标准模板不一致，请下载最新模板")]

    rows: list[ElementImportRow] = []
    for row_number, cells in enumerate(sheet.iter_rows(min_row=2, max_row=MAX_IMPORT_ROWS + 2), start=2):
        values = [cell.value for cell in cells[: len(ELEMENT_HEADERS)]]
        if all(value is None or _text(value) == "" for value in values):
            continue
        if row_number > MAX_IMPORT_ROWS + 1:
            errors.append(_error(row_number, "数据行", f"最多导入 {MAX_IMPORT_ROWS} 条数据"))
            continue
        for cell in cells:
            if cell.data_type == "f":
                errors.append(_error(row_number, "数据", "不允许使用 Excel 公式，请填写实际值"))
                break
        row_values = dict(zip((ELEMENT_HEADER_FIELDS[h] for h in ELEMENT_HEADERS), values, strict=True))
        element_id = _parse_integer(row_values["element_id"], row_number, "元素ID(element_id)", errors)
        source_project_id = _parse_integer(row_values["project_id"], row_number, "项目ID(project_id)", errors)
        if source_project_id is not None and source_project_id != project_id:
            errors.append(_error(row_number, "项目ID(project_id)", "必须与当前项目一致"))
        name = _text(row_values["name"])
        locator_type = _text(row_values["locator_type"])
        locator_value = _text(row_values["locator_value"]) or None
        config_text = _text(row_values["locator_config"])
        locator_config: dict[str, Any] | None = None
        if config_text:
            try:
                parsed = json.loads(config_text)
                if not isinstance(parsed, dict):
                    raise ValueError
                locator_config = parsed
            except (TypeError, ValueError, json.JSONDecodeError):
                errors.append(_error(row_number, "智能定位配置(locator_config)", "必须是合法 JSON 对象"))
        rows.append(
            ElementImportRow(
                row_number=row_number,
                element_id=element_id,
                project_id=source_project_id,
                data={
                    "name": name,
                    "page_name": _text(row_values["page_name"]) or None,
                    "platform": _text(row_values["platform"]) or "both",
                    "scope": _text(row_values["scope"]) or "all",
                    "locator_type": locator_type,
                    "locator_value": locator_value,
                    "locator_config": locator_config,
                    "description": _text(row_values["description"]) or None,
                },
            )
        )
    workbook.close()
    return rows, errors
