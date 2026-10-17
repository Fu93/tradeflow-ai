# Temp REPL check (scratch): trace the price-scan regex and parse path.
import sys, os, re, inspect

sys.path.insert(0, os.getcwd())

text = "can you do 5000 units at 13.8"
pat = re.compile(
    r"(?<![\w.])(?:do|at|offer|price|\$|¥)\s*\$?\s*(\d+(?:\.\d{1,2})?)"
    r"(?!\s*(?:units?|pcs|pieces|sets?|k\b|件|把|个|個|只|隻))",
    re.I,
)
print("regex matches:", [(m.group(0), m.group(1)) for m in pat.finditer(text)])

from src.engine.bilingual import EnglishExecutiveCommandParser as P

c = P.parse(text)
print("parse ->", c.action, c.override_price)
c2 = P.parse("price 5000 units")
print(
    "parse2 ->",
    c2.action,
    c2.override_price,
    "demanded_moq=",
    getattr(c2, "demanded_moq", None),
)
src = inspect.getsource(P.parse)
print("PRICE_REGEX referenced in parse():", "PRICE_REGEX" in src)
print("PRICE_PATTERN referenced in parse():", "PRICE_PATTERN" in src)
# Show every regex class attribute actually referenced inside parse()
import re as _re

for name in dir(P):
    if name.endswith("_REGEX") or name.endswith("_PATTERN"):
        print("attr:", name, "used_in_parse:", name in src)
