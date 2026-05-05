#!/usr/bin/env python3
"""Validate local Markdown links and anchors across this repository."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote


SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}
SKIP_PATH_PREFIXES = {
    ".codex/host-views",
}
SKIP_SCHEMES = (
    "http://",
    "https://",
    "mailto:",
    "tel:",
    "data:",
    "app://",
    "plugin://",
)
INLINE_LINK_RE = re.compile(r"(!?)\[[^\]]*]\(([^)\n]+)\)")
REFERENCE_LINK_RE = re.compile(r"^\s*\[[^\]]+]:\s+(\S+)", re.MULTILINE)
FENCED_BLOCK_RE = re.compile(r"(^|\n)```.*?(\n```|$)", re.DOTALL)
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate repository Markdown links and anchors.")
    parser.add_argument("--root", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    return parser.parse_args()


def strip_fenced_blocks(text: str) -> str:
    return FENCED_BLOCK_RE.sub("\n", text)


def iter_markdown_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.md"):
        relative = path.relative_to(root)
        relative_text = relative.as_posix()
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if any(relative_text.startswith(prefix) for prefix in SKIP_PATH_PREFIXES):
            continue
        files.append(path)
    return sorted(files)


def extract_target(raw: str) -> str:
    value = raw.strip()
    if value.startswith("<") and ">" in value:
        return value[1 : value.index(">")].strip()
    return value.split()[0].strip()


def split_target(target: str) -> tuple[str, str]:
    if "#" not in target:
        return target, ""
    path_part, anchor = target.split("#", 1)
    return path_part, anchor


def is_external_or_special(target: str) -> bool:
    normalized = target.strip().lower()
    if not normalized:
        return True
    if normalized in {"...", "…"}:
        return True
    if normalized.startswith(SKIP_SCHEMES):
        return True
    return normalized.startswith("javascript:")


def github_anchor(text: str) -> str:
    normalized = text.strip().lower()
    normalized = re.sub(r"`([^`]*)`", r"\1", normalized)
    normalized = re.sub(r"<[^>]+>", "", normalized)
    normalized = re.sub(r"[^\w\u4e00-\u9fff\- ]+", "", normalized)
    normalized = re.sub(r"\s+", "-", normalized.strip())
    return normalized


def markdown_anchors(path: Path) -> set[str]:
    text = strip_fenced_blocks(path.read_text(encoding="utf-8"))
    anchors: set[str] = set()
    seen: dict[str, int] = {}
    for match in HEADING_RE.finditer(text):
        base = github_anchor(match.group(2))
        if not base:
            continue
        count = seen.get(base, 0)
        seen[base] = count + 1
        anchors.add(base if count == 0 else f"{base}-{count}")
    return anchors


def link_targets(path: Path) -> list[str]:
    text = strip_fenced_blocks(path.read_text(encoding="utf-8"))
    targets = [extract_target(match.group(2)) for match in INLINE_LINK_RE.finditer(text)]
    targets.extend(extract_target(match.group(1)) for match in REFERENCE_LINK_RE.finditer(text))
    return targets


def resolve_local_path(root: Path, source: Path, path_part: str) -> Path:
    decoded = unquote(path_part)
    if not decoded:
        return source
    if decoded.startswith("/"):
        return root / decoded.lstrip("/")
    return (source.parent / decoded).resolve()


def validate(root: Path) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    anchor_cache: dict[Path, set[str]] = {}
    for source in iter_markdown_files(root):
        for raw_target in link_targets(source):
            if is_external_or_special(raw_target):
                continue
            path_part, anchor = split_target(raw_target)
            if path_part.startswith("/") and not (root / path_part.lstrip("/")).exists():
                issues.append(
                    {
                        "file": str(source.relative_to(root)),
                        "target": raw_target,
                        "issue": "absolute-local-path",
                    }
                )
                continue
            target_path = resolve_local_path(root, source, path_part)
            if not target_path.exists():
                issues.append(
                    {
                        "file": str(source.relative_to(root)),
                        "target": raw_target,
                        "issue": "missing-target",
                    }
                )
                continue
            if anchor and target_path.suffix.lower() in {".md", ".markdown"}:
                if target_path not in anchor_cache:
                    anchor_cache[target_path] = markdown_anchors(target_path)
                normalized_anchor = github_anchor(anchor)
                if normalized_anchor and normalized_anchor not in anchor_cache[target_path]:
                    issues.append(
                        {
                            "file": str(source.relative_to(root)),
                            "target": raw_target,
                            "issue": "missing-anchor",
                        }
                    )
    return issues


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()
    issues = validate(root)
    payload = {"status": "ok" if not issues else "error", "issue_count": len(issues), "issues": issues}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif issues:
        for issue in issues:
            print(f"{issue['file']}: {issue['issue']}: {issue['target']}")
    else:
        print("markdown integrity ok")
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
