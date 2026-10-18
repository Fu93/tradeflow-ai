# 引擎與後端代碼審查（src/engine + src/app）

> 審查者：審查子代理（唯讀、read/grep 實證，行號已釘死於 49e96dd 之後的代碼狀態）。
> 交付說明：該子代理回合無寫檔權限，本檔由主會話代為落盤，內容為其完整結論。

**P0：0 條**——已知崩潰路徑（除零、空列表、NaN/inf、rounds_quotes 邊界、session_id 穿越、assert 護欄、CORS、webhook token）在 49e96dd 後均已被 schema 驗證、AST 護欄或 500 handler 擋住。

**P1：3 條**

1. `src/app/main.py:1309-1314`＋`1538-1543`：v2_belief 用 `sess.spec.prior_reserve_mean/std`（原始先驗）而非累積後驗 `post_mean/post_std`，同一回應中 `bayesian_belief_state.deal_probability` 每輪重置、與 `posterior_reserve_mean` 互相矛盾，輪次越深誤差越大，評審對照兩欄位即穿幫。修法：改傳累積後驗。
2. `src/app/main.py:1395-1396`：`except Exception: auto_reply_text = None` 裸吞 logrolling 全部異常（含 ASTMandateViolationError），回應仍 200、零日誌，功能無聲失效＝假成功。修法：只接 ASTMandateViolationError 並記 warning。
3. `src/engine/bilingual.py:811-816`（同類 regex :745）：高管指令價格掃描 `(?:do|at|to|offer|price|\$|¥)\s*(\d+)` 無 `\b` 且 first-match-wins——「can you do 5000 units at 13.8」把數量 5000 當報價覆寫（通過低價下限檢查），真實報價 13.8 被丟棄；「1000 to 2000 units」同理。修法：加 `\b`、移除 `do`/`to`、量詞排除＋量級 sanity check。

**P2：16 條（摘要）**

- SSE 與行動 webhook 同步阻塞事件迴圈
- `_ROBUSTNESS_CACHE` / `mock_history` 無界成長
- `suppliers=[]` 靜默 fallback 導致 numpy 廣播 500
- API key 非常數時間比較
- `create_session` 無聲清空既有會話
- `classify_with_llm` 吞異常靜默降級
- （其餘 10 條 P2 細節因原回合無法落盤而未保存；如需可重派審查補齊）

**總數統計**：P0＝0、P1＝3、P2＝16。
