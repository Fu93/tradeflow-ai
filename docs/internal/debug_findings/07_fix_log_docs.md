# 文檔修復日誌（對應 debug_findings/04_docs_consistency.md F1–F20）

**定位**：本日誌記錄 2026-10-16 文檔一致性修復的逐條執行結果，供提交前複核與主會話接力。
**適用範圍**：僅六份允許編輯文檔（README.md、SUBMIT.md、PITCH.md、SUBMISSION_DRAFT.md、LIMITATIONS_AND_REAL_WORLD_FINDINGS.md、DEMO_SCRIPT.md）；未動任何代碼、tests/、extension/、results/；未 git commit；未跑 pytest。
**版本**：v1.0，2026-10-16。

## 執行摘要

- 已完成：F1、F3、F5、F7、F8、F9、F10、F11、F12、F15、F16、F17、F18、F19、F20 全部；F6、F13、F14 完成 LIMITATIONS 以外全部落點。
- 跳過：F4（指示明確跳過；全程未動 326/68，未新增測試數聲稱）。
- 被擋：LIMITATIONS_AND_REAL_WORLD_FINDINGS.md 全檔寫入被 Zwork 安全護欄攔截（詳見「被擋修改」節），F2 全部與 F6/F13/F14 的 LIMITATIONS 落點（共 8 處）未套用。

## 逐條紀錄

| F | 狀態 | 文檔:位置 | 改動摘要 |
|---|---|---|---|
| F1 | ✅ | SUBMISSION_DRAFT §1 | 30 場/104 calls/$0.0318/成交 50% vs 70%/違規 16.7–58.3%/0–30/上界 9.5% → 100 場/348 calls/$0.108/對詐唬型 61.1%/模擬器 20.0%–40.0%/0/94/上界 3.1%；移除跨 artefact「vs 70%」比較，與 §5、PITCH Slide 1、LIMITATIONS §2.3 對齊 |
| F2 | ⛔ | LIMITATIONS §6.2（:363、:366-367）、§6.3（:383-384） | 待套用：50%→61.1%、12%~44%→20%~40%（註歷史版本）、0/30→0/94、2.2 秒→3.3 秒、1.1 秒→1.7 秒（見「被擋修改」） |
| F3 | ✅ | README「已知缺口」+評分表 Evolus 列；PITCH Slide 8、Slide 10 第 1 點；SUBMISSION_DRAFT §4 | 「workflow 與 agent 均未建立」→「agent（Supplier Negotiation Analyst，已綁 AMD 託管模型）與 Personal Library 知識庫已於 2026-10-14 建立並實測（LIMITATIONS §12）；workflow 尚未建立、不宣稱完成，屬後續補強項而非資格缺口」 |
| F4 | ⊘ | — | 按指示跳過；未動 326/68，未新增測試數聲稱 |
| F5 | ✅ | SUBMISSION_DRAFT §5 範圍註 | "60 matches (12 per archetype)" → "100 matches (20 per archetype, seed 42)"，消除與同節「20 matches per archetype」的矛盾 |
| F6 | ◐ | README「性能」節；PITCH Slide 6 表列；SUBMISSION_DRAFT §2 | 統一為 20K 預設 ≈1.7s、完整 HTTP 輪次 ≈3.3s、2K ≈0.30s，補量測環境註記（開發機 Intel Core Ultra 7 355，x86-64 CPU；bench.json）。LIMITATIONS 四處殘留（:292、:353、:366-367、:383-384，含自查發現的 :292「1.1 秒」與 :353「約 2 秒」）待套用 |
| F7 | ✅ | DEMO_SCRIPT Scene 3 | 31.7%→28.3%、two yuan→forty cents (¥0.40)；「recommendation holds」改為「低估穩健、高估 DEGRADES，故刻意保守估計」，不再過度聲稱 |
| F8 | ✅ | DEMO_SCRIPT Scene 2 | 刪掉沒有出處的 naive-loop 秒數；CPU 句改對 bench.json；MI300X 句改為 theoretical roofline lower-bound estimate, not measured，並指向 `results/mi300x_roofline.json`；Key line 改「batched rollouts run as one vectorized broadcast on CPU」，去除 GPU 執行聲稱 |
| F9 | ✅ | DEMO_SCRIPT Scene 6；README mermaid 圖 | "vectorized MCTS on AMD MI300X" → "Vectorized Expectimax on CPU (x86)"；連帶排查：README mermaid 節點 id MCTS96M→TensorSolver（3 處，非渲染文字）、Scene 2 Key line（歸 F8）。範圍外殘留：COMPETITION_STRATEGY.md:58,64 與 docs/archive/*.md 仍有 MCTS/AMD 執行聲稱——不在允許清單，未動 |
| F10 | ✅ | PITCH Slide 5 | ¥12.30→¥13.12 改 ¥12.30→¥14.23，與 results/belief_update.png 及 DEMO_SCRIPT Scene 1 一致。範圍外殘留：DEMO_VIDEO_DIRECTOR_SCRIPT.md:40 對白含 ¥13.12（屬 Evolus 500-unit 草稿情境；重錄影片時由主會話決定是否標註情境） |
| F11 | ✅ | PITCH Slide 3 揭露段 | 「每格僅 12 場、4/5 原型一位小數相同、不構成證據」→「每格 20 場（100.0% 實為 20/20）、5/5 原型互不相同、但 Hard-Bargainer 上 8.8% vs 9.2% 仍遜於基準——如實保留該格」，與現 artefact 及 LIMITATIONS §2.2 一致 |
| F12 | ✅ | PITCH Slide 1 兩處 | 「求解器 70%」→「同一自博弈 artefact 中引擎 100%（20/20，僅 20 場樣本；跨 artefact 比較不可直接解讀）」；違規率「12% 與 44%」→「20%–40%（現行 artefact；歷史版本 12%–44%）」，否證表述改為「任一版數字」 |
| F13 | ◐ | README 導論；PITCH Slide 4；SUBMISSION_DRAFT §2 | README 原文無 60% 字樣（grep 證實），故於 Logrolling 括號新增釐清：業務規則 ≤50%、AST 紅線 60% 為最後防線；PITCH Slide 4 於「設計原則」後加註記行（避免破壞 ASCII 圖對齊）；SUBMISSION_DRAFT §2 加括號（AST red line; Logrolling stricter at ≤50%）。LIMITATIONS §五.1、§七.2 兩處待套用 |
| F14 | ◐ | PITCH Slide 7 | 665.9 MB→678.1 MB、9.1 倍→約 9.3 倍（73.2→678.1 換算 ≈9.26，審計建議值）。LIMITATIONS §四（:299-300）待套用 |
| F15 | ✅ | DEMO_SCRIPT Scene 4 + Recording checklist | 「Live end-to-end workflow」改為演示 Evolus agent（Supplier Negotiation Analyst + Personal Library；補 2026-10-14 live-verified、RAG 40–120 秒註記）；workflow 改述為 planned next step；錄製清單對應行同步 |
| F16 | ✅ | README 驗證輸出樣例 | "Supplier A (浙江頂尖雨具製造)" → "Supplier A (敏捷讓步型)"，與 results/multi_supplier_auction.md 供應商命名一致 |
| F17 | ✅ | README 用戶故事 | 約 $2,500 → 約 $2,685（¥18,000 ÷ 匯率錨 6.7050）。⚠ 順手項 scripts/refresh_market_data.py 未執行（本會話無 shell），market_anchor 仍為 2026-10-14 抓取版 |
| F18 | ✅ | README 腳註；PITCH Slide 6 | 三套節省敘事加情境標籤並註明不可互換：¥18,000＝單一供應商議價 vs 開盤報價；多供應商拍賣 22.2%/¥36,000（results/multi_supplier_auction.md）；vs 固定讓步基準邊際 +¥0.40/件（LIMITATIONS §十三.5） |
| F19 | ✅ | SUBMISSION_DRAFT 頭部 tagline | 155→139 字元："AI-native trading company of one: a quantitative negotiation engine running cross-border sourcing end-to-end, powered by AMD ROCm + Evolus."（保留量化引擎+端到端+AMD+Evolus 賣點） |
| F20 | ✅ | SUBMIT.md「Demo / pitch」 | Pitch deck 勾選（results/pitch.pptx 已存在；加註「內容與最新 PITCH.md 的同步未驗證，改動後需重跑 scripts/make_pitch.py」）；demo video 保持未勾選並加註（現檔 ~15 秒無聲，須按修訂旁白重錄）；「Cover image + demo video + pitch」合併行因影片未完成維持未勾 |

## 被擋修改（LIMITATIONS_AND_REAL_WORLD_FINDINGS.md，8 處）

**原因**：寫入掃描器在 §十一 認證端點表（第 510 行）偵測到 `Authorization=` 憑據形態字面量（鍵名 Authorization、值不回顯；`read`/`grep` 回顯均為遮蔽後的 `[redacted]`，無法取得原始位元組構造精確替換）。整檔重寫類工具均會掃描全檔，故該檔任何編輯都被攔。依護欄指引未繞過。

**待主會話處理**：先手動修改第 510 行表格儲存格，移除 `Authorization=` 字面量（建議改為「Bearer 權杖（以環境變數注入，不寫入文檔）」）。若原值為真實權杖而非佔位符，請同步確認權杖輪換與 git 歷史洩漏面。完成後套用以下 8 處（均為單行/雙行精確替換）：

1. :292 「延遲（實測約 1.1 秒/次 `solve()`）才是量測值。」→「延遲（預設 20K 樣本單次 `solve()` 實測約 1.7 秒；完整 HTTP 輪次約 3.3 秒；見 §6.1 與 `results/bench.json`）才是量測值。」（F6 自查發現）
2. :299-300 「低估實際配置量 9.1 倍」「實測峰值為 665.9 MB」→「約 9.3 倍」「678.1 MB」（F14）
3. :353 「完整輪次約 2 秒」→「完整輪次約 3.3 秒」（F6 自查發現）
4. :363 「成交率僅 50%」「12%~44% 穿透率」「實測 0/30」→「61.1%」「20%~40% 穿透率（現行 artefact；歷史版本 12%~44%）」「0/94」（F2）
5. :366-367 「完整 HTTP 輪次實測約 2.2 秒」「約 1.1 秒」→「3.3 秒」「1.7 秒」（F2）
6. :383-384 同上組數字（F2）
7. §五.1 攔截職責 `dep <= 0.60` 後補「（AST 紅線；Logrolling 業務規則更嚴：`max_allowed_deposit_pct=0.50`，見 §十三.2）」（F13）
8. §七.2 定金紅線 $\le 60\%$ 後補「（Logrolling 業務規則更嚴，$\le 50\%$，見 §十三.2）」（F13）

## 複查結果

- 五份已寫檔案 grep 無殘留：2.2 秒、1.1 秒、0/30、46 seconds、MCTS、70%、12 場、60 matches、665.9、$2,500、浙江頂尖、13.12、0.14s、0.0318。
- 護欄測試關注項：禁句四式（`500 條跨領域盲測`、`500 條獨特語料`、`500 distinct natural-language samples`、`blind held-out set`）全庫零命中；92.00% 三處（README:178、PITCH:191、SUBMISSION_DRAFT:96）±3 行限定語完好，誠實揭露段未削弱。
- 相對連結與 results/ 證據路徑：所有編輯僅觸及正文文字與勾選狀態，未改動任何連結。

## 未驗證 / 待主會話

- pitch.pptx 與修訂後 PITCH.md 的內容同步（無法讀 pptx、無 shell 跑 make_pitch.py）。
- scripts/refresh_market_data.py（F17 順手項）。
- F4：326/68 測試數由主會話 pytest 實跑後統一刷新。
- LIMITATIONS 8 處殘留舊值——評審若照 LIMITATIONS §6.2/§6.3 核對，仍會看到 2.2 秒/0/30/50% 舊數，為當前最大未閉環風險。
- 範圍外同類殘留（未動）：COMPETITION_STRATEGY.md、docs/archive/*.md（MCTS/AMD）、DEMO_VIDEO_DIRECTOR_SCRIPT.md:40（¥13.12）、LIMITATIONS §十三.7「quick n=2,000 實測 2.4 秒」與 §6.1「4,000 樣本僅 0.23 秒」（審計未列條、口徑無法自行核實，留主會話判斷）。
