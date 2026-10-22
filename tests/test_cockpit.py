import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vault_cockpit import actions, config, fm, generate, migrate, store  # noqa: E402
from vault_cockpit.txn import CheckFailed  # noqa: E402

DEMO = ROOT / "examples" / "demo-vault"


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class VaultCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.vault = self.tmp / "vault"
        shutil.copytree(DEMO, self.vault)
        self.cfg = config.load(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def reconfigure(self, **over):
        data = json.loads((self.vault / "cockpit.json").read_text())
        for k, v in over.items():
            if isinstance(v, dict):
                data.setdefault(k, {}).update(v)
            else:
                data[k] = v
        (self.vault / "cockpit.json").write_text(json.dumps(data))
        self.cfg = config.load(self.vault)

    def item(self, i):
        return store.parse_item(self.cfg, store.item_path(self.cfg, i))


class FrontmatterTest(unittest.TestCase):
    def test_roundtrip_keeps_unknown_lines(self):
        text = '---\ntitle: "A: b"\nnested:\n  x: 1\nsources:\n  - RAW/a.md\ntags: [a, b]\n---\nbody\n'
        data, body = fm.parse(text)
        self.assertEqual(data["title"], "A: b")
        self.assertEqual(data["sources"], ["RAW/a.md"])
        self.assertEqual(data["tags"], ["a", "b"])
        out = fm.set_fields(text, status="gilt", sources=["RAW/a.md", "RAW/b.md"])
        self.assertIn("nested:\n  x: 1\n", out)
        self.assertIn("sources:\n  - RAW/a.md\n  - RAW/b.md\n", out)
        self.assertIn("tags: [a, b]", out)
        self.assertTrue(out.endswith("---\nbody\n"))
        self.assertEqual(fm.parse(out)[0]["status"], "gilt")

    def test_quotes_when_needed(self):
        self.assertEqual(fm.dump_scalar("plain-slug"), "plain-slug")
        self.assertEqual(fm.dump_scalar("a: b"), '"a: b"')
        self.assertEqual(fm.dump_scalar('say "hi"'), '"say \\"hi\\""')

    def test_block_list_keeps_quotes_on_paths_with_spaces(self):
        text = '---\nsources:\n  - RAW/a.md\n  - "RAW/b c.md"\n---\n'
        data, _ = fm.parse(text)
        out = fm.set_fields(text, sources=data["sources"] + ["RAW/d.md"])
        self.assertIn('  - "RAW/b c.md"', out)
        self.assertEqual(fm.parse(out)[0]["sources"], ["RAW/a.md", "RAW/b c.md", "RAW/d.md"])


class MigrateTest(unittest.TestCase):
    def test_split_row_respects_link_pipes(self):
        cells = migrate.split_row("| [[a|Alias]], [[b]] | text with `x|y` | 2026-01-01 |")
        self.assertEqual(cells, ["[[a|Alias]], [[b]]", "text with `x|y`", "2026-01-01"])

    def test_title(self):
        self.assertEqual(migrate.make_title("Is the deck from 28.09. final? More text."), "Is the deck from 28.09. final?")

    def test_demo_was_migrated_from_fixture(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            v = tmp / "v"
            shutil.copytree(DEMO, v)
            shutil.rmtree(v / "Wiki" / "open")
            shutil.copy(ROOT / "tests" / "fixtures" / "legacy-open.md", v / "Wiki" / "open.md")
            cfg = config.load(v)
            summary, drafts = migrate.run(cfg, write=True)
            self.assertEqual(summary["items"], 14)
            self.assertEqual(summary["by_suggestion"].get("done"), 1)
            self.assertEqual(summary["by_suggestion"].get("duplicate"), 1)
            review = [d for d in drafts if d["decision"]]
            self.assertEqual({d["decision"] for d in review}, {"2026-09-02-hosted-search"})
            self.assertEqual(len(list((v / "Wiki" / "open").glob("*.md"))), 14)
            with self.assertRaises(RuntimeError):
                migrate.run(cfg, write=True)
        finally:
            shutil.rmtree(tmp)


class ItemActionsTest(VaultCase):
    def test_answer_writes_evidence_first(self):
        r = actions.answer(self.cfg, "O-0001", "Cards at launch, invoice in the second release.", "integrate")
        it = self.item("O-0001")
        self.assertEqual(it["status"], "answered")
        self.assertTrue(it["integrate"])
        raw = [f for f in r["files"] if f.startswith("RAW/chat/")]
        self.assertEqual(len(raw), 1)
        self.assertIn(raw[0], it["sources"])
        raw_text = (self.vault / raw[0]).read_text()
        self.assertIn("> Cards at launch, invoice in the second release.", raw_text)
        self.assertIn("## Question (O-0001)", raw_text)
        self.assertIn(f"sources:\n  - {raw[0]}\n", (self.vault / "Wiki/open/O-0001.md").read_text())
        self.assertIn("O-0001", (self.vault / "Wiki/log.md").read_text())
        self.assertIn("Queued for Claude", (self.vault / "Wiki/open.md").read_text())
        actions.integrated(self.cfg, "O-0001")
        self.assertFalse(self.item("O-0001")["integrate"])

    def test_answer_but_still_open(self):
        actions.answer(self.cfg, "O-0002", "Not decided yet, expected in Q4.", "wait", due="2026-12-01")
        it = self.item("O-0002")
        self.assertEqual((it["status"], it["due"]), ("waiting", "2026-12-01"))
        self.assertTrue(it["integrate"])
        self.assertEqual(it["last_answer"]["text"], "Not decided yet, expected in Q4.")
        with self.assertRaises(actions.Invalid):
            actions.answer(self.cfg, "O-0003", "No date given", "wait")

    def test_answer_as_decision(self):
        r = actions.answer(self.cfg, "O-0002", "Garden tools go first.", "decision",
                           {"title": "Garden tools go live first", "subject": "release-order"})
        d = store.parse_decision(self.cfg, store.decision_path(self.cfg, r["decision"]))
        self.assertEqual(d["status"], "valid")
        self.assertEqual(d["subject"], "release-order")
        self.assertTrue(d["integrate"])

    def test_snooze_discard_duplicate_owner_reopen(self):
        actions.snooze(self.cfg, "O-0003", "2026-01-01")
        it = self.item("O-0003")
        self.assertEqual((it["status"], it["due"]), ("waiting", "2026-01-01"))
        self.assertTrue(store.is_due(self.cfg, it))
        actions.reopen(self.cfg, "O-0003")
        self.assertEqual(self.item("O-0003")["due"], "")
        actions.discard(self.cfg, "O-0006", "Export bug, fixed upstream.")
        self.assertEqual(len(self.item("O-0006")["sources"]), 1)
        actions.duplicate(self.cfg, "O-0013", "O-0004")
        self.assertEqual(self.item("O-0013")["duplicate_of"], "O-0004")
        actions.set_owner(self.cfg, "O-0004", "Sam (team lead)")
        self.assertEqual(self.item("O-0004")["owner"], "Sam (team lead)")
        self.assertIn("[[O-0006]]", (self.vault / "Wiki/open.md").read_text())

    def test_create_item(self):
        r = actions.create_item(self.cfg, "New question?", "Some text", "work", ["shop-relaunch"])
        self.assertEqual(r["id"], "O-0015")
        self.assertEqual(self.item("O-0015")["pages"], ["shop-relaunch"])

    def test_triage_batch(self):
        r = actions.triage(self.cfg, [{"id": "O-0005", "verdict": "done"},
                                      {"id": "O-0013", "verdict": "duplicate", "other": "O-0004"},
                                      {"id": "O-0009", "verdict": "keep"}])
        self.assertEqual(self.item("O-0005")["status"], "answered")
        self.assertEqual(self.item("O-0013")["status"], "duplicate")
        self.assertEqual(self.item("O-0009")["status"], "open")
        self.assertEqual(self.item("O-0009")["suggestion"], "")
        self.assertIn(r["raw"], self.item("O-0005")["sources"])

    def test_validation(self):
        with self.assertRaises(actions.Invalid):
            actions.answer(self.cfg, "O-0001", "  ")
        with self.assertRaises(KeyError):
            actions.answer(self.cfg, "../../etc", "x")


class DecisionActionsTest(VaultCase):
    SLUG = "2026-09-02-hosted-search"

    def test_hints_attach(self):
        d = [x for x in store.decisions(self.cfg) if x["slug"] == self.SLUG][0]
        self.assertEqual(len(d["hints"]), 2)
        self.assertTrue(d["unconfirmed_triggers"])

    def test_confirm_closes_hints_and_triggers(self):
        actions.review_decision(self.cfg, self.SLUG, "confirm", "Yes, this holds.", confirm_triggers=True)
        d = store.parse_decision(self.cfg, store.decision_path(self.cfg, self.SLUG))
        self.assertEqual(d["status"], "valid")
        self.assertFalse(d["unconfirmed_triggers"])
        self.assertEqual(self.item("O-0011")["status"], "answered")

    def test_supersede(self):
        r = actions.review_decision(self.cfg, self.SLUG, "supersede", "Use it; the filters stay our own component.",
                                    new_title="Use the hosted search, filters as own component")
        old = store.parse_decision(self.cfg, store.decision_path(self.cfg, self.SLUG))
        new = store.parse_decision(self.cfg, store.decision_path(self.cfg, r["decision"]))
        self.assertEqual(old["status"], "superseded")
        self.assertEqual(old["superseded_by"], r["decision"])
        self.assertEqual(new["supersedes"], [self.SLUG])
        self.assertEqual(new["subject"], old["subject"])
        with self.assertRaises(actions.Invalid):
            actions.review_decision(self.cfg, self.SLUG, "withdraw", "again")

    def test_withdraw_needs_words(self):
        with self.assertRaises(actions.Invalid):
            actions.review_decision(self.cfg, "2026-05-20-sprint-demo", "withdraw", "")

    def test_second_valid_decision_for_subject_is_rejected(self):
        before = snapshot(self.vault)
        with self.assertRaises(CheckFailed):
            actions.create_decision(self.cfg, "Skip the demo", "sprint-review", "We skip the sprint demo.")
        self.assertEqual(before, snapshot(self.vault))


class TransactionTest(VaultCase):
    def test_failing_lint_rolls_back_everything(self):
        self.reconfigure(commands={"lint": [sys.executable, "-c", "import sys; sys.exit(1)"], "lint_mode": "exit"})
        before = snapshot(self.vault)
        with self.assertRaises(CheckFailed):
            actions.answer(self.cfg, "O-0001", "Some answer", "integrate")
        self.assertEqual(before, snapshot(self.vault))

    def test_no_regression_mode(self):
        lint = self.vault / "lint.py"
        lint.write_text("import pathlib\nn=len(list(pathlib.Path('RAW/chat').glob('*.md')))\n"
                        "print('Pages: 9')\nprint(f'Broken links: {n}')\nprint('Duplicates: keine')\n")
        self.reconfigure(commands={"lint": [sys.executable, "lint.py"], "lint_mode": "no-regression"})
        before = snapshot(self.vault)
        with self.assertRaises(CheckFailed) as cm:
            actions.answer(self.cfg, "O-0001", "Some answer")
        self.assertIn("Broken links", cm.exception.problems[0])
        self.assertEqual(before, snapshot(self.vault))
        self.reconfigure(commands={"lint_ignore": ["Broken links"]})
        actions.answer(self.cfg, "O-0001", "Some answer")

    def test_index_command_output_is_not_checked_as_a_page(self):
        (self.vault / "Wiki/index.md").write_text("# Index\n")
        (self.vault / "idx.py").write_text("import pathlib\np=pathlib.Path('Wiki/index.md')\np.write_text(p.read_text()+'- x\\n')\n")
        self.reconfigure(commands={"index": [sys.executable, "idx.py"]})
        r = actions.answer(self.cfg, "O-0001", "Indexed answer", "close")
        self.assertIn("Wiki/index.md", r["files"])

    def test_broken_link_is_caught(self):
        before = snapshot(self.vault)
        with self.assertRaises(CheckFailed):
            actions.create_item(self.cfg, "Points nowhere", pages=["does-not-exist"])
        self.assertEqual(before, snapshot(self.vault))

    def test_raw_is_never_overwritten(self):
        from vault_cockpit.txn import Transaction
        raw = self.vault / "RAW/docs/2026-07-10-quote-shop-relaunch.md"
        with self.assertRaises(ValueError):
            with Transaction(self.cfg, "x") as tx:
                tx.write(raw, "changed")
        self.assertIn("Fictional sample", raw.read_text())

    def test_git_commit(self):
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=self.vault, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=self.vault, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], cwd=self.vault, check=True)
        (self.vault / "unrelated.txt").write_text("not part of the commit")
        self.reconfigure(git={"commit": True})
        env_cfg = ["-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(["git"] + env_cfg + ["config", "user.email", "t@t"], cwd=self.vault, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.vault, check=True)
        actions.answer(self.cfg, "O-0001", "Committed answer", "close")
        log = subprocess.run(["git", "log", "--stat", "-1"], cwd=self.vault, capture_output=True, text=True).stdout
        self.assertIn("cockpit: answer O-0001", log)
        self.assertIn("Wiki/open/O-0001.md", log)
        status = subprocess.run(["git", "status", "--short"], cwd=self.vault, capture_output=True, text=True).stdout
        self.assertIn("unrelated.txt", status)

    def test_failed_git_commit_rolls_back_files_and_index(self):
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=self.vault, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=self.vault, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.vault, check=True)
        self.reconfigure(git={"commit": True})
        subprocess.run(["git", "add", "-A"], cwd=self.vault, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.vault, check=True)
        before = snapshot(self.vault / "Wiki")
        (self.vault / ".git" / "index.lock").write_text("")  # another git process holds the index
        with self.assertRaises(CheckFailed):
            actions.answer(self.cfg, "O-0001", "Blocked answer", "close")
        (self.vault / ".git" / "index.lock").unlink()
        self.assertEqual(snapshot(self.vault / "Wiki"), before)
        status = subprocess.run(["git", "status", "--short"], cwd=self.vault, capture_output=True, text=True).stdout
        self.assertEqual(status.strip(), "")


class GermanVaultTest(VaultCase):
    """A vault that keeps German status values and field names, like a German-language vault."""

    def test_mapping(self):
        self.reconfigure(vault_language="de", item_status={"open": "offen", "answered": "beantwortet"},
                         decision_status={"proposed": "vorgeschlagen", "valid": "gilt", "superseded": "ersetzt"},
                         decision_fields={"subject": "gegenstand", "superseded_by": "ersetzt_durch", "supersedes": "ersetzt"})
        p = store.decision_path(self.cfg, "2026-05-20-sprint-demo")
        p.write_text(p.read_text().replace("status: valid", "status: gilt").replace("subject:", "gegenstand:"))
        for f in (self.vault / "Wiki/open").glob("*.md"):
            f.write_text(f.read_text().replace("status: open", "status: offen"))
        actions.answer(self.cfg, "O-0001", "Antwort.", "close")
        text = (self.vault / "Wiki/open/O-0001.md").read_text()
        self.assertIn("status: beantwortet", text)
        self.assertIn("## History", text)  # existing heading kept
        self.assertIn("beantwortet (Beleg:", text)
        raw = next((self.vault / "RAW/chat").glob("*o-0001.md")).read_text()
        self.assertIn("von: Alex Example", raw)
        self.assertIn("## Antwort (wörtlich)", raw)
        d = store.parse_decision(self.cfg, p)
        self.assertEqual((d["status"], d["subject"]), ("valid", "sprint-review"))
        self.assertIn("Offene Fragen und Lücken", generate.overview(self.cfg))


class ServerTest(VaultCase):
    def setUp(self):
        super().setUp()
        from http.server import ThreadingHTTPServer
        from vault_cockpit.server import make_handler
        import socket
        s = socket.socket(); s.bind(("127.0.0.1", 0)); self.port = s.getsockname()[1]; s.close()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), make_handler(self.cfg, "tok", self.port))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def req(self, path, body=None, token="tok", host=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method="POST" if body is not None else "GET",
                                   data=json.dumps(body).encode() if body is not None else None)
        r.add_header("Content-Type", "application/json")
        if token:
            r.add_header("X-Cockpit-Token", token)
        if host:
            r.add_header("Host", host)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_flow(self):
        code, page = self.req("/")
        self.assertEqual(code, 200)
        self.assertIn(b'let TOKEN = "tok"', page)
        code, st = self.req("/api/state")
        st = json.loads(st)
        self.assertEqual(len(st["items"]), 14)
        self.assertEqual(len(st["decisions"]), 4)
        code, _ = self.req("/api/items/O-0001/answer", {"text": "x"}, token="wrong")
        self.assertEqual(code, 403)
        code, _ = self.req("/api/state", host="evil.example:80")
        self.assertEqual(code, 403)
        code, out = self.req("/api/items/O-0001/answer", {"text": "Answer via HTTP", "then": "close"})
        self.assertEqual(code, 200, out)
        code, out = self.req("/api/items/O-0001/answer", {"text": ""})
        self.assertEqual(code, 400)
        code, out = self.req("/api/items", {"title": "Broken", "pages": ["nope"]})
        self.assertEqual(code, 409)
        self.assertIn("broken link", json.loads(out)["problems"][0])


if __name__ == "__main__":
    unittest.main()
