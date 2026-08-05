#!/usr/bin/env python3
"""Bake ``banner.yml`` into the marked blocks of ``index.html``.

sivacor.org is served by GitHub Pages straight off ``main`` (``build_type:
legacy``) -- there is no build step to hook into, so the notice is baked in here
and committed alongside the config. Run this after every ``banner.yml`` edit and
commit both files:

    python scripts/set_banner.py
    git commit -am "Maintenance notice" && git push

Baking rather than rendering client-side keeps two properties worth having: the
Submit card is *really* inert (no ``href`` at all, not merely styled grey), and
there is no window in which a visitor sees a live Submit button before a script
disables it.

Two regions of ``index.html`` are generated, both delimited by HTML comments:

    NOTICE  -- the warning section above the link cards
    SUBMIT  -- the Submit card, either a live link or a disabled placeholder

Everything outside those markers is left untouched, and the script is
idempotent: it regenerates both regions from scratch on every run.

    python scripts/set_banner.py [--config banner.yml] [--page index.html]
    python scripts/set_banner.py --check   # exit 1 if the page is out of sync
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

import yaml

LEVELS = ("info", "warning", "critical")

# Font Awesome 6 glyphs; the stylesheet is already linked from index.html.
ICONS = {
    "info": "fa-circle-info",
    "warning": "fa-triangle-exclamation",
    "critical": "fa-circle-exclamation",
}

SUBMIT_URL = "https://submit.sivacor.org/"

# The live Submit card, restored whenever the notice is off or `disable_submit`
# is false. Kept here so the two states live side by side in one place.
SUBMIT_ENABLED = f"""\
            <a href="{SUBMIT_URL}" class="link-card">
                <i class="fas fa-cog"></i>
                <span>Submit</span>
            </a>"""

# A <span>, not an <a>: with no href there is nothing to activate, and
# aria-disabled + the visible sub-label tell assistive tech why.
SUBMIT_DISABLED = """\
            <span class="link-card link-card--disabled" role="link" aria-disabled="true">
                <i class="fas fa-cog"></i>
                <span>Submit</span>
                <small>Temporarily unavailable</small>
            </span>"""


def region(name: str) -> re.Pattern[str]:
    """Match a generated block.

    The BEGIN marker's own indentation is captured so the END marker can be
    re-emitted at the same depth, wherever in the page the markers happen to sit.
    """
    return re.compile(
        rf"(?P<indent>[ \t]*)(?P<begin><!-- {name}:BEGIN[^>]*-->)"
        rf"(?P<body>.*?)"
        rf"[ \t]*(?P<end><!-- {name}:END -->)",
        re.DOTALL,
    )


def load_config(path: Path) -> dict:
    """Return the normalized config. `enabled: false` yields a no-notice config."""
    if not path.exists():
        sys.exit(f"{path} not found.")

    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        # A hand-edited config with a broken block scalar or stray colon is the
        # likeliest failure here; a traceback would bury the useful part.
        sys.exit(f"{path} is not valid YAML:\n{exc}")

    if not isinstance(raw, dict):
        sys.exit(f"{path}: expected a mapping, got {type(raw).__name__}")

    message = str(raw.get("message") or "").strip()
    enabled = bool(raw.get("enabled")) and bool(message)
    if raw.get("enabled") and not message:
        print(f"{path}: enabled but message is empty -- treating as disabled.")

    level = str(raw.get("level") or "warning").strip()
    if level not in LEVELS:
        sys.exit(f"{path}: level must be one of {', '.join(LEVELS)}, got {level!r}")

    link = str(raw.get("link") or "").strip()
    link_text = str(raw.get("link_text") or "").strip()

    return {
        "enabled": enabled,
        "level": level,
        "message": message,
        # Only meaningful while a notice is up: a greyed-out Submit card with no
        # explanation on the page would just look broken.
        "disable_submit": enabled and bool(raw.get("disable_submit")),
        "link": link,
        # A link with no label would render as an invisible anchor.
        "link_text": link_text or ("Learn more" if link else ""),
    }


def render_notice(cfg: dict) -> str:
    if not cfg["enabled"]:
        return ""

    parts = [
        f'        <section class="notice notice--{cfg["level"]}" role="alert">',
        f'            <i class="fas {ICONS[cfg["level"]]}" aria-hidden="true"></i>',
        "            <div>",
        f"                <p>{html.escape(cfg['message'])}</p>",
    ]
    if cfg["link"]:
        href = html.escape(cfg["link"], quote=True)
        parts.append(
            f'                <p><a href="{href}">{html.escape(cfg["link_text"])}</a></p>'
        )
    parts += ["            </div>", "        </section>"]
    return "\n".join(parts)


def render(cfg: dict, page: str) -> str:
    """Return `page` with both generated regions rebuilt from `cfg`."""
    notice = render_notice(cfg)
    submit = SUBMIT_DISABLED if cfg["disable_submit"] else SUBMIT_ENABLED

    for name, body in (("NOTICE", notice), ("SUBMIT", submit)):

        def rebuild(match: re.Match[str], body: str = body) -> str:
            indent = match.group("indent")
            inner = f"\n{body}" if body else ""
            return f"{indent}{match.group('begin')}{inner}\n{indent}{match.group('end')}"

        page, count = region(name).subn(rebuild, page, count=1)
        if not count:
            sys.exit(
                f"Could not find the {name}:BEGIN/{name}:END markers in the page. "
                "They must be present for this script to know where to write."
            )
    return page


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=Path("banner.yml"))
    parser.add_argument("--page", type=Path, default=Path("index.html"))
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if the page does not match the config",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    if not args.page.exists():
        sys.exit(f"{args.page} not found.")

    current = args.page.read_text()
    updated = render(cfg, current)

    state = (
        f"{cfg['level']} notice ON" if cfg["enabled"] else "notice OFF"
    ) + (", Submit disabled" if cfg["disable_submit"] else ", Submit live")

    if args.check:
        if current != updated:
            sys.exit(
                f"{args.page} is out of sync with {args.config} "
                f"({state}). Run: python {Path(__file__).name}"
            )
        print(f"{args.page} is in sync with {args.config} ({state}).")
        return

    if current == updated:
        print(f"{args.page} already up to date ({state}).")
        return

    args.page.write_text(updated)
    print(f"{args.page} updated: {state}.")


if __name__ == "__main__":
    main()
