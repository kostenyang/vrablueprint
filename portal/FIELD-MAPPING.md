# 欄位對應表

表單 → 藍圖 → vRA → vCenter 的單一真實來源。**任何一欄要增刪，四個地方要一起改。**

---

## 總表

| 表單欄位 | 誰決定 | 藍圖 input | vRA 自訂屬性 | vRA tag（可 `$filter` 查） | vCenter 自訂屬性 |
|---|---|---|---|---|---|
| （不顯示） | session（AD） | `requester` | `requester` | ✅ `requester` | `customOwner` |
| （不顯示） | session（AD） | `employeeId` | `employeeId` | — | `customEmployeeId` |
| 申請單號 | **使用者填** | `ticket` | `ticket` | ✅ `ticket` | `customTicket` |
| 用途 | 使用者選 | `purpose` | `purpose` | ✅ `purpose` | `customPurpose` |
| 備註 | 使用者填 | `purposeNote` | `purposeNote` | — | `customNote` |
| 作業系統 | 使用者選 | `os` | （→ `image` mapping） | — | — |
| 規格 | 使用者選 | `size` | （→ `flavor` mapping） | — | — |
| 叢集 | 使用者選 | `cluster` | （→ `constraints`） | — | — |
| 網域區隔 | 使用者選 | `zone` | （→ 網路 `constraints`） | ✅ `zone` | — |

### 為什麼有些欄位「不顯示」
`requester` 與 `employeeId` 由後端從 session 填入，表單上**沒有**對應欄位。
前端就算硬塞這兩個 key 也不會被採用（`VmRequest` model 裡根本沒有它們）—— 避免冒名申請。

### 為什麼有些欄位沒有 tag
`tags` 是 vRA 唯一支援 `$filter` 查詢的一層。需要被查詢的維度才放 tag：
- `requester` → 「我的機器」
- `ticket` → 稽核反查「這張單開了哪些機器」
- `purpose` / `zone` → 統計與報表

`employeeId` 與 `purposeNote` 只需要讀得到，不需要被查詢，所以只放自訂屬性。

### 為什麼 os / size / cluster / zone 沒有自訂屬性
這四個不是「屬性」，是**決策**：
- `os` → image mapping，決定用哪個 template
- `size` → flavor mapping，決定 CPU / 記憶體
- `cluster` → compute 的 `cluster:*` tag，決定放在哪個叢集
- `zone` → 網段的 `zone:*` tag，決定落在哪個網段

值本身已經體現在機器的實際規格與位置上，重複寫成屬性沒有意義。
（`zone` 例外，另外放了一份 tag，因為報表要能依網域區隔分群。）

---

## 🔴 vCenter 自訂屬性不會自己出現

實測確認：由藍圖部署出來的 VM，vCenter 端 `customValue` 是空的 ——
**vRA 的自訂屬性只存在 vRA 自己的資料裡，不會同步到 vCenter。**

所以由 portal 在部署成功後主動寫入（`app/vcenter.py`，走 SOAP 的
`CustomFieldsManager.SetField`；vSphere REST API 沒有對應端點）。

| 作法 | 優點 | 缺點 |
|---|---|---|
| **由 portal 寫**（目前作法） | vRA 端完全不用動 | 只有透過本入口申請的機器才會有屬性 |
| 由 vRA ABX action 寫 | 不管誰部署都會寫 | vRA 端要多維護一個 action 與事件訂閱 |
| 由 vRO workflow 寫 | 同上，且可做更複雜的邏輯 | 需要 vRO |

> 若客戶要求「不管從哪裡部署都要有自訂屬性」，就要改用 ABX 或 vRO。
> 目前選擇 portal 寫入，是配合「vRA 端盡量薄」的架構決定。

### 欄位名稱要對上客戶現有的
vCenter 自訂屬性名稱用 `.env` 的 `VC_FIELD_MAP` 覆寫，例如客戶已經在用 `BU` / `APSystem`：

```
VC_FIELD_MAP={"requester":"customOwner","employeeId":"EmpID","ticket":"REQ","purpose":"APSystem","purposeNote":"Note"}
```

欄位若不存在，預設會**略過**並記一筆警告；要自動建立請設 `VC_CREATE_FIELDS=true`
（需要 vCenter 的 `Global.ManageCustomFields` 權限）。

---

## 改欄位的檢查清單

新增一個欄位時，這四個地方都要動：

1. **表單** — `templates/index.html`：加輸入元件，並加進 `body()`
2. **API model** — `app/main.py` 的 `VmRequest`：加欄位與驗證規則
   （若是由 session 決定的，**不要**加進 model，改在 `_build_inputs()` 填）
3. **藍圖** — `../blueprints/vm-selfservice.yaml`：加 `inputs` 項目、加到 `properties`，
   需要被查詢的話再加進 `tags`
4. **vCenter 對應** — `app/vcenter.py` 的 `_DEFAULT_MAP`（或 `.env` 的 `VC_FIELD_MAP`）

漏掉第 3 步的話 vRA 會直接報 input 不存在；漏掉第 4 步則是靜靜地不寫入 —— 後者比較難發現。
