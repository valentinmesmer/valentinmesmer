#!/usr/bin/env python3
"""Count lines of code across all own repositories (including private ones) with
tokei and render a minimal SVG card.

Requirements: gh (logged in, or GH_TOKEN set), git, tokei.

Usage:
    python3 scripts/loc.py                 # writes github-metrics.svg
    python3 scripts/loc.py --out x.svg     # different output file
"""

import argparse
import base64
import json
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from xml.sax.saxutils import escape

# Languages that are not source code and would distort the numbers
IGNORED_LANGUAGES = {
    "Markdown", "JSON", "Plain Text", "SVG", "TOML", "YAML", "XML",
    "License", "Autoconf", "Jinja2", "Org", "ReStructuredText", "gitignore",
}

# Merge variants of the same language
ALIASES = {
    "TSX": "TypeScript", "JSX": "JavaScript",
    "C Header": "C", "C++ Header": "C++",
    "BASH": "Shell", "Zsh": "Shell", "Sh": "Shell",
}

COLORS = {
    "Java": "#b07219", "C": "#555555", "C++": "#f34b7d", "C#": "#178600",
    "TypeScript": "#3178c6", "JavaScript": "#f1e05a", "Python": "#3572A5",
    "Shell": "#89e051", "HTML": "#e34c26", "CSS": "#663399",
    "TeX": "#3D6117", "Go": "#00ADD8", "Rust": "#dea584", "Kotlin": "#A97BFF",
    "Swift": "#F05138", "PHP": "#4F5D95", "Ruby": "#701516", "Dockerfile": "#384d54",
    "SQL": "#e38c00", "Vue": "#41b883", "Svelte": "#ff3e00", "Lua": "#000080",
}
FALLBACK_COLOR = "#8b949e"
OTHER_COLOR = "#6e7681"

DEFAULT_SKIP = [
    "valentinmesmer/valentinmesmer",    # this profile repo
    "valentinmesmer/emberone-v1",       # earlier versions of emberone
    "valentinmesmer/emberone-v2",
    "valentinmesmer/emberone-v3",
    "valentinmesmer/thesis-libxml-2",   # copy of libxml2 (third-party code)
]


def run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def list_repos(skip):
    """All own, non-forked repositories (including private ones)."""
    out = run(["gh", "api", "--paginate", "user/repos?affiliation=owner&per_page=100"]).stdout
    # --paginate concatenates several JSON arrays; parse them one by one
    repos, dec, pos, out = [], json.JSONDecoder(), 0, out.strip()
    while pos < len(out):
        page, end = dec.raw_decode(out, pos)
        repos += page
        pos = end
        while pos < len(out) and out[pos].isspace():
            pos += 1
    own = [r for r in repos if not r["fork"]]
    names = [r["full_name"] for r in own if r["size"] > 0 and r["full_name"] not in skip]
    return names


def fetch_profile():
    """Total commits and join date of the authenticated user."""
    me = json.loads(run(["gh", "api", "user"]).stdout)
    login, since = me["login"], int(me["created_at"][:4])

    def gql(query, **fields):
        args = ["gh", "api", "graphql", "-f", f"query={query}"]
        for k, v in fields.items():
            args += ["-f", f"{k}={v}"]
        return json.loads(run(args).stdout)["data"]["user"]

    commits = 0
    for year in range(since, date.today().year + 1):
        c = gql(
            "query($login:String!,$from:DateTime!,$to:DateTime!){user(login:$login)"
            "{contributionsCollection(from:$from,to:$to){totalCommitContributions restrictedContributionsCount}}}",
            login=login, **{"from": f"{year}-01-01T00:00:00Z", "to": f"{year}-12-31T23:59:59Z"},
        )["contributionsCollection"]
        # private contributions show up as "restricted" unless they are enabled on the profile
        commits += c["totalCommitContributions"] + c["restrictedContributionsCount"]
    joined = datetime.strptime(me["created_at"], "%Y-%m-%dT%H:%M:%SZ").strftime("%b %Y")
    return {"commits": commits, "joined": joined}


def count_repo(full_name, workdir):
    target = workdir / full_name.replace("/", "__")
    try:
        run(["gh", "repo", "clone", full_name, str(target), "--", "--depth", "1", "--quiet"])
        res = run(["tokei", "--output", "json", str(target)])
        data = json.loads(res.stdout)
    except subprocess.CalledProcessError as e:
        print(f"  skipped: {full_name} ({e.stderr.strip()[:120]})", file=sys.stderr)
        return full_name, {}
    finally:
        shutil.rmtree(target, ignore_errors=True)
    langs = {}
    for lang, stats in data.items():
        if lang == "Total" or lang in IGNORED_LANGUAGES:
            continue
        if stats.get("code", 0) > 0:
            lang = ALIASES.get(lang, lang)
            langs[lang] = langs.get(lang, 0) + stats["code"]
    return full_name, langs


def aggregate(skip):
    repos = list_repos(skip)
    print(f"{len(repos)} repositories found", file=sys.stderr)
    totals = {}
    counted = 0
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for name, langs in pool.map(lambda r: count_repo(r, workdir), repos):
                if langs:
                    counted += 1
                for lang, code in langs.items():
                    totals[lang] = totals.get(lang, 0) + code
                print(f"  {name}: {sum(langs.values()):,} lines", file=sys.stderr)
    return totals, counted


def fmt(n):
    return f"{n:,}"


# Metrics card: neutral GitHub-like colors
STYLE = """<style>
  :root { --text:#1f2328; --muted:#656d76; --track:#d0d7de; }
  @media (prefers-color-scheme: dark) { :root { --text:#e6edf3; --muted:#8d96a0; --track:#30363d; } }
  text { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
  .t { fill: var(--text); }
  .m { fill: var(--muted); }
  .track { fill: var(--track); }
</style>"""

# Project card: EmberOne brand colors (Ember Red, charcoal, warm neutrals)
CARD_STYLE = """<style>
  :root { --text:#1a1a1a; --muted:#5c5852; --border:#d8d4cc; --card:#fdfcfa; --accent:#c1272d; }
  @media (prefers-color-scheme: dark) { :root { --text:#ecebe8; --muted:#9b9994; --border:#333436; --card:#1e1f21; --accent:#e0463f; } }
  text { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
  .t { fill: var(--text); }
  .m { fill: var(--muted); }
  .card { fill: var(--card); stroke: var(--border); }
  .accent { fill: var(--accent); }
</style>"""

WIDTH = 640
PAD = 20            # inner padding, same as the project card
INNER = WIDTH - 2 * PAD


def render(totals, repo_count, commits, joined, top=8):
    total = sum(totals.values())
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    shown = ranked[:top]
    rest = sum(v for _, v in ranked[top:])

    def color_of(lang):
        return COLORS.get(lang, FALLBACK_COLOR)

    # Stacked bar (languages beyond the top ones are grouped as grey remainder)
    bar, x = [], float(PAD)
    for lang, code in shown + ([("Other", rest)] if rest else []):
        w = INNER * code / total
        color = OTHER_COLOR if lang == "Other" else color_of(lang)
        bar.append(f'<rect x="{x:.2f}" y="96" width="{w:.2f}" height="8" fill="{color}"/>')
        x += w

    # Legend: two columns, dot + name on the left, lines and percentage on the right
    col_w = (INNER - 24) / 2
    list_y, row_h = 134, 26
    rows = (len(shown) + 1) // 2
    legend = []
    for i, (lang, code) in enumerate(shown):
        cx = PAD + (i % 2) * (col_w + 24)
        cy = list_y + (i // 2) * row_h
        legend.append(
            f'<circle cx="{cx + 4:.2f}" cy="{cy - 4}" r="4" fill="{color_of(lang)}"/>'
            f'<text x="{cx + 16:.2f}" y="{cy}" font-size="13" class="t">{escape(lang)}</text>'
            f'<text x="{cx + col_w:.2f}" y="{cy}" font-size="13" class="m" text-anchor="end">'
            f'{fmt(code)}  ·  {code / total * 100:.1f}%</text>'
        )
    foot_y = list_y + rows * row_h + 22

    height = foot_y + 14
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" role="img" aria-label="Lines of code">
{STYLE}
<text x="{PAD}" y="22" font-size="13" class="m">Lines of code</text>
<text x="{PAD}" y="68" font-size="42" font-weight="600" class="t">{fmt(total)}</text>
<text x="{WIDTH - PAD}" y="22" font-size="13" class="m" text-anchor="end">Repositories</text>
<text x="{WIDTH - PAD}" y="68" font-size="42" font-weight="600" class="t" text-anchor="end">{repo_count}</text>
<clipPath id="r"><rect x="{PAD}" y="96" width="{INNER}" height="8" rx="4"/></clipPath>
<rect x="{PAD}" y="96" width="{INNER}" height="8" rx="4" class="track"/>
<g clip-path="url(#r)">{''.join(bar)}</g>
{''.join(legend)}
<text x="{PAD}" y="{foot_y}" font-size="14" class="m"><tspan font-weight="600" class="t">{fmt(commits)}</tspan> commits</text>
<text x="{WIDTH - PAD}" y="{foot_y}" font-size="14" class="m" text-anchor="end">Joined <tspan font-weight="600" class="t">{escape(joined)}</tspan></text>
</svg>
"""


def render_project_card(icon_path, name_a, name_b, description, url_text):
    """Container card with the app icon (embedded as data URI)."""
    icon = base64.b64encode(Path(icon_path).read_bytes()).decode()
    height = 96
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" role="img" aria-label="{escape(name_a + name_b)}">
{CARD_STYLE}
<rect x="{PAD + 0.5}" y="0.5" width="{INNER - 1}" height="{height - 1}" rx="10" class="card"/>
<image x="{PAD + 20}" y="20" width="56" height="56" href="data:image/png;base64,{icon}"/>
<text x="{PAD + 94}" y="40" font-size="19" font-weight="700" class="t">{escape(name_a)}<tspan class="accent">{escape(name_b)}</tspan></text>
<text x="{PAD + 94}" y="60" font-size="13" class="m">{escape(description)}</text>
<text x="{PAD + 94}" y="78" font-size="12" class="m">{escape(url_text)}</text>
<text x="{WIDTH - PAD - 24}" y="55" font-size="22" class="accent" text-anchor="end">&#8594;</text>
</svg>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="github-metrics.svg")
    ap.add_argument("--card", default="emberone-card.svg")
    ap.add_argument(
        "--skip", nargs="*", default=DEFAULT_SKIP,
        help="repositories (owner/name) that are not counted",
    )
    args = ap.parse_args()

    totals, counted = aggregate(set(args.skip))
    if not totals:
        sys.exit("No lines counted, SVG not written.")
    profile = fetch_profile()
    Path(args.out).write_text(
        render(totals, counted, profile["commits"], profile["joined"]), encoding="utf-8"
    )
    icon = Path(__file__).resolve().parent.parent / "assets" / "emberone-icon.png"
    if icon.exists():
        Path(args.card).write_text(
            render_project_card(
                icon, "Ember", "One",
                "Multi-tenant fire department management platform",
                "github.com/valentinmesmer/emberone",
            ),
            encoding="utf-8",
        )
    print(f"{sum(totals.values()):,} lines in {counted} repos -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
