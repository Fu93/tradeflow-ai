"""TradeFlow CLI — 一個指令跑端到端 demo / 穩健性 / 視覺化 / 對抗式自博弈競技場 / 多供應商逆向拍賣。

python -m src.cli              # 端到端 demo（含信念更新）
python -m src.cli --robustness # 穩健性報告 (regret + prior 敏感度)
python -m src.cli --figures    # 產出 results/posterior.png + belief_update.png
python -m src.cli --self-play  # 5 大非 LLM 對抗式自博弈錦標賽評測
python -m src.cli --multi      # 4 家供應商並行逆向拍賣協同模擬 (28.8M Rollout Tensor)
"""
from __future__ import annotations
import sys, time, argparse, os
from pathlib import Path
import numpy as np
from .engine import solve, recommend, bayes_update, model, robustness, viz, benchmark
from .benchmark import AdversarialArena
from .engine.multi_solver import simulate_reverse_auction
from .app.demo_data import DEMO_SPEC, SUPPLIER_REPLIES
from src.sim.scenarios import UMBRELLA

# Windows consoles default to cp1252 / cp936, which mangled ¥ and — in earlier
# runs (the operator saw "?12.51"). Force UTF-8 output where the stream allows it.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # redirected / non-reconfigurable stream
        pass


def run_demo():
    print("=== TradeFlow Negotiation Engine demo (auto umbrella export) ===")
    print(f"Product: {DEMO_SPEC['product']} | target ¥{DEMO_SPEC['target_price']} | MOQ {DEMO_SPEC['moq']}")

    post_mean, post_std = DEMO_SPEC["prior_reserve_mean"], DEMO_SPEC["prior_reserve_std"]
    print(f"\n[Init belief] 4 supplier-type prior  mean={post_mean}, std={post_std}")

    trace = []
    for round_i in range(model.ROUNDS):
        t0 = time.perf_counter()
        res = solve(n_opp_samples=DEMO_SPEC["n_samples"],
                    lambda_break=DEMO_SPEC["lambda_break"],
                    reserve_mean=post_mean, reserve_std=post_std)
        dt = time.perf_counter() - t0
        rec = recommend(res, target_price=DEMO_SPEC["target_price"])
        trace.append({"round": round_i + 1, "mean": post_mean, "std": post_std,
                      "recommended_open_norm": rec["opening_offer_norm"], "dt": dt})

        print(f"\n--- Round {round_i+1} ({dt*1000:.0f} ms, {res['n_samples']:,} opponent samples) ---")
        print(f"  Belief (reserve posterior) mean={post_mean:.3f}, std={post_std:.3f}")
        print(f"  Recommended opening ¥{rec['opening_offer_price']:.2f} | concession curve {[round(c,2) for c in rec['concession_curve']]}")
        print(f"  Expected margin ¥{rec['expected_margin_price']:.2f}/unit | breakdown prob {rec['breakdown_prob']:.1%}")

        if round_i < len(SUPPLIER_REPLIES):
            offer = SUPPLIER_REPLIES[round_i]
            post_mean, post_std = bayes_update(post_mean, post_std, offer)
            print(f"  Supplier replied ¥{offer*DEMO_SPEC['target_price']:.2f}  →  posterior mean -> {post_mean:.3f}")
        else:
            print("  No supplier reply  →  continue / hand to human approval")

    # The old hardcoded MI300X runtime was removed. It was not a measurement.
    # This path prints the CPU wall clock from this process only.
    # [誠實揭露] 這裡的數字是「張量元素位數」= n_samples × 開盤檔 × 讓步曲線，
    # 不是迴圈執行次數；一次向量化廣播就求完整個張量。舊版把 480 寫成魔法數
    # 並稱其為 measured rollouts，容易讀成「執行了 960 萬次 rollout」。
    from .engine import model as _model
    _opens, _scheds = _model.strategy_grid()
    tensor_positions = DEMO_SPEC["n_samples"] * len(_opens) * len(_scheds)
    elapsed = sum(r["dt"] for r in trace)
    print("\n[Conclusion] Engine turns 'value of waiting' into an executable concession schedule.")
    print(f"  Tensor positions  : {tensor_positions:,} parameter combinations "
          f"({DEMO_SPEC['n_samples']:,} samples x {len(_opens)} opens x {len(_scheds)} curves), "
          f"evaluated by one vectorised pass per round")
    print(f"  Measured this run : {len(trace)} rounds in {elapsed:.2f}s on CPU "
          f"(NumPy vectorised) — wall-clock measured, the position count is arithmetic.")
    print("  GPU note          : no AMD GPU solve is recorded in this repository. "
          "results/mi300x_roofline.json is a theoretical roofline lower-bound estimate, "
          "not measured.")
    return trace


def run_robustness(n=20000):
    print("=== Robustness analysis (n_samples=%d) ===" % n)
    txt, _ = robustness.report(target_price=DEMO_SPEC["target_price"], n_samples=n)
    print(txt)


def run_figures():
    """產出全部圖 + bench.json（給 demo / 提交用）。"""
    print("=== Generate ALL figures + bench.json ===")
    # 信念更新 + 後驗分布
    trace = run_demo()
    p1, p2 = viz.save_all(trace[-1]["mean"], trace[-1]["std"], trace,
                          target_price=DEMO_SPEC["target_price"], out_dir="results")
    print(f"  saved: {p1}\n  saved: {p2}")
    # 後驗分解（4 類供應商）— Wow#1 加強
    p6 = "results/prior_decomp.png"
    viz.plot_prior_decomp(trace[-1]["mean"], trace[-1]["std"],
                          target_price=DEMO_SPEC["target_price"], save_path=p6)
    print(f"  saved: {p6}")
    # Benchmark（CPU 實測 + MI300X 投影）
    rows = benchmark.run()
    p3 = benchmark.save_json(rows)
    p4 = "results/benchmark.png"
    viz.plot_benchmark(rows, p4)
    print(f"  saved: {p3}\n  saved: {p4}")
    # 穩健性（多基準 + prior 敏感度）
    _, rob = robustness.report(target_price=DEMO_SPEC["target_price"], n_samples=20000)
    p5 = "results/robustness.png"
    viz.plot_robustness(rob, p5)
    print(f"  saved: {p5}")
    print("\nresults/: posterior.png, belief_update.png, prior_decomp.png, benchmark.png, robustness.png, bench.json")


def run_self_play(matches=20, out_dir="results"):
    print(f"=== Adversarial Self-Play Tournament (n_matches={matches} per adversary) ===")
    t0 = time.perf_counter()
    arena = AdversarialArena(max_rounds=5, n_opp_samples=1000)
    results = arena.run_tournament(n_matches_per_adversary=matches)
    elapsed = time.perf_counter() - t0
    md_table = arena.export_markdown_table(results)
    # [修正] 舊版寫 `matches * 5 * 2`（誤假設 2 個候選 agent），實際是 3 個
    # （Engine、Vanilla LLM、Fixed 5%/rd）。改成讀取實際 candidate_agents 數量，
    # 避免輸出與實際執行場次不符。
    n_agents = len(arena.candidate_agents)
    print(f"\nCompleted {matches * 5 * n_agents} matches across 5 adversary archetypes in {elapsed:.2f}s\n")
    print(md_table)
    
    out_path = Path(out_dir) / "self_play_benchmark.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # [誠實揭露] 這裡必須與 scripts/run_arena_benchmark.py 一樣，把資料級別、
    # 實際 seed 與每類場次寫進 artefact。舊版本只寫一行 "Matches per Archetype"，
    # 讓產出的表格看起來像實測結果，且覆寫掉腳本版本較完整的 header。
    out_path.write_text(
        "# TradeFlow v4 Adversarial Baseline Tournament Results\n\n"
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Matches per Archetype: {matches} (Total {matches * 5 * n_agents} simulated "
        "matches across 5 archetypes; the simulator runs 5 archetypes)\n"
        f"Candidate agents per archetype: {n_agents} | Seed: "
        f"{getattr(arena, 'last_tournament_meta', {}).get('seed', 'default')}\n"
        "Data class: C-level (parametric behavioural simulator; NOT live LLM API calls)\n\n"
        f"{md_table}\n",
        encoding="utf-8",
    )
    print(f"\n[Saved benchmark report] -> {out_path}")


def run_multi_supplier_demo(out_dir="results"):
    print("=== TradeFlow Multi-Supplier Reverse Auction Coordination Demo ===")
    print(f"Scenario: {UMBRELLA.name} (simulated archetype, MOQ {UMBRELLA.moq:,})")
    print(f"Target Price: ¥{UMBRELLA.target:.2f} | Walk-away Ceiling: ¥{UMBRELLA.ceiling:.2f}\n")

    t0 = time.perf_counter()
    res = simulate_reverse_auction(
        target_price=UMBRELLA.target,
        walk_away_ceiling=UMBRELLA.ceiling,
        moq=UMBRELLA.moq,
        n_samples=20000,
    )
    total_elapsed = time.perf_counter() - t0

    # [修正] 舊版在 CLI 端硬編了 4 個虛構的公司名（浙江頂尖/深圳宏達/義烏博創/東莞榮耀），
    # 卻跟引擎計算出的真實供應商（敏捷讓步型/對等互惠型/強硬死守型/高價溢價型）「錯位混排」：
    # 引擎的 quotes[i] 對上 CLI 自訂之名，導致除了 winner 那一行之外，
    # 其他三家的名字都不是引擎回傳的名字 —— 真實公司名貼在虛構身份上。
    # 現在一律讀取引擎回傳的 suppliers，不再自行硬編。
    suppliers = [s["name"] for s in res["suppliers"]]

    for round_info in res["trace"]:
        r = round_info["round"]
        # Cells scored by the coupled expectimax (scenarios x offers x sweeps).
        # The old K x S x N_open x N_sched product is not this counter.
        print(f"--- Round {r} (Scenario-offer cells {round_info['rollouts']:,} in {round_info['elapsed_seconds']:.2f}s) ---")
        for i, supp in enumerate(suppliers):
            q = round_info["quotes"][i]
            tactic = round_info["tactics"][i]
            eff_c = round_info["effective_ceilings_cny"][i]
            p_brk = round_info["p_break_array"][i]
            counter = round_info["counter_offers_cny"][i]
            print(f"  {supp:<22} | Quote: ¥{q:.2f} | Tactic: [{tactic:<12}] | BATNA Ceiling: ¥{eff_c:.2f} | Counter: ¥{counter:.2f} (Risk {p_brk:.1%})")
        print(f"  --> Lead: {round_info['lead_supplier']} at ¥{round_info['best_quote']:.2f}\n")

    print("=== Reverse Auction Settlement Summary ===")
    print(f"  Winning Vendor     : {res['winning_supplier']}")
    print(f"  Settled Unit Price : ¥{res['winning_price']:.2f} (Target ¥{res['product_target']:.2f}, Ceiling ¥{res['walk_away_ceiling']:.2f})")
    print(
        f"  Headline savings   : {res['total_savings_pct']:.1%} vs first-round best quote "
        f"¥{res['first_round_best_quote']:.2f} (¥{res['total_savings_cny']:,.2f} for MOQ {res['moq']:,})"
    )
    print(
        f"  Versus target      : {res['savings_vs_target_pct']:.1%} "
        f"(¥{res['savings_vs_target_cny']:,.2f}); a negative figure means the settled price is above target"
    )
    print(
        f"  Secondary, ceiling : {res['savings_vs_ceiling_pct']:.1%} vs walk-away ceiling "
        f"¥{res['walk_away_ceiling']:.2f} (¥{res['savings_vs_ceiling_cny']:,.2f})"
    )
    print(f"  Scenario-offer cells: {res['total_rollouts_accum']:,} (coupled expectimax, including sweeps) in {res['total_compute_seconds']:.2f}s")
    print(f"  Total Process Time : {total_elapsed:.2f}s\n")

    # 產出 Markdown 報表
    md_content = f"""# TradeFlow Multi-Supplier Reverse Auction Coordination Benchmark

- Product: Custom Carbon Auto Umbrella (MOQ: 10,000 units)
- Target Price: ¥{res['product_target']:.2f}
- Walk-away Ceiling: ¥{res['walk_away_ceiling']:.2f}
- First-round best quote: ¥{res['first_round_best_quote']:.2f}
- Winning Supplier: {res['winning_supplier']}
- Settled Price: ¥{res['winning_price']:.2f}
- Headline savings vs first-round best quote: {res['total_savings_pct']:.1%} (¥{res['total_savings_cny']:,.2f} total)
- Versus target: {res['savings_vs_target_pct']:.1%} (¥{res['savings_vs_target_cny']:,.2f} total). A negative figure means the settled price is above target.
- Secondary, versus walk-away ceiling: {res['savings_vs_ceiling_pct']:.1%} (¥{res['savings_vs_ceiling_cny']:,.2f} total)
- Scenario-offer cells: {res['total_rollouts_accum']:,} (coupled expectimax cells scored, including repeated sweeps; not the old K x S x N_open x N_sched product)

| Round | Supplier | Quote (¥) | Tactic Classification | Dynamic BATNA Ceiling (¥) | Risk (%) | Counter-Offer (¥) |
|---|---|---|---|---|---|---|
"""
    for r_info in res["trace"]:
        for i, supp in enumerate(suppliers):
            md_content += f"| Round {r_info['round']} | {supp} | ¥{r_info['quotes'][i]:.2f} | {r_info['tactics'][i]} | ¥{r_info['effective_ceilings_cny'][i]:.2f} | {r_info['p_break_array'][i]:.1%} | ¥{r_info['counter_offers_cny'][i]:.2f} |\n"

    out_path = Path(out_dir) / "multi_supplier_auction.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md_content, encoding="utf-8")
    print(f"[Saved auction report] -> {out_path}")


def run_sandbox_demo(host: str = "127.0.0.1", port: int = 8000):
    """一鍵啟動評審互動沙盒 (Zero-friction Sandbox for Hackathon Judges)。
    1. 初始化種子會話 (Session: DEMO_SANDBOX_01)
    2. 自動啟動 FastAPI Uvicorn 伺服器
    3. 自動在瀏覽器開啟 Web 戰情大屏
    """
    import webbrowser
    from .session.manager import SessionManager
    from .session.schema import SessionSpec, AutopilotState

    print("=== TradeFlow Interactive Evaluation Sandbox ===")
    print(f"Target URL: http://{host}:{port}/")

    # 1. 建立種子演示會話
    mgr = SessionManager()
    seed_spec = SessionSpec(
        session_id="DEMO_SANDBOX_01",
        product_name=UMBRELLA.name,
        target_price=UMBRELLA.target,
        ceiling_price=UMBRELLA.ceiling,
        moq=UMBRELLA.moq,
        max_rounds=5,
        supplier_id="simulated_archetype",
        supplier_channel="sandbox",
    )
    sess = mgr.create_session(seed_spec)
    sess = mgr.transition_state("DEMO_SANDBOX_01", AutopilotState.COPILOT_MONITORING)
    print(
        "[Seed Session Created] ID: DEMO_SANDBOX_01. "
        "Simulated umbrella mandate only. No supplier quote was invented."
    )

    # 2. 背景或延遲啟動瀏覽器
    def open_browser():
        time.sleep(1.2)
        url = f"http://{host}:{port}/"
        print(f"[Browser Launch] Opening {url} ...")
        webbrowser.open(url)

    import threading
    t = threading.Thread(target=open_browser, daemon=True)
    t.start()

    # 3. 運行伺服器。與 `python -m src.app.main` 同一條啟動路徑：連線探測、
    #    單進程、不做第二次 bind。
    from src.app.main import app, serve_http

    print(f"[Server Starting] Uvicorn running on {host}:{port}. Press Ctrl+C to terminate.")
    serve_http(app, host, port)


def run_benchmark():
    """執行硬體基準評測 (Hardware Profiling & AMD Roofline Model)。"""
    print("=== TradeFlow Hardware Profiling & AMD Instinct MI300X Audit ===")
    report = benchmark.run()
    p_json = benchmark.save_json(report)
    p_img = "results/benchmark.png"
    viz.plot_benchmark(report, p_img)
    print(f"\n[Hardware Detected] {report['hardware_detected']}")
    # theoretical_max_speedup was removed. Do not subscript it.
    # Host DDR ratios were removed: the bench file does not name a CPU.
    specs = report.get("roofline_specs", {})
    print(f"[Roofline] {specs.get('label', 'theoretical roofline lower-bound estimate, not measured')}")
    print(f"[Roofline Formula] {specs.get('formula', '(missing formula)')}")
    print(f"[Roofline Note] {specs.get('note', '(none)')}")
    print(f"[Saved Profile] -> {p_json}")
    print(f"[Saved Plot] -> {p_img}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="Launch interactive Web Sandbox & auto-open browser")
    ap.add_argument("--benchmark", action="store_true", help="Run hardware profiling & AMD Roofline model audit")
    ap.add_argument("--robustness", action="store_true")
    ap.add_argument("--figures", action="store_true")
    ap.add_argument("--self-play", action="store_true", help="Run 5-adversary tournament benchmark")
    ap.add_argument("--multi", action="store_true", help="Run 4-supplier reverse auction coordination simulation")
    ap.add_argument("--matches", type=int, default=20, help="Matches per adversary for self-play")
    ap.add_argument("--n", type=int, default=20000)
    args = ap.parse_args()

    if args.demo:
        run_sandbox_demo()
    elif args.benchmark:
        run_benchmark()
    elif args.multi:
        run_multi_supplier_demo()
    elif args.self_play:
        run_self_play(matches=args.matches)
    elif args.robustness:
        run_robustness(args.n)
    elif args.figures:
        run_figures()
    else:
        run_demo()


if __name__ == "__main__":
    sys.path.insert(0, ".")
    main()
