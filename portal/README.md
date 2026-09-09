# VM 申請入口（VM Request Portal）

半導體客戶自助式 VM 申請網頁：AD 登入 → 選 OS / 規格 / 叢集 / 網域區隔 → 呼叫 vRA 藍圖建機器。

- 開發依據：[`USER-STORIES.md`](USER-STORIES.md)（22 則）
- **欄位對應：[`FIELD-MAPPING.md`](FIELD-MAPPING.md)** — 表單 / 藍圖 / vRA / vCenter 四者的對應關係，改欄位先看這份
- 後端藍圖：[`../blueprints/vm-selfservice.yaml`](../blueprints/vm-selfservice.yaml)
- vRA API 細節：[`../docs/api-runbook.md`](../docs/api-runbook.md)

---

## 架構

```
AD ──LDAP bind──▶ 這支應用（可多節點） ──REST──▶ vRA 藍圖
                        ▲                          ├── flavor mapping  ← size
                   AVI 負載平衡                     ├── image mapping   ← os
                                                   ├── compute tags    ← cluster
                                                   └── network tags    ← mes / fdc / oa
```

**vRA 端刻意做薄**：藍圖只有一張，選項來自 vRA 的 mapping 與 tag。
新增一個叢集 / 規格 / OS / 網域，只要在 vRA 加 mapping 或打 tag —— **藍圖與這支程式都不用改**。

---

## 🔴 無狀態（AVI 負載平衡的前提）

這支程式**不保存任何伺服器端狀態**：

| 狀態 | 放哪裡 |
|---|---|
| 登入 session | 簽章 JWT，放在 HttpOnly cookie |
| 申請紀錄 / 機器清單 | vRA（靠 `requester` tag 查詢） |
| vRA token | 各節點自己的記憶體快取（純快取，掉了自動重取） |

因此任一節點都能處理任何請求，**不需要 sticky session**，節點掛掉使用者也不會被登出。

> **前提：所有節點的 `SESSION_SECRET` 必須完全相同**，
> 否則 A 節點簽的 session 到 B 節點會驗不過。

「我的機器」是靠 vRA 的 tag 查詢實作的：

```
GET /iaas/api/machines?$filter=tags.item.key eq 'requester' and tags.item.value eq '<upn>'
```

已實測可用（UPN 可以當 tag 值）。**所以中介層不需要自己的資料庫。**

單號同樣進 `tags`，因此稽核也能反查「這張單開了哪些機器」。

---

## 欄位來源

完整的四方對應（表單 / 藍圖 / vRA / vCenter）見 **[`FIELD-MAPPING.md`](FIELD-MAPPING.md)**。

| 欄位 | 誰決定 | 為什麼 |
|---|---|---|
| 申請人 `requester` | **session（AD 登入者）** | 避免冒名；打字也會拼錯 |
| 員工編號 `employeeId` | **session**（AD 的 `employeeID` / `employeeNumber`） | 同上；AD 本來就有，沒有理由讓人重打 |
| 申請單號 `ticket` | **使用者填**（必填） | 只有申請人知道既有 ITSM 的單號 |
| OS / 規格 / 叢集 / 網域 | 使用者從選單挑 | 選項由 vRA 現況決定 |
| 用途 / 備註 | 使用者填 | 用途為代碼（可統計），備註為自由文字 |

> 若之後改成由本系統核發單號（而非串既有 ITSM），把 `ticket` 從表單欄位改成後端產生即可，
> 藍圖與資料流都不用動。

---

## 安全設計

| 項目 | 作法 |
|---|---|
| vRA 憑證 | 只存在這一層，**絕不傳到瀏覽器**。vRA 的 bearer token 是全權限（實測 8 小時有效） |
| 申請人身分 / 員工編號 | 一律取自 session（員工編號從 AD 讀），**前端傳什麼都不採用**，避免冒名申請 |
| 刪除機器 | 先確認該部署的 `requester` 等於登入者，才允許刪除（vRA 端所有部署都掛同一個服務帳號，權限得自己把關） |
| Cookie | `HttpOnly` + `SameSite=Lax`，走 https 時自動加 `Secure` |

---

## 安裝與啟動

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # 填入 AD 與 vRA 連線資訊
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

正式環境建議用 systemd 拉起來，多節點就是每台跑同一份、`.env` 一致。

### 需要準備的 vRA 設定

| 設定 | 說明 |
|---|---|
| `VRA_PROJECT_ID` | 部署要落在哪個 project |
| `VRA_BLUEPRINT_ID` | 把 `../blueprints/vm-selfservice.yaml` 建立到 vRA 之後拿到的 id |
| 服務帳號 | 建議另開一個專用帳號，不要用 admin |
| compute tags | 每個叢集打上 `cluster:<值>`，藍圖以 constraint 比對 |
| network tags | 每個網段打上 `zone:mes` / `zone:fdc` / `zone:oa` |
| IP range | 讓 vRA 自動配發靜態 IP；template 需安裝 VMware Tools |

---

## API

| 方法 | 路徑 | 說明 |
|---|---|---|
| `GET` | `/healthz` | 健康檢查，vRA 連不上回 **503**（供 AVI 探測） |
| `GET` | `/api/me` | 目前登入者（申請人欄位的來源） |
| `GET` | `/api/catalog` | OS / 規格 / 叢集 / 網域選單，內容來自 vRA 現況 |
| `POST` | `/api/requests/validate` | dry-run（`plan=true`），驗證但不開機 |
| `POST` | `/api/requests` | 送出申請，回 `deploymentId` |
| `GET` | `/api/requests/{id}` | 查進度；失敗時 `message` 帶 vRA 的具體原因 |
| `GET` | `/api/my-vms` | 我申請的機器 |
| `DELETE` | `/api/deployments/{id}` | 回收機器（會先驗證擁有者） |

互動式文件在 `/api/docs`。

---

## AVI 負載平衡設定要點

1. Pool 成員 = 各節點的 `:8000`
2. Health monitor 指到 **`GET /healthz`**，期待 HTTP 200
   （vRA 連不上時本程式回 503，該節點會自動被移出輪替）
3. **不需要** persistence / sticky session —— 應用是無狀態的
4. Virtual Service 建議終結 TLS，後端走 http 即可

---

## 已知前置條件與限制

- AD 走 **LDAP bind**（使用者需輸入帳密）。若要真正的 SSO 需改走 OIDC/SAML，是另一項工作。
- 用途選項目前寫在前端頁面上，待客戶確認代碼後可改為由後端提供。
- vRA 帳號有 **lockout**，不要用猛打登入的方式做健康檢查 —— 本程式的 token 是快取的。
- `?expand=resources` 是摘要視圖，拿不到自訂屬性；本程式取屬性時走單一 resource 端點。
