# 欄位對應表

表單 → 藍圖 → vRA → vCenter 的單一真實來源。

**核心設計：自訂屬性欄位只定義在一個地方 —— [`fields.json`](fields.json)。**
客戶要增減欄位時，藍圖不用改、程式不用改、網頁樣板也不用改。

---

## 兩類欄位，走的路完全不同

| | 決策欄位 | 自訂屬性欄位 |
|---|---|---|
| 有哪些 | 作業系統、規格、叢集、網域區隔 | 申請人、員工編號、部門、單號、用途、備註…（**由客戶決定**） |
| 決定什麼 | 機器長什麼樣、放在哪裡 | 這台機器是誰的、為什麼開的 |
| 藍圖怎麼收 | 各自一個 input | **合併成一個 `attributes` 物件整包收** |
| 增減欄位要動藍圖嗎 | 要 | **不用** |
| 最終落在哪 | 機器的實際規格與位置 | vCenter 的自訂屬性 |

決策欄位的結果已經體現在機器本身（幾核幾 GB、在哪個叢集、哪個網段），
所以不需要再寫一份屬性去描述它，否則會有「標記說在 A、實際在 B」的不一致風險。

---

## 資料流

```
AD ──────────────┐
                 ├──► 申請入口 ──► 藍圖 attributes（一包）──► vRA
表單使用者填 ─────┘                                            │
                                                              ▼
                                          部署完成後由入口寫入 vCenter 自訂屬性
```

藍圖只有五個 input：`os` / `size` / `cluster` / `zone` / `attributes`。
前四個是決策，最後一個是整包自訂屬性 —— **藍圖不需要知道裡面有哪些 key**。

已實測：送進去的 `attributes` 原封不動出現在機器屬性上，連藍圖從未定義過的 key
（測試時放了一個 `costCenter`）也照樣帶到 VM。

> ⚠️ vRA 回傳時會把該物件**序列化成 JSON 字串**，讀取端要自己解析（`app/vra.py` 已處理）。

---

## fields.json 的欄位定義

```jsonc
{
  "key":         "ticket",              // 內部識別，也是 attributes 裡的 key
  "label":       "申請單號",             // 表單與清單顯示的名稱
  "source":      "user",                // ad = 由 AD 帶入 / user = 使用者填
  "type":        "text",                // text | textarea | select
  "required":    true,
  "pattern":     "^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$",
  "vcAttribute": "customTicket"         // 對應的 vCenter 自訂屬性名稱
}
```

| 設定 | 效果 |
|---|---|
| `source: "ad"` | 表單上顯示但不可編輯，值取自登入身分。**前端傳什麼都不採用** —— 冒用不了 |
| `source: "user"` | 使用者填寫，依 `required` / `pattern` / `maxLength` / `options` 驗證 |
| `vcAttribute` | 部署完成後寫進 vCenter 的哪個自訂屬性；留空則只存在 vRA，不寫 vCenter |
| `adAttribute` / `adFallback` | 要跟 AD 要哪個屬性（不同 AD 架構欄位名稱不同，可設兩個） |

### 目前預設的欄位

| 欄位 | 來源 | vCenter 自訂屬性 |
|---|---|---|
| 申請人 `requester` | AD（userPrincipalName） | `customOwner` |
| 員工編號 `employeeId` | AD（employeeID / employeeNumber） | `customEmployeeId` |
| 部門 `department` | AD（department） | `customDepartment` |
| 申請單號 `ticket` | 使用者填（必填，有格式驗證） | `customTicket` |
| 用途 `purpose` | 使用者選（下拉） | `customPurpose` |
| 備註 `purposeNote` | 使用者填（選填） | `customNote` |

**`vcAttribute` 請改成客戶 vCenter 上既有的欄位名稱**，不必另建新欄位。
欄位不存在時預設會略過並記警告；要自動建立請設 `VC_CREATE_FIELDS=true`
（需要 vCenter 的 `Global.ManageCustomFields` 權限）。

---

## 🔴 vCenter 自訂屬性不會自己出現

實測確認：由藍圖部署出來的 VM，vCenter 端 `customValue` 是空的 ——
**vRA 的屬性只存在 vRA 自己的資料裡，不會同步到 vCenter。**

因此由申請入口在部署成功後主動寫入（`app/vcenter.py`，走 SOAP 的
`CustomFieldsManager.SetField`；vSphere REST API 沒有對應端點）。

| 作法 | 優點 | 缺點 |
|---|---|---|
| **由申請入口寫**（目前作法） | vRA 端完全不用動 | 只有透過本入口申請的機器才會有屬性 |
| 由 vRA ABX action 寫 | 不管誰部署都會寫 | vRA 端要多維護一個 action 與事件訂閱 |
| 由 vRO workflow 寫 | 同上，且可做更複雜的邏輯 | 需要 vRO |

> 若客戶要求「不管從哪裡部署都要有自訂屬性」，就要改用 ABX 或 vRO。
> 目前選擇由入口寫入，是配合「vRA 端盡量薄」的架構決定。

---

## 查詢：以 vCenter 為準

自訂屬性既然以 vCenter 為系統紀錄，查詢也走 vCenter：

- **我的機器** — 依 `requester` 自訂屬性反查
- **稽核** — 依任一欄位反查，例如某張單號、某個員工編號

自訂屬性沒有伺服器端的篩選 API，所以用 PropertyCollector 一次取回所有 VM 的
`name` / `customValue` / 電源 / IP 再在本地過濾（只取這幾個屬性，不會拉整份 inventory）。

> 若日後機器數量成長到讓這個查詢變慢，再考慮在入口加一層快取，
> 或改用 vSphere Tag（有伺服器端篩選）承載需要高頻查詢的維度。

---

## 改欄位的檢查清單

### 增減「自訂屬性欄位」（絕大多數情況）
1. 改 [`fields.json`](fields.json) —— **就這樣**
2. 若有指定 `vcAttribute`，確認該欄位在 vCenter 上存在（或開啟自動建立）

藍圖、後端程式、網頁樣板都不需要修改。

### 增減「決策欄位」（少數情況，例如要讓使用者選儲存等級）
1. 藍圖 [`../blueprints/vm-selfservice.yaml`](../blueprints/vm-selfservice.yaml)：加 input 與對應的 property/constraint
2. `app/main.py` 的 `VmRequest`：加欄位
3. `app/vra.py` 的 `catalog()`：加選項來源
4. `templates/index.html`：加下拉選單
