"""Minimal, format-preserving frontmatter handling.

Reads the YAML subset LLM wikis actually use (scalars, quoted strings, inline
lists, block lists) and edits single keys in place, so a write never reflows
the rest of a hand-written header.
"""
import re

_FM = re.compile(r"^---\n(.*?)\n---[ \t]*\n?", re.S)
_KEY = re.compile(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$")


def split(text):
    """Return (header_lines, body). header_lines is None without frontmatter."""
    m = _FM.match(text)
    if not m:
        return None, text
    return m.group(1).split("\n"), text[m.end():]


def _scalar(raw):
    raw = raw.strip()
    if raw == "" or raw in ("~", "null"):
        return None
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1].replace("''", "'")
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_scalar(x) for x in _split_inline(inner)]
    if raw in ("true", "false"):
        return raw == "true"
    return raw


def _split_inline(s):
    out, cur, q = [], "", None
    for ch in s:
        if q:
            cur += ch
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
            cur += ch
        elif ch == ",":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [x.strip() for x in out if x.strip()]


def _spans(lines):
    """Map key -> (start, end) line span, including block list items."""
    spans, i = {}, 0
    while i < len(lines):
        m = _KEY.match(lines[i])
        if not m:
            i += 1
            continue
        j = i + 1
        while j < len(lines) and (lines[j].startswith((" ", "\t")) or lines[j].strip() == ""):
            if lines[j].strip() == "" and (j + 1 >= len(lines) or _KEY.match(lines[j + 1])):
                break
            j += 1
        spans[m.group(1)] = (i, j)
        i = j
    return spans


def parse(text):
    """Return (data, body). data is {} without frontmatter."""
    lines, body = split(text)
    if lines is None:
        return {}, body
    data = {}
    for key, (a, b) in _spans(lines).items():
        first = _KEY.match(lines[a]).group(2)
        rest = [l.strip() for l in lines[a + 1:b] if l.strip()]
        if first.strip() == "" and rest and all(l.startswith("- ") or l == "-" for l in rest):
            data[key] = [_scalar(l[1:]) for l in rest]
        elif first.strip() == "" and rest:
            data[key] = None  # nested mapping or block scalar: preserved, not interpreted
        else:
            data[key] = _scalar(first)
    return data, body


_PLAIN = re.compile(r"^[\w./@+-][\w ./@+()-]*$", re.U)


def dump_scalar(v, item=False):
    if v is None:
        return ""
    if v is True or v is False:
        return "true" if v else "false"
    s = str(v)
    if item and " " in s:
        # list items with blanks stay quoted: line-based wiki lints split them otherwise
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if _PLAIN.match(s) and s not in ("true", "false", "null", "~") and not s.endswith(" "):
        return s
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _dump(key, v, block):
    if isinstance(v, list):
        if not v:
            return [f"{key}: []"]
        if block:
            return [f"{key}:"] + [f"  - {dump_scalar(x, True)}" for x in v]
        return [f"{key}: [" + ", ".join(dump_scalar(x, True) for x in v) + "]"]
    out = dump_scalar(v)
    return [f"{key}: {out}" if out != "" else f"{key}:"]


BLOCK_LISTS = {"sources"}


def set_fields(text, **fields):
    """Set keys in place. Keeps order and style; new keys go to the end."""
    lines, body = split(text)
    if lines is None:
        lines, body = [], text
    for key, v in fields.items():
        spans = _spans(lines)
        if key in spans:
            a, b = spans[key]
            # sources always as a block list: wiki lints read RAW paths line by line
            was_block = b - a > 1 or key in BLOCK_LISTS
            lines[a:b] = _dump(key, v, was_block)
        else:
            lines.extend(_dump(key, v, key in BLOCK_LISTS))
    return "---\n" + "\n".join(lines) + "\n---\n" + body


def render(data, body, block_lists=BLOCK_LISTS):
    lines = []
    for k, v in data.items():
        lines.extend(_dump(k, v, k in block_lists))
    return "---\n" + "\n".join(lines) + "\n---\n" + body


LINK = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")


def links(text):
    return [m.strip() for m in LINK.findall(text)]
