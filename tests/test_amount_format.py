"""Money on the auction desk keeps its decimals, and CVaR is shown in currency."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _eval(expression: str):
    script = r"""
const fs = require("fs");
const vm = require("vm");
const src = fs.readFileSync(process.argv[1], "utf8");
const sandbox = {
  localStorage: { getItem() { return ""; } },
  document: { addEventListener() {}, getElementById() { return null; } },
  console,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "app.js" });
const out = vm.runInContext(process.argv[2], sandbox);
process.stdout.write(JSON.stringify(out));
"""
    proc = subprocess.run(
        ["node", "-e", script, str(ROOT / "src/web/static/js/app.js"), expression],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_unknown_currency_keeps_two_decimals():
    shown = _eval('[tfAmount(14.8, ""), tfAmount(12.5, ""), tfAmount(35, "CNY"), tfAmount(1234.5, "CNY"), tfCount(10000)]')
    assert shown == ["14.80", "12.50", "35.00 CNY", "1,234.50 CNY", "10,000"]


def test_cvar_ratio_is_money_in_the_deal_currency():
    # 36.5 / 28 is the saturated ceiling/target ratio the desk used to print raw.
    shown = _eval('tfDealMoney(36.5 / 28, 28, "CNY")')
    assert shown == "36.50 CNY"
    label = _eval(
        '(() => { const money = tfDealMoney(36.5 / 28, 28, "CNY");'
        ' return "CVaR cost " + money + " (tail saturated at the ceiling)."; })()'
    )
    assert label == "CVaR cost 36.50 CNY (tail saturated at the ceiling)."
    assert "1.303" not in label


def test_validation_prefix_is_not_shown():
    shown = _eval('tfValidationMessage("Value error, Duplicate supplier name: A. Each supplier needs its own name.")')
    assert shown == "Duplicate supplier name: A. Each supplier needs its own name."
