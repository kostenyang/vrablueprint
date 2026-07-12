# vRA (VMware Aria Automation 8.x) 藍圖範例與建置流程

這個 repo 收錄了在 **VMware Aria Automation 8.18（vRA）** 上，用 **API 全自動**建立「Cloud Template（藍圖 / Blueprint）」並實際部署 VM 的完整流程與範例。

三個藍圖（在 [`blueprints/`](blueprints/)）：

| 藍圖 | 說明 |
|---|---|
| `centos7-small` | 單台 CentOS 7、small（1 vCPU / 2 GB）、靜態 IP |
| `ubuntu-medium` | 單台 Ubuntu、medium（2 vCPU / 4 GB）、靜態 IP |
| `web-app` | 帶下拉選單（OS / 大小）的參數化範例，部署時才選 |

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
| 查部署 | `GET  /deployment/api/deployments/{id}?expand=resources` |

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

---

## 六、實測結果

`centos7-small` 藍圖部署 → `CREATE_SUCCESSFUL`，VM 自動拿到靜態 IP、ping 得到。整套（cloud account → profiles → 藍圖 → VM）皆由 `scripts/` 的腳本自動完成。
