#!/usr/bin/env python3
"""CI: every `--ctl` op handled in daemon/companion.py must appear in the README's $CTL line.
README may use brace shorthand: set-{vision,reasoning}-effort."""
import itertools, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEGACY = {"set-effort", "toggle-thinking", "text", "status"}  # aliases / not ctl ops

ops = set(re.findall(r'op == "([a-z-]+)"', (ROOT / "daemon/companion.py").read_text())) - LEGACY
ctl_line = next(l for l in (ROOT / "README.md").read_text().splitlines() if l.startswith("$CTL"))
documented = set()
for token in (part.split()[0] for part in ctl_line.split("|") if part.split()):
    m = re.match(r"([a-z-]*)\{([a-z,]+)\}([a-z-]*)$", token)
    documented.update(f"{m[1]}{alt}{m[3]}" for alt in m[2].split(",")) if m else documented.add(token)
documented.add("status")
missing = sorted(ops - documented)
for op in missing:
    print(f"undocumented --ctl op: {op}")
sys.exit(1 if missing else 0)
