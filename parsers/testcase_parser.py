import re
from typing import List, Dict


def parse_generated_test_cases(text: str) -> List[Dict]:
    """Parse generated test cases with improved BDD parsing (extracted from bot)."""
    try:
        test_cases: List[Dict] = []
        test_case_patterns = [
            r'(?=\*\*Test Case \d+:)', r'(?=Test Case \d+:)', r'(?=\d{3}\.\s)', r'(?=\[TC-\d+\])',
            r'(?=TC_\d+)', r'(?=\*\*\d+\.)', r'(?=## Test Case)', r'(?=### \d+\.)', r'(?=Scenario:)', r'(?=\*\*Scenario:)',
        ]
        case_blocks: List[str] = []
        for pattern in test_case_patterns:
            try:
                blocks = re.split(pattern, text)
                if len(blocks) > 1:
                    case_blocks = blocks
                    break
            except Exception:
                continue
        if not case_blocks or len(case_blocks) <= 1:
            case_blocks = [text]
        for block_idx, block in enumerate(case_blocks):
            if not block.strip():
                continue
            lines = block.strip().split('\n')
            tc_data = {
                'name': '', 'description': '', 'prerequisite': '', 'nature': 'FUNCTIONAL', 'type': 'COMPLIANCE', 'status': 'WORK_IN_PROGRESS', 'steps': []
            }
            title_found = False
            description_lines = []
            bdd_steps = []
            current_step: Dict = {}
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if not title_found:
                    title_patterns = [
                        r'\*\*Test Case (\d+):\s*(.+)\*\*', r'Test Case (\d+):\s*(.+)', r'(\d{3})\.\s*(.+)', r'\[TC-(\d+)\]\s*(.+)',
                        r'TC_(\d+)\s*(.+)', r'\*\*(\d+)\.\s*(.+)\*\*', r'## (.+)', r'### (\d+)\.\s*(.+)', r'Scenario:\s*(.+)', r'\*\*Scenario:\s*(.+)\*\*',
                    ]
                    for p in title_patterns:
                        m = re.match(p, line)
                        if m:
                            tc_data['name'] = m.group(2).strip() if len(m.groups()) == 2 else m.group(1).strip()
                            title_found = True
                            break
                    if not title_found and line and not line.startswith(('Given', 'When', 'Then', 'And')):
                        clean_line = re.sub(r'[*#]+\s*', '', line).strip()
                        if clean_line and len(clean_line) > 5:
                            tc_data['name'] = clean_line
                            title_found = True
                    continue
                if line.startswith('Given'):
                    if current_step:
                        bdd_steps.append(current_step)
                    current_step = {'type': 'given', 'action': line, 'expected': ''}
                elif line.startswith('When'):
                    if current_step:
                        bdd_steps.append(current_step)
                    current_step = {'type': 'when', 'action': line, 'expected': ''}
                elif line.startswith('Then'):
                    if current_step:
                        current_step['expected'] = line
                        bdd_steps.append(current_step)
                        current_step = {}
                    else:
                        bdd_steps.append({'type': 'then', 'action': line.replace('Then ', 'When the user performs the validation step: '), 'expected': line})
                elif line.startswith('And'):
                    if current_step:
                        if current_step.get('expected'):
                            current_step['expected'] += f"\n{line}"
                        else:
                            current_step['action'] += f"\n{line}"
                    else:
                        bdd_steps.append({'type': 'and', 'action': line, 'expected': ''})
                elif line.startswith('But'):
                    if current_step:
                        if current_step.get('expected'):
                            current_step['expected'] += f"\n{line}"
                        else:
                            current_step['action'] += f"\n{line}"
                    else:
                        bdd_steps.append({'type': 'but', 'action': line, 'expected': ''})
                elif line.startswith('Description:'):
                    tc_data['description'] = line.replace('Description:', '').strip()
                elif line.startswith('Pre-condition:') or line.startswith('Prerequisite:'):
                    tc_data['prerequisite'] = re.sub(r'^(Pre-condition|Prerequisite):\s*', '', line).strip()
                elif line.startswith('Nature:'):
                    nature = line.replace('Nature:', '').strip().upper()
                    if nature in ['FUNCTIONAL', 'NON_FUNCTIONAL', 'BUSINESS', 'USER_STORY']:
                        tc_data['nature'] = nature
                elif line.startswith('Type:') or line.startswith('Importance:'):
                    type_val = re.sub(r'^(Type|Importance):\s*', '', line).strip().upper()
                    type_mapping = {'HIGH': 'CRITICAL', 'CRITICAL': 'CRITICAL', 'MEDIUM': 'MAJOR', 'MAJOR': 'MAJOR', 'LOW': 'MINOR', 'MINOR': 'MINOR'}
                    tc_data['type'] = type_mapping.get(type_val, 'COMPLIANCE')
                elif not line.startswith(('Given', 'When', 'Then', 'And', 'Description:', 'Pre-condition:', 'Prerequisite:', 'Nature:', 'Type:', 'Importance:')):
                    if len(line) > 10:
                        description_lines.append(line)
            if current_step:
                bdd_steps.append(current_step)
            for step in bdd_steps:
                tc_data['steps'].append({'action': step['action'], 'expected': step['expected'] if step['expected'] else 'Step completed successfully'})
            if not tc_data['steps'] and description_lines:
                tc_data['description'] = ' '.join(description_lines[:2])
            if not tc_data['description']:
                tc_data['description'] = tc_data['name'] if tc_data['name'] else f"Test case {block_idx + 1}"
            if not tc_data['name']:
                tc_data['name'] = f"Test Case {block_idx + 1}"
            test_cases.append(tc_data)
        if not test_cases:
            test_cases.append({
                'name': 'Generated Test Case', 'description': 'Auto-generated test case due to parsing error', 'prerequisite': 'System is accessible', 'nature': 'FUNCTIONAL', 'type': 'COMPLIANCE', 'status': 'WORK_IN_PROGRESS',
                'steps': [{'action': 'Given the system is ready\nWhen user performs the test steps', 'expected': 'Then the system should respond correctly'}]
            })
        return test_cases
    except Exception:
        return [{
            'name': 'Generated Test Case', 'description': 'Auto-generated test case due to parsing error', 'prerequisite': 'System is accessible', 'nature': 'FUNCTIONAL', 'type': 'COMPLIANCE', 'status': 'WORK_IN_PROGRESS',
            'steps': [{'action': 'Given the system is ready\nWhen user performs the test steps', 'expected': 'Then the system should respond correctly'}]
        }]
