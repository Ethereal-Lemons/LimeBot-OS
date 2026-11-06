"""Spreadsheet creation exposed through the agent's existing tool surface."""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Union


class SpreadsheetToolService:
    """Create bounded XLSX workbooks using the host's existing path policy."""

    def __init__(
        self,
        *,
        is_path_allowed: Callable[[Union[str, Path]], bool],
        resolve_output_path: Callable[[Union[str, Path]], Path],
        is_persona_managed_path: Callable[[Path], bool],
    ) -> None:
        self._is_path_allowed = is_path_allowed
        self._resolve_output_path = resolve_output_path
        self._is_persona_managed_path = is_persona_managed_path

    async def create(
        self,
        path: str,
        sheets: List[Dict[str, Any]],
        title: str = "",
    ) -> str:
        """Authorize, validate, build, and atomically save one workbook."""
        if not self._is_path_allowed(path):
            return f"Error: Access denied to path '{path}'."
        target = self._resolve_output_path(path)
        if target.suffix.lower() != ".xlsx":
            return "Error: create_spreadsheet requires a path ending in .xlsx."
        if self._is_persona_managed_path(target):
            return "Error: Direct modification of state-managed files under 'persona/' is blocked."

        if not isinstance(sheets, list) or not sheets:
            return "Error: At least one worksheet is required."
        if len(sheets) > 20:
            return "Error: A workbook may contain at most 20 worksheets."

        total_cells = 0
        normalized_sheets: List[tuple[str, List[List[Any]]]] = []
        used_names: set[str] = set()
        for index, sheet in enumerate(sheets, start=1):
            if not isinstance(sheet, dict):
                return f"Error: Worksheet {index} must be an object."
            raw_name = str(sheet.get("name") or f"Sheet{index}")
            name = re.sub(r"[\\/*?:\[\]]", "-", raw_name).strip(" '")[:31]
            name = name or f"Sheet{index}"
            base_name = name
            suffix = 2
            while name.lower() in used_names:
                marker = f"-{suffix}"
                name = f"{base_name[: 31 - len(marker)]}{marker}"
                suffix += 1
            used_names.add(name.lower())

            rows = sheet.get("rows")
            if not isinstance(rows, list) or not rows:
                return f"Error: Worksheet '{name}' must include at least one row."
            if len(rows) > 2000:
                return f"Error: Worksheet '{name}' exceeds the 2,000-row limit."
            normalized_rows: List[List[Any]] = []
            for row_number, row in enumerate(rows, start=1):
                if not isinstance(row, list):
                    return f"Error: Row {row_number} in worksheet '{name}' must be an array."
                if len(row) > 50:
                    return f"Error: Row {row_number} in worksheet '{name}' exceeds 50 columns."
                normalized_rows.append(row)
                total_cells += len(row)
                if total_cells > 20_000:
                    return "Error: Workbook exceeds the 20,000-cell safety limit."
            normalized_sheets.append((name, normalized_rows))

        def build_workbook() -> tuple[int, int]:
            try:
                from openpyxl import Workbook
                from openpyxl.styles import Alignment, Font, PatternFill
                from openpyxl.utils import get_column_letter
            except ImportError as exc:
                raise RuntimeError(
                    "Spreadsheet support is unavailable because openpyxl is not installed."
                ) from exc

            workbook = Workbook()
            workbook.remove(workbook.active)
            workbook.calculation.fullCalcOnLoad = True
            workbook.calculation.forceFullCalc = True
            workbook.calculation.calcMode = "auto"
            if title:
                workbook.properties.title = str(title)[:255]
            header_fill = PatternFill("solid", fgColor="1F4E78")
            header_font = Font(color="FFFFFF", bold=True)
            band_fill = PatternFill("solid", fgColor="DDEBF7")
            formulas = 0

            for sheet_name, rows in normalized_sheets:
                worksheet = workbook.create_sheet(sheet_name)
                for row_index, row in enumerate(rows, start=1):
                    for column_index, value in enumerate(row, start=1):
                        cell = worksheet.cell(
                            row=row_index, column=column_index, value=value
                        )
                        cell.alignment = Alignment(vertical="top", wrap_text=True)
                        if isinstance(value, str) and value.startswith("="):
                            formulas += 1
                        if isinstance(value, str) and value.startswith(
                            ("https://", "http://")
                        ):
                            cell.hyperlink = value
                            cell.style = "Hyperlink"
                    if row_index > 1 and row_index % 2 == 0:
                        for cell in worksheet[row_index]:
                            if cell.fill.fill_type is None:
                                cell.fill = band_fill

                for cell in worksheet[1]:
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = Alignment(vertical="center", wrap_text=True)

                if len(rows) > 1:
                    worksheet.freeze_panes = "A2"
                    worksheet.auto_filter.ref = worksheet.dimensions

                headers = [str(value or "").strip().lower() for value in rows[0]]
                for column_index in range(1, worksheet.max_column + 1):
                    header = (
                        headers[column_index - 1]
                        if column_index <= len(headers)
                        else ""
                    )
                    values = [
                        worksheet.cell(row=r, column=column_index).value
                        for r in range(1, worksheet.max_row + 1)
                    ]
                    width = min(
                        60,
                        max(
                            10,
                            max(
                                (len(str(value)) for value in values if value is not None),
                                default=8,
                            )
                            + 2,
                        ),
                    )
                    worksheet.column_dimensions[
                        get_column_letter(column_index)
                    ].width = width
                    if any(
                        token in header
                        for token in (
                            "usd",
                            "cost",
                            "price",
                            "monthly",
                            "annual",
                            "total",
                        )
                    ):
                        for row_index in range(2, worksheet.max_row + 1):
                            worksheet.cell(
                                row=row_index, column=column_index
                            ).number_format = "$#,##0.00"
                    elif "percent" in header or "%" in header:
                        for row_index in range(2, worksheet.max_row + 1):
                            worksheet.cell(
                                row=row_index, column=column_index
                            ).number_format = "0.00%"

            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(
                f".{target.stem}.{uuid.uuid4().hex}.tmp.xlsx"
            )
            try:
                workbook.save(temporary)
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink(missing_ok=True)
            return len(normalized_sheets), formulas

        try:
            sheet_count, formula_count = await asyncio.to_thread(build_workbook)
            return (
                f"Successfully created spreadsheet '{path}' with {sheet_count} worksheet(s), "
                f"{total_cells} cells, and {formula_count} formula(s). Use send_media to deliver it."
            )
        except Exception as exc:
            return f"Error creating spreadsheet: {exc}"
