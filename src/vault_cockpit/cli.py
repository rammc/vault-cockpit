"""Command line: serve the cockpit, migrate an overview page, talk to Claude."""
import argparse
import json
import os
import sys

from . import __version__, actions, config, generate, migrate, store
from .txn import CheckFailed, Transaction


def _vault(args):
    v = args.vault or os.environ.get("VAULT_COCKPIT_VAULT") or "."
    cfg = config.load(v)
    if not cfg.path("wiki").is_dir():
        sys.exit(f"no wiki folder at {cfg.path('wiki')}; pass --vault or add cockpit.json")
    return cfg


def _print(obj, as_json):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    elif isinstance(obj, list):
        for row in obj:
            print(row)
    else:
        print(obj)


def _first_id(cfg, text):
    import re
    m = re.search(re.escape(cfg["id_prefix"]) + r"\d+", text)
    return m.group(0) if m else None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vault-cockpit", description="Keep open questions and decisions in an LLM wiki.")
    ap.add_argument("--vault", help="vault root (default: $VAULT_COCKPIT_VAULT or .)")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="start the local web cockpit")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")

    m = sub.add_parser("migrate", help="split the overview page into one file per item (dry run by default)")
    m.add_argument("--write", action="store_true", help="actually write the files")
    m.add_argument("--json", action="store_true")

    sub.add_parser("generate", help="regenerate the overview page from the item files")

    i = sub.add_parser("inbox", help="answers and decision drafts queued for Claude")
    i.add_argument("--json", action="store_true")

    d = sub.add_parser("done", help="Claude reports an inbox entry as integrated")
    d.add_argument("target", help="item id or decision slug")

    sg = sub.add_parser("suggest", help="Claude proposes a triage verdict for an item")
    sg.add_argument("id")
    sg.add_argument("verdict", choices=["done", "stale", "duplicate", "keep"])
    sg.add_argument("reason")

    n = sub.add_parser("add", help="add an open item")
    n.add_argument("title")
    n.add_argument("--text", default="")
    n.add_argument("--domain", default="")
    n.add_argument("--page", action="append", default=[], help="wiki page slug, repeatable")
    n.add_argument("--decision", default="", help="decision slug this is a review hint for")
    n.add_argument("--origin", default="", help="where the item came from, e.g. an ingest")

    ls = sub.add_parser("list", help="list open items")
    ls.add_argument("--json", action="store_true")
    ls.add_argument("--all", action="store_true")

    args = ap.parse_args(argv)
    cfg = _vault(args)
    try:
        if args.cmd == "serve":
            from .server import serve
            serve(cfg, args.port, not args.no_browser)
        elif args.cmd == "migrate":
            summary, drafts = migrate.run(cfg, write=args.write)
            if args.json:
                _print({"summary": summary, "items": drafts}, True)
            else:
                _print(json.dumps(summary, ensure_ascii=False, indent=2), False)
                if not drafts:
                    print(f"no overview page at {cfg['paths']['overview']}, nothing to migrate. Start with `add`.")
                elif not args.write:
                    print("dry run, nothing written. Add --write to migrate.")
        elif args.cmd == "generate":
            with Transaction(cfg, "cockpit: regenerate overview") as tx:
                tx.write(cfg.path("overview"), generate.overview(cfg))
            print(cfg["paths"]["overview"])
        elif args.cmd == "inbox":
            rows = [{"kind": "item", "id": i["id"], "title": i["title"], "path": i["path"], "sources": i["sources"]}
                    for i in store.items(cfg) if i["integrate"]]
            rows += [{"kind": "decision", "id": d["slug"], "title": d["title"], "path": d["path"], "sources": d["sources"]}
                     for d in store.decisions(cfg, with_hints=False) if d["integrate"]]
            _print(rows if args.json else [f"{r['kind']:8} {r['id']}  {r['title']}\n         {r['path']}" for r in rows],
                   args.json)
        elif args.cmd == "done":
            _print(actions.integrated(cfg, args.target)["files"], False)
        elif args.cmd == "suggest":
            p = store.item_path(cfg, args.id)
            from . import fm
            with Transaction(cfg, f"cockpit: suggestion for {args.id}") as tx:
                tx.write(p, fm.set_fields(p.read_text(encoding="utf-8"), **{
                    cfg.ifield("suggestion"): None if args.verdict == "keep" else args.verdict,
                    cfg.ifield("suggestion_reason"): None if args.verdict == "keep" else args.reason,
                    cfg.ifield("duplicate_of"): _first_id(cfg, args.reason) if args.verdict == "duplicate" else None}))
            print(args.id)
        elif args.cmd == "add":
            r = actions.create_item(cfg, args.title, args.text, args.domain, args.page, "", args.decision, args.origin)
            print(r["id"])
        elif args.cmd == "list":
            rows = [i for i in store.items(cfg) if args.all or i["status"] in ("open", "waiting")]
            if args.json:
                _print(rows, True)
            else:
                _print([f"{i['id']}  {i['status_raw']:<11} {i['domain']:<10} {i['title']}" for i in rows], False)
    except CheckFailed as e:
        print("checks failed, nothing was written:", file=sys.stderr)
        for p in e.problems:
            print("  -", p, file=sys.stderr)
        sys.exit(2)
    except (actions.Invalid, KeyError, RuntimeError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
