from typing import List
from datetime import datetime
import logging


logger = logging.getLogger(__name__)


class VisualAgent:
    """Visual test generation agent.

    Encapsulates visual prompts and generation for text-only and multimodal
    using the host bot's model and enforcement utilities.
    """

    def __init__(self, host_bot):
        self.bot = host_bot

    # ------------------------------
    # Public utility: format template (visual)
    # ------------------------------
    def get_format_template(self) -> str:
        """Return the user-facing format guide for visual test cases."""
        return (
            "\n🎨 Visual Test Format:\n"
            "Type: Visual\n"
            "Feature: [Nama fitur]\n"
            "Design Reference: [Figma link atau deskripsi]\n"
            "Device: [Desktop/Mobile/Tablet]\n"
            "Requirements: [Visual requirements]\n\n"
            "🎨 Contoh Visual Test:\n"
            "Type: Visual\n"
            "Feature: Dashboard Layout\n"
            "Design Reference: Figma dashboard design\n"
            "Device: Desktop 1920x1080\n"
            "Requirements: Validasi layout sesuai desain Figma\n"
        )

    # ------------------------------
    # Knowledge & prompts (visual)
    # ------------------------------
    def _load_visual_knowledge(self) -> str:
        # No-op: external knowledge files disabled
        return ''

    def _visual_only_guidelines(self) -> str:
        return (
            "STRICT VISUAL-ONLY GUARDRAILS:\n"
            "- SCOPE: UI/UX appearance only. Validate layout, alignment, spacing, size, color, typography, icons, images, borders, shadows, responsiveness, and accessibility.\n"
            "- DO NOT include functional flows, data processing, API/backend behavior, authentication, form submission logic, CRUD, DB validation, or calculations.\n"
            "- STEPS must NOT require clicking buttons to trigger business logic (e.g., create/save/login). Clicks are allowed only to reveal UI states (hover, focus, open modal).\n"
            "- Focus on what is visually present in the provided sources. Avoid inferring invisible behavior.\n"
            "- Use concrete visual assertions: exact labels/text, presence/absence of icons, color codes, pixel/spacing consistency, grid alignment, truncation/ellipsis rules, contrast ratio hints, and responsive breakpoints.\n"
            "- If a requirement implies functionality, restate it as a visual expectation (e.g., button state, disabled style, tooltip visibility).\n"
            "OUTPUT VALIDATION (append at the end):\n"
            "- Add a short 'Visual-only self-check' list confirming no functional steps or backend validations are present."
        )

    def _system_prompt(self) -> str:
        base = getattr(self.bot, 'qa_system_prompt', '')
        kb = self._load_visual_knowledge()
        suffix = (
            "\n\nROLE: You are a meticulous UI/UX visual QA generator. You ONLY produce visual assertions, "
            "never functional/business logic or backend/API checks."
        )
        if kb:
            suffix += f"\n\nTYPE-SPECIFIC KNOWLEDGE (VISUAL):\n{kb}"
        return base + suffix

    # ------------------------------
    # Text-only generation (visual)
    # ------------------------------
    async def generate_from_text(self, text: str) -> str:
        try:
            user_limit = self.bot._extract_requested_case_count(text or '')
            size_clause = (
                "GENERATE BDD TEST CASES"
                if not user_limit else f"GENERATE UP TO {user_limit} BDD TEST CASES"
            )

            prompt = f"""{self._system_prompt()}

TASK: {size_clause} FROM TEXT REQUIREMENTS (ENGLISH ONLY, NO ASTERISKS)

TEXT REQUIREMENTS:
{text}

TARGET: Generate VISUAL TEST CASES using custom knowledge base standards.

STRICT FORMAT INSTRUCTIONS (APPLY TO EVERY TEST CASE):
- Each test case MUST include exactly:
    - Numbered title line (001., 002., ...)
    - Given (required)
    - When (required)
    - Then (required)
    - And (optional; 0–2 lines)
- Do NOT skip Given/When/Then. English only. No asterisks (*). Only output test cases.
- Include positive, negative, and edge cases.

ADDITIONAL GWT RULES:
- Exactly one Given, one When, and one Then per test case, in that order. Use And for any additional preconditions/actions/outcomes.
- Each step line must start with exactly one keyword: Given/When/Then/And (no chained keywords, e.g., "When Given ...").
- Never place Given or When after Then; start a new numbered test case instead.
- Split different intents/states (e.g., empty vs large dataset) into separate numbered cases or use a Scenario Outline with Examples.
- Every test case must end with a Then step.

EXAMPLE SKELETON:
001. Title
Given ...
When ...
Then ...
And ... (optional)

TYPE-SPECIFIC RULES:
- VISUAL: Only UI/UX appearance. Do NOT assert actual data result correctness (filtering, search, sort), API/backend behavior, or business logic. Convert such intents into visual assertions (selected filter chip state, active sort icon, focus ring, placeholder, empty-state components, responsive layout, contrast).

OUTPUT SIZE: {'Generate 15–20 test cases (aim for 20)' if not user_limit else f'Generate no more than {user_limit} test cases; fewer is acceptable if content is insufficient.'} that follow project standards.
"""

            # Few-shot titles from Squash folder cache if available
            try:
                folder_samples = getattr(self.bot, 'squash_api_testcases_folder', []) or []
                if folder_samples:
                    examples_sorted = sorted(folder_samples, key=lambda x: x.get('id') or 0)
                    example_block_lines = [
                        "EXISTING PROJECT TEST CASE TITLE EXAMPLES (STYLE REFERENCE ONLY):"
                    ]
                    for ex in examples_sorted:
                        title = (ex.get('name') or '').strip()
                        if title:
                            example_block_lines.append(f"- {title}")
                    if len(example_block_lines) > 1:
                        prompt += "\n\n" + "\n".join(example_block_lines) + "\n"
            except Exception as inject_err:
                logger.debug(f"Visual few-shot injection skipped: {inject_err}")

            # Visual-only guardrails
            prompt += "\n\n" + self._visual_only_guidelines()

            response = self.bot.safe_generate(prompt)
            raw = (getattr(response, 'text', '') or '').strip()
            result = self.bot._finalize_output(raw, prompt, [prompt])
            result = self._enforce_bdd_and_type(result, max_count=user_limit)
            if not result:
                result = (
                    "001. Verify Visual Rendering From Requirements\n"
                    "Given the UI is available for inspection\n"
                    "When reviewing the described screens\n"
                    "Then the visual state matches the expected UI (labels/icons/spacing/colors)"
                )

            final_output = f"""🎨 VISUAL TEST CASES GENERATED (English Only)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 Source: Text Requirements
🔧 Test Type: Visual Testing
⏰ Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{result}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Enforcement: English only, asterisks removed
✅ Test cases generated successfully!
🔗 Ready for Squash TM import"""
            return final_output
        except Exception as e:
            logger.error(f"VisualAgent.generate_from_text error: {e}")
            return f"❌ Error generating visual test cases: {e}"

    # ------------------------------
    # Multimodal generation (visual)
    # ------------------------------
    async def generate_multimodal(self, images: List, text: str) -> str:
        try:
            format_instructions = ""
            examples = ""
            if getattr(self.bot, 'squash_integration', None):
                try:
                    format_instructions = self.bot.squash_integration.generate_format_instructions()
                    examples = self.bot.squash_integration.get_sample_test_cases_for_reference(test_type='visual')
                    if isinstance(examples, str) and examples:
                        examples = self.bot.sanitize_example_titles(examples)
                except Exception:
                    pass

            user_limit = self.bot._extract_requested_case_count(text or '')
            size_clause = "GENERATE BDD TEST CASES" if not user_limit else f"GENERATE UP TO {user_limit} BDD TEST CASES"

            prompt = f"""{self._system_prompt()}

TASK: {size_clause} FROM MULTIPLE IMAGES + TEXT REQUIREMENTS (ENGLISH ONLY, NO ASTERISKS)

STRICT FORMAT INSTRUCTIONS (APPLY TO EVERY TEST CASE):
- Each test case MUST have exactly these sections in order:
    1) Numbered title line (001., 002., ...)
    2) Given ...
    3) When ...
    4) Then ...
    5) And ... (optional; 0–2 lines only if needed)
- No bullets or numbering inside steps; each step must begin with Given/When/Then/And.
- English only. Do NOT use asterisks (*). No bracketed tags in titles.
- Output ONLY the test cases, no explanations.

ADDITIONAL GWT RULES:
- Exactly one Given, one When, and one Then per test case, in that order. Use And for any additional preconditions/actions/outcomes.
- Each step line must start with exactly one keyword: Given/When/Then/And (no chained keywords).
- Do not place Given or When after Then; start a new numbered test case instead.
- Split different intents/states into separate numbered cases or use a Scenario Outline with Examples.
- Ensure every test case ends with a Then.

PROJECT FORMAT REFERENCE:
{format_instructions}

EXAMPLES FROM PROJECT (SANITIZED):
{examples}

TARGET SCOPE: VISUAL

AGGREGATED REQUIREMENTS TEXT:
{text if text else 'Generate test cases based on images only'}

CONTENT RULES:
1) {'Generate 15–20 comprehensive test cases (aim for 20)' if not user_limit else f'Generate no more than {user_limit} comprehensive test cases; fewer is acceptable if content is insufficient.'}.
2) Include positive, negative, and edge cases where relevant.
3) Ensure naming and structure match Squash TM expectations.
4) Transform any functional intent into visual assertions only (appearance, alignment, spacing, color, typography, icons, responsive behavior, contrast, accessibility). Do NOT assert data correctness, filtering/search/sort outcomes, API/backend behavior, or business logic. You MAY assert visual cues (selected filter chip, active sort indicator, focus ring, placeholder visibility, empty-state card presence).
"""

            prompt += "\n\n" + self._visual_only_guidelines()

            parts = [prompt] + (images or [])
            # Centralized Gemini 2.0 multimodal fallback
            response = self.bot.multimodal_generate(parts)
            raw = (getattr(response, 'text', '') or '').strip()
            cleaned = self.bot._finalize_output(raw, prompt, [prompt])
            cleaned = self._enforce_bdd_and_type(cleaned, max_count=user_limit)
            if not cleaned:
                cleaned = (
                    "001. Verify Combined Visual Consistency\nGiven the screenshots are available\nWhen reviewing UI elements across images\nThen the visual state is consistent (labels/icons/spacing/colors)"
                )
            return cleaned
        except Exception as e:
            logger.error(f"VisualAgent.generate_multimodal error: {e}")
            return f"Error generating visual multi-image test cases: {e}"

    # ------------------------------
    # Image-only visual generation
    # ------------------------------
    async def image_only(self, image) -> str:
        """Generate visual test cases from a single image using custom knowledge base."""
        try:
            # Knowledge disabled; use minimal inline guidance
            cleaned_kb = (
                "Visual test case format: 001. <Title>\nGiven <precondition>\nWhen <user action>\nThen <expected strictly visual result>."
            )

            prompt = (
                cleaned_kb
                + "\n\nTASK: From the provided image generate strictly VISUAL BDD test cases covering all visible UI elements.\n"
                "OUTPUT RULES:\n"
                "- ENGLISH ONLY (no Indonesian words).\n"
                "- NO asterisk (*) or markdown bold.\n"
                "- Numbering: 001., 002., 003., ...\n"
                "- Each test: one title line then Given / When / Then lines.\n"
                "- Visual scope only: do not describe backend logic or data processing.\n"
                "- Omit any introduction or explanation; return ONLY the test cases.\n"
            )
            prompt += "\n\n" + self._visual_only_guidelines()

            response = self.bot.multimodal_generate([prompt, image])
            generated = (getattr(response, 'text', '') or '').strip()
            if not generated:
                generated = (
                    "001. Verify Visual Consistency Of UI Components\nGiven the UI screen is displayed\nWhen the UI is reviewed\nThen all visible labels, icons, colors, and spacing comply with the design"
                )
            generated = self.bot._sanitize_generated_output(generated)
            # Drop obvious Indonesian lines
            lines = []
            indo_tokens = [" aplikasi ", " ditampilkan", " ketika", " tombol", " ukuran", " konsisten", " berdasarkan", " gambar", " validasi", " Elemen", " terlihat", " pengguna", " dengan "]
            for l in generated.splitlines():
                low = f" {l.lower()} "
                if any(tok in low for tok in indo_tokens):
                    continue
                lines.append(l)
            cleaned_output = '\n'.join([l for l in lines if l.strip()])
            if not cleaned_output or cleaned_output.count('Given') == 0:
                cleaned_output = (
                    "001. Verify Header Layout\nGiven the dashboard screen is visible\nWhen the header area is reviewed\nThen title, logo, and user controls match the design (position, size, spacing)"
                )
            finalized = self.bot._finalize_output(cleaned_output, prompt, [prompt])
            enforced = self._enforce_bdd_and_type(finalized)
            return (
                "📸 Image-Only Visual Test Cases (English Only)\n\n---\n\n"
                + enforced
                + "\n\n---\n\n🛡 Enforcement: English only, no asterisks\n📊 Coverage: VISUAL scenarios\n🎯 Format: BDD (Given-When-Then)"
            )
        except Exception as e:
            logger.error(f"VisualAgent.image_only error: {e}")
            return f"❌ Error generating test cases from image: {e}"

    # ------------------------------
    # Enforcement: BDD + type (visual)
    # ------------------------------
    def _enforce_bdd_and_type(self, raw_text: str, max_count: int | None = None) -> str:
        if not raw_text:
            return raw_text
        import re
        lines = [ln.rstrip() for ln in raw_text.splitlines()]
        cases = []
        cur = None
        title_pat = re.compile(r"^\s*(\d{1,3})\.[\s\)]*(.+)")
        for ln in lines:
            m = title_pat.match(ln)
            if m:
                if cur:
                    cases.append(cur)
                num = m.group(1)
                title_raw = m.group(2).strip().rstrip('.')
                # If the title looks like a BDD step, record it to preserve as a step later
                import re as _re0
                is_bdd_title = _re0.match(r"^(Given|When|Then|And)\b", title_raw, flags=_re0.I) is not None
                cur = { 'num': num, 'title': title_raw, 'steps': [] }
                if is_bdd_title:
                    cur['title_as_step'] = title_raw
            else:
                if cur is None:
                    continue
                s = ln.strip()
                if not s:
                    continue
                if re.match(r"^(Given|When|Then|And)\b", s, flags=re.I):
                    head, rest = s.split(' ', 1) if ' ' in s else (s, '')
                    cur['steps'].append(f"{head.title()} {rest.strip()}".strip())
        if cur:
            cases.append(cur)

        def ensure_gwt(case):
            steps = case['steps']
            title = case['title']
            import re as _re
            def sanitize(line: str) -> str:
                pat = _re.compile(r"^(Given|When|Then|And|But)\s+", _re.I)
                tokens, rest = [], line
                while True:
                    m = pat.match(rest)
                    if not m:
                        break
                    tokens.append(m.group(1).title())   
                    rest = rest[m.end():]
                chosen = tokens[0] if tokens else 'When'
                if chosen in ('When','Then') and len(tokens) > 1 and tokens[1].lower() == 'given':
                    chosen = 'Given'
                return f"{chosen} {rest.strip()}".strip()

            steps = [sanitize(s) for s in steps]
            # If the title originally contained a BDD step, make sure it is preserved as a step
            tstep = case.get('title_as_step')
            if tstep:
                tstep_s = sanitize(tstep)
                if not any(_re.match(rf"^{_re.escape(tstep_s)}$", x, flags=_re.I) for x in steps):
                    # Insert based on the keyword
                    if tstep_s.lower().startswith('given '):
                        steps.insert(0, tstep_s)
                    elif tstep_s.lower().startswith('when '):
                        # Prefer after Given if present, else at start
                        gi = next((i for i,x in enumerate(steps) if x.lower().startswith('given ')), None)
                        insert_at = gi+1 if isinstance(gi,int) else 0
                        steps.insert(insert_at, tstep_s)
                    else:
                        steps.append(tstep_s)

            has_given = any(s.startswith('Given ') for s in steps)
            has_when = any(s.startswith('When ') for s in steps)
            has_then = any(s.startswith('Then ') for s in steps)
            if not has_given:
                steps.insert(0, 'Given the page or feature under test is available and visible')
            if not has_when:
                steps.append(f"When the scenario '{title}' is reviewed visually")
            if not has_then:
                steps.append('Then the visual state matches the expected UI (labels/icons/spacing/colors)')
            case['steps'] = steps
            # Normalize title: avoid BDD keywords in the title; derive from Then/When if needed
            def derive_title(ttl: str, stps: list[str]) -> str:
                if _re.match(r"^(Given|When|Then|And)\b", ttl, flags=_re.I):
                    # Prefer Then step for a "Verify ..." style
                    for s in stps:
                        if s.lower().startswith('then '):
                            return ('Verify ' + s[5:].strip()).rstrip('.')
                    for s in stps:
                        if s.lower().startswith('when '):
                            return s[5:].strip().rstrip('.')
                    for s in stps:
                        if s.lower().startswith('given '):
                            return s[6:].strip().rstrip('.')
                return ttl
            case['title'] = derive_title(title, steps)
            return case

        cases = [ensure_gwt(c) for c in cases]
        cap = max(1, min(int(max_count), 50)) if isinstance(max_count, int) and max_count > 0 else 20
        cases = cases[:cap]
        out_lines = []
        for idx, c in enumerate(cases, 1):
            num = f"{idx:03d}"
            out_lines.append(f"{num}. {c['title']}")
            out_lines.extend(c['steps'])
            out_lines.append('')
        return '\n'.join(out_lines).strip()

    # ------------------------------
    # Multimodal generation (image + text)
    # ------------------------------
    async def generate_multimodal_content(self, image, text: str, target_format: str) -> str:
        try:
            multimodal_prompt = f"""{self.bot.qa_system_prompt}

TASK: GENERATE MULTIMODAL TEST CASES IN {target_format.upper()} FORMAT (ENGLISH ONLY, NO ASTERISKS)

ENGLISH-ONLY ADAPTED GUIDELINES:
- Use numbering 001., 002., 003., ... (no bullets or asterisks)
- BDD lines strictly start with: Given, When, Then (capitalized, no bold, no asterisks)
- All content MUST be English (do NOT output Indonesian words)
- Absolutely NO asterisk (*) characters or Markdown bold formatting
- Provide clear concise title line per test: 001. <Title>
- If source content is insufficient, infer sensible UI/functional scenarios from image + text

SOURCES:
IMAGE: Derive UI components, layout, visible states
TEXT: {text if text else "No text provided; rely entirely on UI image"}

OUTPUT FORMAT SPEC:
Plain text test cases only, no decorative separators. Each test case: numbering + title, then Given / When / Then lines. No extra commentary after the last test case.

STRICT REQUIREMENTS:
✅ English only
✅ No asterisks
✅ Sequential numbering
✅ Valid BDD triad each test
✅ Visual assertions stay visual unless functional logic clearly stated in text

Generate the normalized test cases now."""
            response = self.bot.multimodal_generate([multimodal_prompt, image])
            raw = (getattr(response, 'text', '') or '').strip()
            cleaned = self.bot._finalize_output(raw, multimodal_prompt, [multimodal_prompt])
            if not cleaned:
                cleaned = (
                    "001. Sample Combined Scenario\nGiven the interface is displayed\nWhen the user reviews the combined elements\nThen all UI and functional aspects match the design and requirements"
                )
            return (
                f"🎯 Multi-Modal Generation Complete (English Only)\n\n🔄 Target Format: {target_format.upper()}\n\n---\n\n"
                + cleaned
                + "\n\n---\n\n🛡 Enforcement: English only, asterisks removed\n📁 Ready for: Squash TM import"
            )
        except Exception as e:
            logger.error(f"VisualAgent.generate_multimodal_content error: {e}")
            return f"❌ Error generating multimodal content: {e}"

    # ------------------------------
    # Visual-only image analysis (non-testcase)
    # ------------------------------
    async def image_analysis(self, image, context_text: str = "") -> str:
        """Analyze a single image for QA insights (no test case generation)."""
        try:
            image_prompt = f"""{self.bot.qa_system_prompt}

SINGLE-SOURCE IMAGE ANALYSIS

Analyze the provided image for QA purposes.

CONTEXT:
{context_text if context_text else "General QA analysis of the provided image"}

ANALYSIS REQUIREMENTS:
1. Visual Content Assessment: Identify all visible elements, UI components, features
2. Quality Assessment: Look for potential issues, inconsistencies, problems
3. Testing Opportunities: Suggest what can be tested based on what's visible
4. Recommendations: Provide actionable QA insights

Provide comprehensive analysis covering:
- What is shown in the image
- Potential testing scenarios based on visual content
- Quality considerations and recommendations
- Suggested test approach for the visible elements"""

            response = self.bot.safe_generate([image_prompt, image])
            body = getattr(response, 'text', '') or ''
            return (
                "📸 Image-Only Analysis\n\n"
                "📋 Analysis Source:\n"
                "✅ Image Content: Visual elements and interface analysis\n\n---\n\n"
                + body +
                "\n\n---\n\n✅ Image Analysis Complete!\n💡 Tip: Add text requirements for more comprehensive test case generation"
            )
        except Exception as e:
            logger.error(f"VisualAgent.image_analysis error: {e}")
            return f"❌ Error analyzing image: {e}"

    # ------------------------------
    # Visual elements extraction (structured)
    # ------------------------------
    async def extract_visual_elements(self, image) -> dict:
        """Extract consistent visual elements from an image into a structured dict."""
        try:
            visual_prompt = """EXTRACT VISUAL ELEMENTS from this image.

Return ONLY structured data in this exact format:
{
    "ui_components": ["component1", "component2", "component3"],
    "data_elements": ["element1", "element2", "element3"],
    "interactive_elements": ["button1", "link1", "icon1"],
    "layout_sections": ["header", "main content", "sidebar"],
    "visual_indicators": ["colors", "icons", "status indicators"],
    "testable_areas": ["area1", "area2", "area3"]
}

Focus on concrete, testable visual elements."""

            response = self.bot.multimodal_generate([visual_prompt, image])
            try:
                import json
                return json.loads(getattr(response, 'text', '') or '{}')
            except Exception:
                return {
                    "ui_components": ["scorecard", "data table", "navigation menu"],
                    "data_elements": ["device counts", "status values", "metrics"],
                    "interactive_elements": ["tooltips", "hover states", "clickable areas"],
                    "layout_sections": ["header", "scorecard section", "table section"],
                    "visual_indicators": ["status colors", "warning icons", "count displays"],
                    "testable_areas": ["scorecard validation", "table display", "tooltip behavior"],
                }
        except Exception as e:
            logger.error(f"VisualAgent.extract_visual_elements error: {e}")
            return {"ui_components": [], "data_elements": [], "interactive_elements": [], "layout_sections": [], "visual_indicators": [], "testable_areas": []}
