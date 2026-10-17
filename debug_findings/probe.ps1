# Runtime probe for TradeFlow AI (temp verification artifact, not repo source)
$ErrorActionPreference = 'Continue'
$base = 'http://localhost:8000'
$out = New-Object System.Collections.Generic.List[string]

# ---- 1. Wait for startup, measure time to first 200 on /health ----
$t0 = Get-Date
$up = $false
for ($i = 0; $i -lt 240; $i++) {
  try {
    $r = Invoke-WebRequest -Uri "$base/health" -UseBasicParsing -TimeoutSec 2
    if ($r.StatusCode -eq 200) { $up = $true; break }
  } catch { Start-Sleep -Milliseconds 500 }
}
$dt = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
$out.Add("STARTUP_OK=$up ELAPSED_TO_FIRST_200_SECONDS=$dt")

function Probe($method, $url, $body, $label) {
  try {
    if ($body) {
      $r = Invoke-WebRequest -Uri $url -Method $method -Body ($body | ConvertTo-Json -Depth 6) -ContentType 'application/json' -UseBasicParsing -TimeoutSec 120
    } else {
      $r = Invoke-WebRequest -Uri $url -Method $method -UseBasicParsing -TimeoutSec 120
    }
    $snippet = ''
    if ($r.Content) { $snippet = ($r.Content -replace '\s+', ' '); if ($snippet.Length -gt 220) { $snippet = $snippet.Substring(0, 220) } }
    $out.Add("[$method $url] -> HTTP $($r.StatusCode) | $label | body: $snippet")
    return $r
  } catch {
    $code = ''
    if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
    $out.Add("[$method $url] -> HTTP $code EXCEPTION: $($_.Exception.Message)" )
    return $null
  }
}

Probe 'GET' "$base/health" $null 'health check'
$home = Probe 'GET' "$base/" $null 'web portal dashboard'
if ($home) { $out.Add(("HOME_CONTAINS_TRADEFLOW_AI=" + ($home.Content -match 'TradeFlow AI'))) }
Probe 'GET' "$base/static/css/theme.css" $null 'static css'
Probe 'GET' "$base/static/js/app.js" $null 'static js'
Probe 'GET' "$base/api/v1/sessions" $null 'list sessions'

# ---- unique session ----
$sid = 'rtv-' + (Get-Date -Format 'yyyyMMddHHmmss')
$spec = @{ session_id = $sid; product_name = '自動反開摺疊傘'; target_price = 12.0; ceiling_price = 15.0; moq = 1000; max_rounds = 5; supplier_id = 'supplier-zhejiang-01' }
Probe 'POST' "$base/api/v1/sessions" $spec 'create session'

$r1 = @{ opponent_raw_text = '老闆，現在銅和碰擊布原料都在漲，最低報 14.20 元/把，沒利潤！'; opponent_offer_price = 14.20 }
$resp1 = Probe 'POST' "$base/api/v1/sessions/$sid/round" $r1 'round 1 intent'
try { if ($resp1) { $j = $resp1.Content | ConvertFrom-Json; $out.Add("ROUND1_INTENT=$($j.inbound_intent) CIRCUIT_TRIPPED=$($j.circuit_tripped)") } } catch {}

$r2 = @{ opponent_raw_text = '平台聊天總卡，加個微信 13812345678 私下聊，價格好商量'; opponent_offer_price = 13.80 }
$resp2 = Probe 'POST' "$base/api/v1/sessions/$sid/round" $r2 'round 2 circuit breaker'
try { if ($resp2) { $j = $resp2.Content | ConvertFrom-Json; $out.Add("ROUND2_TRIPPED=$($j.circuit_tripped) REASON=$($j.trip_reason)") } } catch {}

$cmd = @{ command = '1'; session_id = $sid }
$respC = Probe 'POST' "$base/webhook/mobile-command" $cmd 'mobile command 1'
try { if ($respC) { $j = $respC.Content | ConvertFrom-Json; $out.Add("CMD_STATUS=$($j.status) ACTION=$($j.action)") } } catch {}

# Run Auction (multi-auction tensor run)
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$respA = Probe 'POST' "$base/api/v1/multi-auction/run" @{} 'multi-auction run'
$sw.Stop()
$out.Add("MULTI_AUCTION_ELAPSED_SECONDS=$([math]::Round($sw.Elapsed.TotalSeconds,2))")
try { if ($respA) { $j = $respA.Content | ConvertFrom-Json; $out.Add("AUCTION_WINNER=$($j.winning_supplier) K=$($j.k_suppliers) ROLLOUTS=$($j.total_rollouts) BACKEND=$($j.backend)") } } catch {}

Probe 'POST' "$base/negotiate" @{} 'legacy negotiate'
Probe 'POST' "$base/api/v1/bayesian/update" @{ observed_offer = 14.2 } 'bayesian update'
Probe 'POST' "$base/api/v1/tco/evaluate" @{ raw_price = 14.2 } 'tco evaluate'
Probe 'GET' "$base/api/v1/config/telemetry" $null 'telemetry config read'
Probe 'POST' "$base/api/v1/classify/semantic" @{ text = '老闆，原料漲價了，最低 14.2'; enable_stage2 = $false } 'semantic classify stage1 only'
Probe 'POST' "$base/webhook/evolus" @{ email_body = 'test' } 'evolus webhook (no token)'

# ---- SSE immediate response check via curl.exe ----
$curlOut = & curl.exe -s -N -i --max-time 5 -w "`nCURL_METRICS http_code=%{http_code} time_starttransfer=%{time_starttransfer} size_download=%{size_download}" "$base/api/v1/stream/session" 2>&1
$sseHead = ($curlOut | Select-Object -First 12) -join ' | '
$sseMetrics = ($curlOut | Select-Object -Last 1)
$out.Add("SSE_RESPONSE: $sseHead")
$out.Add("SSE_METRICS: $sseMetrics")

$out | Out-File -FilePath debug_findings\probe_results.txt -Encoding utf8
$out | ForEach-Object { Write-Output $_ }
