# vRA (VMware Aria Automation 8.x) 藍圖範例與建置流程

這個 repo 收錄了在 **VMware Aria Automation 8.18（vRA）** 上，用 **API 全自動**建立「Cloud Template（藍圖 / Blueprint）」並實際部署 VM 的完整流程與範例。

三個藍圖（在 [`blueprints/`](blueprints/)）：

| 藍圖 | 說明 |
|---|---|
| `centos7-small` | 單台 CentOS 7、small（1 vCPU / 2 GB）、靜態 IP |
| `ubuntu-medium` | 單台 Ubuntu、medium（2 vCPU / 4 GB）、靜態 IP |
| `web-app` | 帶下拉選單（OS / 大小）的參數化範例，部署時才選 |
| `vm-metadata-attributes` | 自助式部署：使用者可選 **OS / 叢集 / 靜態 IP**；同時示範 `formatVersion: 2` 的頂層 `metadata:` 與自訂 attributes（resource properties / tags） |
| `vm-selfservice` | [`portal/`](portal/) 這支網頁用的藍圖：使用者選 **OS / 規格 / 叢集 / 網域區隔（MES / FDC / OA）**，並帶入申請人、員工編號、申請單號 |

---

## 一、核心觀念：藍圖不能單獨存在

一張藍圖只是一段 YAML，裡面寫「我要一台 `image=centos7`、`flavor=small` 的機器」。這些名字（`centos7`、`small`、網路、儲存）都是**對應（mapping）**，vRA 要先有底層基礎設施，藍圖才知道 `centos7` 是 vCenter 裡哪個 template。

所以順序永遠是：

```
Cloud Account (接 vCenter)
   └─> Cloud Zone (哪個 cluster 可以放機器)
         └─> Project (誰能用、配到哪個 zone)
Region 上再掛四種 Profile：
   ├─ Flavor Profile   : small / medium  -> vCPU / RAM
   ├─ Image  Profile   : centos7 / ubuntu -> vCenter VM template
   ├─ Network Profile  : 對應一個 port group（VM Network）
   └─ Storage Profile  : 對應一個 datastore（pcssd3）
最後才是：
   Blueprint (藍圖 YAML) ─部署→ Deployment ─vCenter clone→ VM
```

[`scripts/10-setup-infrastructure.sh`](scripts/10-setup-infrastructure.sh) 就是把上面這一整串用 IaaS API 一次建好。

---

## 二、藍圖 YAML 長怎樣

以 [`blueprints/centos7-small.yaml`](blueprints/centos7-small.yaml) 為例：

```yaml
formatVersion: 1
inputs: {}                       # 沒有輸入參數
resources:
  Cloud_Machine_1:
    type: Cloud.vSphere.Machine  # 一台 vSphere 虛擬機
    properties:
      image: centos7             # ← Image Mapping 的名字
      flavor: small              # ← Flavor Mapping 的名字
      networks:
        - network: '${resource.Cloud_Network_1.id}'
          assignment: static     # 由 vRA 從 IP range 配靜態 IP
  Cloud_Network_1:
    type: Cloud.vSphere.Network
    properties:
      networkType: existing      # 掛既有 port group
```

- `${resource.X.id}` / `${input.X}` 是 vRA 的綁定語法（跨資源、取輸入值）。
- `web-app.yaml` 示範 `inputs:` + `enum:` 下拉選單 + `image: '${input.osimage}'`。

---

## 三、怎麼透過 API 建藍圖 / 部署

所有 API 都要先拿 **bearer token**（兩段式，見 [`scripts/lib.sh`](scripts/lib.sh)）：

```
POST /csp/gateway/am/api/login?access_token   {username,password,domain}  -> refresh_token
POST /iaas/api/login                          {refreshToken}              -> bearer token
```

拿到 token 後：

| 動作 | API |
|---|---|
| 建藍圖 | `POST /blueprint/api/blueprints` `{name, projectId, content:<YAML>}` |
| 改藍圖 | `PUT  /blueprint/api/blueprints/{id}`（注意是 PUT，不是 PATCH）|
| 部署 | `POST /blueprint/api/blueprint-requests` `{blueprintId, deploymentName, projectId, inputs}` |
| 查部署 | `GET  /deployment/api/deployments/{id}` |
| 取 inputs schema（前端畫表單） | `GET  /blueprint/api/blueprints/{id}/inputs-schema` |
| 只驗證不開機（dry-run） | 部署 body 加 `"plan": true` |
| 不存藍圖、直接送 YAML | 部署 body 把 `blueprintId` 換成 `content` |
| 讀回自訂 attributes | `GET  /deployment/api/deployments/{did}/resources/{rid}` |
| 刪除部署 | `DELETE /deployment/api/deployments/{id}` |

> 前端串接的完整流程、每支端點的實測 request / response，見 **[docs/api-runbook.md](docs/api-runbook.md)**。

---

## 三之二、自助式申請入口（portal/）

[`portal/`](portal/) 是一支 Python（FastAPI）網頁，讓使用者以 AD 帳號登入後自助申請 VM，
背後就是呼叫本 repo 的 `vm-selfservice` 藍圖。

- [`portal/USER-STORIES.md`](portal/USER-STORIES.md) — 22 則 user story（開發依據）
- [`portal/FIELD-MAPPING.md`](portal/FIELD-MAPPING.md) — **表單 / 藍圖 / vRA / vCenter 四方欄位對應**，改欄位先看這份
- [`portal/README.md`](portal/README.md) — 架構、安全設計、AVI 負載平衡設定

三個重點：

1. **vRA 端刻意做薄** —— 選單來自 vRA 的 mapping 與 tag，新增叢集 / 規格 / OS / 網域都不用改藍圖。
2. **無狀態** —— session 是簽章 cookie、機器清單直接查 vRA，所以可以多節點掛在 AVI 後面，不需要 sticky session 也不需要資料庫。
3. **vCenter 自訂屬性要自己寫** —— vRA 的自訂屬性不會同步到 vCenter（實測 `customValue` 是空的），由 portal 在部署成功後補寫。

---
## 四、快速開始

```bash
cd scripts
cp env.example.sh env.sh      # 填入你的 vRA / vCenter 連線與命名
source env.sh

./10-setup-infrastructure.sh  # 建 cloud account / project / profiles / IP range
node 20-create-blueprints.js  # 把 ../blueprints/*.yaml 建成藍圖
./30-deploy.sh centos7-small my-vm-01   # 部署一台並等到好
```

> `env.sh` 內含密碼，已被 `.gitignore` 排除，**不會**被 commit。

---

## 五、踩過的坑（很重要）

1. **Image mapping 的 fabric image ID 是長 hash**（例：`666bc14d...`），不是短數字。用程式解析要**依 `name` 找 `content[].id`**，別抓到記錄裡第一個看到的 `id`（那可能是網路或其他物件的 id）。mapping 建錯會空掉，部署時報 `Cannot find matching image mappings for image: centos7`。

2. **老 template 常常 clone 後 DHCP 不起來**（例如 CentOS 7 template 的 `ifcfg-eth0` 綁死舊 HWADDR）。這時「等 DHCP 拿 IP」會一直卡住、部署失敗。解法就是本 repo 用的 **`assignment: static` + 在 Network 上建 IP range**，讓 vRA 直接透過 guest customization 把 IP 推進去（機器要有 VMware Tools）。

3. **藍圖更新用 `PUT` 不是 `PATCH`**（PATCH 會回 405）。

4. **頂層 `metadata:` 需要 `formatVersion: 2`**。用 `formatVersion: 1` 會被判 invalid，訊息是
   `Blueprint format version should be at least 2 to support metadata`。

5. 🔴 **`?expand=resources` 是摘要視圖，看不到自訂 properties**。它只回 `address` / `powerState` /
   `resourceName`。要拿藍圖裡寫的自訂 attributes，得打單一 resource 端點
   `GET /deployment/api/deployments/{did}/resources/{rid}`，
   或 `GET /iaas/api/machines` 看 `customProperties`。

6. 🔴 **`metadata:` 任何 API 都查不到**（blueprint / versions / catalog items 都沒有這個欄位），
   只能自己抓 `content` 再 parse YAML。所以：要給平台 / 前端消費的資訊放 `resources.*.properties`；
   要能 **filter 查詢** 的維度放 `tags`
   （`/iaas/api/machines?$filter=tags.item.key eq 'app' and tags.item.value eq 'web'` 實測可用）。

7. 🔴 **藍圖裡不能直接指定叢集名稱**。要讓使用者選叢集，得先替 Cloud Zone 內的 compute
   （cluster / resource pool）打 tag，藍圖再用 constraint 比對：

   ```
   PATCH /iaas/api/fabric-computes/{id}   {"tags":[{"key":"cluster","value":"linux-test"}]}
   ```
   ```yaml
   constraints:
     - tag: cluster:${input.cluster}
   ```

   沒打 tag 就會停在放置階段：`No placement exists that satisfies all of the request requirements.`

8. **使用者指定 IP** 用 `networks[].address` 搭配 `assignment: static`；IP 必須落在該 fabric
   network 已建立的 IP range 內、且尚未被配發，template 也要有 VMware Tools（靠 guest
   customization 推進去）。輸入欄位可加 `pattern`，會一併帶進 `inputs-schema` 給前端擋格式。

9. **下拉選單用 `oneOf`**（`title` 顯示、`const` 送出值），`inputs-schema` 會原樣回傳，
   前端不用自己 parse YAML。

---

## 六、實測結果

`centos7-small` 藍圖部署 → `CREATE_SUCCESSFUL`，VM 自動拿到靜態 IP、ping 得到。整套（cloud account → profiles → 藍圖 → VM）皆由 `scripts/` 的腳本自動完成。

`vm-metadata-attributes` 藍圖（2026-09-03）→ `CREATE_SUCCESSFUL`。帶入 `os=ubuntu2004temp` / `cluster=vra-pool` / `ipAddress=10.0.0.213` 部署後：VM 取得**指定的 10.0.0.213**、落在**指定的 `Cluster / VRA`**（不帶 constraint 時會落在 `Cluster / Linux TestTools`，確認 tag constraint 有生效）、ping 通；自訂 attributes 與 tags 都能經由 API 讀回、tags 可用 `$filter` 查詢。
