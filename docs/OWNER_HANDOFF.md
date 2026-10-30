# 接手操作手冊

此輪程式已補上部署與資料恢復驗收、精確金額計算、簽章核准介面及操作自檢。正式帳號、Render 磁碟與線上部署仍由你接手；已合併不等於正式站已更新。最新程式版本與 CI 請以交付包的 RELEASE.json 及 GitHub 為準。

## 1. 先保留既有資料與身分

升級原有服務時，保留原本的 API／chain 金鑰、退役 chain 金鑰、資料庫與 anchor。不要用範例覆蓋 `.env`，也不要為了通過檢查而重設審計鏈。新環境才使用根目錄的 `.env.production.example`，所有 `__FILL_ME__` 都要填妥。

正式核准私鑰在你自己的電腦建立一次，存放在不會提交 Git 的私人目錄：

```bash
python -m src.desk.approval_signing keygen --out approval_keypair.json
```

只把檔案中的 `public_key` 填入 `TRADEFLOW_APPROVAL_PUBKEY`。不要上傳 `private_key`。這個指令現在會拒絕覆寫既有檔案；需要換金鑰時應明確規劃輪替，而不是刪掉舊檔重跑。

新環境自行產生不重複的 API／chain 金鑰，填入私人 `.env`；不要使用公開示範值。樣板預設關閉郵件發送、模型付費呼叫與搜尋，先把手動流程跑通。

## 2. 啟動並檢查設定

Docker Compose 會載入專案的 `.env`，資料位於 named volume `/app/data`。

```bash
GIT_SHA=$(git rev-parse HEAD) docker compose up --build -d
docker compose exec -T tradeflow python scripts/check_live_readiness.py --intake manual --probe-db
```

PowerShell 可先執行 `$env:GIT_SHA = git rev-parse HEAD`，再執行 `docker compose up --build -d`。

自檢預設只讀目前程序的環境變數，不會自行讀取 `.env`、建立資料庫或連線。上述容器指令會使用 Compose 已載入的環境；`--probe-db` 只檢查既有資料庫，驗證 schema、參照與內部審計鏈，再以回滾的寫入檢查可寫性，不做遷移。

`CONFIGURATION OK` 只表示本次設定檢查通過。`/health` 是程序存活，`/ready` 是服務依賴就緒；持久磁碟仍須做重部署演練，模型及聊天平台仍須實帳驗證。啟用 SMTP 時需 `SMTP_HOST`、`EMAIL_USERNAME`、`EMAIL_PASSWORD` 全部存在；目前寄送採 implicit TLS，通常使用 465。

如要測試專用郵箱登入，明確使用：

```bash
docker compose exec -T tradeflow python scripts/check_live_readiness.py --intake imap --probe-db --connect
```

`--connect` 只做 SMTP／IMAP 登入，不寄信、不讀郵件；錯誤輸出不包含憑證值。Gmail OAuth 的實際權限與收件仍需另外驗收。所有自檢選項都不能證明郵件已送達。

## 3. 正式簽章核准可從網頁操作

1. 在 `/desk` 建立交易、匯入原文、確認供應商自己的單價。
2. 檢查草稿、供應商及聯絡地址。按 **Download approval payload**，把下載檔存成 `payload.json`。
3. 在你自己的電腦，用既有私鑰簽署：

```bash
python -m src.desk.approval_signing sign --key approval_keypair.json --payload payload.json --out approval.json
```

4. 回到同一份草稿，按 **Import signature and approve**，選擇 `approval.json`，再次確認畫面的操作。

只匯入簽章檔，不能選擇 `approval_keypair.json`；介面會拒絕私鑰格式、異常檔案與過大檔案，不把這些內容傳到伺服器。後端仍驗證 pinned public key、草稿識別及本文雜湊；報價或草稿改變後，舊簽章不能核准新版本。切換交易時尚未讀完的檔案不會送出核准。

SMTP 關閉時只記錄核准，聊天草稿仍由你複製傳送。SMTP 開啟時，匯入有效簽章並確認會嘗試真正寄信。`transmitted` 表示已交給郵件伺服器，不是供應商已讀或保證送達。若傳送結果不明，先查寄件備份及 Message-ID；不要重複批准另一份同內容草稿來繞過防重送。

## 4. 維護備份與還原

以下依新樣板的 Docker 路徑示範。每次用新的備份檔名；工具會拒絕覆寫既有備份。維護前先停止服務，讓資料庫與檔案 anchor 的備份屬於同一個安靜的狀態。

```bash
docker compose stop tradeflow
docker compose run --rm --no-deps tradeflow python scripts/backup_desk_db.py backup --output /app/data/backups/desk-before-upgrade.db
mkdir -p backups
docker compose cp tradeflow:/app/data/backups/desk-before-upgrade.db ./backups/desk-before-upgrade.db
docker compose cp tradeflow:/app/data/chain-anchor.json ./backups/chain-anchor-before-upgrade.json
docker compose start tradeflow
```

將 `backups` 再複製到不同主機或備份服務；同卷副本不能抵抗整台主機或磁碟遺失。另保管 chain 金鑰與輪替歷史，只有資料庫但沒有驗證金鑰也不能完成恢復。

還原時先停止服務，再使用已驗證且可信的資料庫及對應 anchor。以下會覆蓋現有資料，操作前保留現況副本：

```bash
docker compose stop tradeflow
docker compose cp ./backups/desk-before-upgrade.db tradeflow:/app/data/backups/restore-input.db
docker compose run --rm --no-deps --user root tradeflow python scripts/backup_desk_db.py restore --from /app/data/backups/restore-input.db --target /app/data/desk.db --yes
docker compose cp ./backups/chain-anchor-before-upgrade.json tradeflow:/app/data/chain-anchor.json
docker compose run --rm --no-deps --user root --entrypoint chown tradeflow 10001:10001 /app/data/desk.db /app/data/chain-anchor.json
docker compose start tradeflow
docker compose exec -T tradeflow python scripts/check_live_readiness.py --intake manual --probe-db
```

root 僅用於離線還原及修復 Docker cp 可能造成的檔案擁有者；服務仍以映像的非 root 使用者執行。若採用 HTTP／外部 anchor，不能照抄檔案 anchor 指令，需按該服務的恢復規則核對。不要修改 schema 版本數字來強迫舊程式開啟新資料庫。

CI 已測試：真正映像內備份、複製到呼叫端、停止容器、複製回卷、離線還原與擁有者修復，再刪除舊容器，讓新容器由還原檔啟動；供應商、事件、草稿及審計驗證一致。這不取代你在正式磁碟上的演練。

## 5. Render 與真實帳號驗收

先確認持久磁碟已掛載，再設定資料庫及 anchor 到實際掛載路徑；不要把 `.env.production.example` 的 `/app/data` 當成 Render 已掛載的證明。保留現有資料後部署 master，健康檢查採 `/ready`。

```bash
python scripts/check_deployed_release.py --base-url https://tradeflow-ai-demo.onrender.com --expected-sha $(git rev-parse HEAD) --output live-readiness.json
```

版本不符會失敗，即使舊版 `/health` 是 200。更新後，用專用測試交易走完確認、簽章、核准、真實收發，然後重新部署再核對資料。先使用專用收件者，確認成功後才處理真正供應商。

Chrome 擷取需瀏覽器開啟、已登入且頁面可讀；已擷取訊息可保存重試，關閉 Chrome 不會繼續接收。1688、Alibaba、WhatsApp Web、微信網頁版仍需實帳核對 DOM。微信桌面版與核准後自動聊天發送未實作。純手動貼入及複製草稿可先使用。

## 6. 驗證與提交素材

數學及容器驗證詳見 [正式驗收紀錄](PRODUCTION_ACCEPTANCE.md)。關稅計算使用已揭露的簡化稅基；真實風險模型仍需供應商資料校準。任何綠燈都不能證明已成交或節省成本。

交付包含已合併原始碼、Chrome 擴充功能、新錄製的離線展示影片及驗證摘要，並附 SHA-256 完整性清單。影片中的供應商為虛構資料，沒有實際寄送或付費模型呼叫。YouTube 公開上傳、Devpost 最終提交、Render 設定及正式服務費用均由你接手。
