"""检查公开文档的仓库内链接 / Check repository-relative public documentation links."""

from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\[[^\]\n]+\]\((?:<([^>\n]+)>|([^\s()]+))(?:\s+\"[^\"]*\")?\)")
failures = []
checked = 0

for document in sorted((*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md"))):
    for number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
        for match in LINK.finditer(line):
            url = urlsplit(match[1] or match[2])
            if url.scheme or url.netloc or not url.path:
                continue
            target = (document.parent / unquote(url.path)).resolve()
            checked += 1
            if not target.is_relative_to(ROOT) or not target.exists():
                failures.append(f"{document.relative_to(ROOT).as_posix()}:{number}: {url.path}")

for failure in failures:
    print(failure)
print(f"Checked {checked} relative links; {len(failures)} broken.")
raise SystemExit(bool(failures))
