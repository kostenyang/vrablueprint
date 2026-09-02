# vRA 8 Blueprint API — 前端串接 Runbook

> 全部端點都在 **vRA 8.18.1** 實測過（2026-09-02），下面的 request/response 都是實跑結果。
> `$VRA` = vRA 的 base URL。憑證是自簽的，前端/後端要嘛匯入 CA、要嘛開 skip verify。

---

## 0. 認證（兩步，缺一不可）

```bash
# Step 1: 拿 refresh_token
curl -sk -X POST "$VRA/csp/gateway/am/api/login?access_token" \
  -H 'Content-Type: application/json' \
  -d '{"username":"<user>","password":"<pass>","domain":"System Domain"}'
# -> {"refresh_token":"..."}

# Step 2: 換 bearer token
curl -sk -X POST "$VRA/iaas/api/login" \
  -H 'Content-Type: application/json' \
  -d '{"refreshToken":"<refresh_token>"}'
# -> {"token":"eyJ..."}
```

之後所有呼叫都帶 `Authorization: Bearer <token>`。

> ⚠️ **token 會過期**（長時間輪詢要重新換）。refresh_token 生命週期較長，前端建議只存 refresh_token、
> 每次操作前換一次 bearer。**不要為了確認服務活著猛打 login**，帳號有 lockout。

---

## 1. 建立 / 更新 Blueprint

```bash
# 建立
curl -sk -X POST "$VRA/blueprint/api/blueprints" -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' \
  -d '{"name":"my-bp","description":"...","projectId":"<projectId>","content":"<YAML 字串>"}'
```

回應重點欄位：

```json
{ "id":"...", "status":"DRAFT", "valid":true, "validationMessages":[] }
```

**`valid` / `validationMessages` 就是前端要顯示的驗證結果。** 語法有問題時會像這樣：

```json
{ "valid": false,
  "validationMessages":[
    {"resourceName":"Cloud_vSphere_Machine_1",
     "path":"$.resources.Cloud_vSphere_Machine_1",
     "message":"Resource type Cloud.vSphere.MachineTypo not found"}]}
```

- **更新用 `PUT`**（`PATCH` 會回 405），body 一樣帶 `{name, projectId, content}`。
- 讀回：`GET /blueprint/api/blueprints/{id}` → `content` 是原封不動的 YAML 字串。

---

## 2. 取 inputs schema（前端畫表單用這個）

```bash
curl -sk "$VRA/blueprint/api/blueprints/{id}/inputs-schema" -H "Authorization: Bearer $TOK"
```

直接回一份 JSON Schema，`title` / `description` / `default` / `enum` 都在裡面，前端可以直接渲染：

```json
{ "type":"object","required":[],
  "properties":{
    "cpuCount":{"type":"integer","title":"CPU Count",
                "description":"Number of virtual processors","default":2,"enum":[1,2,4,8]},
    "totalMemoryMB":{"type":"integer","title":"Memory in MB","default":4096,
                     "enum":[1024,4096,8192,16384]},
    "template":{"type":"string","title":"VM Template","default":"ubuntu2004temp",
                "enum":["ubuntu2004temp"]}}}
```

（注意端點是 `/inputs-schema`，`/inputs` 是 404。）

---

## 3. 部署

```bash
curl -sk -X POST "$VRA/blueprint/api/blueprint-requests" -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' \
  -d '{"blueprintId":"<bpId>","deploymentName":"demo-01","projectId":"<projectId>",
       "inputs":{"cpuCount":2,"totalMemoryMB":4096,"template":"ubuntu2004temp"}}'
# -> {"id":"<requestId>","deploymentId":"<deploymentId>", ...}
```

兩個對前端很有用的變化：

| 需求 | 做法 |
|---|---|
| **不想先存 blueprint**，前端自己組 YAML 直接送 | 把 `blueprintId` 換成 `"content":"<YAML>"`，其餘一樣（實測 OK） |
| **只驗證不真的開機**（dry-run） | body 加 `"plan": true`，回傳 `status:"FINISHED"` 但不會建 VM |

---

## 4. 查部署狀態（輪詢）

```bash
curl -sk "$VRA/deployment/api/deployments/{deploymentId}" -H "Authorization: Bearer $TOK"
```

`status` 會走 `CREATE_INPROGRESS` → `CREATE_SUCCESSFUL` / `CREATE_FAILED`。
失敗原因不在這支，要另外打：

```bash
curl -sk "$VRA/deployment/api/deployments/{deploymentId}/requests" -H "Authorization: Bearer $TOK"
# -> content[].details 就是人看得懂的錯誤訊息
```

實例：`"No placement exists that satisfies all of the request requirements. ...
Allocation filter error: The storage requirements for hosts selection are not satisfied."`

---

## 5. 🔴 讀回自訂 attributes（前端最容易踩的坑）

寫在 blueprint `resources.<x>.properties` 底下的自訂 key（`osFamily` / `appName` / `appEnvironment` …），
部署後**讀得回來，但不是每支 API 都看得到**：

| 端點 | 看得到自訂 key？ |
|---|---|
| `GET /deployment/api/deployments/{id}?expand=resources` | ❌ **只回摘要** `address` / `powerState` / `resourceName` |
| `GET /deployment/api/deployments/{id}/resources/{resourceId}` | ✅ 完整 properties |
| `GET /iaas/api/machines` → `customProperties` | ✅ |

前端要拿 attributes，**打單一 resource 端點**，不要用 `?expand=resources` 那個摘要視圖。

實測回傳（節錄）：

```json
{ "properties": {
    "osFamily":"Linux", "osDistribution":"Ubuntu", "osVersion":"20.04",
    "appType":"Web", "appName":"Test Application", "appEnvironment":"Development",
    "characteristics":"Linux,Ubuntu,Web",
    "tags":[{"key":"os","value":"linux"},{"key":"env","value":"development"},
            {"key":"app","value":"web"}],
    "address":"10.0.0.212", "datastoreName":"pcssd3", "imageRef":"ubuntu2004temp",
    "cpuCount":2, "totalMemoryMB":4096, "moref":"VirtualMachine:vm-17717" }}
```

### tags 可以直接用 filter 查（自訂 properties 不行）

```bash
curl -sk -G "$VRA/iaas/api/machines" -H "Authorization: Bearer $TOK" \
  --data-urlencode "\$filter=tags.item.key eq 'app' and tags.item.value eq 'web'"
# -> totalElements: 1
```

**所以資訊要分三層放：**

| 放哪裡 | 平台可讀性 | 適合放什麼 |
|---|---|---|
| 頂層 `metadata:` | ❌ 只存在 blueprint `content` 字串裡，任何 API 都不會單獨吐出來 | 文件性質、給人看或外部 pipeline 自己 parse YAML |
| `resources.*.properties` 自訂 key | ✅ 部署後查得到（但不能 filter） | 要跟著 VM 走的描述性欄位 |
| `resources.*.properties.tags` | ✅ 查得到 **而且可以 filter / 搜尋** | 前端要拿來分類、篩選、做清單的維度 |

---

## 6. 刪除部署

```bash
curl -sk -X DELETE "$VRA/deployment/api/deployments/{deploymentId}" -H "Authorization: Bearer $TOK"
# -> {"id":"<requestId>","status":"PENDING"}
```

非同步，一樣回 requestId 去輪詢。

---

## 7. 前端典型流程

```
login (2 步) 
  -> GET /blueprint/api/blueprints?$filter=projectId eq '<pid>'    列出可用藍圖
  -> GET /blueprint/api/blueprints/{id}/inputs-schema              渲染表單
  -> POST /blueprint/api/blueprint-requests  (plan:true)           送出前先驗證
  -> POST /blueprint/api/blueprint-requests                        真的部署
  -> poll GET /deployment/api/deployments/{did}                    進度條
  -> GET /deployment/api/deployments/{did}/resources/{rid}         拿 IP + attributes
  -> DELETE /deployment/api/deployments/{did}                      回收
```
