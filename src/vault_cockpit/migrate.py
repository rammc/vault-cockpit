"""One-off: split a hand-kept overview page into one file per open item.

Understands the two shapes such pages grow into: tables (`| pages | what is
missing | since |`) and bullet lists under a heading, optionally introduced by
a context line with links. Nothing is dropped: every row and bullet becomes an
item, and obvious cases get a triage suggestion instead of being closed.
"""
import datetime as dt
import re

from . import fm, store
from .texts import T
from .txn import Transaction

DATE_ISO = re.compile(r"(\d{4}-\d{2}-\d{2})")
DATE_FULL = re.compile(r"\d{4}-\d{2}-\d{2}|\b\d{1,2}\.\d{1,2}\.\d{4}")
DATE_DE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})?")
DONE = re.compile(r"\*\*(Erledigt|Beantwortet|Done|Answered|Resolved)\b|^(Beantwortet|Erledigt|Answered|Done)\s*:", re.I)
PREP = re.compile(r"(Meeting Prep|Vorbereitung|Prep)\b.*?(?:für|for)\s+(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.)", re.I)


def split_row(line):
    """Split a markdown table row on pipes outside [[...]] and `...`."""
    cells, cur, depth, code = [], "", 0, False
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
    i = 0
    while i < len(s):
        ch = s[i]
        if s.startswith("[[", i):
            depth += 1; cur += "[["; i += 2; continue
        if s.startswith("]]", i) and depth:
            depth -= 1; cur += "]]"; i += 2; continue
        if ch == "`":
            code = not code
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur += "|"; i += 2; continue
        if ch == "|" and not depth and not code:
            cells.append(cur.strip()); cur = ""
        else:
            cur += ch
        i += 1
    cells.append(cur.strip())
    return cells


def _date_from(text, year):
    m = DATE_ISO.search(text)
    if m:
        return m.group(1)
    m = DATE_DE.search(text)
    if m:
        y = int(m.group(3) or year)
        try:
            return dt.date(y, int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return ""
    return ""


def plain(text):
    s = fm.LINK.sub(lambda m: m.group(0)[2:-2].split("|")[-1], text)
    s = re.sub(r"\*\*|__|`", "", s)
    return " ".join(s.split())


ABBR = re.compile(r"(?:\b(?:bzw|ggf|vgl|ca|Nr|inkl|etc|evtl|usw|vs|z\. ?B|u\. ?a|d\. ?h|e\.g|i\.e|S)\.)$")


def make_title(text, n=110):
    """First sentence if it is short enough, else the text cut at n characters."""
    s = plain(text)
    for c in re.finditer(r"(?:[?!]|(?<![\d.])\.)(?=\s|$)", s):
        if c.end() < 20 or (s[c.start()] == "." and ABBR.search(s[:c.end()])):
            continue
        if c.end() <= n:
            return s[:c.end()]
        break
    return s if len(s) <= n else s[: n - 1].rstrip(" ,;:") + "…"


def parse_overview(text, fallback_date):
    """Yield raw entries: {origin, pages, text, created, context, kind}."""
    year = fallback_date[:4]
    heading, context, entries = "", "", []
    cur = None
    in_table = False
    for line in text.split("\n"):
        if line.startswith("## "):
            heading, context, cur, in_table = line[3:].strip(), "", None, False
            continue
        if not heading:
            continue
        s = line.rstrip()
        if s.startswith("|"):
            cells = split_row(s)
            if all(re.match(r"^:?-{2,}:?$", c) for c in cells if c):
                in_table = True
                continue
            if not in_table:
                continue  # header row
            first, what, since = (cells + ["", "", ""])[:3]
            pages = fm.links(first)
            label = "" if pages else plain(first)
            body = what if not label else f"{label}: {what}"
            entries.append({"origin": heading, "pages": pages, "text": body, "first": first,
                            "created": _date_from(since, year) or _date_from(heading, year) or fallback_date,
                            "context": context, "kind": "table"})
            cur = None
            continue
        in_table = False
        if re.match(r"^[-*] ", s):
            full = DATE_FULL.search(s)
            cur = {"origin": heading, "pages": [], "text": s[2:].strip(), "first": "",
                   "created": (_date_from(full.group(0), year) if full else "") or _date_from(heading, year) or fallback_date,
                   "context": context, "kind": "bullet"}
            entries.append(cur)
            continue
        if cur is not None and s.startswith(("  ", "\t")) and s.strip():
            cur["text"] += "\n" + s.strip()
            continue
        if s.strip():
            context = s.strip()  # a context line such as "Quelle [[x]], Thema [[y]]:"
            cur = None
        else:
            cur = None if cur is None or cur["kind"] == "bullet" else cur
    for e in entries:
        if not e["pages"]:
            e["pages"] = list(dict.fromkeys(fm.links(e["text"]) or fm.links(e["context"])))
    return entries


def plan(cfg, today=None):
    """Turn the current overview into item drafts, with suggestions. No writes."""
    today = today or store.today()
    t = T(cfg)
    src = cfg.path("overview").read_text(encoding="utf-8")
    m = re.search(r"(?:Stand|As of):?\s*(\d{4}-\d{2}-\d{2})", src)
    entries = parse_overview(src, m.group(1) if m else today)
    stems = store.wiki_stems(cfg)
    decision_stems = {p.stem for d in cfg.decision_dirs() for p in d.glob("*.md")}
    review_heads = {t["overview_review"].lower(), "entscheidungen prüfen", "decisions to review"}
    seen, drafts = {}, []
    for n, e in enumerate(entries, 1):
        item_id = f"{cfg['id_prefix']}{n:04d}"
        domain = ""
        for d in cfg["domains"]:
            if e["origin"].strip().lower() == d.lower():
                domain = d
        if not domain:
            for p in e["pages"]:
                if p in stems:
                    domain = store.page_domain(cfg, stems[p])
                    if domain:
                        break
        decision = ""
        if e["origin"].lower() in review_heads:
            decision = next((p for p in fm.links(e["text"]) if p in decision_stems), "")
        suggestion, reason = "", ""
        key = plain(e["text"]).lower()
        if DONE.search(e["text"]):
            suggestion, reason = "done", t["sg_done"]
        elif key in seen:
            suggestion, reason = "duplicate", t["sg_duplicate"].format(other=seen[key])
        else:
            pm = PREP.search(e["origin"])
            if pm:
                when = _date_from(pm.group(2), e["created"][:4])
                if when and when < today:
                    suggestion, reason = "stale", t["sg_stale"].format(date=when)
        seen.setdefault(key, item_id)
        drafts.append({
            "id": item_id, "title": make_title(e["text"]), "text": e["text"], "context": e["context"],
            "first": e["first"], "pages": e["pages"], "origin": e["origin"], "created": e["created"],
            "domain": domain or cfg["default_domain"], "decision": decision,
            "suggestion": suggestion, "suggestion_reason": reason,
            "duplicate_of": seen[key] if suggestion == "duplicate" else "",
        })
    return drafts


def render_item(cfg, d):
    t = T(cfg)
    f = cfg.ifield
    data = {
        "id": d["id"], "title": d["title"], "type": cfg["types"]["item"], "domain": d["domain"],
        "status": cfg.ist("open"), f("owner"): cfg["owner_default"], f("created"): d["created"],
        f("origin"): d["origin"], f("pages"): d["pages"], "updated": store.today(), "sources": [],
    }
    if d["decision"]:
        data[f("decision")] = d["decision"]
    if d["suggestion"]:
        data[f("suggestion")] = d["suggestion"]
        data[f("suggestion_reason")] = d["suggestion_reason"]
    if d["duplicate_of"]:
        data[f("duplicate_of")] = d["duplicate_of"]
    # no heading when the title is just the first sentence of the text
    head = "" if plain(d["text"]).startswith(d["title"].rstrip("…")) else f"# {d['title']}\n\n"
    body = f"\n{head}{d['text'].strip()}\n"
    if d["first"] and fm.links(d["first"]):
        body += f"\n{t['regarding']}: {d['first']}\n"
    if d["context"] and fm.links(d["context"]):
        body += f"\n{t['context']}: {d['context']}\n"
    origin = t["created_by_migration"].format(overview=cfg["paths"]["overview"], origin=d["origin"])
    body += f"\n## {t['history']}\n\n- {store.today()} {origin}\n"
    return fm.render(data, body)


def run(cfg, write=False):
    drafts = plan(cfg)
    summary = {"items": len(drafts), "by_suggestion": {}, "by_domain": {}, "by_origin": {}}
    for d in drafts:
        k = d["suggestion"] or "keep"
        summary["by_suggestion"][k] = summary["by_suggestion"].get(k, 0) + 1
        summary["by_domain"][d["domain"]] = summary["by_domain"].get(d["domain"], 0) + 1
        summary["by_origin"][d["origin"]] = summary["by_origin"].get(d["origin"], 0) + 1
    if not write:
        return summary, drafts
    items_dir = cfg.path("items")
    if items_dir.is_dir() and any(items_dir.glob("*.md")):
        raise RuntimeError(f"{cfg['paths']['items']} already has items; migration runs once")
    with Transaction(cfg, f"cockpit: migrate {cfg['paths']['overview']} into {len(drafts)} items") as tx:
        for d in drafts:
            tx.write(items_dir / f"{d['id']}.md", render_item(cfg, d))
        tx.append(cfg.path("log"),
                  f"\n## [{store.today()}] cockpit | {cfg['paths']['overview']}\n"
                  f"{len(drafts)} items migrated into {cfg['paths']['items']}/; overview is generated from now on.\n")
        summary["files"] = len(tx.files())
    return summary, drafts
