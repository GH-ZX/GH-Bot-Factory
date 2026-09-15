from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CURRENT_STATE = ROOT / "docs/operations/CURRENT_STATE.md"
CHANGELOG = ROOT / "docs/operations/CHANGELOG_AGENT.md"
ROADMAP = ROOT / "docs/roadmap.md"
HANDOFF = ROOT / "docs/operations/AGENT_HANDOFF.md"
MIGRATIONS = ROOT / "migrations/versions"


def _read(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"Missing required handoff file: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def _field(text: str, label: str) -> str:
    match = re.search(rf"^- \*\*{re.escape(label)}:\*\*\s*(.+?)\s*$", text, flags=re.MULTILINE)
    if not match:
        raise SystemExit(f"CURRENT_STATE.md is missing required field: {label}")
    return match.group(1).strip()


def main() -> None:
    current = _read(CURRENT_STATE)
    changelog = _read(CHANGELOG)
    roadmap = _read(ROADMAP)
    handoff = _read(HANDOFF)

    phase = _field(current, "Current phase")
    migration_head = _field(current, "Current migration head").strip("`")

    if phase not in changelog:
        raise SystemExit(f"Current phase is not represented in CHANGELOG_AGENT.md: {phase}")
    phase_label = phase.split(" — ", 1)[0]
    if phase_label not in roadmap:
        raise SystemExit(f"Current phase label is not represented in docs/roadmap.md: {phase_label}")

    migration_matches = list(MIGRATIONS.glob(f"{migration_head}_*.py"))
    if not migration_matches:
        raise SystemExit(
            "CURRENT_STATE.md migration head does not map to a migration file: "
            f"{migration_head}"
        )

    required_handoff_phrases = (
        "Milestone Completion Documentation Gate",
        "Canonical gate",
        "Runnable/local gate",
        "Blocked gate",
    )
    for phrase in required_handoff_phrases:
        if phrase not in handoff:
            raise SystemExit(f"AGENT_HANDOFF.md is missing required protocol phrase: {phrase}")

    print(f"Handoff consistency OK: {phase}; migration head {migration_head}.")


if __name__ == "__main__":
    main()
