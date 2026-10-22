"""All-or-nothing writes: evidence first, then wiki, log and overview, then lint.

If a check fails, every file is restored to its previous state and new files
are removed. RAW files are only ever created, never modified.
"""
import ast
import hashlib
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: in-process lock only
    fcntl = None

from . import fm

LOCK = threading.Lock()


class _VaultLock:
    """Serialises transactions across processes (server and CLI) on one vault."""

    def __init__(self, cfg):
        key = hashlib.sha1(str(cfg.vault.resolve()).encode()).hexdigest()[:12]
        self.path = Path(tempfile.gettempdir()) / f"vault-cockpit-{key}.lock"  # outside the vault
        self.fh = None

    def acquire(self):
        LOCK.acquire()
        if fcntl:
            try:
                self.fh = open(self.path, "w")
                fcntl.flock(self.fh, fcntl.LOCK_EX)
            except BaseException:
                self.release()
                raise

    def release(self):
        if self.fh:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_UN)
            finally:
                self.fh.close()
                self.fh = None
        LOCK.release()


class CheckFailed(Exception):
    def __init__(self, problems, output=""):
        super().__init__("; ".join(problems))
        self.problems = problems
        self.output = output


class Transaction:
    def __init__(self, cfg, message):
        self.cfg = cfg
        self.message = message
        self.before = {}   # path -> original text, or None if the file is new
        self.order = []
        self.generated = set()  # rewritten by the index command; checked by the vault lint, not here
        self.lock = _VaultLock(cfg)

    # -- writing ---------------------------------------------------------
    def _guard(self, path):
        path = path.resolve()
        if self.cfg.vault not in path.parents:
            raise ValueError(f"outside the vault: {path}")
        raw = self.cfg.vault / "RAW"
        if raw in path.parents and path.exists() and path not in self.before:
            raise ValueError(f"RAW is immutable: {path.relative_to(self.cfg.vault)}")
        return path

    def _remember(self, path):
        if path not in self.before:
            self.before[path] = path.read_text(encoding="utf-8") if path.exists() else None
            self.order.append(path)

    def write(self, path, text):
        path = self._guard(path)
        self._remember(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def create(self, path, text):
        """Write a new file; on name clash append -2, -3 ... Returns the path used."""
        base, n = path, 2
        while path.exists() and path not in self.before:
            path = base.with_name(f"{base.stem}-{n}{base.suffix}")
            n += 1
        return self.write(path, text)

    def append(self, path, text):
        path = self._guard(path)
        cur = path.read_text(encoding="utf-8") if path.exists() else ""
        if cur and not cur.endswith("\n"):
            cur += "\n"
        return self.write(path, cur + text)

    def rel(self, path):
        return str(path.resolve().relative_to(self.cfg.vault))

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self):
        self.lock.acquire()
        try:
            self.baseline = lint_counts(self.cfg) if self._lint_mode() == "no-regression" else None
        except Exception:
            self.lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                try:
                    self._finish()
                except BaseException:
                    self.rollback()
                    raise
            else:
                self.rollback()
        finally:
            self.lock.release()
        return False

    def _lint_mode(self):
        return self.cfg["commands"]["lint_mode"] if self.cfg["commands"]["lint"] else None

    def _finish(self):
        from . import generate  # late import, generate uses store
        if self.cfg["overview"]["generate"] and self.cfg.path("items").is_dir():
            self.write(self.cfg.path("overview"), generate.overview(self.cfg))
        if self.cfg["commands"]["index"]:
            self._run_tracked(self.cfg["commands"]["index"])
        problems = builtin_check(self.cfg, [p for p in self.order if p.suffix == ".md" and p not in self.generated])
        out = ""
        mode = self._lint_mode()
        if mode == "exit":
            code, out = run(self.cfg, self.cfg["commands"]["lint"])
            if code != 0:
                problems.append(f"lint exited with {code}")
        elif mode == "no-regression":
            after, out = lint_counts(self.cfg, with_output=True)
            ignore = set(self.cfg["commands"]["lint_ignore"])
            for k, v in after.items():
                if k in ignore:
                    continue
                if v > self.baseline.get(k, 0):
                    problems.append(f"lint: “{k}” rose from {self.baseline.get(k, 0)} to {v}")
        if problems:
            raise CheckFailed(problems, out)
        if getattr(self, "result", None) is not None:
            self.result["files"] = self.files()
        if self.cfg["git"]["commit"]:
            git_commit(self.cfg, [p for p in self.order if p.exists() or self.before[p] is not None], self.message)

    def _run_tracked(self, cmd):
        """Run a generator (e.g. an index builder) and fold the files it changed into the transaction."""
        snap = _snapshot(self.cfg)
        code, out = run(self.cfg, cmd)
        if code != 0:
            raise CheckFailed([f"index command exited with {code}"], out)
        for p, old in snap.items():
            if p.exists() and p.read_text(encoding="utf-8", errors="replace") != old and p not in self.before:
                self.before[p] = old
                self.order.append(p)
                self.generated.add(p)

    def rollback(self):
        for p in reversed(self.order):
            old = self.before[p]
            if old is None:
                if p.exists():
                    p.unlink()
            else:
                p.write_text(old, encoding="utf-8")

    def files(self):
        return [self.rel(p) for p in self.order]


def _snapshot(cfg):
    """Top-level wiki files a generator is likely to rewrite (index, overview)."""
    w = cfg.path("wiki")
    return {p.resolve(): p.read_text(encoding="utf-8", errors="replace") for p in w.glob("*.md")}


def run(cfg, cmd):
    r = subprocess.run(cmd, cwd=cfg.vault, capture_output=True, text=True, timeout=120)
    return r.returncode, (r.stdout + r.stderr)


_COUNT = re.compile(r"^([^\s:][^:]{0,80}):\s*(.*)$")


def lint_counts(cfg, with_output=False):
    """Parse `Label: N` lines. `none`/`keine` count as 0, a printed list by its length."""
    _, out = run(cfg, cfg["commands"]["lint"])
    counts = {}
    for line in out.splitlines():
        m = _COUNT.match(line)
        if not m:
            continue
        label, val = m.group(1).strip(), m.group(2).strip()
        n = None
        mm = re.match(r"^(\d+)\b", val)
        if mm:
            n = int(mm.group(1))
        elif val.lower() in ("keine", "none", "no", "0"):
            n = 0
        elif val.startswith("["):
            try:
                n = len(ast.literal_eval(val))
            except Exception:
                n = val.count(",") + 1
        if n is not None:
            counts[label] = n
    return (counts, out) if with_output else counts


def builtin_check(cfg, paths):
    """Checks on the files this transaction touched."""
    problems = []
    wiki = cfg.path("wiki").resolve()
    raw_dirs = {cfg.path("raw_answers").resolve(), cfg.path("raw_decisions").resolve()}
    stems = None
    for p in paths:
        if not p.exists() or wiki not in p.parents:
            continue
        if raw_dirs & set(p.resolve().parents):
            continue  # RAW evidence, even when it sits inside the wiki folder
        if p.resolve() == cfg.path("overview").resolve() or p.resolve() == cfg.path("log").resolve():
            continue
        text = p.read_text(encoding="utf-8")
        data, body = fm.parse(text)
        r = str(p.relative_to(cfg.vault))
        if not data:
            problems.append(f"{r}: no frontmatter")
            continue
        for k in cfg["required_fields"]:
            if k not in data:
                problems.append(f"{r}: missing field {k}")
        for s in data.get("sources") or []:
            if s and not (cfg.vault / s).exists():
                problems.append(f"{r}: source does not exist: {s}")
        if stems is None:
            stems = {q.stem for q in cfg.path("wiki").rglob("*.md")}
        for link in fm.links(body):
            if link.startswith("attachments/"):
                continue
            if link not in stems:
                problems.append(f"{r}: broken link [[{link}]]")
    # one valid decision per subject
    from . import store
    seen = {}
    for d in store.decisions(cfg, with_hints=False):
        if d["status"] == "valid" and d["subject"]:
            seen.setdefault(d["subject"], []).append(d["slug"])
    touched = {p.stem for p in paths}
    for subj, slugs in seen.items():
        if len(slugs) > 1 and touched & set(slugs):
            problems.append(f"more than one valid decision for “{subj}”: {', '.join(slugs)}")
    return problems


def git_commit(cfg, paths, message, attempts=5):
    """Commit exactly the touched files. Retries while another git process holds the index;
    on failure the paths are unstaged again so the rollback leaves the index clean."""
    if not (cfg.vault / ".git").exists():
        return
    rels = [str(p.resolve().relative_to(cfg.vault)) for p in paths]
    for i in range(attempts):
        add = subprocess.run(["git", "add", "-A", "--"] + rels, cwd=cfg.vault, capture_output=True, text=True)
        res = add if add.returncode else subprocess.run(
            ["git", "commit", "-q", "-m", message, "--"] + rels, cwd=cfg.vault, capture_output=True, text=True)
        if res.returncode == 0:
            return
        if "index.lock" not in (res.stderr or "") or i == attempts - 1:
            subprocess.run(["git", "reset", "-q", "--"] + rels, cwd=cfg.vault, capture_output=True)
            raise CheckFailed([f"git commit failed: {(res.stderr or res.stdout).strip()[:300]}"])
        time.sleep(0.2 * (i + 1))
