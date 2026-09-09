"""vRA (VMware Aria Automation 8.x) 用戶端。

🔴 這一層是唯一持有 vRA 憑證的地方。vRA 的 bearer token 是**全權限**
   （實測有效期 8 小時），絕不可以傳到瀏覽器 —— 對應 US-21。

所有端點都已在 vRA 8.18.1 實測，細節見 vrablueprint repo 的 docs/api-runbook.md。
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from .config import settings

# bearer 實測 8 小時，提早 30 分鐘換，避免長流程中途過期
_TOKEN_TTL = 8 * 3600
_TOKEN_SKEW = 30 * 60


class VraError(RuntimeError):
    """把 vRA 回來的錯誤包成可以直接顯示給使用者的訊息（US-10）。"""


class VraClient:
    def __init__(self) -> None:
        self._token: str | None = None
        self._token_at: float = 0.0

    # ------------------------------------------------------------------ auth
    async def _login(self, client: httpx.AsyncClient) -> str:
        """兩段式登入：refresh_token -> bearer。

        不要每個 request 都重跑一次 —— vRA 帳號有 lockout，狂打會被鎖。
        """
        r = await client.post(
            f"{settings.vra_url}/csp/gateway/am/api/login?access_token",
            json={
                "username": settings.vra_username,
                "password": settings.vra_password,
                "domain": settings.vra_domain,
            },
        )
        r.raise_for_status()
        refresh = r.json()["refresh_token"]

        r = await client.post(f"{settings.vra_url}/iaas/api/login", json={"refreshToken": refresh})
        r.raise_for_status()
        return r.json()["token"]

    async def _headers(self, client: httpx.AsyncClient) -> dict[str, str]:
        if self._token is None or time.time() - self._token_at > _TOKEN_TTL - _TOKEN_SKEW:
            self._token = await self._login(client)
            self._token_at = time.time()
        return {"Authorization": f"Bearer {self._token}"}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(verify=settings.vra_verify_tls, timeout=60.0)

    async def _get(self, path: str, **params: Any) -> Any:
        async with self._client() as c:
            r = await c.get(f"{settings.vra_url}{path}", headers=await self._headers(c), params=params)
            if r.status_code >= 400:
                raise VraError(_message(r))
            return r.json()

    async def _post(self, path: str, payload: dict) -> Any:
        async with self._client() as c:
            r = await c.post(f"{settings.vra_url}{path}", headers=await self._headers(c), json=payload)
            if r.status_code >= 400:
                raise VraError(_message(r))
            return r.json()

    async def _delete(self, path: str) -> Any:
        async with self._client() as c:
            r = await c.delete(f"{settings.vra_url}{path}", headers=await self._headers(c))
            if r.status_code >= 400:
                raise VraError(_message(r))
            return r.json() if r.content else {}

    # --------------------------------------------------------------- catalog
    async def catalog(self) -> dict[str, list[dict[str, str]]]:
        """選單一律從 vRA 現況推導，不在程式裡寫死。

        對應 US-16：平台管理員在 vRA 加一筆 mapping 或打一個 tag 就會多一個選項，
        既不用改藍圖、也不用改這支程式。
        """
        sizes: list[dict[str, str]] = []
        for profile in (await self._get("/iaas/api/flavor-profiles")).get("content", []):
            mapping = profile.get("flavorMappings", {}).get("mapping", {})
            for name, spec in mapping.items():
                cpu, mem = spec.get("cpuCount"), spec.get("memoryInMB")
                # 規格直接寫進標籤，讓使用者看得到自己選的是什麼（US-05）
                label = f"{name} ({cpu} vCPU / {mem // 1024} GB)" if cpu and mem else name
                sizes.append({"value": name, "label": label})

        oses: list[dict[str, str]] = []
        for profile in (await self._get("/iaas/api/image-profiles")).get("content", []):
            for name in profile.get("imageMappings", {}).get("mapping", {}):
                oses.append({"value": name, "label": name})

        clusters = await self._tag_values("/iaas/api/fabric-computes", "cluster")
        zones = await self._tag_values("/iaas/api/fabric-networks", "zone")

        return {
            "sizes": _dedupe(sizes),
            "os": _dedupe(oses),
            "clusters": clusters,
            "zones": zones,
        }

    async def _tag_values(self, path: str, key: str) -> list[dict[str, str]]:
        """把 fabric 物件上 key=<key> 的 tag 值收集起來當選單。"""
        data = await self._get(path, **{"$top": 500})
        values: set[str] = set()
        for item in data.get("content", []):
            for tag in item.get("tags", []) or []:
                if tag.get("key") == key and tag.get("value"):
                    values.add(tag["value"])
        return [{"value": v, "label": v} for v in sorted(values)]

    # ---------------------------------------------------------------- deploy
    async def deploy(self, *, deployment_name: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """送出部署。inputs 由呼叫端組好，requester 一定是 session 帶的（US-03）。"""
        return await self._post(
            "/blueprint/api/blueprint-requests",
            {
                "blueprintId": settings.vra_blueprint_id,
                "deploymentName": deployment_name,
                "projectId": settings.vra_project_id,
                "inputs": inputs,
            },
        )

    async def validate(self, *, deployment_name: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """plan=true 的 dry-run：驗證但不會真的開機。"""
        return await self._post(
            "/blueprint/api/blueprint-requests",
            {
                "blueprintId": settings.vra_blueprint_id,
                "deploymentName": deployment_name,
                "projectId": settings.vra_project_id,
                "inputs": inputs,
                "plan": True,
            },
        )

    # ---------------------------------------------------------------- status
    async def deployment_detail(self, deployment_id: str) -> dict[str, Any]:
        """回傳狀態 + 機器資訊（IP / 規格 / 自訂屬性）。

        ⚠️ 不要用 ?expand=resources 拿屬性 —— 那是摘要視圖，只有
           address / powerState / resourceName，看不到自訂欄位。
           要打單一 resource 端點才拿得到完整 properties。
        """
        dep = await self._get(f"/deployment/api/deployments/{deployment_id}", expand="resources")
        machines = []
        for res in dep.get("resources", []):
            if res.get("type") != "Cloud.vSphere.Machine":
                continue
            full = await self._get(f"/deployment/api/deployments/{deployment_id}/resources/{res['id']}")
            machines.append(_machine_view(full.get("properties", {})))

        detail = {
            "id": dep.get("id"),
            "name": dep.get("name"),
            "status": dep.get("status"),
            "createdAt": dep.get("createdAt"),
            "machines": machines,
            "message": None,
        }
        # 失敗原因不在 deployment 本身，要另外打 requests 端點（US-10）
        if str(dep.get("status", "")).endswith("FAILED"):
            detail["message"] = await self.failure_reason(deployment_id)
        return detail

    async def failure_reason(self, deployment_id: str) -> str | None:
        try:
            data = await self._get(f"/deployment/api/deployments/{deployment_id}/requests")
        except VraError:
            return None
        for req in data.get("content", []):
            if req.get("details"):
                return " ".join(req["details"].split())
        return None

    # ----------------------------------------------------------- my machines
    async def machines_of(self, requester: str) -> list[dict[str, Any]]:
        """US-14「我的機器」。

        vRA 端所有部署都掛在同一個服務帳號底下，所以「誰申請的」要靠 tag 記。
        實測 tag 值可以放 UPN，而且 $filter 查得到 —— 因此不需要中介層自己維護
        資料庫，應用可以維持完全無狀態（US-20，AVI 負載平衡的前提）。
        """
        return await self.machines_by_tag("requester", requester)

    async def machines_by_ticket(self, ticket: str) -> list[dict[str, Any]]:
        """US-17 稽核：這張單開了哪些機器。"""
        return await self.machines_by_tag("ticket", ticket)

    async def machines_by_tag(self, key: str, value: str) -> list[dict[str, Any]]:
        """依 tag 查機器。tags 是 vRA 唯一支援 $filter 的一層（實測）。"""
        flt = f"tags.item.key eq '{key}' and tags.item.value eq '{value}'"
        data = await self._get("/iaas/api/machines", **{"$filter": flt})
        return [
            {
                "id": m.get("id"),
                "name": m.get("name"),
                "address": m.get("address"),
                "powerState": m.get("powerState"),
                "tags": {t["key"]: t["value"] for t in (m.get("tags") or [])},
                "customProperties": _public_props(m.get("customProperties") or {}),
            }
            for m in data.get("content", [])
        ]

    async def destroy(self, deployment_id: str) -> dict[str, Any]:
        return await self._delete(f"/deployment/api/deployments/{deployment_id}")

    # ---------------------------------------------------------------- health
    async def ping(self) -> bool:
        """給 /healthz 用：確認這個節點真的連得到 vRA（US-22）。"""
        try:
            await self._get("/iaas/api/about")
            return True
        except Exception:
            return False


# --------------------------------------------------------------------- utils
def _public_props(props: dict[str, Any]) -> dict[str, Any]:
    """只回申請相關的欄位，不要把 vRA 的內部屬性整包吐給前端。"""
    keep = ("requester", "employeeId", "ticket", "purpose", "purposeNote", "osType", "datastoreName")
    return {k: v for k, v in props.items() if k in keep}


def _machine_view(props: dict[str, Any]) -> dict[str, Any]:
    nets = props.get("networks") or []
    return {
        "name": props.get("resourceName"),
        # moref 是回頭寫 vCenter 自訂屬性時定位 VM 用的（例：VirtualMachine:vm-17717）
        "moref": props.get("moref"),
        "address": props.get("address") or (nets[0].get("address") if nets else None),
        "network": nets[0].get("name") if nets else None,
        "cpuCount": props.get("cpuCount"),
        "totalMemoryMB": props.get("totalMemoryMB"),
        "zone": props.get("zone"),
        "requester": props.get("requester"),
        "employeeId": props.get("employeeId"),
        "ticket": props.get("ticket"),
        "purpose": props.get("purpose"),
        "purposeNote": props.get("purposeNote"),
        "tags": {t["key"]: t["value"] for t in (props.get("tags") or [])},
    }


def _dedupe(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for item in items:
        if item["value"] not in seen:
            seen.add(item["value"])
            out.append(item)
    return out


def _message(r: httpx.Response) -> str:
    """把 vRA 的錯誤挖成一句人看得懂的話。"""
    try:
        body = r.json()
    except Exception:
        return f"vRA 回應 HTTP {r.status_code}"
    for key in ("message", "errorMessage", "details"):
        if isinstance(body, dict) and body.get(key):
            return str(body[key])
    return f"vRA 回應 HTTP {r.status_code}"


vra = VraClient()
