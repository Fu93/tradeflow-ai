# Temp one-shot transform (scratch, not repo source): reword the app.js webhook
# comment block whose original text contains an un-reproducible (display-masked)
# "X-Webhook-Token=..." fragment that trips the workspace write guard.
# Guard remedy path: rewrite/remove the offending lines, then retry normal edits.
p = "src/web/static/js/app.js"
lines = open(p, encoding="utf-8").read().splitlines(keepends=True)

start = None
for i, ln in enumerate(lines):
    if ln.startswith("// H9 (webhook half):"):
        start = i
        break
assert start is not None, "comment block not found"

# The block is 4 comment lines: H9 intro, the masked X-Webhook-Token line, and
# two continuation lines ("// the server-side token ..." / "// header when ...").
end = start + 4
assert all(lines[j].startswith("//") for j in range(start, end)), "unexpected block shape"

new_block = [
    "// H9 (webhook half): POST /webhook/mobile-command is gated by a shared secret\n",
    "// header (X-Webhook-Token), sent only when a value is configured. It stays\n",
    "// fail-open in sandbox when the server-side value is unset. Same truthy-guard\n",
    "// rule as TF_API_KEY (send no header when we have no value configured). Set a\n",
    "// value at runtime via localStorage.setItem(\"tfWebhookToken\", ...) in DevTools.\n",
]
lines[start:end] = new_block
open(p, "w", encoding="utf-8", newline="").writelines(lines)
print("app.js comment block rewritten: lines", start + 1, "to", end)
