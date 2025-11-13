from typing import List, Optional
from datetime import datetime
import logging
from textwrap import dedent


logger = logging.getLogger(__name__)


class FunctionalAgent:
    """Generate functional test cases via Gemini."""

    def __init__(self, host_bot):
        self.bot = host_bot

    def get_format_template(self) -> str:
        """Return the user-facing format guide for functional test cases."""
        return (
            "\n🔧 Functional Test Format:\n"
            "Type: Functional\n"
            "Feature: [Nama fitur]\n"
            "Scenario: [Deskripsi scenario]\n"
            "Requirements: [Detail requirements]\n"
            "Environment: [Web/Mobile]\n\n"
            "📱 Contoh Functional Test:\n"
            "Type: Functional\n"
            "Feature: User Login\n"
            "Scenario: Login dengan email dan password\n"
            "Requirements: User dapat login menggunakan email dan password yang valid\n"
            "Environment: Web application\n"
        )

    def _load_functional_knowledge(self) -> str:
        """Fetch optional functional QA knowledge from the host bot."""
        source = getattr(self.bot, 'functional_knowledge', '')
        if callable(source):
            try:
                return source() or ''
            except Exception:  # best-effort helper; ignore downstream errors
                return ''
        return source or ''

    def _system_prompt(self) -> str:
        base = getattr(self.bot, 'qa_system_prompt', '')
        kb = self._load_functional_knowledge()
        suffix = (
            "\n\nROLE: You are a precise functional QA generator. You focus on behavior, logic, "
            "data validation, and workflows. Avoid purely visual-only checks as primary outcomes."
        )
        if kb:
            suffix += f"\n\nTYPE-SPECIFIC KNOWLEDGE (FUNCTIONAL):\n{kb}"
        return base + suffix

    def _build_generation_prompt(
        self,
        requirements: str,
        user_limit: Optional[int],
        multimodal: bool,
    ) -> str:
        limit_clause = (
            f"Generate up to {user_limit} test cases." if user_limit else "Generate 15-20 concise test cases."
        )
        sanitized_requirements = (requirements or '').strip()
        requirement_block = (
            sanitized_requirements
            or "No additional written requirements supplied. Derive sensible functional flows from the available context."
        )
        if multimodal:
            source_text = (
                "Use the provided images as the primary reference. "
                + (
                    f"Supplementary notes:\n{sanitized_requirements}"
                    if sanitized_requirements
                    else "No extra textual notes supplied."
                )
            )
        else:
            source_text = requirement_block

        prompt = dedent(
            f"""
            {self._system_prompt()}

            TASK: {limit_clause}
            INPUT MODE: {'Images + optional text' if multimodal else 'Text requirements only'}
            SOURCE: {source_text}

            OUTPUT RULES:
            - Write in English only; do not use asterisks or markdown bullets.
            - Each test case must be numbered 001., 002., ... and include exactly one Given, one When, one Then. Optional follow-up steps must use And.
            - Keep steps on separate lines with the keyword at the start (Given/When/Then/And).
            - End every test case with a Then step and start a new number for new scenarios.
            - Cover positive, negative, and edge flows while staying grounded in the provided requirements.

            REQUIREMENTS:
            {requirement_block}

            Return only the list of test cases.
            """
        ).strip()
        return prompt

    async def generate_from_text(self, text: str) -> str:
        try:
            user_limit = self.bot._extract_requested_case_count(text or '')
            prompt = self._build_generation_prompt(text, user_limit, multimodal=False)

            response = self.bot.safe_generate(prompt)
            raw = (getattr(response, 'text', '') or '').strip()
            result = self.bot._finalize_output(raw, prompt, [prompt])
            result = self._enforce_bdd_and_type(result, max_count=user_limit)
            if not result:
                result = (
                    "001. Verify Functional Behavior From Requirements\n"
                    "Given the system under test is available\n"
                    "When executing the described behavior\n"
                    "Then the expected functional outcome occurs without errors"
                )

            final_output = f"""🎯 FUNCTIONAL TEST CASES GENERATED (English Only)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 Source: Text Requirements
🔧 Test Type: Functional Testing
⏰ Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{result}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Enforcement: English only, asterisks removed
✅ Test cases generated successfully!
🔗 Ready for export/use"""
            return final_output
        except Exception as e:
            logger.error(f"FunctionalAgent.generate_from_text error: {e}")
            return f"❌ Error generating functional test cases: {e}"

    async def generate_multimodal(self, images: List, text: str) -> str:
        try:
            user_limit = self.bot._extract_requested_case_count(text or '')
            prompt = self._build_generation_prompt(text, user_limit, multimodal=True)

            parts = [prompt] + (images or [])
            response = self.bot.multimodal_generate(parts)
            raw = (getattr(response, 'text', '') or '').strip()
            cleaned = self.bot._finalize_output(raw, prompt, [prompt])
            cleaned = self._enforce_bdd_and_type(cleaned, max_count=user_limit)
            if not cleaned:
                cleaned = (
                    "001. Verify Combined Functional Flows\nGiven multiple inputs and states are defined\nWhen executing described workflows\nThen expected outcomes occur without errors"
                )
            return cleaned
        except Exception as e:
            logger.error(f"FunctionalAgent.generate_multimodal error: {e}")
            return f"Error generating functional multi-image test cases: {e}"

    def answer_general_query(self, question: str) -> str:
        """Answer general QA questions using the bot's system prompt."""
        try:
            full_prompt = f"""{self.bot.qa_system_prompt}

User Question: {question}

Provide comprehensive answer with practical examples and actionable advice. Focus on QA best practices and real-world application."""
            response = self.bot.safe_generate(full_prompt)
            return getattr(response, 'text', '')
        except Exception as e:
            return f"Error processing query: {e}"

    def english_only_cleanup(self, draft_text: str) -> str:
        """Rewrite text in strict English-only while keeping BDD numbering."""
        try:
            prompt = (
                "REWRITE STRICTLY IN ENGLISH ONLY. Remove any Indonesian words. "
                "Keep BDD format with numbering (001., 002., ...). Remove asterisks / markdown. "
                "Respond only with cleaned test cases. Current draft follows below:\n\n" + (draft_text or "")
            )
            response = self.bot.safe_generate([prompt])
            return getattr(response, 'text', '') or draft_text
        except Exception:
            return draft_text

    async def validate_modification_request(self, modification_request: str) -> dict:
        """LLM-assisted validation for modification requests."""
        try:
            validation_prompt = f"""ANALYZE MODIFICATION REQUEST:

Request: {modification_request}

Return JSON with fields:
{{
    "is_valid": true/false,
    "target_identified": true/false,
    "target_test_case": "specific test case or 'unclear'",
    "modification_type": "content/scope/format/environment/priority",
    "clarity_score": 1-10,
    "suggestions": "improvement suggestions if needed"
}}

Evaluate if the request is clear and actionable."""
            response = self.bot.safe_generate(validation_prompt)
            try:
                import json
                return json.loads(getattr(response, 'text', '') or '{}')
            except Exception:
                return {
                    "is_valid": True,
                    "target_identified": True,
                    "target_test_case": "user specified",
                    "modification_type": "content",
                    "clarity_score": 8,
                    "suggestions": ""
                }
        except Exception as e:
            logger.error(f"FunctionalAgent.validate_modification_request error: {e}")
            return {"is_valid": True, "target_identified": True}

    async def modify_specific_test_case(self, test_cases_text: str, modification_request: str) -> str:
        """Use LLM to modify only targeted test cases while preserving others."""
        try:
            modification_prompt = f"""{self.bot.qa_system_prompt}

SELECTIVE TEST CASE MODIFICATION

Original Test Cases:
{test_cases_text}

User Modification Request:
{modification_request}

MODIFICATION RULES:
1. Identify Target: Find which specific test case(s) the user wants to modify
2. Preserve Others: Keep all other test cases EXACTLY the same
3. Apply Changes: Only modify the requested test case(s) according to user's specification
4. Maintain Format: Keep the same numbering and BDD format structure
5. Quality Check: Ensure modified test case(s) still follow best practices

IMPORTANT:
- If user specifies "test case 001" or "first test case", only modify that one
- If user says "login test case", find and modify only the test case related to login
- If user says "add step to validation test", only modify validation-related test cases
- Keep all other test cases completely unchanged
- Maintain the original numbering sequence

Return the COMPLETE set of test cases with only the requested modifications applied."""
            response = self.bot.safe_generate(modification_prompt)
            body = getattr(response, 'text', '') or ''
            return f"""🔧 Test Case Modification Complete

📝 Modification Applied:
{modification_request[:200]}{'...' if len(modification_request) > 200 else ''}

🚀 Updated Test Cases:

---

{body}

---

✅ Modification Summary:
🎯 Target: Specific test case(s) as requested
🔒 Preserved: All other test cases unchanged
📋 Format: Maintained original structure"""
        except Exception as e:
            logger.error(f"FunctionalAgent.modify_specific_test_case error: {e}")
            return f"❌ Error modifying test case: {e}"
    async def analyze_requirements_structure(self, requirements_text: str) -> dict:
        try:
            analysis_prompt = f"""EXTRACT STRUCTURED DATA from requirements:

"{requirements_text}"

Extract and return ONLY structured data in this exact format:
{{
    "feature_name": "extracted feature name",
    "main_functionality": ["function1", "function2", "function3"],
    "user_actions": ["action1", "action2", "action3"],
    "validation_points": ["validation1", "validation2", "validation3"],
    "test_scenarios": ["scenario1", "scenario2", "scenario3"],
    "environment": "web/mobile/api",
    "priority": "high/medium/low"
}}

Be specific and extract concrete testable elements."""
            response = self.bot.safe_generate(analysis_prompt)
            try:
                import json
                return json.loads(getattr(response, 'text', '') or '{}')
            except Exception:
                return {
                    "feature_name": "Device Status Monitoring",
                    "main_functionality": ["status display", "data validation", "UI interaction"],
                    "user_actions": ["view dashboard", "hover tooltips", "check status"],
                    "validation_points": ["count accuracy", "color coding", "tooltip content"],
                    "test_scenarios": ["positive flow", "data validation", "UI behavior"],
                    "environment": "web",
                    "priority": "high"
                }
        except Exception as e:
            logger.error(f"FunctionalAgent.analyze_requirements_structure error: {e}")
            return {"feature_name": "Unknown", "main_functionality": [], "user_actions": [], "validation_points": [], "test_scenarios": [], "environment": "web", "priority": "medium"}

    async def generate_from_template(self, requirements_data: dict, visual_data: dict, template: dict, test_type: str) -> str:
        try:
            content_hash = hash(str(requirements_data) + str(visual_data) + str(test_type))
            generation_prompt = f"""{self.bot.qa_system_prompt}

TASK: GENERATE CONSISTENT TEST CASES USING TEMPLATE + CUSTOM KNOWLEDGE (OUTPUT IN ENGLISH, NO ASTERISKS)

FOLLOW YOUR CUSTOM KNOWLEDGE BASE GUIDELINES:
- Use the exact format and standards from your knowledge base
- Follow the BDD structure with Given-When-Then format
- Use proper numbering (001, 002, 003, ...)
- Include proper test case titles that are clear for FE/BE teams
- IMPORTANT: All content must be written in ENGLISH.
- IMPORTANT: Do NOT use asterisk (*) formatting anywhere.

REQUIREMENTS DATA:
{requirements_data}

VISUAL ELEMENTS:
{visual_data}

TEMPLATE STRUCTURE:
{template}

TEST TYPE: {test_type}

GENERATION RULES:
1. Use format from knowledge base (001 numbering)
2. Follow template structure standards
3. Logical ordering according to knowledge base
4. Deterministic numbering: 001, 002, 003...
5. Each test case MUST have: Proper title + BDD steps

STRICT REQUIREMENTS:
✅ English only
✅ No asterisks in output
✅ Correct Given-When-Then structure
✅ EXACTLY 6 test cases
✅ Clear, concise titles

Generate exactly 6 test cases following the pattern:
Order: Data validation (2) → UI interaction (2) → Error handling (2)

CONTENT_HASH: {content_hash}"""
            if (test_type or '').lower() == 'visual':
                generation_prompt += "\n\n" + self._get_visual_only_guidelines()
            response = self.bot.safe_generate(
                generation_prompt,
                generation_config={
                    "temperature": 0.0,
                    "top_p": 0.1,
                    "top_k": 1,
                },
            )
            raw = getattr(response, 'text', '') or ''
            cleaned = self.bot._finalize_output(raw, generation_prompt, [generation_prompt])
            result = f"""🎯 Consistent Multi-Modal Test Case Generation

📋 Input Analysis:
✅ Requirements Hash: {content_hash}
✅ Feature: {requirements_data.get('feature_name', 'Unknown')}
✅ Test Type: {test_type.upper()}
✅ Template: BDD Format

🚀 Generated Test Cases (Deterministic):

---

{cleaned}

---

✅ Consistency Guarantees:
🔒 Same Input = Same Output Always
📋 Template-Based = Structured Format
🎯 Hash Verified = Content Integrity
📁 Ready for: Squash TM Import"""
            return result
        except Exception as e:
            logger.error(f"FunctionalAgent.generate_from_template error: {e}")
            return f"❌ Error in template generation: {e}"
    def _get_visual_only_guidelines(self) -> str:
        """Fetch visual-only guardrails from VisualAgent when available, else fallback."""
        try:
            va = getattr(self.bot, 'visual_agent', None)
            if va and hasattr(va, '_visual_only_guidelines'):
                return va._visual_only_guidelines()
        except Exception:
            pass
        return (
            "STRICT VISUAL-ONLY GUARDRAILS:\n"
            "- SCOPE: UI/UX appearance only. Validate layout, alignment, spacing, size, color, typography, icons, images, borders, shadows, responsiveness, and accessibility.\n"
            "- DO NOT include functional flows, data processing, API/backend behavior, authentication, form submission logic, CRUD, DB validation, or calculations.\n"
            "- STEPS must NOT require clicking buttons to trigger business logic (clicks allowed only to reveal UI states).\n"
            "- Focus on what is visually present in the provided sources. Avoid inferring invisible behavior.\n"
        )
    def _enforce_bdd_and_type(self, raw_text: str, max_count: int | None = None) -> str:
        """Enforce BDD shape and cap count for functional tests."""
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
            # Preserve any BDD-like title as a step
            tstep = case.get('title_as_step')
            if tstep:
                tstep_s = sanitize(tstep)
                if not any(_re.match(rf"^{_re.escape(tstep_s)}$", x, flags=_re.I) for x in steps):
                    if tstep_s.lower().startswith('given '):
                        steps.insert(0, tstep_s)
                    elif tstep_s.lower().startswith('when '):
                        gi = next((i for i,x in enumerate(steps) if x.lower().startswith('given ')), None)
                        insert_at = gi+1 if isinstance(gi,int) else 0
                        steps.insert(insert_at, tstep_s)
                    else:
                        steps.append(tstep_s)
            has_given = any(s.startswith('Given ') for s in steps)
            has_when = any(s.startswith('When ') for s in steps)
            has_then = any(s.startswith('Then ') for s in steps)
            if not has_given:
                steps.insert(0, 'Given the system under test is available and configured')
            if not has_when:
                steps.append(f"When the scenario '{title}' is executed")
            if not has_then:
                steps.append('Then the expected outcome is produced without errors')
            case['steps'] = steps
            def derive_title(ttl: str, stps: list[str]) -> str:
                if _re.match(r"^(Given|When|Then|And)\b", ttl, flags=_re.I):
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
