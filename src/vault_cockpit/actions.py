"""Every change the cockpit can make. Each one is a single transaction.

Rule of thumb: whatever a human says goes verbatim into a new RAW file first,
the wiki only references it.
"""
import re
import unicodedata

from . import fm, store
from .store import today
from .texts import T
from .txn import Transaction


class Invalid(Exception):
    pass


# -- helpers ------------------------------------------------------------------

def slugify(s, limit=60):
    s = s.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:limit].rstrip("-") or "eintrag"


def _speaker(cfg):
    return cfg["owner_name"] or cfg["owner_default"]


def _quote_block(text):
    return "\n".join("> " + l if l.strip() else ">" for l in text.strip().split("\n"))


def add_history(cfg, text, line):
    head = f"## {T(cfg)['history']}"
    entry = f"- {today()} {line}"
    if head in text:
        a = text.index(head) + len(head)
        nxt = text.find("\n## ", a)
        if nxt == -1:
            return text.rstrip("\n") + "\n" + entry + "\n"
        return text[:nxt].rstrip("\n") + "\n" + entry + "\n" + text[nxt:]
    return text.rstrip("\n") + f"\n\n{head}\n\n{entry}\n"


def log(tx, cfg, target, text):
    tx.append(cfg.path("log"), f"\n## [{today()}] {T(cfg)['log_kind']} | {target}\n{text}\n")


def _add_source(data_sources, raw):
    src = list(data_sources or [])
    if raw not in src:
        src.append(raw)
    return src


def raw_answer(tx, cfg, item, statement, title=None):
    t = T(cfg)
    data = {
        "title": title or t["raw_answer_title"].format(id=item["id"], title=item["title"]),
        "date": today(),
        t["raw_from"]: _speaker(cfg),
        "channel": cfg["channel"],
        t["raw_regarding"]: item["path"],
    }
    body = (f"\n## {t['raw_question'].format(id=item['id'])}\n\n{item['title']}\n\n"
            f"## {t['raw_answer']}\n\n{_quote_block(statement)}\n")
    name = f"{today()}-{item['id'].lower()}.md"
    return tx.rel(tx.create(cfg.path("raw_answers") / name, fm.render(data, body, set())))


def raw_decision(tx, cfg, slug, title, statement, occasion=""):
    t = T(cfg)
    data = {
        "title": title,
        "date": today(),
        "decided_by": _speaker(cfg),
        "channel": cfg["channel"],
        "note": t["raw_note"],
    }
    body = ""
    if occasion.strip():
        body += f"\n## {t['raw_occasion']}\n\n{occasion.strip()}\n"
    body += f"\n## {t['raw_statement']}\n\n{_quote_block(statement)}\n"
    name = slug if slug.startswith(f"{today()}-") else f"{today()}-{slug}"
    return tx.rel(tx.create(cfg.path("raw_decisions") / f"{name}.md", fm.render(data, body, set())))


def _update_item(tx, cfg, item_id, history_line, raw=None, **fields):
    p = store.item_path(cfg, item_id)
    text = p.read_text(encoding="utf-8")
    data, _ = fm.parse(text)
    fields["updated"] = today()
    if raw:
        fields["sources"] = _add_source(data.get("sources"), raw)
    mapped = {}
    for k, v in fields.items():
        mapped[cfg.ifield(k) if k in cfg["item_fields"] else k] = v
    text = fm.set_fields(text, **mapped)
    if history_line:
        text = add_history(cfg, text, history_line)
    tx.write(p, text)
    return p


def _need(value, what):
    if not value or not str(value).strip():
        raise Invalid(f"{what} is required")
    return str(value).strip()


def _result(tx, **extra):
    # the transaction fills in the final file list (overview, index) when it closes
    tx.result = dict(files=tx.files(), **extra)
    return tx.result


# -- open items -----------------------------------------------------------------

def create_item(cfg, title, text="", domain="", pages=(), owner="", decision="", origin=""):
    t = T(cfg)
    title = _need(title, "title")
    with Transaction(cfg, f"cockpit: new open item “{title[:50]}”") as tx:
        item_id = store.next_id(cfg)
        f = cfg.ifield
        data = {
            "id": item_id,
            "title": title,
            "type": cfg["types"]["item"],
            "domain": domain or cfg["default_domain"],
            "status": cfg.ist("open"),
            f("owner"): owner or cfg["owner_default"],
            f("created"): today(),
            f("origin"): origin or cfg["channel"],
            f("pages"): list(pages),
            "updated": today(),
            "sources": [],
        }
        if decision:
            data[f("decision")] = decision
        refs = ", ".join(f"[[{p}]]" for p in pages)
        body = f"\n# {title}\n\n"
        if text.strip():
            body += text.strip() + "\n\n"
        if refs:
            body += f"{t['regarding']}: {refs}\n\n"
        body += f"## {t['history']}\n\n- {today()} {t['created_by_cockpit']}\n"
        tx.write(cfg.path("items") / f"{item_id}.md", fm.render(data, body))
        log(tx, cfg, f"{cfg['paths']['items']}/{item_id}.md", f"{item_id}: {title}")
        return _result(tx, id=item_id)


def answer(cfg, item_id, statement, then="integrate", decision=None, due=""):
    """then: integrate (Claude works it in) | close | decision (record as decision)
    | wait (still open: keep the answer as the current state, back on the list on `due`)."""
    t = T(cfg)
    statement = _need(statement, "answer")
    if then == "wait" and not re.match(r"^\d{4}-\d{2}-\d{2}$", due or ""):
        raise Invalid("a follow-up date (YYYY-MM-DD) is required")
    item = store.parse_item(cfg, store.item_path(cfg, item_id))
    with Transaction(cfg, f"cockpit: answer {item_id}") as tx:
        raw = raw_answer(tx, cfg, item, statement)
        extra = {}
        if then == "wait":
            line = t["answered_wait"].format(raw=raw, due=due)
            fields = {"status": cfg.ist("waiting"), "due": due, "integrate": True}
        else:
            line = t["answered_integrate" if then == "integrate" else "answered"].format(raw=raw)
            fields = {"status": cfg.ist("answered"), "integrate": then == "integrate"}
        if then == "decision":
            d = decision or {}
            slug = _create_decision(tx, cfg, title=_need(d.get("title"), "decision title"),
                                    subject=_need(d.get("subject"), "subject"), statement=statement,
                                    domain=d.get("domain") or item["domain"],
                                    decided_by=d.get("decided_by") or _speaker(cfg),
                                    occasion=item["title"], refs=[item_id])
            line += "; " + t["decision_created"].format(slug=slug)
            extra["decision"] = slug
        _update_item(tx, cfg, item_id, line, raw=raw, **fields)
        log(tx, cfg, item["path"], f"{item_id} {line}.")
        return _result(tx, **extra)


def snooze(cfg, item_id, due):
    t = T(cfg)
    due = _need(due, "date")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", due):
        raise Invalid("date must be YYYY-MM-DD")
    with Transaction(cfg, f"cockpit: {item_id} waiting until {due}") as tx:
        _update_item(tx, cfg, item_id, t["snoozed"].format(due=due), status=cfg.ist("waiting"), due=due)
        return _result(tx)


def reopen(cfg, item_id):
    with Transaction(cfg, f"cockpit: reopen {item_id}") as tx:
        _update_item(tx, cfg, item_id, T(cfg)["reopened"], status=cfg.ist("open"), due=None)
        return _result(tx)


def discard(cfg, item_id, reason=""):
    t = T(cfg)
    item = store.parse_item(cfg, store.item_path(cfg, item_id))
    with Transaction(cfg, f"cockpit: discard {item_id}") as tx:
        raw = raw_answer(tx, cfg, item, reason) if reason.strip() else None
        line = t["discarded"].format(raw=raw) if raw else t["discarded_plain"]
        _update_item(tx, cfg, item_id, line, raw=raw, status=cfg.ist("discarded"), integrate=False)
        log(tx, cfg, item["path"], f"{item_id} {line}.")
        return _result(tx)


def duplicate(cfg, item_id, other):
    other = _need(other, "other item")
    store.item_path(cfg, other)
    if other == item_id:
        raise Invalid("an item cannot duplicate itself")
    with Transaction(cfg, f"cockpit: {item_id} duplicates {other}") as tx:
        _update_item(tx, cfg, item_id, T(cfg)["duplicate"].format(other=other),
                     status=cfg.ist("duplicate"), duplicate_of=other)
        return _result(tx)


def set_owner(cfg, item_id, owner):
    owner = _need(owner, "owner")
    with Transaction(cfg, f"cockpit: owner of {item_id}") as tx:
        _update_item(tx, cfg, item_id, T(cfg)["owner"].format(owner=owner), owner=owner)
        return _result(tx)


def integrated(cfg, target):
    """Claude reports that an answer or a decision draft has been worked in."""
    with Transaction(cfg, f"cockpit: integrated {target}") as tx:
        if store.ID_RE.match(target):
            _update_item(tx, cfg, target, T(cfg)["integrated"], integrate=False)
        else:
            p = store.decision_path(cfg, target)
            tx.write(p, fm.set_fields(p.read_text(encoding="utf-8"), **{cfg.ifield("integrate"): False}))
        return _result(tx)


VERDICTS = ("keep", "done", "stale", "duplicate")


def triage(cfg, verdicts):
    """verdicts: [{id, verdict: keep|done|stale|duplicate, other?}]. One RAW note for the batch."""
    t = T(cfg)
    rows = []
    for v in verdicts:
        if v.get("verdict") not in VERDICTS:
            raise Invalid(f"unknown verdict {v.get('verdict')}")
        it = store.parse_item(cfg, store.item_path(cfg, v["id"]))
        rows.append((it, v))
    if not rows:
        raise Invalid("nothing selected")
    with Transaction(cfg, f"cockpit: triage of {len(rows)} items") as tx:
        lines = [f"- {it['id']} ({it['title']}): {v['verdict']}"
                 + (f" → {v['other']}" if v.get("other") else "")
                 + (f". {it['suggestion_reason']}" if it["suggestion_reason"] and v["verdict"] != "keep" else "")
                 for it, v in rows]
        data = {"title": t["raw_triage_title"].format(date=today()), "date": today(),
                t["raw_from"]: _speaker(cfg), "channel": cfg["channel"],
                t["raw_regarding"]: cfg["paths"]["items"]}
        body = f"\n{t['raw_triage_intro']}\n\n" + "\n".join(lines) + "\n"
        raw = tx.rel(tx.create(cfg.path("raw_answers") / f"{today()}-triage.md", fm.render(data, body, set())))
        status = {"keep": None, "done": "answered", "stale": "discarded", "duplicate": "duplicate"}
        for it, v in rows:
            fields = {"suggestion": None, "suggestion_reason": None}
            if status[v["verdict"]]:
                fields["status"] = cfg.ist(status[v["verdict"]])
            if v["verdict"] == "duplicate":
                fields["duplicate_of"] = _need(v.get("other"), "other item")
            _update_item(tx, cfg, it["id"], t["triage"].format(verdict=v["verdict"], raw=raw), raw=raw, **fields)
        log(tx, cfg, raw, f"Triage: {len(rows)} items.")
        return _result(tx, raw=raw)


# -- decisions ------------------------------------------------------------------

def _create_decision(tx, cfg, title, subject, statement, domain, decided_by, occasion="", refs=(), supersedes=None):
    t = T(cfg)
    slug = f"{today()}-{slugify(title)}"
    raw = raw_decision(tx, cfg, slug, t["raw_decision_title"].format(title=title), statement, occasion)
    f = cfg.dfield
    data = {
        "title": title,
        "type": cfg["types"]["decision"],
        "domain": domain or cfg["default_domain"],
        "status": cfg.dst("valid"),
        "updated": today(),
        f("decided_by"): decided_by,
        f("decided_on"): today(),
        f("subject"): subject,
        f("supersedes"): [supersedes] if supersedes else [],
        f("superseded_by"): None,
        "sources": [raw],
        "summary": statement.strip().split("\n")[0][:240],
        cfg.ifield("integrate"): True,
    }
    target_dir = _decision_dir(cfg, domain, supersedes)
    body = f"\n# {title}\n\n## {t['sec_decision']}\n\n{t['quote'].format(text=statement.strip())} {t['cite'].format(raw=raw)}\n"
    if supersedes:
        body += "\n" + t["supersedes_note"].format(old=supersedes) + "\n"
    for sec in ("sec_assumptions", "sec_rejected", "sec_outcome", "sec_review"):
        body += f"\n## {t[sec]}\n\n{t['draft']}\n"
    links = ([supersedes] if supersedes else []) + list(refs)
    body += f"\n## {t['sec_refs']}\n\n" + (", ".join(f"[[{l}]]" for l in links) if links else "") + "\n"
    path = tx.create(target_dir / f"{slug}.md", fm.render(data, body))
    log(tx, cfg, tx.rel(path), f"{title}. {t['cite'].format(raw=raw)}")
    return path.stem


def _decision_dir(cfg, domain, near=None):
    if near:
        return store.decision_path(cfg, near).parent
    dirs = cfg.decision_dirs()
    for d in dirs:
        if domain and domain in d.parts:
            return d
    if dirs:
        return dirs[0]
    first = cfg["paths"]["decisions"][0]
    if "*" in first:
        raise Invalid("no decision folder exists yet")
    return cfg.vault / first


def create_decision(cfg, title, subject, statement, domain="", decided_by="", occasion=""):
    with Transaction(cfg, f"cockpit: decision “{title[:50]}”") as tx:
        slug = _create_decision(tx, cfg, _need(title, "title"), _need(subject, "subject"),
                                _need(statement, "statement"), domain, decided_by or _speaker(cfg), occasion)
        return _result(tx, decision=slug)


REVIEW = ("confirm", "keep", "supersede", "withdraw")


def review_decision(cfg, slug, action, statement="", new_title="", confirm_triggers=False):
    if action not in REVIEW:
        raise Invalid(f"unknown action {action}")
    t = T(cfg)
    p = store.decision_path(cfg, slug)
    d = store.parse_decision(cfg, p)
    if d["status"] in ("superseded", "withdrawn"):
        raise Invalid("this decision is no longer in force")
    if action in ("supersede", "withdraw"):
        _need(statement, "statement")
    if action == "confirm" and d["status"] != "proposed" and not confirm_triggers:
        raise Invalid("already valid; nothing to confirm")
    statement = statement.strip() or t["act_" + action].capitalize() + "."
    hints = [x["id"] for dd in store.decisions(cfg) if dd["slug"] == slug for x in dd["hints"]]
    with Transaction(cfg, f"cockpit: decision {slug} {action}") as tx:
        occasion = "; ".join(store.parse_item(cfg, store.item_path(cfg, h))["title"] for h in hints)
        raw = raw_decision(tx, cfg, f"{slug}-{action}",
                           t["raw_decision_review_title"].format(slug=slug, action=t["act_" + action]),
                           statement, occasion)
        text = p.read_text(encoding="utf-8")
        data, _ = fm.parse(text)
        fields = {"updated": today(), "sources": _add_source(data.get("sources"), raw)}
        result = {}
        if action == "confirm":
            fields["status"] = cfg.dst("valid")
        elif action == "withdraw":
            fields["status"] = cfg.dst("withdrawn")
        elif action == "supersede":
            new = _create_decision(tx, cfg, title=_need(new_title, "title of the new version"),
                                   subject=d["subject"], statement=statement, domain=d["domain"],
                                   decided_by=_speaker(cfg), occasion=d["title"], supersedes=slug)
            fields["status"] = cfg.dst("superseded")
            fields[cfg.dfield("superseded_by")] = new
            result["decision"] = new
        if action != "keep":
            text = fm.set_fields(text, **fields)
        if confirm_triggers and cfg["unconfirmed_trigger_marker"]:
            marker = cfg["unconfirmed_trigger_marker"]
            text = "\n".join(t["triggers_confirmed"].format(date=today(), raw=raw) if marker in l else l
                             for l in text.split("\n"))
            if action == "keep":
                text = fm.set_fields(text, updated=today(), sources=_add_source(data.get("sources"), raw))
        tx.write(p, text)
        for h in hints:
            _update_item(tx, cfg, h, t["answered"].format(raw=raw), raw=raw, status=cfg.ist("answered"))
        log(tx, cfg, d["path"], f"{t['act_' + action].capitalize()}. {t['cite'].format(raw=raw)}")
        return _result(tx, **result)
