"""Versioned prompt loader (T-507).

Each prompt file has a front-matter block with ``prompt_version`` and ``purpose``. ``manifest.json``
stores the sha256 of every prompt file; loading a prompt whose content changed without a manifest
update raises ``PromptIntegrityError`` — any prompt change must ship as a new version.
Update the manifest with ``python -m siavash.agents.prompts --update-manifest``.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent
MANIFEST = PROMPT_DIR / "manifest.json"
_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class PromptIntegrityError(RuntimeError):
    pass


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _parse(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    m = _FRONT.match(text)
    if not m:
        raise PromptIntegrityError(f"{path.name}: missing front matter")
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    body = re.sub(r"<!--.*?-->\s*", "", text[m.end() :], flags=re.DOTALL).strip()
    return meta, body


def load_manifest() -> dict[str, dict[str, str]]:
    return json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}


def load_prompt(name: str) -> tuple[str, str]:
    """``(text, prompt_version)`` for ``name`` (e.g. ``"reviewer_v1"``), verified against the manifest."""
    path = PROMPT_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(path)
    entry = load_manifest().get(name)
    if entry is None or entry.get("sha256") != file_hash(path):
        raise PromptIntegrityError(f"prompt {name} changed without a manifest update / new version")
    meta, body = _parse(path)
    version = meta.get("prompt_version", name)
    if version != entry.get("prompt_version"):
        raise PromptIntegrityError(
            f"prompt {name}: version {version} != manifest {entry.get('prompt_version')}"
        )
    return body, version


def build_manifest() -> dict[str, dict[str, str]]:
    out = {}
    for p in sorted(PROMPT_DIR.glob("*.md")):
        meta, _ = _parse(p)
        out[p.stem] = {
            "prompt_version": meta.get("prompt_version", p.stem),
            "purpose": meta.get("purpose", ""),
            "sha256": file_hash(p),
        }
    return out


def main(argv: list[str]) -> int:
    if "--update-manifest" in argv:
        MANIFEST.write_text(
            json.dumps(build_manifest(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"wrote {MANIFEST}")
        return 0
    print(json.dumps(build_manifest(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
