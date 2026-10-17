# 文檔一致性審計（文檔 vs 現實）— TradeFlow AI

- **定位**：黑客松提交前的文檔交叉核對報告，供提交負責人在 2026-10-17 截止前修正文檔與分級提交阻塞。
- **適用範圍**：`C:\Users\nn201\Desktop\tradeflow-ai`，以 2026-10-16 工作區狀態為準；核對對象為 README.md、SUBMIT.md、PITCH.md、SUBMISSION_DRAFT.md、DEMO_SCRIPT.md、LIMITATIONS_AND_REAL_WORLD_FINDINGS.md、results/，並抽查 tests/、src/、knowledge/。
- **版本**：v1.0，2026-10-16。審計方式為只讀，未修改任何專案檔案。

## 一、方法與局限

- 數字核對：逐檔讀取六份文檔 + results/（bench.json、self_play_benchmark.md、multi_supplier_auction.md、四張 PNG 的像素尺寸與圖內數值）+ knowledge/market_anchor.md，與 src/、tests/ 關鍵行 grep 比對。
- 測試數採**靜態統計**：30 個測試檔共 330 個 `def test_` 函數，另有 1 處 3 值 parametrize（tests/test_benchmark_integrity.py:299），靜態展開約 332 items。**局限**：無 shell 工具，pytest 未實跑；「68 subtests」來自 pytest-subtests 的動態迴圈，9 處 `subTest` 迴圈次數無法靜態確定，未驗證。
- 未驗證項：results/pitch.pptx 內容是否與 PITCH.md 最新修正同步（無法讀 pptx）、results/demo_video.mp4 實際時長與音畫、`naive loop 46 seconds` 的出處（benchmark.py 全文 grep 無此數）、圖表數值為 OCR 目測讀值（非程式輸出）。
- 本報告所有「文檔:位置」均已實讀核對；推斷處以「（推斷）」標註。

## 二、HIGH：評審照文檔核對必被抓包

### F1 真實 LLM 實驗新舊兩版本混用（最大風險）
- **文檔:位置**：SUBMISSION_DRAFT.md §1 vs SUBMISSION_DRAFT.md §5、PITCH.md Slide 1、LIMITATIONS §2.3。
- **問題**：§1 寫 30 matches/104 calls/$0.0318/成交率 50% vs 70%/0–30 違規/上界 9.5%/模擬器 16.7–58.3%（舊版 30 場實驗）；§5 與 PITCH、LIMITATIONS §2.3 寫 100 matches/348 calls/$0.108/81.9% 整體成交、對詐唬型 61.1%/0/94 違規/上界 3.1%（新版 100 場實驗）。SUBMISSION_DRAFT 內部 §1 與 §5 自相矛盾。
- **影響**：評審同一份文檔內看到兩套「真實 LLM 實驗」數字，會質疑數據真實性或挑選不利版本追問。
- **建議**：SUBMISSION_DRAFT §1 全部改用 100 場數字（或刪除舊段落）；如要保留歷史，明確標「v1 預實驗（30 場），已由 v2（100 場）取代」。

### F2 LIMITATIONS 內部：舊延遲與舊違規數未清乾淨
- **文檔:位置**：LIMITATIONS §6.1 vs 同檔 §6.2、§6.3。
- **問題**：§6.1 已更正為 solve() ≈1.7s、完整 HTTP 輪次 3.3s（mean, n=12），並自註「先前寫 ~2.2s 為舊數字」；但 §6.2、§6.3 仍寫 2.2s/1.1s，且沿用 0/30、50% 舊違規數（§2.3 已是 0/94、3.1%）。
- **影響**：LIMITATIONS 是評審最信任的「誠實文檔」，內部兩套數字等於自證修訂不徹底。
- **建議**：以 §6.1/§2.3 為準，全文替換 §6.2/§6.3 的延遲與違規數。

### F3 Evolus agent 建立狀態：三文檔過期
- **文檔:位置**：README「已知缺口」、PITCH.md Slide 8 與 Slide 10、SUBMISSION_DRAFT.md §4 vs SUBMIT.md（agent 已建立，HTTP 200）、LIMITATIONS §12。
- **問題**：前三處稱 agent「尚未建立/Planned」；SUBMIT 與 LIMITATIONS §12 記錄 Supplier Negotiation Analyst agent 已驗證建立（2026-10-14）、Personal Library 知識庫已實裝，且 §11.4 的舊缺口已劃刪。workflow 未建則各方一致。
- **影響**：Evolus 賽道評審照 README/PITCH 會以為核心整合沒做——既低估自己又與提交清單矛盾。
- **建議**：README/PITCH/SUBMISSION_DRAFT 統一改為「agent + 知識庫已建立（10-14 驗證），workflow 為後續補強項」。

### F4 測試數 326 過期（靜態實數 330）
- **文檔:位置**：README「測試套件」節、SUBMIT.md「Core Numbers」、SUBMISSION_DRAFT.md §3 vs tests/（30 檔）。
- **問題**：三份文檔均稱 326 tests + 68 subtests；靜態統計為 330 個 `def test_`（+1 處 3 值 parametrize，約 332 items）。SUBMIT.md 自己未勾選「Refresh hardcoded counts」，屬實。注意 tests/test_benchmark_integrity.py:299 會 parametrize 讀取 README/PITCH/SUBMISSION_DRAFT 做文檔護欄測試，改文檔時不得讓 92%/blind-dataset 類禁句回歸。
- **影響**：評審跑 `pytest` 一眼可見數字不符；這是 SUBMIT.md 明列卻未做的項。
- **建議**：實跑一次 pytest，以實際輸出刷新三處數字（含 subtests 計數）；68 subtests 本審計無法靜態驗證，以實跑為準。

### F5 SUBMISSION_DRAFT §5 內部：committed artefact 規模自相矛盾
- **文檔:位置**：SUBMISSION_DRAFT.md §5 第一段 vs 同節 scope note。
- **問題**：第一段稱「20 matches per archetype（100 total, seed 42）」，同節 note 又稱「committed artifact holds only 60 matches (12 per archetype)」。現 artefact（results/self_play_benchmark.md，Generated 2026-10-16 04:09:27）為每原型 20 場、共 100 場、samples/solve=1000。
- **影響**：評審對照 artefact 必然發現 note 過期；這句話原是誠實揭露，過期後反成瑕疵。
- **建議**：刪除 60-matches note 或改為「arctefact 已含 100 場（seed 42）」。

## 三、MED：數字/敘事過期，會被追問

### F6 solve() 延遲數字群與 bench.json 脫節
- **文檔:位置**：README「性能」節（約 1.1 秒）、PITCH.md Slide 6（完整輪次約 2s）、SUBMISSION_DRAFT.md §2（~1.1s；2K grid ~0.14s）vs results/bench.json（2000→0.3021s、10000→1.1111s、20000→1.6825s、50000→3.8335s）、LIMITATIONS §6.1（1.7s/3.3s）。
- **問題**：「1.1s」對應 10K 行而非預設 20K；「0.14s」與 bench.json 2K 行 0.30s 不符（舊量測）；「完整輪次約 2s」低於 §6.1 更正值 3.3s。
- **影響**：評審用 bench.json 或 benchmark.png 核對即不一致；demo 現場跑出 1.7s 會與旁白打架。
- **建議**：統一口徑「20K 預設 ≈1.7s；完整 HTTP 輪次 ≈3.3s；2K ≈0.30s」，並統一註明量測環境。

### F7 DEMO_SCRIPT Scene 3 三處過期數字 + 一處過度聲稱
- **文檔:位置**：DEMO_SCRIPT.md Scene 3 vs LIMITATIONS §4.5/§十三.5、results/robustness.png、results/cover_1280x720.png。
- **問題**：旁白「6.7% vs 31.7%」「多賺約 2 元/把」；現值為破局 6.7% vs 28.2%（§4.5 fixed 5% 表）/28.3%（§十三.5 與封面圖）、+¥0.40/件。另「off by fifty percent…The recommendation holds」與 §十三.4/robustness.py 現行輸出（misbelief ×1.5 → 破局 40%，程式印 DEGRADES）矛盾——僅低估方向成立。
- **影響**：錄影旁白與銀幕上封面圖/表格數字直接衝突；敏感度結論過度聲稱。
- **建議**：改為 28.3%、+¥0.40；敏感度句改為「低估 50% 仍穩健；高估 50% 會惡化（robustness.py 會標 DEGRADES）——因此我們要保守估計」。

### F8 DEMO_SCRIPT Scene 2：無出處的 46 秒 + 把解析下界講成實測
- **文檔:位置**：DEMO_SCRIPT.md Scene 2 vs results/bench.json、results/benchmark.png。
- **問題**：「naive loop 46 seconds」在 benchmark.py 全文與 bench.json、benchmark.png 均無出處（**未驗證**）；「Vectorized CPU about one second」與圖上 1.68s（20K）不符；「On an AMD MI300X…under a tenth of a second」把 bench.json 0.125ms 解析下界（資料搬運下限，非 runtime）講成事實——bench.json disclosure 明文禁止如此使用。
- **影響**：錄影旁白的過度聲稱是最容易被評審質疑誠信的點；46s 無法重現。
- **建議**：刪 46s 或補出處；CPU 句改「約 1.7 秒（20K 樣本）」；MI300X 句改「架構上，僅資料搬運下界就在 0.1ms 量級——這是解析推算，不是實測」。

### F9 DEMO_SCRIPT Scene 6：演算法名錯誤 + AMD 執行聲稱
- **文檔:位置**：DEMO_SCRIPT.md Scene 6 vs README/SUBMISSION_DRAFT/PITCH/LIMITATIONS（一致聲明）。
- **問題**：「vectorized MCTS on AMD MI300X」——全專案演算法為 Vectorized Expectimax（無 MCTS）；且所有文檔一致聲明開發機為 Intel Core Ultra 7 355，從未在 AMD 硬體執行。
- **影響**：演算法名錯誤屬技術硬傷；AMD 聲稱與自家 LIMITATIONS 衝突，踩中 hackathon 誠信紅線。
- **建議**：改為「Vectorized Expectimax on CPU (x86)」。

### F10 PITCH Slide 5 價格軌跡與 belief_update.png 衝突
- **文檔:位置**：PITCH.md Slide 5（¥12.30 → ¥13.12）vs DEMO_SCRIPT.md Scene 1（¥12.30 → ¥14.23 by round five）、results/belief_update.png（normalized 開盤 1.03→1.19，×¥12 ≈ ¥12.36→¥14.28）。
- **問題**：¥14.23 與圖一致；¥13.12 出自 Evolus 500-unit 草稿情境（PITCH Slide 8/LIMITATIONS §11.3），兩情境混用。
- **影響**：demo 播放 12.30→14.23 時，評審手上的 PITCH 寫 13.12。
- **建議**：Slide 5 改 ¥14.23（或標明 13.12 為另一情境的草稿價）。

### F11 PITCH Slide 3 揭露段過期（自我更正註記與正文打架）
- **文檔:位置**：PITCH.md Slide 3 vs 同 slide 更正註記、results/self_play_benchmark.md、LIMITATIONS §2.2。
- **問題**：揭露段仍寫「每格僅 12 場」「4/5 原型一位小數相同」；現 artefact 為每格 20 場，LIMITATIONS §2.2 稱 5/5 互不相同（仍 8.8% vs 9.2% 遜於固定 5% 基準——README/LIMITATIONS 如實保留 ✓）。
- **影響**：誠實揭露段本身過期，反而讓評審懷疑全檔時效。
- **建議**：揭露段改為 20 場/5 of 5 互不相同，保留 8.8% vs 9.2% 的遜色事實。

### F12 PITCH Slide 1 求解器成交率 70% 為舊 artefact 數
- **文檔:位置**：PITCH.md Slide 1 vs results/self_play_benchmark.md（引擎 20/20 = 100%）、LIMITATIONS §2.3。
- **問題**：「面對詐唬型對手 61% vs 我們的求解器 70%」——70% 出自舊 12 場 artefact；現 artefact 引擎成交率 100%，且 §2.3 明言跨 artefact 比較不可過度解讀。同 slide「12% 與 44%」違規率也是舊模擬器版本（現 artefact 為 20.0%/40.0%，SUBMISSION_DRAFT §1 又寫 16.7–58.3%——三版本並存）。
- **影響**：同一張 slide 內新舊數字混排（348 calls 是新的、70% 是舊的）。
- **建議**：改「61% vs 引擎 100%（20/20，僅 20 場樣本）」，並註明跨 artefact 不可直接比較；違規率統一引用現 artefact 20/40%。

### F13 定金上限 50% vs 60% 雙軌未釐清
- **文檔:位置**：README/PITCH Slide 4/SUBMISSION_DRAFT §2/LIMITATIONS §五、§七.2（≤60% AST 紅線）vs src/engine/logrolling.py:35（`max_allowed_deposit_pct=0.50`）、LIMITATIONS §十三.2、results/cover_1280x720.png（Mandate ≤ 50%）。
- **問題**：（推斷）實為兩層：Logrolling mandate 0.50 嚴於 AST 紅線 0.60，但文檔通篇只講 60%，封面圖卻講 50%，未說明兩者關係。bilingual.py 的 AST 實際閾值未逐行確認。
- **影響**：評審讀代碼（0.50）對照文檔（60%）會問「哪個是真的」。
- **建議**：一句話釐清「Logrolling 強制 ≤50%（嚴於）作為業務規則；AST 硬護欄紅線 60% 作為最後防線」，三處文檔統一。

### F14 tracemalloc 665.9 MB vs bench.json 678.1 MB
- **文檔:位置**：PITCH.md Slide 7、LIMITATIONS §四（註 bench.json:37）vs results/bench.json（20,000 samples 實為 678.1 MB）。
- **問題**：引註指向 bench.json 但數值不符；benchmark.py:65 的「低估 9.1 倍」對應 665.9，對 678.1 應為 ≈9.26 倍。
- **影響**：小，但這是自帶引註的數字，核對成本為零。
- **建議**：兩處改 678.1 MB、9.3×（或 9.26×）。

### F15 DEMO_SCRIPT Scene 4 旁白與現實不符
- **文檔:位置**：DEMO_SCRIPT.md Scene 4 vs SUBMIT.md、LIMITATIONS §11.4。
- **問題**：旁白現場演示 Evolus workflow，但 workflow 各方文檔一致確認未建立（agent 與知識庫已建）。
- **影響**：若照旁本錄影將錄入不存在的畫面；或被迫砍掉一幕。
- **建議**：Scene 4 改為演示 agent（Supplier Negotiation Analyst + Personal Library），workflow 旁白改「planned next step」。

## 四、LOW：低成本修正

### F16 README 驗證輸出樣例使用已刪除的佔位供應商名
- **文檔:位置**：README.md:189 vs src/cli.py:125（舊虛構公司名已刪除）、results/multi_supplier_auction.md（Supplier A（敏捷讓步型））。
- **問題**：驗證輸出樣例含「浙江頂尖雨具製造」等佔位名；程式已改為動態類型名。DEEP_DEFECT_AUDIT.md:339 已記錄此點。
- **影響**：評審重跑 verify_live.py 輸出與 README 樣例不同。
- **建議**：樣例改為動態輸出格式（Supplier A（敏捷讓步型））。

### F17 $2,500 換算與專案自身匯率錨不符
- **文檔:位置**：README（約 $2,500）vs knowledge/market_anchor.md（USD/CNY 6.7050，2026-10-14 抓取）。
- **問題**：¥18,000 ÷ 6.7050 ≈ $2,685；$2,500 對應 ~7.2 的非本專案匯率。
- **影響**：小；但 market_anchor 亦已 2 天未刷新（SUBMIT 未勾選刷新項）。
- **建議**：改 ~$2,685；順手跑 scripts/refresh_market_data.py（約 1 分鐘）。

### F18 三套節省敘事並存未標明情境
- **文檔:位置**：README/PITCH（用戶故事 ¥18,000 = 10,000 件 ×（14.80−13.00））vs results/bench.json、multi_supplier_auction.md（拍賣 22.2%、¥36,000 = 16.20→12.60）vs 封面圖/LIMITATIONS §十三.5（邊際 +¥0.40/件 vs 固定基準）。
- **問題**：數字各自內部一致（¥18,000 算術 ✓、22.2% 與 bench.json savings 0.2222 ✓），但三個情境（固定供應商議價/多供應商拍賣/邊際增益）未標明不可互換。
- **影響**：評審質疑「到底是 1.8 萬還是 3.6 萬」。
- **建議**：每處加情境標籤（一句話），不改數字。

### F19 SUBMISSION_DRAFT tagline 超自訂限制
- **文檔:位置**：SUBMISSION_DRAFT.md 頭部 vs 自訂「140 chars」註記。
- **問題**：實測 155 字元（人工逐詞計數）。
- **影響**：平台表單若有硬限制會貼不上。
- **建議**：提交前按平台實際限制刪減。

### F20 SUBMIT.md 勾選狀態過期
- **文檔:位置**：SUBMIT.md vs results/ 實況。
- **問題**：results/pitch.pptx ✓、results/cover_1280x720.png ✓（實測恰為 1280×720）已存在但仍未勾選；demo_video.mp4 存在但僅約 15 秒無聲，不符合 3–5 分鐘要求——此項勾選狀態「看似過期、實則未完成」。
- **影響**：pitch.pptx/cover 為假阻塞（勾掉即可，前提是內容與最新文檔同步——pptx 內容本審計未驗證）；影片為真阻塞。
- **建議**：確認 pitch.pptx 由 scripts/make_pitch.py 重新生成後勾選；影片照 F7–F9、F15 修訂後的旁本重錄。

## 五、相對連結與引用檔案核查：全部通過

逐一到檔系統核實存在（find/ls 讀取目錄，非腳本）：docs/archive/README.md（docs/archive/ 含 README + 8 檔）✓；results/ 下 belief_update.png、benchmark.png、robustness.png、posterior.png、prior_decomp.png、cover_1280x720.png（1280×720 ✓）、self_play_benchmark.md、multi_supplier_auction.md、bench.json、real_llm_benchmark_100.json、pitch.pptx、demo_video.mp4 ✓；knowledge/market_anchor.md、spec_baseline.md、compliance_policy.md ✓；scripts/refresh_market_data.py、run_real_llm_benchmark.py、benchmark_stats.py、check_doc_links.py、secret_scan.py ✓；src/benchmark/arena.py、adversaries.py、src/app/main.py、orchestrator.py、src/evolus/client.py、src/session/manager.py、schema.py、src/transport/telemetry.py、webhook_hub.py、src/engine/ 全套 ✓；tests/ 30 檔（含 test_async_stage2.py、test_intent_benchmark.py、test_ood_500_benchmark.py）✓；extension/mock_chat.html ✓；HARDCODED_DATA_AUDIT.md、REAL_WORLD_STRESS_TEST_TRANSCRIPT.md、TARGET_ARCHITECTURE_AND_ROADMAP.md、DEMO_VIDEO_DIRECTOR_SCRIPT.md、DEMO_VIDEO_FILMING_GUIDE.md ✓。**未發現斷鏈。** 專案自帶 scripts/check_doc_links.py，提交前可再跑一次實體確認。

## 六、SUBMIT.md 未勾選項分級

**提交硬阻塞（不做無法交）**
1. demo_video 3–5 分鐘有聲版（現僅 ~15s 無聲；須含 Human-in-the-loop 審批畫面入鏡——tests/test_copilot_human_approval.py 已有對應功能，錄 CLI 操作即可）。
2. 兩把 Evolus API key 輪換（明文共用過，屬安全硬要求）。
3. 提交平台表單本身：欄位、標籤、截止時刻今日內確認（tagline 按 F19 刪減）。
4. 依 F1–F15 刷新文檔數字（評審照文檔核對，數字矛盾等同硬傷）。

**可放棄（文檔已給台階，放棄不扣誠信分）**
1. MI300X 實機量測與 rocm-smi 截圖——無 AMD 硬體；bench.json/benchmark.png 已標 analytical lower bound，LIMITATIONS §10.1 明言不需 $100 credit。
2. Evolus workflow——LIMITATIONS §11.4 自稱「建議補強項而非資格缺口」；3 AI tools + agent + 知識庫已覆蓋主張。
3. market data 刷新——2 天舊可接受，但 1 分鐘能做，建議做。

## 七、PASS 附錄：已驗證一致項

- Evolus credits 2.46+2.12+3.75=8.33：README/PITCH/SUBMISSION_DRAFT/LIMITATIONS 全一致 ✓。
- 28.8M/72M/96M rollout 定義（9.6M×3/×7.5/×10）：與 bench.json、LIMITATIONS §四表一致，且一致標註「算術乘積非執行計數」✓。
- OOD 63.08%（123/195）、195 held-out、92.79% train：與 tests/test_ood_500_benchmark.py:48/117 一致 ✓。
- intent「60 條中 54 條重疊」與 tests/test_intent_benchmark.py:7 一致 ✓。
- benchmark.png 四柱（0.30/1.11/1.68/3.83s）與 bench.json 一致；MI300X 標為 roofline lower bound（免責聲明在圖上）✓；robustness.png 與 LIMITATIONS §4.5 一致 ✓。
- 所有相對連結目標存在（第五節）✓。
