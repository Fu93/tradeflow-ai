# 運行可靠性：故障前後的證據

本輪不以測試數量作為可用性結論。先在一次性資料庫重現故障，再對同一個中斷點重跑。原始結果放在 [reliability](reliability)；CI 也會執行實驗並上傳 JSON 證據。

## 已重現且修復

| 故障 | 修復前觀察 | 修復與重跑結果 |
| --- | --- | --- |
| 確認報價後、寫入 audit 前直接終止程序 | 重啟後 accepted，但沒有 review 紀錄；無法重新確認 | 業務狀態與鏈紀錄放入同一 SQLite transaction；中斷後仍為 review，可正常重試一次 |
| 寄信函式在可能已交付後拋出 TypeError | 同一次核准呼叫 transport 兩次 | 在執行前判斷函式參數相容性；內部失敗只呼叫一次，記為交付結果不明，阻擋重複核准 |
| 還原格式錯誤或不完整的備份 | 先覆蓋正常資料庫，再報驗證錯誤；原檔雜湊改變 | 先建立 SQLite 一致性副本、驗證結構、schema、外鍵及鏈，再以檔案更名替換；拒絕後原檔逐位元不變 |
| 資料與 audit 已提交、但重算快照前當機 | 可能留著過期快照 | 在同一 transaction 使快照失效；重新讀取會在交易鎖內重算 |
| 不同交易共用 SQLite connection | 一筆操作可能提交另一執行緒未完成的寫入 | 整個 transaction 持有 connection lock；實驗確認失敗交易不會被另一執行緒提交 |
| 報價存好但 HTTP 回應遺失，使用者重試 | UI 沒帶去重識別，可能新增另一份報價 | 相同待重試輸入沿用 operation ID；瀏覽器刻意丟掉已提交的回應後重送，最終只留一份 |
| 使用中資料庫有 WAL/SHM 或 journal 時還原 | 舊 journal 可能與替換檔案混用 | 拒絕還原，不刪除 journal。操作人員必須先停止服務並完成 checkpoint/close |
| 線上備份後，來源又新增一筆交易 | 驗證讀到不同時間點的來源清單 | 從一致副本本身列舉與驗證，避免把後來的新資料誤認成備份遺失 |

## 可自行重跑

```bash
python scripts/audit_runtime_reliability.py --output /tmp/runtime-faults.json
python scripts/audit_runtime_load.py --output /tmp/runtime-load.json
python -m pytest tests/test_runtime_reliability.py -q
```

兩個腳本只操作暫存目錄，關閉模型、搜尋、收信與 SMTP。故障腳本以 subprocess 真正終止程序，驗證重新開啟的 SQLite 狀態；transport 是會計數並丟出例外的本機替身。這不能證明真實郵件供應商的可用性。

HTTP 實驗使用 8 個並行工作者，對 4 筆交易送出 48 次報價請求，應只得到 16 份不同報價，並確認 16 次審核。成本排序由腳本以 Decimal 對報價、運費、費用、關稅獨立計算，沒有呼叫專案的成本或排序函式。之後直接 kill 伺服器、保留資料庫並重啟，逐一比較供應商、事件與草稿。效能數據只代表該次本機條件，不是 Render 容量保證。

## 仍不能視為已完成的線上條件

- 本機保留同一資料庫的重啟已驗證；Render 重新部署若換掉 ephemeral disk，資料仍可能消失。正式使用必須配置持久儲存，並在實際部署上演練備份與還原。
- SQLite 與外部 anchor 無法共用單一 transaction。若在匯出 anchor 與資料庫提交之間當機，既有完整性驗證會拒絕傳送，需要操作人員依 anchor recovery 流程處理；本輪不宣稱該中斷點可自動恢復。
- SMTP 在接受郵件後斷線時，寄送端無法保證「一定沒寄出」。系統避免自動重送；需要核對郵件服務紀錄，不能把 unknown 當成安全可重試。
- 目前未取得 Render、實際模型及郵件服務的部署存取權，因此不能宣稱線上服務或第三方整合已達正式運行標準。先前觀察到線上版本仍為 532f52a，必須以部署後的 `/version` 再核對。
