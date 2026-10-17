/**
 * Verification harness (one-off) — P0-1 session_id sanitizer.
 *
 * Loads the REAL extension/content.js source text, extracts the sanitizer
 * functions that ship in the file (not a copy), and asserts that every value it
 * can ever produce satisfies the backend whitelist:
 *   src/session/schema.py SAFE_SESSION_ID_RE = ^[A-Za-z0-9._-]{1,128}$
 *   (enforced at the path layer in src/app/main.py:246)
 *
 * Output: ids.json  (consumed by capture.py, which POSTs the ids to a real
 * TestClient instance to prove 200 vs 422).
 */
const fs = require("fs");
const path = require("path");

const repo = process.argv[2] || process.cwd();
const outPath = process.argv[3] || path.join(__dirname, "ids.json");

const src = fs.readFileSync(path.join(repo, "extension", "content.js"), "utf8");
const startMarker = "const SESSION_ID_MAX_LEN = 128;";
const endMarker = "function extractSessionContext() {";
const start = src.indexOf(startMarker);
const end = src.indexOf(endMarker);
if (start < 0 || end <= start) {
  throw new Error(`cannot locate sanitizer block in content.js (start=${start}, end=${end})`);
}
const block = src.slice(start, end).trimEnd();
// eslint-disable-next-line no-new-func
const api = new Function(`${block}\nreturn { sanitizeSessionId, SESSION_ID_RE };`)();
const { sanitizeSessionId, SESSION_ID_RE } = api;

const BACKEND_RE = /^[A-Za-z0-9._-]{1,128}$/;
// The exact value the DOM branch feeds the sanitizer: mock_chat.html's
// ".chat-header .supplier-name" text, sliced to 30 chars by content.js.
const mockName = "浙江頂尖雨具製造實業有限公司 (1688 認證金牌廠商) —【假設性測試對象】".slice(0, 30);

const cases = [
  ["mock supplier name (DOM branch, real mock_chat.html text)", mockName, "sess_"],
  ["other CJK supplier name", "深圳宏達精密製造有限公司", "sess_"],
  ["pure CJK, no ASCII at all", "義烏博創工貿有限公司", "sess_"],
  ["ASCII url param", "abc123", "sess_"],
  ["ASCII supplier name (legacy behaviour preserved)", "ACME_Corp", "sess_"],
  ["200-char ASCII value", "A".repeat(200), "sess_"],
  ["path traversal attempt", "../../../../etc/passwd", "sess_"],
  ["whitespace only", "   ", "sess_"],
  ["200-char CJK with tab prefix", "浙".repeat(200), "sess_tab_"],
];

let failures = 0;
const rows = [];
for (const [name, raw, prefix] of cases) {
  const id = sanitizeSessionId(raw, prefix);
  const again = sanitizeSessionId(raw, prefix);
  const okWhitelist = BACKEND_RE.test(id);
  const okStable = id === again;
  const okLen = id.length <= 128;
  const ok = okWhitelist && okStable && okLen;
  if (!ok) failures += 1;
  rows.push({ name, id, okWhitelist, okStable, okLen });
  console.log(`${ok ? "PASS" : "FAIL"} | ${name} -> ${id}`);
}

const a = sanitizeSessionId("浙江頂尖雨具製造實業有限公司", "sess_");
const b = sanitizeSessionId("深圳宏達精密製造有限公司", "sess_");
const distinct = a !== b;
if (!distinct) failures += 1;
console.log(`${distinct ? "PASS" : "FAIL"} | distinct CJK names -> distinct ids`);

// The legacy regex from the pre-fix DOM branch, kept here so capture.py can POST
// it and demonstrate the HTTP 422 the fix removes.
const legacyCjkId = `sess_${mockName.replace(/[^a-zA-Z0-9_\u4e00-\u9fa5-]/g, "_")}`;

const payload = {
  harness: "extension/content.js sanitizer extracted from source",
  backendPattern: "^(?:[A-Za-z0-9._-]{1,128})$",
  sanitizerPattern: String(SESSION_ID_RE),
  mockName,
  mockNameId: sanitizeSessionId(mockName, "sess_"),
  legacyCjkId,
  rows,
  failures,
};
fs.writeFileSync(outPath, JSON.stringify(payload, null, 2), "utf8");
console.log(`\nmockNameId   = ${payload.mockNameId}`);
console.log(`legacyCjkId  = ${legacyCjkId}`);
console.log(`failures=${failures}`);
process.exit(failures ? 1 : 0);
