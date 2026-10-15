"""TradeFlow 引擎 — 穩健性層（評審最會捅的點，主動量化）。

三項：
1. gain vs 固定類人基準（例如固定每輪讓 5%/輪）：引擎推薦策略優於固定策略多少。
2. prior 敏感度：prior reserve mean/std 在 ±50% 下，引擎「實際推薦策略」的邊際/破局。
3. 綜合報告：把「對手模型是假設的」轉化為「我們量化了假設敏感性」。
"""
from __future__ import annotations
import numpy as np
from . import model
from .solver import solve


def _baseline_at_own_best(res_b) -> tuple[float, float, int]:
    """在固定讓步基準**自己的**最優開盤檔取 margin/break。

    [修正] 舊版以 `bi, bj = len(opens) // 2, 0` 讀取基準，即固定取第 20 檔開盤，
    但引擎那一邊是用 `res["best"]` 取**全網格最優**。兩邊的選取規則不一致，
    使比較偏向引擎：實測基準 margin 從 index 0 的 0.2085 單調遞減到 index 39 的
    負值，硬取 index 20 得 0.0933，據此報出的 gain (+0.1623) 是誠實值 (+0.0360)
    的 4.5 倍。

    公平做法：兩邊都取各自可達的最優。這會讓 gain 變小，但那才是真的。
    """
    flat = int(np.argmax(res_b["exp_margin"]))
    bi, bj = np.unravel_index(flat, res_b["exp_margin"].shape)
    return float(res_b["exp_margin"][bi, bj]), float(res_b["exp_break"][bi, bj]), int(bi)


def fixed_baseline_margin(rng, n_samples: int = 20_000, fixed_conc: float = 0.05,
                          reserve_mean=1.08, reserve_std=0.10):
    """固定類人基準：每輪固定讓 fixed_conc（如 5%/輪），與我們引擎的同情境期望邊際比。

    回傳 (engine_margin, engine_break, baseline_margin, baseline_break, gain)。
    gain = engine_margin - baseline_margin（>0 表示引擎優於固定策略）。

    兩邊皆取各自網格上的最優開盤檔（見 `_baseline_at_own_best` 的修正說明）。
    """
    # 引擎解（在 480 策略中選最優）
    res = solve(n_opp_samples=n_samples, rng=rng, reserve_mean=reserve_mean, reserve_std=reserve_std)
    i, j = res["best"]
    engine_margin = float(res["exp_margin"][i, j])
    engine_break = float(res["exp_break"][i, j])

    # 固定基準：讓步曲線全 fixed_conc（人類常見的「固定讓步」），
    # 開盤檔由基準自己選最優，而非人為指定中間檔。
    opens, scheds = model.strategy_grid()
    scheds_b = np.full_like(scheds, fixed_conc)
    res_b = solve(n_opp_samples=n_samples, opens=opens, scheds=scheds_b,
                  rng=rng, reserve_mean=reserve_mean, reserve_std=reserve_std)
    baseline_margin, baseline_break, _ = _baseline_at_own_best(res_b)
    return engine_margin, engine_break, baseline_margin, baseline_break, engine_margin - baseline_margin


def multi_baseline(rng, n_samples: int = 20_000, reserve_mean=1.08, reserve_std=0.10):
    """多個類人基準對比（不同固定讓步率），回傳 [ {name,margin,break,gain}, ... ]。

    每個基準都取自己的最優開盤檔，與引擎的最優選取規則一致。
    """
    res = solve(n_opp_samples=n_samples, rng=rng, reserve_mean=reserve_mean, reserve_std=reserve_std)
    i, j = res["best"]
    em = float(res["exp_margin"][i, j]); eb = float(res["exp_break"][i, j])
    opens, scheds = model.strategy_grid()
    out = []
    for conc in (0.03, 0.05, 0.10, 0.15):
        sb = np.full_like(scheds, conc)
        rb = solve(n_opp_samples=n_samples, opens=opens, scheds=sb, rng=rng,
                   reserve_mean=reserve_mean, reserve_std=reserve_std)
        bm, bb, _ = _baseline_at_own_best(rb)
        out.append({"name": f"fixed {conc*100:.0f}%/rd", "margin": bm, "break": bb,
                    "gain": em - bm})
    out.insert(0, {"name": "TradeFlow engine", "margin": em, "break": eb, "gain": 0.0})
    return out


def prior_sensitivity(rng, n_samples: int = 20_000, base_mean=1.08, base_std=0.10,
                      factors=(0.5, 1.0, 1.5)):
    """prior reserve mean/std 在 ±50% 下，引擎「實際推薦策略」的期望邊際與破局率。

    回傳 list of (factor, mean, rec_margin, rec_break) —— 量化先驗偏離的後果。

    [誠實] 實測（2026-10-14，n=20,000）結果為**不穩定**：
      ×0.5 (mean=0.54): margin -0.016, breakdown 81.1%
      ×1.0 (mean=1.08): margin +0.254, breakdown  6.7%
      ×1.5 (mean=1.62): margin -0.110, breakdown 45.9%
    即先驗是該圖的承載參數（load-bearing parameter）。本函式的作用是**暴露**這一
    依賴性，而非證明穩定；結論須由 report() 依實測值計算，禁止寫死。
    """
    out = []
    for f in factors:
        m = base_mean * f
        s = base_std * f
        res = solve(n_opp_samples=n_samples, rng=rng, reserve_mean=m, reserve_std=s)
        i, j = res["best"]
        out.append((f, m, float(res["exp_margin"][i, j]), float(res["exp_break"][i, j])))
    return out


def misbelief_sensitivity(n_samples: int = 20_000, base_mean=1.08, base_std=0.10,
                          beliefs=(0.5, 1.0, 1.5)):
    """真正的「信念錯誤」測試 (misbelief test)：現實不變，僅引擎先驗偏移。

    與 prior_sensitivity() 的關鍵差別：
      * prior_sensitivity：對手樣本也從偏移後的先驗抽樣 → **世界與信念同時偏移**，
        度量的是「場景敏感度」(scenario sensitivity)，不是信念錯誤的後果。
      * 本函式：規劃用偏移先驗挑出策略 (i,j)，評估卻用基準分布（現實）的
        全策略矩陣讀取該策略的結局 → 度量「引擎以為 X、實際世界是 base」時的表現。

    方法：
      1. reality = solve(prior=base)：在真實分布下算出全網格期望矩陣。
      2. plan    = solve(prior=shifted)：引擎在錯誤信念下選 best(i, j)。
      3. 讀 reality.exp_margin[i, j] / exp_break[i, j]：錯誤策略在現實的結局。

    配對設計：所有 solve 呼叫使用同一預設種子 (20261013)，對手類型抽樣與
    標準化噪聲在各組間完全一致，差異僅來自先驗位移，排除抽樣噪聲。

    回傳 list of (factor, belief_mean, margin_in_reality, breakdown_in_reality)。
    """
    from .solver import solve
    # 現實（基準先驗）下的全策略評估矩陣
    reality = solve(n_opp_samples=n_samples, reserve_mean=base_mean, reserve_std=base_std)
    out = []
    for f in beliefs:
        bm, bs = base_mean * f, base_std * f
        # 引擎在「以為先驗是 bm/bs」的信念下挑策略
        plan = solve(n_opp_samples=n_samples, reserve_mean=bm, reserve_std=bs)
        i, j = plan["best"]
        # 該策略放回現實（基準分布）評估
        out.append((f, bm,
                    float(reality["exp_margin"][i, j]),
                    float(reality["exp_break"][i, j])))
    return out


def report(target_price=12.0, n_samples=20000, seed=20261013):
    """Produce text + numeric report (for Demo / README / pitch)."""
    rng = np.random.default_rng(seed)
    em, eb, bm, bb, gain = fixed_baseline_margin(rng, n_samples)
    sens = prior_sensitivity(rng, n_samples)
    multi = multi_baseline(rng, n_samples)
    lines = []
    lines.append("=== TradeFlow Robustness Report ===")
    lines.append(f"Engine recommendation  margin(norm): {em:.3f}   breakdown: {eb:.1%}")
    lines.append(f"Fixed 5%-per-round baseline margin: {bm:.3f}   breakdown: {bb:.1%}")
    lines.append(f"Gain (engine - baseline): {gain:+.3f}  (~{gain*target_price:.2f} ¥/unit)")
    lines.append("Multi-baseline comparison:")
    for b in multi:
        lines.append(f"  {b['name']:>18}: margin {b['margin']:.3f}  breakdown {b['break']:.1%}  "
                     f"gain {b['gain']:+.3f}")
    # [審計修正] 結論必須由下方實測數字計算，不可寫死。
    # 舊版無條件印出「recommended strategy is stable」，但同一次執行的實測值為
    # breakdown 6.7% → 81.1% → 45.9%（先驗 ×0.5/×1.0/×1.5），結論與數字直接矛盾。
    base_m, base_b = sens[1][2], sens[1][3]
    worst_b = max(rb for _, _, _, rb in sens)
    worst_gap = max(abs(rm - base_m) for _, _, rm, _ in sens)
    stable = (worst_b <= max(0.15, 2.0 * base_b)) and (worst_gap <= 0.05)
    if stable:
        lines.append("Prior sensitivity: recommended strategy remains stable across the tested prior range.")
    else:
        worst_f = next(f for f, _, _, rb in sens if rb == worst_b)
        lines.append(
            "Prior sensitivity: DEGRADES under prior miss — prior-dependence is material, not stable. "
            f"Worst case prior×{worst_f}: breakdown {worst_b:.1%} (vs {base_b:.1%} at base), "
            f"expected margin swing {worst_gap:+.3f}. The engine's edge is conditional on an "
            "accurate reserve prior (see LIMITATIONS §十三)."
        )
    for f, m, rm, rb in sens:
        lines.append(f"  prior×{f} (mean={m:.3f}): margin {rm:.3f}   breakdown {rb:.1%}")
    # 信念錯誤測試（現實固定在 base，僅引擎信念偏移）—— 場景敏感度之外的獨立維度。
    # 注意：迴圈變數不可用 bm（會遮蔽上方 baseline_margin 的 bm）。
    mis = misbelief_sensitivity(n_samples)
    lines.append("Misbelief test (reality fixed at base prior; only the engine's belief shifts):")
    for f_bel, belief_mean, m_mis, b_mis in mis:
        lines.append(f"  belief×{f_bel} (planning mean={belief_mean:.3f}): "
                     f"margin {m_mis:.3f}   breakdown {b_mis:.1%}")
    return "\n".join(lines), {
        "engine_margin": em, "engine_break": eb,
        "baseline_margin": bm, "baseline_break": bb, "gain": gain,
        "multi_baseline": multi,
        "sensitivity": [{"factor": f, "mean": m, "margin": rm, "break": rb} for f, m, rm, rb in sens],
        "misbelief": [{"factor": f, "belief_mean": bm, "margin": rm, "break": rb}
                      for f, bm, rm, rb in mis],
    }


if __name__ == "__main__":
    txt, data = report()
    print(txt)
