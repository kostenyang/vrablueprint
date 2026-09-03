# Blueprint 範例回覆

已在 **VMware Aria Automation 8.18.1** 實機驗證，隨附 `blueprint.yaml` 為修正後版本。

---

## 1. `formatVersion: 2` — 正確，而且是必要的

原範例寫 `formatVersion: 2` 沒有問題。這不是筆誤，而是**使用頂層 `metadata:` 區塊的前提**。

實測把同一份改成 `formatVersion: 1` 後送出，vRA 直接判定 invalid：

```
Blueprint format version should be at least 2 to support metadata
```

改回 `formatVersion: 2` 即通過（`valid: true`，`validationMessages: []`）。

---

## 2. `metadata:` 可以用，但**平台端讀不回來**

metadata 區塊會被完整保存（讀回藍圖時內容一字不差），但 vRA 8 **不會**把它拆成可查詢的欄位。實測三支 API 都沒有這個欄位：

| API | 是否回傳 metadata |
|---|---|
| `GET /blueprint/api/blueprints/{id}` | ✗ 僅存在於 `content`（YAML 字串）之中 |
| `GET /blueprint/api/blueprints/{id}/versions` | ✗ |
| `GET /catalog/api/admin/items` | ✗ |

**影響**：若平台需要取用這些資訊，只能自行取得 `content` 後再解析 YAML；也**無法**用於 catalog 的搜尋或篩選。

因此建議 metadata 僅放「文件性質」的描述。

---

## 3. attributes 改走 resource `properties` — 可行，且這條路才讀得到

在 `resources.<name>.properties` 底下直接加自訂欄位（`osFamily`、`appName`、`appEnvironment`、`characteristics` 等），驗證通過（`valid: true`），且**部署後可經由 API 讀回**：

| API | 是否看得到自訂欄位 |
|---|---|
| `GET /deployment/api/deployments/{id}/resources/{resourceId}` | ✓ 完整 properties |
| `GET /iaas/api/machines` → `customProperties` | ✓ |
| `GET /deployment/api/deployments/{id}?expand=resources` | ✗ **摘要視圖**，僅回 `address` / `powerState` / `resourceName` |

> ⚠️ 最後一列是容易誤判的地方：用摘要視圖會以為自訂欄位沒寫進去，實際上要打單一 resource 端點。

實測回傳（節錄）：

```json
{ "osFamily":"Linux", "osDistribution":"Ubuntu", "osVersion":"20.04",
  "appType":"Web", "appName":"Test Application", "appEnvironment":"Development",
  "characteristics":"Linux,Ubuntu,Web",
  "tags":[{"key":"os","value":"linux"},{"key":"app","value":"web"},
          {"key":"env","value":"development"}],
  "address":"10.0.0.212", "cpuCount":2, "totalMemoryMB":4096 }
```

### `tags` 是唯一可以查詢的一層

```
GET /iaas/api/machines?$filter=tags.item.key eq 'app' and tags.item.value eq 'web'
-> totalElements: 1
```

自訂 properties 讀得到但不能 filter；`tags` 兩者皆可。建議**要用於分類／篩選的維度一律放 `tags`**。

### 建議的三層配置

| 位置 | 平台可讀性 | 適合放什麼 |
|---|---|---|
| 頂層 `metadata:` | ✗ 需自行解析 YAML | 文件性質的描述 |
| `resources.*.properties` 自訂欄位 | ✓ 可讀，不可 filter | 隨 VM 帶走的屬性 |
| `resources.*.properties.tags` | ✓ 可讀，**可 filter** | 分類／篩選／清單維度 |

---

## 4. 客戶輸入的三個參數：OS／Cluster／IP

三者機制不同，分述如下。

### 4-1 OS 與 Cluster：用 `oneOf` 提供友善下拉選單

`title` 為顯示名稱、`const` 為實際送出的值：

```yaml
  os:
    type: string
    title: Operating System
    default: UBUNTU2204_TMP_TW
    oneOf:
      - title: Ubuntu 22.04 LTS
        const: UBUNTU2204_TMP_TW
      - title: Windows Server 2016
        const: VRA_WIN2016_TMP_TW
```

`GET /blueprint/api/blueprints/{id}/inputs-schema` 會原樣回傳 `oneOf`（含 `title` / `const` / `default`），前端可直接依此渲染表單，不需自行解析 YAML。

### 4-2 🔴 Cluster 需要前置作業：先替 compute 打 tag

**vRA 的藍圖無法直接指定叢集名稱。** 放置位置是由 Cloud Zone ＋ tag constraint 決定，因此必須：

1. 先替 Cloud Zone 內的各 compute（cluster / resource pool）加上 tag，例如 `cluster: prod`、`cluster: dev`
   （API：`PATCH /iaas/api/fabric-computes/{id}`，body `{"tags":[{"key":"cluster","value":"prod"}]}`；
   或於 Cloud Assembly UI 的 Infrastructure → Compute 逐一設定）
2. 藍圖再以 constraint 比對：

```yaml
      constraints:
        - tag: cluster:${input.cluster}
```

若 compute 未打 tag，部署會在放置階段失敗：
`No placement exists that satisfies all of the request requirements.`

### 4-3 IP：`pattern` 驗證 ＋ `address` 指派

```yaml
  ipAddress:
    type: string
    title: IP Address
    pattern: ^([0-9]{1,3}\.){3}[0-9]{1,3}$
```

```yaml
      networks:
        - network: ${resource.Cloud_vSphere_Network_1.id}
          assignment: static
          address: ${input.ipAddress}
```

`pattern` 一併帶進 inputs-schema，前端可直接用來擋格式。

前置條件：該 fabric network 需先設定網段 / 閘道 / DNS，並建立 IP range；
客戶輸入的 IP **必須落在該 range 內**，且該 IP 尚未被配發。
另外靜態 IP 是透過 guest customization 推入，**template 需安裝 VMware Tools**。

---

## 5. 隨附版本另外調整的四處

1. **補上 `networks`** — 原範例沒有網路設定，vRA 不會介入網路配置，VM 僅沿用 template 的網卡、不會取得 IP。
2. **`totalMemoryMB` 預設值 1024 → 4096** — 1 GB 對 Windows template 不足。
3. **`imageRef` 的取捨** — `imageRef` 直接指定 vCenter template 名稱可行（已驗證），但等同硬編碼，更換 template 需修改藍圖。若希望降低維護成本，建議改用 `image:` 搭配 Image Mapping。
4. **metadata 與 template 的一致性** — 原範例 metadata 標示 Ubuntu 22.04，但 template enum 為 `VRA_WIN2016_TMP_TW`，兩者不一致。隨附版本已將 OS 改為選單，請依實際環境替換 template 名稱。

---

## 6. 驗證結果

| 項目 | 結果 |
|---|---|
| 建立藍圖 `POST /blueprint/api/blueprints` | HTTP 201，`valid: true`，無 validation message |
| Dry-run（`plan: true`） | FINISHED |
| 實際部署（含 os / cluster / ipAddress 三個輸入） | **CREATE_SUCCESSFUL** |
| 佈署結果 | VM 取得指定的靜態 IP、落在指定叢集、ping 正常；自訂 attributes 與 tags 均可由 API 讀回 |
