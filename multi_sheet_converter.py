import os
import re
from datetime import datetime
from typing import Dict, List, Optional

import xlwt
from xlwt import Workbook, easyxf


class SquashTMImportConverter:
	"""Convert generated test cases text into Squash TM multi-sheet XLS.

	Key behaviors (last-known-good):
	- TC_KIND = GHERKIN
	- TC_SCRIPT starts with Feature then Scenario then steps
	- Given/When/Then required; And optional; chained keywords sanitized
	- Preserve original step order if steps already exist (no extra synthetic Given)
	- STEPS/PARAMETERS/DATASETS/LINK_REQ_TC sheets created (steps left empty for BDD)
	"""

	def __init__(self, template_folder: str = "import_template"):
		self.template_folder = template_folder
		self.headers = {
			"TEST_CASES": [
				"ACTION",
				"TC_PATH",
				"TC_NUM",
				"TC_UUID",
				"TC_REFERENCE",
				"TC_NAME",
				"TC_MILESTONE",
				"TC_WEIGHT_AUTO",
				"TC_WEIGHT",
				"TC_NATURE",
				"TC_TYPE",
				"TC_STATUS",
				"TC_DESCRIPTION",
				"TC_PRE_REQUISITE",
				"TC_CREATED_ON",
				"TC_CREATED_BY",
				"TC_KIND",
				"TC_SCRIPT",
				"TC_AUTOMATABLE",
				"TC_CUF_<cuf's code>",
			],
			"STEPS": [
				"ACTION",
				"TC_OWNER_PATH",
				"TC_STEP_NUM",
				"TC_STEP_IS_CALL_STEP",
				"TC_STEP_CALL_DATASET",
				"TC_STEP_ACTION",
				"TC_STEP_EXPECTED_RESULT",
			],
			"PARAMETERS": [
				"ACTION",
				"TC_OWNER_PATH",
				"TC_PARAM_NAME",
				"TC_PARAM_DESCRIPTION",
			],
			"DATASETS": [
				"ACTION",
				"TC_OWNER_PATH",
				"TC_DATASET_NAME",
				"TC_PARAM_OWNER_PATH",
				"TC_DATASET_PARAM_NAME",
				"TC_DATASET_PARAM_VALUE",
			],
			"LINK_REQ_TC": ["REQ_PATH", "REQ_VERSION_NUM", "TC_PATH"],
		}
		self.setup_styles()

	def setup_styles(self):
		self.header_style = easyxf(
			"font: bold on, color black; "
			"align: wrap on, vert centre, horiz center; "
			"borders: left thin, right thin, top thin, bottom thin; "
			"pattern: pattern solid, fore_colour light_blue;"
		)
		self.data_style = easyxf(
			"align: wrap on, vert top, horiz left; "
			"borders: left thin, right thin, top thin, bottom thin;"
		)

	# ---------------------- Parsing helpers ----------------------
	def _sanitize_title_for_name(self, title: str) -> str:
		if not title:
			return ""
		t = re.sub(r"\[[^\]]*\]", "", title)  # drop tags like [BUG]
		t = re.sub(r"^(?:Given|When|Then|And)\s*[:\-]?\s*", "", t, flags=re.IGNORECASE)
		t = re.sub(r"^(?:Diberikan|Ketika|Maka|Dan)\s*[:\-]?\s*", "", t, flags=re.IGNORECASE)
		t = re.sub(r"\s+", " ", t).strip(" -:").strip()
		if not re.match(r"^Verify\b", t, flags=re.IGNORECASE):
			t = f"Verify {t}" if t else "Verify"
		return t

	def format_tc_name_english(self, title: str, tc_num: str) -> str:
		clean_title = re.sub(r"[^\w\s-]", "", title).strip()
		words = clean_title.split()
		mapped = []
		for w in words:
			lw = w.lower()
			if "sort" in lw:
				mapped.append("Sort")
			elif "button" in lw:
				mapped.append("Button")
			elif "customer" in lw:
				mapped.append("Customer")
			elif "feature" in lw:
				mapped.append("Feature")
			elif "verify" in lw or "check" in lw:
				mapped.append("Verify")
			elif any(x in lw for x in ("absence", "tidak", "no")):
				mapped.append("Absence")
			elif any(x in lw for x in ("display", "show")):
				mapped.append("Display")
			elif "data" in lw:
				mapped.append("Data")
			elif "login" in lw:
				mapped.append("Login")
			elif "system" in lw:
				mapped.append("System")
			else:
				mapped.append(w.capitalize())
		test_name = " ".join(mapped)[:100]
		return f"{tc_num} {test_name}" if test_name else f"{tc_num} Test Functionality"

	# ---------------------- Test-case text parsing ----------------------
	def parse_test_cases_from_telegram_result(self, test_cases_text: str) -> List[Dict]:
		"""Loosely parse test cases from free text (robust to chat formatting)."""
		test_cases: List[Dict] = []
		if not test_cases_text:
			return []

		normalized_text = (
			test_cases_text.replace("\u2013", "-").replace("\u2014", "-")
		)
		lines = normalized_text.splitlines()
		current_tc: Optional[Dict] = None
		collecting_steps = False
		bdd_mode = False
		step_buffer: List[str] = []

		def _strip_marks(s: str) -> str:
			s2 = re.sub(r"^[#>\s]*", "", s)
			s2 = re.sub(r"^(?:[-*•●▪︎▶️►]+\s*)+", "", s2)
			s2 = re.sub(r"^(?:[\u2600-\u27BF\uE000-\uF8FF\U0001F000-\U0001FAFF]+\s*)+", "", s2)
			s2 = s2.strip()
			if s2.startswith("**") and s2.endswith("**") and len(s2) > 4:
				s2 = s2[2:-2].strip()
			return s2.replace("**", "").strip()

		for raw in lines:
			line = raw.strip()
			if not line:
				continue
			norm = _strip_marks(line)
			if norm.startswith("```"):
				continue

			# Various headers like "Test Case 1: Title" / "001. Title"
			tc_match = None
			if re.match(r"^Test\s*Case\s*\d+\s*[:\-]\s*", norm, re.IGNORECASE):
				m = re.search(r"[Tt]est\s*[Cc]ase\s*(\d+)\s*[:\-]\s*(.+?)\s*$", norm)
				if m:
					tc_match = (m.group(1), m.group(2))
			elif re.match(r"^TC\s*\d+\s*[:\-]\s*", norm, re.IGNORECASE):
				m = re.search(r"TC\s*(\d+)\s*[:\-]\s*(.+?)\s*$", norm, re.IGNORECASE)
				if m:
					tc_match = (m.group(1), m.group(2))
			elif re.match(r"^(\d+)\.[\s\)]*(.+)$", norm):
				md = re.match(r"^(\d+)\.[\s\)]*(.+)$", norm)
				if md:
					tc_match = (md.group(1), md.group(2).strip())

			if tc_match:
				# push previous
				if current_tc:
					if collecting_steps and step_buffer:
						if bdd_mode:
							current_tc["bdd_lines"] = list(step_buffer)
						else:
							current_tc["steps"] = self.parse_numbered_steps(
								"\n".join(step_buffer), "Expected outcome is achieved"
							)
					test_cases.append(current_tc)
				num, title = tc_match
				display_title = self._sanitize_title_for_name(title)
				tc_name = self.format_tc_name_english(display_title, f"{int(num):03d}")
				current_tc = {
					"id": f"TC_{int(num):03d}",
					"name": tc_name,
					"description": title,
					"preconditions": "",
					"steps": [],
					"nature": "NAT_FUNCTIONAL_TESTING",
					"importance": "MEDIUM",
					"status": "WORK_IN_PROGRESS",
				}
				collecting_steps = False
				bdd_mode = False
				step_buffer = []
				continue

			if not current_tc:
				continue

			if re.match(r"^(Steps|Test Steps|Langkah|Langkah-langkah)\s*[:\-]?", norm, re.IGNORECASE):
				collecting_steps = True
				step_buffer = []
				bdd_mode = False
				continue

			if collecting_steps:
				if norm and not norm.startswith("**"):
					step_buffer.append(norm)
				continue

			# Direct BDD lines
			if re.match(r"^(Given|When|Then|And)\b[: ]", norm):
				if not collecting_steps:
					collecting_steps = True
					step_buffer = []
					bdd_mode = True
				step_buffer.append(norm)

		if current_tc:
			if collecting_steps and step_buffer:
				if bdd_mode:
					# Preserve raw BDD lines exactly as in chat
					current_tc["bdd_lines"] = list(step_buffer)
				else:
					current_tc["steps"] = self.parse_numbered_steps(
						"\n".join(step_buffer), "Expected outcome is achieved"
					)
			test_cases.append(current_tc)

		if not test_cases and test_cases_text:
			# Minimal fallback single test case
			test_cases = [
				{
					"id": "TC_001",
					"name": "001 Verify Scenario",
					"description": test_cases_text[:200],
					"steps": self.parse_numbered_steps("1. Execute scenario", "Expected outcome"),
					"nature": "NAT_FUNCTIONAL_TESTING",
					"importance": "MEDIUM",
					"status": "WORK_IN_PROGRESS",
				}
			]
		return test_cases

	def parse_numbered_steps(self, steps_text: str, expected_result: str) -> List[Dict]:
		steps: List[Dict] = []
		if not steps_text:
			return [
				{
					"action": "Given the system is ready When user executes the test scenario",
					"expected": f"Then {expected_result.lower()}",
				}
			]
		lines = [ln.strip() for ln in steps_text.splitlines() if ln.strip()]
		buf = ""
		for ln in lines:
			m = re.match(r"^(\d+)\.\s*(.+)", ln)
			if m:
				if buf:
					steps.append(
						{"action": self.format_bdd_action(buf.strip()), "expected": "Then step completed successfully"}
					)
				buf = m.group(2)
			else:
				buf = (buf + " " + ln).strip() if buf else ln
		if buf:
			steps.append(
				{"action": self.format_bdd_action(buf.strip()), "expected": self.format_bdd_expected(expected_result)}
			)
		if not steps:
			steps = [
				{
					"action": self.format_bdd_action(
						"Given the initial test conditions are met\nWhen the user performs the required test actions\nAnd all necessary validations are completed"
					),
					"expected": self.format_bdd_expected(expected_result),
				}
			]
		return steps

	def format_bdd_action(self, action_text: str) -> str:
		if not action_text:
			return "When the test scenario is executed"
		action_lower = action_text.lower().strip()
		clean = re.sub(r"^[\d\.\s]*", "", action_text).strip().replace("**", "").strip()
		if not clean:
			return "When the test scenario is executed"
		if any(k in action_lower for k in ["login", "open", "navigate", "access", "setup", "prepare", "ensure", "system is ready"]):
			return f"Given {clean}"
		if any(k in action_lower for k in ["click", "select", "input", "press", "submit", "enter", "choose", "verify", "check", "observe", "confirm", "validate", "ensure"]):
			return f"When {clean}"
		return f"When {clean}"

	def format_bdd_expected(self, expected_text: str) -> str:
		if not expected_text:
			return "Then the system should behave as expected"
		clean = expected_text.strip().replace("**", "")
		if not clean:
			return "Then the system should behave as expected"
		return clean if clean.lower().startswith("then ") else f"Then {clean.lower()}"

	# ---------------------- Export building ----------------------
	def convert_to_squash_import_xls(self, test_cases_text: str, output_filename: Optional[str] = None, username: str = "QA_Bot") -> str:
		test_cases = self.parse_test_cases_from_telegram_result(test_cases_text)
		sheets = self.generate_squash_sheets_data(test_cases, username=username)
		if not output_filename:
			ts = datetime.now().strftime("%Y%m%d_%H%M%S")
			output_filename = f"squash_import_{ts}.xls"
		wb = xlwt.Workbook()
		for internal, rows in (
			("TEST_CASES", sheets.get("TEST_CASES", [])),
			("STEPS", sheets.get("STEPS", [])),
			("PARAMETERS", sheets.get("PARAMETERS", [])),
			("DATASETS", sheets.get("DATASETS", [])),
			("LINK_REQ_TC", sheets.get("LINK_REQ_TC", [])),
		):
			self.create_sheet(wb, internal, rows, internal)
		wb.save(output_filename)
		return output_filename

	def generate_squash_sheets_data(self, test_cases: List[Dict], username: str = "QA_Bot") -> Dict[str, List[Dict]]:
		sheets_data: Dict[str, List[Dict]] = {
			"TEST_CASES": [],
			"STEPS": [],
			"PARAMETERS": [],
			"DATASETS": [],
			"LINK_REQ_TC": [],
		}
		base_path = f"/Netmonk/G Generated Test Cases/Generated/{username}"
		current_date = datetime.now().strftime("%Y-%m-%d")

		for i, tc in enumerate(test_cases, 1):
			tc_name = tc.get("name", f"Test Case {i}")
			if re.match(r"^\d{3}\s+", tc_name):
				formatted_tc_name = tc_name
			else:
				formatted_tc_name = f"{i:03d} {tc_name}"
			clean_name = re.sub(r"[^\w\s]", "", formatted_tc_name)
			tc_path = f"{base_path}/{clean_name}"


			# Use preserved BDD lines exactly if present; otherwise, derive from steps without adding defaults
			if tc.get("bdd_lines"):
				bdd_lines = list(tc["bdd_lines"])  # already exact from chat
			else:
				# Extract only lines that already look like BDD from steps, without normalization or injection
				bdd_lines: List[str] = []
				for st in tc.get("steps", []) or []:
					for seg in ((st.get("action") or ""), (st.get("expected") or "")):
						seg = seg.strip()
						if not seg:
							continue
						for ln in seg.split("\n"):
							ln = ln.strip()
							if re.match(r"^(Given|When|Then|And)\b", ln):
								bdd_lines.append(ln)

			tc_script = f"Feature: Netmonk\nScenario: {tc_name}\n" + "\n".join(bdd_lines)
			tc_script = tc_script.replace("*", "")[:4000]

			test_case_row = {
				"ACTION": "C",
				"TC_PATH": tc_path,
				"TC_NUM": str(i),
				"TC_UUID": "",
				"TC_REFERENCE": "",
				"TC_NAME": tc_name[:255],
				"TC_MILESTONE": "",
				"TC_WEIGHT_AUTO": "0",
				"TC_WEIGHT": "",
				"TC_NATURE": tc.get("nature", "NAT_UNDEFINED"),
				"TC_TYPE": "TYP_UNDEFINED",
				"TC_STATUS": "WORK_IN_PROGRESS",
				"TC_DESCRIPTION": "",
				"TC_PRE_REQUISITE": "",
				"TC_CREATED_ON": current_date,
				"TC_CREATED_BY": username,
				"TC_KIND": "GHERKIN",
				"TC_SCRIPT": tc_script,
				"TC_AUTOMATABLE": "M",
			}
			sheets_data["TEST_CASES"].append(test_case_row)

		return sheets_data

	def create_sheet(self, workbook: Workbook, sheet_name: str, sheet_data: List[Dict], internal_name: str):
		ws = workbook.add_sheet(sheet_name)
		headers = self.headers.get(internal_name, [])
		for col, header in enumerate(headers):
			ws.write(0, col, header, self.header_style)
			if header in ["TC_PATH", "TC_OWNER_PATH", "TC_NAME", "TC_DESCRIPTION"]:
				ws.col(col).width = 256 * 30
			elif header in ["TC_STEP_ACTION", "TC_STEP_EXPECTED_RESULT"]:
				ws.col(col).width = 256 * 40
			else:
				ws.col(col).width = 256 * 15
		for row_idx, row in enumerate(sheet_data, 1):
			for col_idx, header in enumerate(headers):
				ws.write(row_idx, col_idx, str(row.get(header, "")), self.data_style)


# Convenience wrapper
def convert_to_squash_import_xls(test_cases_text: str, output_filename: Optional[str] = None, username: str = "QA_Bot") -> str:
	converter = SquashTMImportConverter()
	return converter.convert_to_squash_import_xls(test_cases_text, output_filename, username)


if __name__ == "__main__":
	sample = (
		"1) [VISUAL] Login Button Alignment Check\n"
		"Description: Validate layout\n"
		"Steps:\n"
		"1. Open the login page\n"
		"2. Observe the button alignment\n"
		"Expected Result: The button aligns correctly"
	)
	out = convert_to_squash_import_xls(sample, "test_squash_import.xls", "rakhaalif")
	print(f"Created: {out}")

