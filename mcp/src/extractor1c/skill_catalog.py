"""Read-only catalog of Markdown skills bundled with the MCP server."""
from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / "skills"
LEGACY_SKILL_IDS = {"codex/extractor1c": "extractor1c"}


def _skills() -> dict[str, Path]:
    skills: dict[str, Path] = {}
    if not SKILL_ROOT.is_dir():
        return skills
    for path in sorted(SKILL_ROOT.rglob("SKILL.md")):
        if not path.is_file() or not path.resolve().is_relative_to(SKILL_ROOT.resolve()):
            continue
        skill_id = path.parent.relative_to(SKILL_ROOT).as_posix()
        skills[skill_id] = path.parent
    return skills


def _description(text: str) -> str:
    match = re.match(r"\A---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return ""
    for line in match.group(1).splitlines():
        if line.startswith("description:"):
            return line.partition(":")[2].strip().strip('"\'')
    return ""


def list_skills_data() -> dict:
    """Return identifiers and Markdown documents available for each skill."""
    items = []
    for skill_id, directory in _skills().items():
        documents = sorted(
            path.relative_to(directory).as_posix()
            for path in directory.rglob("*.md")
            if path.is_file() and path.resolve().is_relative_to(directory.resolve())
        )
        items.append({
            "id": skill_id,
            "description": _description((directory / "SKILL.md").read_text(encoding="utf-8")),
            "documents": documents,
        })
    return {"skills": items}


def get_skill_data(skill_id: str, document: str = "SKILL.md") -> dict:
    """Read one cataloged Markdown document without accepting filesystem paths."""
    skill_id = LEGACY_SKILL_IDS.get(skill_id, skill_id)
    directory = _skills().get(skill_id)
    if directory is None:
        raise ValueError(f"Unknown skill: {skill_id}")
    documents = {
        path.relative_to(directory).as_posix(): path
        for path in directory.rglob("*.md")
        if path.is_file() and path.resolve().is_relative_to(directory.resolve())
    }
    path = documents.get(document)
    if path is None or not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError(f"Unknown skill document: {document}")
    return {"id": skill_id, "document": document,
            "content": path.read_text(encoding="utf-8")}
