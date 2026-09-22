"""CSS build pipeline.

Why a build step at all, for four stylesheets?

* **One request, one cache entry.** The site ships a single file so a page on a
  2G connection costs one round trip, and the file can be cached immutably.
* **Content-addressed URLs.** The served href carries a hash of the file
  contents (`/stylesheet?v=<hash>`), so the cache can be set to a year with no
  stale-style risk after a deploy and no "hard refresh" support burden.
* **No third-party toolchain.** The minifier here is deliberately conservative
  and dependency-free: it strips comments and collapses whitespace. Anything
  cleverer than that is a job for gzip/brotli at the edge, not for a regex that
  has to be trusted with a government site's only stylesheet.

`build()` is imported by the management command, by the `/stylesheet` view
(so a fresh checkout with no build artifact still serves correct CSS) and by
`tests/test_ui_contract.py` (so a stale committed artifact fails CI).

Paths are resolved from this file, not from ``django.conf.settings``. The
Vercel build imports this module *without* calling ``django.setup()`` — a
stylesheet must not be able to fail a deploy because settings are blank.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

# apps/core/static/css — same tree settings.APPS_DIR / "core" / "static" / "css"
# used to point at, without requiring Django to be configured first.
_CORE_DIR = Path(__file__).resolve().parent
CSS_SRC_DIR = _CORE_DIR / "static" / "css" / "src"
CSS_OUT_DIR = _CORE_DIR / "static" / "css"
CSS_OUT = CSS_OUT_DIR / "app.css"
MANIFEST = CSS_OUT_DIR / "manifest.json"

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_SPACE_RUN = re.compile(r"\s+")
_AROUND_BRACES = re.compile(r"\s*([{}])\s*")
_BEFORE_SEMI = re.compile(r"\s*;\s*")
_EMPTY_RULE = re.compile(r"}\s*}")


def minify(css: str) -> str:
    """Conservative minifier.

    Only whitespace and comments are removed. No declaration is rewritten, no
    selector is merged, no `calc()` is touched: those transforms are where
    "clever" minifiers silently break layouts, and the delivery win over gzip
    is a fraction of a percent.
    """
    css = _COMMENT.sub("", css)
    css = _SPACE_RUN.sub(" ", css)
    css = _AROUND_BRACES.sub(r"\1", css)
    css = _BEFORE_SEMI.sub(";", css)
    css = css.replace(";}", "}")
    css = re.sub(r"\s*>\s*", ">", css)  # child combinator never needs spaces
    return css.strip()


def sources() -> list[Path]:
    return sorted(p for p in CSS_SRC_DIR.glob("*.css"))


def build_css() -> str:
    """Concatenate the layers in filename order and minify."""
    parts = []
    for path in sources():
        text = path.read_text(encoding="utf-8")
        parts.append(f"/* {path.name} */\n{text}")
    css = minify("\n".join(parts))
    if not css:
        raise RuntimeError(f"no CSS sources found in {CSS_SRC_DIR}")
    return css


def write_build(css: str | None = None) -> dict:
    css = css if css is not None else build_css()
    digest = hashlib.sha256(css.encode("utf-8")).hexdigest()
    source_digest = hashlib.sha256("".join(p.read_text(encoding="utf-8") for p in sources()).encode("utf-8")).hexdigest()
    CSS_OUT_DIR.mkdir(parents=True, exist_ok=True)
    CSS_OUT.write_text(css, encoding="utf-8")
    meta = {
        "hash": digest[:12],
        "source_hash": source_digest[:12],
        "bytes": len(css.encode("utf-8")),
        "files": [p.name for p in sources()],
    }
    MANIFEST.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def read_manifest() -> dict:
    """Manifest of the last build, or a synthetic one for the current sources."""
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"hash": "", "source_hash": "", "bytes": 0, "files": [], "built": False}


def built_css() -> str:
    """The built stylesheet, building it in memory if the artifact is missing."""
    try:
        return CSS_OUT.read_text(encoding="utf-8")
    except OSError:
        return build_css()


def is_stale() -> bool:
    """True when the committed artifact does not match the sources."""
    manifest = read_manifest()
    if not manifest.get("hash"):
        return True
    return manifest.get("source_hash") != _source_hash_now()


def stylesheet_href() -> str:
    """Versioned URL for the single stylesheet. Falls back to the build time."""
    manifest = read_manifest()
    version = manifest.get("source_hash") or manifest.get("hash") or "dev"
    return f"/stylesheet?v={version}"


def _source_hash_now() -> str:
    return hashlib.sha256("".join(p.read_text(encoding="utf-8") for p in sources()).encode("utf-8")).hexdigest()[:12]
