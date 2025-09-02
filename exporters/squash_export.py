from __future__ import annotations

import io
import os
from datetime import datetime
from typing import Dict, List, Optional

import xlwt

from multi_sheet_converter import (
    SquashTMImportConverter,
    convert_to_squash_import_xls as _text_to_xls,
)


def generate_filename(test_type: str = "test_cases") -> str:
    safe_type = (test_type or "test_cases").strip().replace(" ", "_")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"squash_import_{safe_type}_{ts}.xls"


def export_squash_xls_file(
    test_cases: List[Dict],
    *,
    username: str = "QA_Bot",
    output_filename: Optional[str] = None,
    test_cases_text: Optional[str] = None,
) -> str:
    converter = SquashTMImportConverter()

    # Build sheet rows using the converter's logic (ensures BDD/TC_SCRIPT formatting)
    sheets = converter.generate_squash_sheets_data(test_cases, username=username)

    # Resolve filename
    filename = output_filename or generate_filename(
        ("visual" if any("VISUAL" in (tc.get("description", "").upper()) for tc in (test_cases or [])) else "functional")
    )

    wb = xlwt.Workbook()
    for internal in ("TEST_CASES", "STEPS", "PARAMETERS", "DATASETS", "LINK_REQ_TC"):
        converter.create_sheet(wb, internal, sheets.get(internal, []), internal)

    wb.save(filename)
    return filename


def convert_to_squash_excel(
    test_cases: List[Dict],
    username: str = "QA_Bot",
) -> io.BytesIO:
    path = export_squash_xls_file(test_cases, username=username)
    try:
        with open(path, "rb") as f:
            data = f.read()
        return io.BytesIO(data)
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


def convert_to_squash_import_xls(
    test_cases_text: str,
    output_filename: Optional[str] = None,
    username: str = "QA_Bot",
) -> str:
    return _text_to_xls(test_cases_text, output_filename, username)
