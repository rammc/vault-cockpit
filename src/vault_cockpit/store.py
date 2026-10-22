"""Read open items and decisions from the vault."""
import datetime as dt
import re

from . import fm
from .texts import T

ID_RE = re.compile(r"^([A-Za-z]+-)(\d+)$")


def today():
    return dt.date.today().isoformat()


def rel(cfg, p):
    return str(p.relative_to(cfg.vault))


def _as_list(v):
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]


def _sections(body):
    """Split a markdown body into (title, preamble, {heading: text}, order)."""
    title, pre, secs, order, cur = "", [], {}, [], None
    for line in body.split("\n"):
        if line.startswith("# ") and not title and cur is None and not "".join(pre).strip():
            title = line[2:].strip()
            continue
        if line.startswith("## "):
            cur = line[3:].strip()
            secs[cur] = []
            order.append(cur)
            continue
        (secs[cur] if cur is not None else pre).append(line)
    return title, "\n".join(pre).strip(), {k: "\n".join(v).strip() for k, v in secs.items()}, order


def parse_item(cfg, path):
    text = path.read_text(encoding="utf-8")
    data, body = fm.parse(text)
    f = cfg.ifield
    title, pre, secs, _ = _sections(body)
    hist_head = T(cfg)["history"]
    history = [l[2:].strip() for l in secs.get(hist_head, "").split("\n") if l.startswith("- ")]
    raw_status = data.get("status") or cfg.ist("open")
    sources = _as_list(data.get("sources"))
    return {
        "id": data.get("id") or path.stem,
        "last_answer": last_answer(cfg, sources),
        "path": rel(cfg, path),
        "title": data.get("title") or title or path.stem,
        "text": pre,
        "history": history,
        "status": cfg.ist_canon(raw_status),
        "status_raw": raw_status,
        "domain": data.get("domain") or "",
        "owner": data.get(f("owner")) or "",
        "created": str(data.get(f("created")) or ""),
        "due": str(data.get(f("due")) or ""),
        "origin": data.get(f("origin")) or "",
        "pages": _as_list(data.get(f("pages"))),
        "suggestion": data.get(f("suggestion")) or "",
        "suggestion_reason": data.get(f("suggestion_reason")) or "",
        "integrate": bool(data.get(f("integrate"))),
        "decision": data.get(f("decision")) or "",
        "duplicate_of": data.get(f("duplicate_of")) or "",
        "updated": str(data.get("updated") or ""),
        "sources": sources,
    }


def last_answer(cfg, sources):
    """The quoted statement in the newest answer evidence, shown as the item's current state."""
    prefix = cfg["paths"]["raw_answers"].rstrip("/") + "/"
    for s in reversed(sources):
        if s and s.startswith(prefix):
            p = cfg.vault / s
            if p.exists():
                q = [l[1:].strip() for l in p.read_text(encoding="utf-8").split("\n") if l.startswith(">")]
                return {"date": s[len(prefix):len(prefix) + 10], "text": "\n".join(q).strip(), "path": s}
    return None


def items(cfg):
    d = cfg.path("items")
    if not d.is_dir():
        return []
    out = [parse_item(cfg, p) for p in sorted(d.glob("*.md"))]
    out.sort(key=lambda i: _id_num(i["id"]), reverse=True)
    return out


def _id_num(i):
    m = ID_RE.match(i)
    return int(m.group(2)) if m else -1


def item_path(cfg, item_id):
    if not ID_RE.match(item_id):
        raise KeyError(item_id)
    p = cfg.path("items") / f"{item_id}.md"
    if not p.exists():
        raise KeyError(item_id)
    return p


def next_id(cfg, taken=()):
    nums = [_id_num(p.stem) for p in cfg.path("items").glob("*.md")] if cfg.path("items").is_dir() else []
    nums += [_id_num(t) for t in taken]
    return f"{cfg['id_prefix']}{max(nums + [0]) + 1:04d}"


def is_due(cfg, item, on=None):
    return item["status"] == "waiting" and item["due"] and item["due"] <= (on or today())


def parse_decision(cfg, path):
    data, body = fm.parse(path.read_text(encoding="utf-8"))
    f = cfg.dfield
    title, pre, secs, order = _sections(body)
    raw_status = data.get("status") or ""
    marker = cfg["unconfirmed_trigger_marker"]
    review = secs.get(T(cfg)["sec_review"], "")
    return {
        "slug": path.stem,
        "path": rel(cfg, path),
        "title": data.get("title") or title or path.stem,
        "status": cfg.dst_canon(raw_status),
        "status_raw": raw_status,
        "domain": data.get("domain") or "",
        "decided_by": data.get(f("decided_by")) or "",
        "decided_on": str(data.get(f("decided_on")) or ""),
        "subject": data.get(f("subject")) or "",
        "supersedes": _as_list(data.get(f("supersedes"))),
        "superseded_by": data.get(f("superseded_by")) or "",
        "updated": str(data.get("updated") or ""),
        "sources": _as_list(data.get("sources")),
        "summary": data.get("summary") or "",
        "sections": [{"heading": h, "text": secs[h]} for h in order],
        "unconfirmed_triggers": bool(marker and marker in review),
        "integrate": bool(data.get(cfg.ifield("integrate"))),
    }


def decisions(cfg, with_hints=True):
    out = []
    for d in cfg.decision_dirs():
        out.extend(parse_decision(cfg, p) for p in sorted(d.glob("*.md")))
    out.sort(key=lambda x: x["decided_on"] or x["slug"], reverse=True)
    if with_hints:
        hints = {}
        for it in items(cfg):
            if it["decision"] and it["status"] in ("open", "waiting"):
                hints.setdefault(it["decision"], []).append({"id": it["id"], "title": it["title"]})
        for d in out:
            d["hints"] = hints.get(d["slug"], [])
    return out


def decision_path(cfg, slug):
    if not re.match(r"^[\w.-]+$", slug):
        raise KeyError(slug)
    for d in cfg.decision_dirs():
        p = d / f"{slug}.md"
        if p.exists():
            return p
    raise KeyError(slug)


def wiki_stems(cfg):
    return {p.stem: p for p in cfg.path("wiki").rglob("*.md")}


def page_domain(cfg, path):
    """Domain of a wiki page from its frontmatter or first folder below the wiki root."""
    data, _ = fm.parse(path.read_text(encoding="utf-8", errors="replace"))
    if data.get("domain"):
        return data["domain"]
    parts = path.relative_to(cfg.path("wiki")).parts
    if len(parts) > 1 and parts[0] in cfg["domains"]:
        return parts[0]
    return ""
