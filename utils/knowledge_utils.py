import os


def load_custom_knowledge() -> str:
    """Load custom knowledge base from file or create default."""
    knowledge_file = "custom_knowledge.txt"

    if os.path.exists(knowledge_file):
        try:
            with open(knowledge_file, 'r', encoding='utf-8') as f:
                content = f.read()
            print(f"✅ Loaded custom knowledge from {knowledge_file}")
            return content
        except Exception as e:
            print(f"⚠️ Error loading knowledge file: {e}")
            return create_default_knowledge()
    else:
        print("📝 Creating default knowledge base...")
        return create_default_knowledge()


def _read_file_if_exists(file_path: str) -> str:
    """Read a file if it exists, otherwise return empty string."""
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read().strip()
        return ""
    except Exception as e:
        print(f"⚠️ Error reading {file_path}: {e}")
        return ""


def load_combined_knowledge() -> str:
    """Load and combine only functional and visual knowledge files.

    Order of concatenation (if present):
    1) functional_knowledge.txt
    2) visual_knowledge.txt
    If neither exists, return empty string (do not load or create custom knowledge).
    """
    functional = _read_file_if_exists("functional_knowledge.txt")
    visual = _read_file_if_exists("visual_knowledge.txt")

    parts = []
    if functional:
        parts.append("=== FUNCTIONAL KNOWLEDGE ===\n" + functional)
    if visual:
        parts.append("=== VISUAL KNOWLEDGE ===\n" + visual)

    if parts:
        combined = "\n\n\n".join(parts)
        print(
            f"✅ Combined knowledge loaded (functional={bool(functional)}, visual={bool(visual)})"
        )
        return combined

    # No functional/visual knowledge found; proceed without combined knowledge
    print("ℹ️ No functional_knowledge.txt or visual_knowledge.txt found; proceeding without combined knowledge.")
    return ""


def create_default_knowledge() -> str:
    """Create and persist a default knowledge base template."""
    default_knowledge = """
    === CUSTOM QA KNOWLEDGE BASE ===

    1. FUNCTIONAL TESTING PRINCIPLES:
    - Test semua user flows dan business logic
    - Validasi input dan output sesuai requirements
    - Test positive, negative, dan edge cases
    - Verifikasi error handling dan validation
    - Test integrasi antar komponen

    2. VISUAL TESTING STANDARDS:
    - Validasi UI sesuai desain Figma
    - Test responsivitas di berbagai device
    - Verifikasi color, typography, dan spacing
    - Test layout dan positioning elements
    - Accessibility testing (contrast, keyboard navigation)

    3. BDD FORMAT STANDARDS:
    - Given: Kondisi awal/prerequisites
    - When: Aksi yang dilakukan user
    - Then: Hasil yang diharapkan
    - And: Langkah tambahan jika diperlukan

    4. MOBILE TESTING CHECKLIST:
    - Test di berbagai device dan OS
    - Orientation changes (portrait/landscape)
    - Touch gestures dan interactions
    - Network conditions (WiFi/4G/offline)
    - App permissions dan notifications

    5. DEVICE MONITORING TESTING:
    - Status validation (UP, DOWN, UNDETECTED)
    - Scorecard accuracy testing
    - Color coding verification
    - Tooltip functionality testing
    - Modal popup behavior testing
    - Table filtering and priority testing

    Add your own knowledge here...
    """

    # Save default knowledge to file
    try:
        with open("custom_knowledge.txt", 'w', encoding='utf-8') as f:
            f.write(default_knowledge)
        print("📁 Default knowledge saved to custom_knowledge.txt")
    except Exception as e:
        print(f"⚠️ Error creating default knowledge file: {e}")

    return default_knowledge
