"""The overview page (offen.md / open.md) is generated, never edited by hand."""
import datetime as dt

from . import store
from .texts import T


def _cell(s):
    return " ".join(str(s).split()).replace("|", "\\|")


def _short(s, n=160):
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def overview(cfg):
    t = T(cfg)
    items = store.items(cfg)
    open_ = [i for i in items if i["status"] == "open"]
    waiting = [i for i in items if i["status"] == "waiting"]
    inbox = [i for i in items if i["integrate"]]
    since = (dt.date.today() - dt.timedelta(days=cfg["overview"]["closed_days"])).isoformat()
    closed = [i for i in items if i["status"] in ("answered", "discarded", "duplicate") and i["updated"] >= since]

    def pages(i):
        return ", ".join(f"[[{p}]]" for p in i["pages"])

    def table(rows, cols):
        head = "| " + " | ".join(t[c] for c, _ in cols) + " |"
        sep = "|" + "---|" * len(cols)
        return [head, sep] + ["| " + " | ".join(_cell(fn(r)) for _, fn in cols) + " |" for r in rows]

    item_col = ("col_item", lambda i: _short(i["title"]))
    id_col = ("col_id", lambda i: f"[[{i['id']}]]")
    out = [
        f"# {t['overview_title']}",
        "",
        t["overview_note"].format(items=cfg["paths"]["items"]),
        "",
        t["overview_stand"].format(date=store.today(), open=len(open_), waiting=len(waiting), inbox=len(inbox)),
        "",
    ]
    review = [i for i in open_ + waiting if i["decision"]]
    if review:
        out += [f"## {t['overview_review']}", ""]
        out += table(review, [id_col, item_col, ("col_pages", lambda i: f"[[{i['decision']}]]"),
                              ("col_since", lambda i: i["created"])])
        out.append("")
    groups = {}
    for i in open_:
        if not i["decision"]:
            groups.setdefault(i["domain"] or "", []).append(i)
    order = list(cfg["domains"]) + sorted(k for k in groups if k not in cfg["domains"])
    for dom in order:
        rows = groups.get(dom)
        if not rows:
            continue
        out += [f"## {dom.capitalize() if dom else t['no_domain']}", ""]
        out += table(rows, [id_col, item_col, ("col_pages", pages), ("col_since", lambda i: i["created"]),
                            ("col_owner", lambda i: i["owner"])])
        out.append("")
    if waiting:
        out += [f"## {t['overview_waiting']}", ""]
        out += table(waiting, [id_col, item_col, ("col_until", lambda i: i["due"]), ("col_owner", lambda i: i["owner"])])
        out.append("")
    if inbox:
        out += [f"## {t['overview_inbox']}", ""]
        out += [f"- [[{i['id']}]] {_short(i['title'], 120)}" for i in inbox]
        out.append("")
    if closed:
        out += [f"## {t['overview_closed']} ({cfg['overview']['closed_days']})", ""]
        out += table(closed, [id_col, item_col, ("col_status", lambda i: i["status_raw"]),
                              ("col_since", lambda i: i["updated"])])
        out.append("")
    archive = [i for i in items if i["status"] in ("answered", "discarded", "duplicate") and i not in closed]
    if archive:
        # keeps every item page linked, so closed items never turn into orphans
        out += [f"## {t['overview_archive']}", "", " · ".join(f"[[{i['id']}]]" for i in archive), ""]
    return "\n".join(out)
