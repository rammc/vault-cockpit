"""Vault configuration: `cockpit.json` in the vault root, merged over defaults.

Status values and field names are vault data, so they are configurable. The
code only ever speaks in the canonical English keys below.
"""
import copy
import json
from pathlib import Path

DEFAULTS = {
    "ui_language": "en",
    "vault_language": "en",
    "owner_default": "me",
    "owner_name": "",
    "owners": [],
    "id_prefix": "O-",
    "types": {"item": "open", "decision": "decision"},
    "channel": "Vault Cockpit",
    "domains": [],
    "default_domain": "",
    "obsidian_vault": "",
    "paths": {
        "wiki": "Wiki",
        "items": "Wiki/open",
        "overview": "Wiki/open.md",
        "log": "Wiki/log.md",
        "decisions": ["Wiki/decisions"],
        "raw_answers": "RAW/chat",
        "raw_decisions": "RAW/decisions",
    },
    "item_status": {
        "open": "open",
        "waiting": "waiting",
        "answered": "answered",
        "discarded": "discarded",
        "duplicate": "duplicate",
    },
    "item_fields": {
        "created": "created",
        "due": "due",
        "origin": "origin",
        "pages": "pages",
        "owner": "owner",
        "suggestion": "suggestion",
        "suggestion_reason": "suggestion_reason",
        "integrate": "integrate",
        "decision": "decision",
        "duplicate_of": "duplicate_of",
    },
    "decision_status": {
        "proposed": "proposed",
        "valid": "valid",
        "superseded": "superseded",
        "withdrawn": "withdrawn",
    },
    "decision_fields": {
        "decided_by": "decided_by",
        "decided_on": "decided_on",
        "subject": "subject",
        "supersedes": "supersedes",
        "superseded_by": "superseded_by",
    },
    "required_fields": ["title", "type", "domain", "status", "updated", "sources"],
    "unconfirmed_trigger_marker": "",
    "commands": {
        "lint": [],
        "lint_mode": "exit",
        "lint_ignore": [],
        "index": [],
    },
    "overview": {"generate": True, "closed_days": 14},
    "git": {"commit": False},
}


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


class Config:
    def __init__(self, vault, data):
        self.vault = Path(vault).expanduser().resolve()
        self.data = data

    def __getitem__(self, k):
        return self.data[k]

    def path(self, key):
        return self.vault / self.data["paths"][key]

    def decision_dirs(self):
        out = []
        for pattern in self.data["paths"]["decisions"]:
            out.extend(sorted(p for p in self.vault.glob(pattern) if p.is_dir()))
        return out

    def ist(self, canon):
        return self.data["item_status"][canon]

    def ist_canon(self, value):
        for k, v in self.data["item_status"].items():
            if v == value:
                return k
        return "open"

    def ifield(self, canon):
        return self.data["item_fields"][canon]

    def dst(self, canon):
        return self.data["decision_status"][canon]

    def dst_canon(self, value):
        for k, v in self.data["decision_status"].items():
            if v == value:
                return k
        return None

    def dfield(self, canon):
        return self.data["decision_fields"][canon]

    def public(self):
        """What the browser needs to know."""
        d = self.data
        return {
            "ui_language": d["ui_language"],
            "owner_default": d["owner_default"],
            "owners": d["owners"],
            "domains": d["domains"],
            "obsidian_vault": d["obsidian_vault"],
            "vault_name": self.vault.name,
            "lint": bool(d["commands"]["lint"]),
        }


def load(vault):
    vault = Path(vault).expanduser().resolve()
    data = copy.deepcopy(DEFAULTS)
    f = vault / "cockpit.json"
    if f.exists():
        _merge(data, json.loads(f.read_text(encoding="utf-8")))
    if not data["default_domain"] and data["domains"]:
        data["default_domain"] = data["domains"][0]
    return Config(vault, data)
