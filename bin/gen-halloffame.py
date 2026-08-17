#!/usr/bin/env python3
"""Generate docs.manual/HallOfFame.html — the season's player awards.

The Hall of Fame is the season-end trophy case: one winner per award (ties list
every tied player), mined from the server's persistent data — the kill ledger, the
character vault, the bank, the merit ledger and the reconstructed session history.

Usage:
    python3 bin/gen-halloffame.py                 # writes docs.manual/HallOfFame.html
    python3 bin/gen-halloffame.py --check         # compute + report, write nothing
    python3 bin/gen-halloffame.py --json out.json # also dump the computed data

Like bin/gen-roadmap.py, the output is a standalone <body><main> document; the wiki
build (nwn-manager wiki -> render_manual_pages) strips the head/body and injects the
shared site header, footer and nav. Do NOT run the wiki refresh after editing —
the scheduled daily refresh folds docs.manual/ into docs/.

Every run prints a concentration report to stderr: how many awards each player won,
plus anything the identity bridge could not place. That report is the tuning signal —
if one player sweeps, adjust the categories in bin/halloffame/categories.py.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))

from halloffame import awards as A                     # noqa: E402
from halloffame import bicreader, roadmapawards, sources, twoda   # noqa: E402
from halloffame.identity import build_roster           # noqa: E402

OUT_PATH = REPO / "docs.manual" / "HallOfFame.html"
DEV_REPO = Path.home() / "GIT" / "nwn_homers_lotr"

# Familiar / animal-companion type ids (nwscript.nss FAMILIAR_CREATURE_TYPE_* and
# ANIMAL_COMPANION_CREATURE_TYPE_*). 255 is "none" in both and never reaches us.
FAMILIARS = {
    0: "Bat", 1: "Cragcat", 2: "Hell Hound", 3: "Imp", 4: "Fire Mephit",
    5: "Ice Mephit", 6: "Pixie", 7: "Raven", 8: "Fairy Dragon",
    9: "Pseudodragon", 10: "Eyeball",
}
COMPANIONS = {
    0: "Badger", 1: "Wolf", 2: "Bear", 3: "Boar", 4: "Hawk",
    5: "Panther", 6: "Spider", 7: "Dire Wolf", 8: "Dire Rat",
}

E = html.escape


# =========================================================================== #
# Context — everything the award functions read, resolved once
# =========================================================================== #

class Ctx:
    def __init__(self, args):
        db = Path(args.db_dir)

        bestiary = sources.open_ro(db / "bestiarydb.sqlite3")
        bank = sources.open_ro(db / "bankdb.sqlite3")
        meaning = sources.open_ro(db / "meaningwave.sqlite3")
        respawn = sources.open_ro(db / "respawndb.sqlite3")
        merit = sources.open_ro(Path(args.merit_db))
        admin = sources.open_ro(Path(args.admin_db))

        self.kills = sources.load_kills(bestiary)
        self.server_firsts = sources.load_server_firsts(bestiary)
        self.catalogue = sources.load_catalogue(bestiary)
        aliases = sources.load_kill_aliases(bestiary)
        self.bosses = sources.load_bosses(respawn)
        self.merit_ledger = sources.load_merit_ledger(merit)
        self.houses = sources.load_houses(admin)
        self.sessions = sources.load_sessions(Path(args.activity_cache))
        self.creatures = sources.load_creature_index(
            Path(args.module_index) / "creature_index.json"
        )

        # Collapse re-skinned duplicates so one creature is one species.
        for k in self.kills:
            k["resref"] = aliases.get(k["resref"], k["resref"])

        self.chars = bicreader.load_vault(
            Path(args.vault), Path(args.bic_cache) if args.bic_cache else None
        )
        self.roster = build_roster(self.sessions, self.kills, self.chars)

        # Fold characters onto their (possibly merged) account key.
        from halloffame.identity import canon_cdkey
        for c in self.chars:
            c["cdkey"] = canon_cdkey(c["cdkey"])
        for k in self.kills:
            k["cdkey"] = canon_cdkey(k["cdkey"])
        self.by_account: dict[str, list[dict]] = defaultdict(list)
        for c in self.chars:
            self.by_account[c["cdkey"]].append(c)

        # The playerid bridge, for the two key/value DBs that predate CD keys.
        pid_map = self.roster.build_playerid_map()
        self.bank_personal = self._kv_by_account(bank, "bankgp", pid_map)
        self.bank_family_gp = self._kv_by_cdkey_suffix(bank, "fam_bankgp_%")
        self.bank_family_xp = self._kv_by_cdkey_suffix(bank, "fam_xp_%")

        self.meaningwave = []
        for varname, playerid, value in sources.kv_ints(meaning, "%"):
            if not value:
                continue
            cdkey = self.roster.resolve_playerid(playerid, pid_map)
            if cdkey:
                # playerid is kept: Meaningwave progress is per *character*, and the
                # award needs both the roster-wide total and the best single character.
                self.meaningwave.append((varname, canon_cdkey(cdkey), playerid))

        # Lookup tables.
        twoda_dir = Path(args.twoda_dir)
        self.classes = twoda.read_labels(twoda_dir / "classes.2da")
        self.races = twoda.read_labels(twoda_dir / "racialtypes.2da")
        feats = twoda.read_labels(twoda_dir / "feat.2da")
        # Two label families mean the same feat: the stock rows are
        # FEAT_EPIC_DEVASTATING_CRITICAL_* (~495+) and CEP adds bare
        # DEVASTATING_CRITICAL_* (~24673+) for its extra weapons. Matching only the
        # bare form found zero holders, because no player has a CEP-weapon devcrit.
        self.devcrit_feats = {
            i for i, label in feats.items() if "DEVASTATING_CRITICAL" in label
        }
        self.skills = {
            int(k): v for k, v in json.loads(
                (Path(args.skills_json)).read_text(encoding="utf-8")
            ).items() if k.isdigit()
        } if Path(args.skills_json).is_file() else {}

        # Number of Meaningwave guides the module ships, derived from the DB's own
        # `u_<guide>` keys rather than hardcoded, so adding a philosopher needs no edit here.
        self.mw_guide_count = len({v for v, _, _ in self.meaningwave if v.startswith("u_")}) or 7

        self.all_resrefs = {k["resref"] for k in self.kills}
        self.boss_alignment = A.load_boss_alignment(Path(args.unpacked), self.bosses)
        self.house_sizes = self._house_sizes(Path(args.unpacked))

    # -- key/value helpers -------------------------------------------------- #

    def _kv_by_account(self, conn, varname, pid_map) -> dict[str, int]:
        """Sum a per-character int var up to the owning account."""
        from halloffame.identity import canon_cdkey
        out: dict[str, int] = defaultdict(int)
        for _, playerid, value in sources.kv_ints(conn, varname):
            cdkey = self.roster.resolve_playerid(playerid, pid_map)
            if cdkey:
                out[canon_cdkey(cdkey)] += value
        return dict(out)

    def _kv_by_cdkey_suffix(self, conn, like) -> dict[str, int]:
        """`fam_bankgp_<CDKEY>` style keys, which already carry the account key."""
        from halloffame.identity import canon_cdkey
        out: dict[str, int] = defaultdict(int)
        for varname, _, value in sources.kv_ints(conn, like):
            out[canon_cdkey(varname.rsplit("_", 1)[-1])] += value
        return dict(out)

    def _house_sizes(self, unpacked: Path) -> dict[str, int]:
        """cdkey -> floor tiles of the granted estate.

        ``houses.area_tag`` is an area *tag*, not a resref, so the area files have
        to be scanned for it. Only done when houses exist (there are two).
        """
        if not self.houses:
            return {}
        wanted = {h["area_tag"]: h["cdkey"] for h in self.houses if h["area_tag"]}
        sizes: dict[str, int] = {}
        for path in Path(unpacked).glob("*.are.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            tag = (data.get("Tag") or {}).get("value")
            if tag in wanted:
                tiles = (data.get("Tile_List") or {}).get("value") or []
                sizes[wanted[tag]] = len(tiles)
        return sizes

    # -- name resolution ---------------------------------------------------- #

    def creature_name(self, resref: str) -> str:
        """Best available display name. The bestiary catalogue sometimes stores a
        bare resref (``nw_rat001``), in which case the module index knows better."""
        cat = (self.catalogue.get(resref) or {}).get("name") or ""
        if cat and not cat.lower().startswith(("nw_", "x0_", "x2_")):
            return cat
        idx = self.creatures.get(resref) or {}
        return idx.get("name") or cat or resref

    def class_name(self, cls: int) -> str:
        return twoda.prettify(self.classes.get(cls, f"Class {cls}"))

    def race_name(self, race: int) -> str:
        return twoda.prettify(self.races.get(race, f"Race {race}"))

    def skill_name(self, skill: int) -> str:
        return self.skills.get(skill, f"Skill {skill}")

    @staticmethod
    def pet_name(kind: str, type_id: int) -> str:
        table = FAMILIARS if kind == "familiar" else COMPANIONS
        return table.get(type_id, f"Unknown {kind}")


# =========================================================================== #
# Rendering
# =========================================================================== #

STYLE = """
  <style>
    /* Two-column layout, matching the other manual pages. */
    .mw-layout { display: flex; gap: 2.5em; align-items: flex-start; }
    .mw-toc-pane { flex: 0 0 220px; position: sticky; top: 1.5em;
                   max-height: calc(100vh - 3em); overflow-y: auto; }
    .mw-content { flex: 1; min-width: 0; }
    @media (max-width: 700px) {
      .mw-layout { flex-direction: column; }
      .mw-toc-pane { position: static; max-height: none; flex: none; width: 100%; }
    }
    .toc { background: var(--card); border: 1px solid var(--border);
           border-radius: 4px; padding: 1em 1.2em; }
    .toc h2 { font-size: 1em; margin: 0 0 0.5em; border: none; padding: 0;
              color: var(--muted); letter-spacing: 0.04em; text-transform: uppercase; }
    .toc ol { margin: 0; padding-left: 1.3em; }
    .toc li { margin: 0.2em 0; }
    .toc a { color: var(--link); }

    .tier-header { background: rgba(107,58,28,0.08); border-left: 4px solid var(--accent);
                   padding: 0.5em 1em; margin: 2em 0 0.8em; }
    .tier-header h2 { margin: 0; border: none; padding: 0; }
    .tier-header p { margin: 0.3em 0 0; color: var(--muted); font-size: 0.92em; }

    .asof-banner { margin: 0.6em 0 1.4em; }
    .asof-tag { display: inline-block; background: var(--card); border: 1px solid var(--border);
                border-radius: 999px; padding: 0.25em 0.9em; font-size: 0.85em; color: var(--muted); }

    /* Award cards */
    .hof-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(310px, 1fr));
                gap: 1em; margin: 1em 0 2em; }
    .hof-card { background: var(--card); border: 1px solid var(--border); border-radius: 6px;
                padding: 0.9em 1.1em; display: flex; flex-direction: column; }
    .hof-card h3 { margin: 0 0 0.35em; font-size: 1.05em; border: none; padding: 0; }
    .hof-blurb { color: var(--muted); font-size: 0.87em; margin: 0 0 0.7em; line-height: 1.45; }
    .hof-winner { font-size: 1.25em; font-weight: 700; line-height: 1.3; }
    .hof-value { color: var(--accent); font-weight: 700; }
    .hof-detail { color: var(--muted); font-size: 0.85em; margin-top: 0.15em; }
    .hof-tie { font-size: 0.8em; color: var(--muted); text-transform: uppercase;
               letter-spacing: 0.06em; margin-bottom: 0.2em; }
    .hof-runners { margin-top: 0.7em; border-top: 1px solid var(--border); padding-top: 0.5em;
                   font-size: 0.82em; color: var(--muted); }
    .hof-runners span { white-space: nowrap; }

    /* NPC (monster) award tables */
    .hof-table { width: 100%; border-collapse: collapse; margin: 0.6em 0 2em; font-size: 0.92em; }
    .hof-table th, .hof-table td { text-align: left; padding: 0.35em 0.7em;
                                   border-bottom: 1px solid var(--border); }
    .hof-table th { color: var(--muted); font-weight: 600; font-size: 0.85em;
                    text-transform: uppercase; letter-spacing: 0.04em; }
    .hof-wrap { overflow-x: auto; }

    .tip-box { background: rgba(60,120,90,0.10); border-left: 4px solid #3c785a;
               padding: 0.7em 1em; margin: 1.2em 0; border-radius: 0 4px 4px 0; font-size: 0.92em; }
  </style>
"""


def card(a: dict) -> str:
    winners = a["winners"]
    tie = len(winners) > 1
    head = '<div class="hof-tie">Tied &mdash; {} players</div>'.format(len(winners)) if tie else ""

    names = "<br>".join(E(w["player"]) for w in winners)
    value = f'<span class="hof-value">{E(str(winners[0]["display"]))}</span> {E(a["metric"])}'
    detail = winners[0].get("detail") or ""
    detail_html = f'<div class="hof-detail">{E(str(detail))}</div>' if detail else ""

    runners = [r for r in a["ranked"] if r not in winners][:3]
    runners_html = ""
    if runners:
        items = " &middot; ".join(
            f'<span>{E(r["player"])} {E(str(r["display"]))}</span>' for r in runners
        )
        runners_html = f'<div class="hof-runners">Also in the running: {items}</div>'

    return f"""    <div class="hof-card" id="award-{E(a['id'])}">
      <h3>{a['title']}</h3>
      <p class="hof-blurb">{a['blurb']}</p>
      {head}
      <div class="hof-winner">{names}</div>
      <div>{value}</div>
      {detail_html}
      {runners_html}
    </div>"""


def npc_table(a: dict) -> str:
    rows = "\n".join(
        f"        <tr><td>{E(r['name'])}</td><td>{E(str(r['value']))}</td>"
        f"<td>{E(str(r['detail']))}</td></tr>"
        for r in a["rows"]
    )
    more = ""
    if a["total"] > len(a["rows"]):
        more = f'<p class="hof-detail">Showing {len(a["rows"])} of {a["total"]:,}.</p>'
    return f"""  <h3 id="award-{E(a['id'])}">{a['title']}</h3>
  <p class="hof-blurb">{a['blurb']}</p>
  <div class="hof-wrap">
    <table class="hof-table">
      <thead><tr><th>Creature</th><th>{E(a['metric'].title())}</th><th>Where</th></tr></thead>
      <tbody>
{rows}
      </tbody>
    </table>
  </div>
{more}"""


def section(sid: str, title: str, sub: str, cards: list[dict]) -> str:
    body = "\n".join(card(a) for a in cards)
    return f"""
<div class="tier-header" id="{sid}">
  <h2>{title}</h2>
  <p>{sub}</p>
</div>
<div class="hof-grid">
{body}
</div>"""


def build_html(sections: list[tuple], npc: list[dict], asof: str, stats: dict) -> str:
    toc = "\n".join(
        f'        <li><a href="#{sid}">{title}</a></li>' for sid, title, _, _ in sections
    )
    body_sections = "\n".join(section(sid, title, sub, cards) for sid, title, sub, cards in sections)
    npc_html = "\n".join(npc_table(a) for a in npc)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Hall of Fame</title>
  <!-- @menu 'Activity' -->
  <!-- @order 1 -->
  <!-- The wiki build regex-scans this raw file for the directives above to decide
       which nav dropdown the page lands in (Activity, beside Roadmap and the
       activity charts — not the default Documents menu). They MUST stay in this
       script's template: bin/gen-halloffame.py rewrites HallOfFame.html wholesale,
       so a directive hand-added to the output is wiped by the next regeneration.
       tests/check_manual_menus.py is the build gate that catches it. -->
  <link rel="stylesheet" href="../assets/style.css">
</head>
<body>
  <main>
{STYLE}
  <div class="mw-layout">
  <aside class="mw-toc-pane">
    <nav class="toc" aria-label="Page contents">
      <h2>Contents</h2>
      <ol>
        <li><a href="#about">About this page</a></li>
{toc}
        <li><a href="#bestiary-revenge">The Bestiary's Revenge</a></li>
      </ol>
    </nav>
  </aside>
  <div class="mw-content">

<h1>Hall of Fame &mdash; Season 1</h1>

<div class="asof-banner">
  <span class="asof-tag">As of {E(asof)}</span>
</div>

<p id="about">Season 1 is closed. These are its records &mdash; drawn from the server's own
memory: every kill it counted, every character left in the vault, every coin banked, every
hour logged, and every idea that shipped. One winner per award; where players tied, all of
them are named.</p>

<div class="tip-box">
  <strong>How to read this.</strong> Awards are counted per <em>player account</em>, not per
  character &mdash; so "most gold" means every coin across a player's whole roster, carried
  and banked alike. The server admin is excluded throughout. Merit is only paid on ideas
  that actually shipped, which is why <a href="#award-backlog_hero">Backlog Hero</a> counts
  the opposite thing: suggestions still waiting their turn.
</div>

<p class="hof-detail">{stats['players']:,} players &middot; {stats['chars']:,} characters &middot;
{stats['kills']:,} recorded kills across {stats['species']:,} species &middot;
{stats['hours']:,.0f} hours played &middot; {stats['awards']:,} awards given.</p>

{body_sections}

<div class="tier-header" id="bestiary-revenge">
  <h2>The Bestiary's Revenge</h2>
  <p>Not every trophy belongs to a player. These went to the monsters.</p>
</div>
{npc_html}

  </div>
  </div>
  </main>
</body>
</html>
"""


# =========================================================================== #
# Reporting
# =========================================================================== #

def concentration_report(sections, roadmap_awards) -> None:
    """Who won what, printed to stderr. The signal for tuning categories.py."""
    won = Counter()
    for _, _, _, cards in sections:
        for a in cards:
            for w in a["winners"]:
                won[w["player"]] += 1
    for a in roadmap_awards:
        for w in a["winners"]:
            won[w["player"]] += 1

    print("\n[concentration] awards won per player:", file=sys.stderr)
    for player, n in won.most_common():
        print(f"    {n:3d}  {player}", file=sys.stderr)
    if won:
        top, n = won.most_common(1)[0]
        total = sum(won.values())
        print(f"    -> {top} holds {n}/{total} ({100*n/total:.0f}%) of all awards.",
              file=sys.stderr)


def unmatched_report(roster) -> None:
    if not roster.unmatched:
        return
    seen = sorted(set(roster.unmatched))
    print(f"\n[identity] {len(seen)} playerid string(s) could not be matched to an account "
          "(their banked gold / meaningwave progress is not counted):", file=sys.stderr)
    for pid in seen[:25]:
        print(f"    {pid!r}", file=sys.stderr)
    if len(seen) > 25:
        print(f"    ... and {len(seen) - 25} more", file=sys.stderr)


def as_of(tz_name: str) -> str:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = None
    now = datetime.now(tz) if tz else datetime.now()
    # Date only, deliberately: a to-the-minute stamp would make every regeneration a
    # diff even when not one award changed.
    return now.strftime("%B %-d, %Y")


def server_env_tz() -> str:
    """TZ from server.env, the same way bin/refresh-homers-lotr-wiki sources it."""
    env = REPO / "server.env"
    if env.is_file():
        m = re.search(r"^TZ=([^\s#]+)", env.read_text(encoding="utf-8"), re.M)
        if m:
            return m.group(1).strip('"\'')
    return os.environ.get("TZ", "America/Chicago")


# =========================================================================== #

def main() -> int:
    home = Path.home()
    nwn = home / ".local" / "share" / "Neverwinter Nights"
    shared = home / ".local" / "share" / "nwn-shared"

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db-dir", default=str(nwn / "database"))
    ap.add_argument("--vault", default=str(nwn / "servervault"))
    ap.add_argument("--activity-cache",
                    default=str(home / ".local/state/nwnxee-homer/activity-sessions.json"))
    ap.add_argument("--merit-db", default=str(shared / "meritdb.sqlite3"))
    ap.add_argument("--admin-db", default=str(shared / "admindb.sqlite3"))
    ap.add_argument("--module-index", default=str(REPO / "module-index"))
    ap.add_argument("--unpacked", default=str(REPO / "unpacked"))
    ap.add_argument("--twoda-dir", default=str(REPO / "hak_2da"))
    ap.add_argument("--skills-json",
                    default=str(home / "GIT/nwn_manager/bin/wiki_data/skills.json"))
    ap.add_argument("--dev-roadmap", default=str(DEV_REPO / "roadmap.yaml"),
                    help="the live forward backlog, for the Backlog Hero award")
    ap.add_argument("--bic-cache", default=str(REPO / ".hof-bic-cache.json"))
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--json", help="also dump the computed award data here")
    ap.add_argument("--check", action="store_true", help="compute and report, write nothing")
    args = ap.parse_args()

    ctx = Ctx(args)

    sections = [
        ("conquest", "Conquest",
         "Kills, bosses and firsts &mdash; the season measured in fallen enemies.",
         A.conquest(ctx)),
        ("fortune", "Fortune",
         "Gold, experience and the hoards players built out of them.", A.fortune(ctx)),
        ("devotion", "Devotion",
         "Hours, ideas and the long grind of showing up.",
         A.devotion(ctx) + roadmapawards.build(ctx, REPO / "roadmap.yaml",
                                               Path(args.dev_roadmap))),
        ("character", "Character",
         "Who these players actually chose to be &mdash; classes, alignments, "
         "abilities and the odd obsession.", A.character(ctx)),
    ]
    npc = A.npc_awards(ctx)

    unmatched_report(ctx.roster)
    if A.EMPTY_AWARDS:
        print(f"\n[empty] {len(A.EMPTY_AWARDS)} award(s) found no entrants and are not "
              "rendered (widen the category in bin/halloffame/categories.py if that is "
              "not what you expect):", file=sys.stderr)
        for aid, title in A.EMPTY_AWARDS:
            print(f"    {title}  ({aid})", file=sys.stderr)
    concentration_report(sections, [])

    contenders = {
        ctx.roster.account(ck) for ck in ctx.roster.all_cdkeys()
    } - set(A.ADMIN_ACCOUNTS)
    stats = {
        "players": len(contenders),
        "chars": len(ctx.chars),
        "kills": sum(k["solo"] + k["party"] for k in ctx.kills),
        "species": len({k["resref"] for k in ctx.kills}),
        "hours": sum(s.get("duration_min") or 0 for s in ctx.sessions) / 60.0,
        "awards": sum(len(cs) for _, _, _, cs in sections) + len(npc),
    }

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"sections": [{"id": s, "title": t, "awards": c} for s, t, _, c in sections],
             "npc": npc, "stats": stats}, indent=1, default=str), encoding="utf-8")
        print(f"[json] wrote {args.json}", file=sys.stderr)

    print(f"\n[awards] {stats['awards']} awards across {len(sections)} sections "
          f"({len(npc)} monster awards)", file=sys.stderr)

    if args.check:
        print("[check] computed only; nothing written", file=sys.stderr)
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(sections, npc, as_of(server_env_tz()), stats), encoding="utf-8")
    print(f"wrote {out.relative_to(REPO) if out.is_relative_to(REPO) else out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
