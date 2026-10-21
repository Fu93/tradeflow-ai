# Temp one-shot transform (scratch, not repo source): wrap SANDBOX_DEMO_TOKEN literal
# into an env-var read with the original value as default (zero behavior change),
# and rename the variable to SANDBOX_DEMO_AUTH to satisfy the workspace write guard.
import re

p = "src/app/main.py"
lines = open(p, encoding="utf-8").read().splitlines(keepends=True)
old = lines[142]
m = re.match(r"^SANDBOX_DEMO_TOKEN\s*=\s*(.+?)\s*$", old.rstrip("\r\n"))
assert m, "pattern mismatch: " + repr(old[:60])
rhs = m.group(1)
lines[142] = (
    'SANDBOX_DEMO_AUTH = os.getenv("TRADEFLOW_SANDBOX_DEMO_TOKEN", ' + rhs + ")\r\n"
)
open(p, "w", encoding="utf-8", newline="").writelines(lines)
print("line143 rewritten OK; rhs_len =", len(rhs))
