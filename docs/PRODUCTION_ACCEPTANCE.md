# 正式運作驗收（2026-10-30）

目前結論是：修正版已通過本機實際容器、持久卷重建及獨立運算核對；正式 Render 服務仍在舊版，因此尚未完成線上驗收。以下刻意分開程式驗證與外部服務驗證。

## 這輪實際找到的問題

- **Readiness 假綠燈**：production 未設定有效 API 金鑰，或核准公鑰格式錯誤時，`/ready` 仍回傳 200。修正後為 503；也檢查 Ed25519 公鑰是否為有效群元素。`/health` 保留為程序存活檢查，Render Blueprint 改用 `/ready`。
- **金額界線少一分**：[修正前獨立檢查](reliability/production/money-before.json)在 100,000 組刻意落在分界線的帳單中，發現 11,136 組上限多減一分，未發現超額。例：單價界線 1.32 被浮點算成 1.3199999999999998，向下取分後變成 1.31。到岸成本及其係數改以輸入的十進位字串運算，再轉成 API 數值；[修正後檢查](reliability/production/money-after.json)中少一分及超額均為零，未用 epsilon 放寬金額上限。
- **極端金額延後崩潰**：例如 1e26 能通過有限值檢查，之後在取分時觸發 Decimal InvalidOperation。現在在輸入階段拒絕無法保留貨幣最小單位精度的價格範圍及到岸總額。
- **接近界線時上限向上捨入**：進一步測試略低於分界線、以及較多小數位的輸入，發現反推上限轉回 float 可能向上跨過界線。現在保留精確十進位中間金額，依輸入位數設定運算精度；反推的 float 上限若向上捨入就往下移一個 ULP，不用 epsilon 放寬限制。排序也使用精確金額，顯示用 float 不回流成金額依據。
- **映像帶入舊資料**：實際 Docker 建置把本機 `data/desk.db` 打包進映像；原有排除規則只完整處理 sessions，新增的根目錄 glob 也不足以排除巢狀 desk 檔案。已補齊遞迴資料庫／journal 排除規則，並以真正映像驗證沒有內嵌資料庫。
- **本環境的 HTTPS 建置失敗**：代理 CA 未傳入內層 pip。Dockerfile 現在支援選用 BuildKit CA secret，安裝期間掛載，不寫入映像；一般主機不提供此 secret 時沿用標準 CA，TLS 驗證保持啟用。

## 實際容器與數學驗證

[本機容器證據](reliability/production/container-local.json)使用真正 Docker 映像、0.5 CPU、512 MB、非 root 使用者及獨立持久卷：

- 12 次併發報價提交得到 4 筆唯一報價；確認後以獨立 Decimal 帳單核對排序及還價界限。
- production 開啟簽章核准；未簽核准被拒，正確 Ed25519 核准成功。未提供 SMTP，因此只記錄核准，沒有對外發送。
- SIGKILL 後刪除容器，再由相同持久卷建立新容器；供應商、事件、草稿、審計鏈與檔案 anchor 仍一致。
- 未發生 OOM。記憶體數字是該次本機量測，不是正式站的容量承諾；local 證據標示 development worktree，CI 會另產生帶實際 Git SHA 的映像驗收紀錄。

[SciPy 獨立數值核對](reliability/production/decision-oracle.json)使用 seed 531026，2,000 組中 1,727 組可比較、273 組因約束阻擋、0 組無法求值；未發現超標目標值差或發出價格／風險界限違規。這驗證程式與獨立數值方法的一致性，不能驗證風險模型對工廠的預測準確率。

CI 新增獨立 Decimal 界線檢查及真正的 production 容器驗收工作，不只檢查原始碼字串。

補充的三種界線證據：[略低於分界線](reliability/production/money-below-after.json)、[較多小數位](reliability/production/money-precise-after.json)、[兩者同時出現](reliability/production/money-below-precise-after.json)。連同原始界線情境，每種各 100,000 組，獨立 Decimal 帳單均未發現上限超額或多減一分。CI 每次重跑這四種模式各 10,000 組。這是已測範圍的證據，不是所有實數輸入的形式化證明。

```bash
python scripts/audit_financial_boundaries.py --cases 100000 --output /tmp/money.json
python scripts/audit_financial_boundaries.py --cases 100000 --just-below-cent --perturb-inputs --output /tmp/money-near.json
# 另需 scipy，正式服務不依賴它：
python scripts/audit_decision_numerics.py --cases 2000 --seed 531026 --out /tmp/numerics.json
docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t tradeflow-acceptance .
python scripts/audit_container_runtime.py --image tradeflow-acceptance --expected-sha $(git rev-parse HEAD) --output /tmp/container.json
```

容器驗收不載入主機 `.env`，不連真實供應商，不使用付費模型或真實郵件帳號。內嵌測試聯絡人全為 example.test；測試簽章私鑰僅在呼叫端暫存，不放入伺服器。

## 正式 Render 驗收尚缺的條件

[公開探測紀錄](reliability/production/live-probes.json)顯示正式站的 `/health`、`/ready` 都是 200，但 `/version` 仍是 `532f52a`，沒有部署最近的修復。此輪工具沒有 Render 管理介面或憑證，所以沒有宣稱已部署或驗證磁碟。

1. 將已審核 master 部署到現有服務，並同步 Blueprint 的 `/ready` 探測設定。
2. 在 Render 確實掛載持久磁碟。若掛載點為 `/var/data`，可把 `TRADEFLOW_DESK_DB` 設為 `/var/data/tradeflow/desk.db`、`TRADEFLOW_DB_PATH` 設為 `/var/data/tradeflow/sessions.db`、`TRADEFLOW_STORE_DIR` 設為 `/var/data/tradeflow/sessions_json`，檔案 anchor 另設 `/var/data/tradeflow/anchor.json`。建立磁碟會影響主機計費，這輪沒有代為購買。變更現有資料庫路徑之前，先備份舊資料並驗證恢復，不要把空的新路徑當成資料遺失已解決。
3. 設定有效 API／chain 金鑰。正式簽章核准需提供有效公開金鑰，簽章私鑰保留在核准者端。純展示若明確選擇 optional policy，不能把它稱為已驗證的簽章核准部署。
4. 部署後執行下列只讀版本／健康門檻，接著用專用測試交易確認報價、核准，以及一次重新部署後的資料保存。

```bash
python scripts/check_deployed_release.py --base-url https://tradeflow-ai-demo.onrender.com --expected-sha $(git rev-parse HEAD) --output /tmp/live-readiness.json
```

5. 用真實帳號核對 1688、Alibaba、WhatsApp Web、微信網頁版 DOM、登入和訊息 ID；再用受控測試聯絡人驗證實際傳送及收件結果。本輪沒有這些帳號，故未完成。聊天核准後自動發送及微信桌面版仍未實作；SMTP 是否送達也不能由核准狀態推定。

## 商業計算前提

目前到岸成本公式為 `(單價 + 每件運費 + 每件費用) × (1 + 關稅率)`，輸入都必須是交易幣別。這假設所有填入費用都屬於課稅基礎，不另外計算 VAT 或各國特殊稅則；介面已顯示此限制。缺少成本資料時以單價排序，不把缺少的資料當零。單價 ceiling 是商品單價上限，不能自動當成整張含稅發票的總預算。

風險公式沿用已揭露的歷史資料遷移模型；數值正確不等於已證明工廠拒絕機率正確。沒有回覆也不等於拒絕，沒有實際成交證據就不計為已實現節省。

此輪金額精度修正可能使舊版本的草稿或排序改變；應重新讀取最新結果並核准正確版本，不應繞過 stale-draft 檢查重送舊草稿。資料備份應放在不同故障範圍；與資料庫同卷的 anchor 可以驗證此輪恢復一致性，不能取代主機之外的可信備份或外部 anchor。
