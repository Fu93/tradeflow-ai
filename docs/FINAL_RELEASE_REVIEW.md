# 最後一輪穩定性檢查（2026-10-30）

基準為 master `3ae1588`，使用獨立 worktree，保留先前中斷環境的本機修改。以下為已執行驗證及限制，不代表正式平台或部署已驗收。

## 實際重現與修正

| 操作情境 | 修正前觀察 | 修正後行為 |
| --- | --- | --- |
| 舊聊天室標題隱藏但留在 DOM，新聊天室收到報價 | Beta 報價被歸給 Alpha | 店名及聯絡人只讀可見節點，Beta 訊息歸給 Beta |
| 同筆交易先有 12 則無法對應的擷取，後面有可對應報價，連按兩次重試 | 有效報價仍未到後端，固定批次一直處理前面的訊息 | 保存最後嘗試時間，優先處理未嘗試或較久未嘗試的項目；有效報價送達，未知來源仍保留 |
| 收到報價後又加入隱藏舊報價及帶有 `outgoing-meta` 的買方發言，再手動匯入 | 選到另一則訊息，未辨識為原供應商報價的重複匯入 | 手動與自動都排除隱藏訊息及買方方向，只保留原供應商報價 |

以上情境先在原版失敗，再於修正版通過。測試使用真正的 Chromium MV3、擴充功能儲存及本機 HTTP 後端；平台 HTML 為虛構 fixture，無真實聊天發送或付費模型呼叫。另確認同批 DOM 更新中加入後立即移除的暫態訊息不會匯入；該情境原本就通過，並非新缺陷。

`tests/test_chat_multisupplier.py`、`tests/test_desk_mock_import.py` 及 `tests/test_supplier_threads.py` 共 25 個情境於本機通過。完整 CI 結果見對應 PR 的 GitHub Actions。

## 運算與故障證據

- [獨立數值比對](reliability/final/decision-oracle.json)：SciPy CDF 及獨立最佳化搜尋，seed 301026，共 1,000 組；860 組可比較、140 組因約束阻擋、0 組無法求值、無超標差異。最大目標值差約 8.44e-15；同時核對發出價格與風險界限。這是離線交叉檢查，不是預測準確率或節省金額證明。
- [程序中斷與還原](reliability/final/runtime-faults.json)：真實程序退出後，未完成確認回滾、重試只確認一次、提交後快照可重建；不明寄送結果不重送；損壞備份不覆寫既有資料庫；有效備份保留事件與審計鏈。
- [併發 HTTP 與強制重啟](reliability/final/runtime-load.json)：8 個工作者、4 筆交易、48 次提交，最後 16 筆唯一報價與 16 次確認；獨立 Decimal 計算核對原始輸入及競爭價格界限，強制停止再啟動後資料保留。延遲只描述本次本機測試，不能當成正式容量承諾。

可重跑：

```bash
python -m pytest tests/test_chat_multisupplier.py tests/test_desk_mock_import.py tests/test_supplier_threads.py -q
python scripts/audit_runtime_reliability.py --output /tmp/runtime-faults.json
python scripts/audit_runtime_load.py --output /tmp/runtime-load.json
# 獨立數值審計另需 scipy；正式執行不依賴 scipy。
python scripts/audit_decision_numerics.py --cases 1000 --seed 301026 --out /tmp/decision-oracle.json
```

## 提交及使用邊界

- 報價原文先進待確認清單，確認單價後才更新比較與還價；慢回覆不會自動變成拒絕，也不會替供應商自動讓價。
- Chrome 開啟且聊天頁可讀時才會抓取新訊息。已抓到的訊息可持久保存及重試；關閉 Chrome 不會繼續接收。
- 每筆交易仍依既有規則接受 4 或 5 家，尚未驗證大量帳號或正式站容量。
- 1688、Alibaba、WhatsApp Web、微信網頁版已測接收流程 fixture；真實帳號登入、DOM、iframe 與訊息識別仍需實站驗證。微信桌面版尚未支援。
- 聊天發送仍由人複製傳送；核准後直接聊天發送尚未接上。Email 只有 SMTP 已設定且通過該份草稿核准時才會嘗試傳送，不能把核准當成送達。
- Render 部署與持久磁碟依使用者要求延後。正式站目前版本、重部署保留資料、真實模型與傳送結果，不在此輪離線驗證範圍。
- 現有展示影片為離線錄製，含模擬供應商標示；不能證明登入正式聊天平台或發送成功。
