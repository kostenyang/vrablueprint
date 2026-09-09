"""VM 申請入口 — FastAPI 應用程式。

兩個貫穿全域的設計：

無狀態    不保存 session 或申請紀錄；狀態在 vRA / vCenter 或簽章 cookie 裡，
          因此可以多節點掛在負載平衡器後面（US-19 / US-20）。
設定驅動  「有哪些自訂屬性欄位」由 fields.json 決定。客戶要增減欄位時
          不需要改這支程式、不需要改網頁樣板、也不需要改 vRA 藍圖。
"""
from __future__ import annotations

import re
import time
import uuid

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import vcenter
from .auth import COOKIE_NAME, AuthError, User, authenticate, current_user, is_auditor, issue_token
from .config import settings
from .fields import FieldConfigError, ad_fields, all_fields, form_schema, validate
from .vra import VraError, vra

app = FastAPI(title="VM Request Portal", docs_url="/api/docs")
templates = Jinja2Templates(directory="templates")

_NAME_SAFE = re.compile(r"[^a-zA-Z0-9-]")


class VmRequest(BaseModel):
    """使用者送出的申請。

    決策欄位（os / size / cluster / zone）是固定的 —— 它們決定機器長什麼樣、放哪裡。
    描述性欄位全部放在 `fields` 裡，內容由 fields.json 定義，這支程式不寫死。
    身分欄位不在這裡：申請人與員工編號一律取自 session。
    """

    os: str = Field(min_length=1)
    size: str = Field(min_length=1)
    cluster: str = Field(min_length=1)
    zone: str = Field(min_length=1)
    fields: dict[str, str] = Field(default_factory=dict)


def _build_attributes(user: User, req: VmRequest) -> dict[str, str]:
    """組出要一路帶到 VM 上的自訂屬性。

    AD 來源的欄位取自 session（前端傳什麼都不採用），使用者填的欄位依
    fields.json 驗證後才收下。兩者合併成一個物件，整包丟給藍圖的 attributes。
    """
    attributes = {f.key: user.attributes.get(f.key, "") for f in ad_fields()}
    attributes.update(validate(req.fields))
    return {k: v for k, v in attributes.items() if v}


# --------------------------------------------------------------------- 頁面
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    try:
        user = authenticate(username, password)
    except AuthError as exc:
        return templates.TemplateResponse(
            request, "login.html", {"error": str(exc)}, status_code=status.HTTP_401_UNAUTHORIZED
        )

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        COOKIE_NAME,
        issue_token(user),
        httponly=True,          # 瀏覽器端的 JS 拿不到，降低 XSS 風險
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=settings.session_ttl_minutes * 60,
    )
    return response


@app.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME)
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    try:
        user = current_user(request)
    except HTTPException:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "index.html", {"user": user})


# ---------------------------------------------------------------------- API
@app.get("/api/form")
async def api_form(user: User = Depends(current_user)):
    """一次給前端畫整張表單需要的東西。

    decisions 來自 vRA 現況（加一個叢集不用改程式），
    fields 來自 fields.json（加一個自訂屬性不用改程式），
    identity 是唯讀的身分資訊，顯示用。
    """
    try:
        decisions = await vra.catalog()
    except VraError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return {
        "identity": {
            "displayName": user.display_name,
            "upn": user.upn,
            "attributes": [
                {"key": f.key, "label": f.label, "value": user.attributes.get(f.key, "")}
                for f in ad_fields()
            ],
        },
        "decisions": decisions,
        "fields": form_schema(),
    }


@app.post("/api/requests", status_code=status.HTTP_202_ACCEPTED)
async def api_create_request(req: VmRequest, user: User = Depends(current_user)):
    """送出申請。回 deploymentId，前端再輪詢狀態（部署是非同步的）。"""
    try:
        attributes = _build_attributes(user, req)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    name = _deployment_name(user.upn, attributes)
    inputs = {"os": req.os, "size": req.size, "cluster": req.cluster,
              "zone": req.zone, "attributes": attributes}
    try:
        result = await vra.deploy(deployment_name=name, inputs=inputs)
    except VraError as exc:
        # vRA 的訊息夠具體（例如「找不到符合 zone:fdc 的網段」），直接透出（US-10）
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return {"deploymentId": result.get("deploymentId"), "deploymentName": name}


@app.post("/api/requests/validate")
async def api_validate_request(req: VmRequest, user: User = Depends(current_user)):
    """送出前的 dry-run（plan=true），不會真的開機（US-09）。"""
    try:
        attributes = _build_attributes(user, req)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    inputs = {"os": req.os, "size": req.size, "cluster": req.cluster,
              "zone": req.zone, "attributes": attributes}
    try:
        result = await vra.validate(
            deployment_name=_deployment_name(user.upn, attributes), inputs=inputs
        )
    except VraError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"ok": result.get("status") == "FINISHED", "status": result.get("status")}


@app.get("/api/requests/{deployment_id}")
async def api_request_status(deployment_id: str, _: User = Depends(current_user)):
    """輪詢進度（US-11 / US-12）。失敗時 message 會帶 vRA 的具體原因。

    部署成功時順手把自訂屬性寫進 vCenter —— vRA 的屬性不會自己跑到 vCenter 上
    （已實測），要有人主動寫。寫入是冪等的，重複輪詢不會有副作用。
    """
    try:
        detail = await vra.deployment_detail(deployment_id)
    except VraError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    if detail.get("status") == "CREATE_SUCCESSFUL":
        for machine in detail.get("machines", []):
            # pyVmomi 是同步的，丟到執行緒池避免擋住 event loop
            machine["customAttributes"] = await run_in_threadpool(
                vcenter.apply_custom_attributes,
                machine.get("moref") or "",
                machine.get("attributes") or {},
            )
    return detail


@app.get("/api/my-vms")
async def api_my_vms(user: User = Depends(current_user)):
    """US-14「我的機器」。

    自訂屬性以 vCenter 為準，所以直接依 requester 欄位反查 vCenter，
    中介層不需要自己的資料庫，也就能維持無狀態。
    """
    machines = await run_in_threadpool(vcenter.find_by_attribute, "requester", user.upn)
    return {"machines": machines}


@app.get("/api/audit/{field_key}/{value}")
async def api_audit(field_key: str, value: str, user: User = Depends(current_user)):
    """US-17 稽核：依任一自訂屬性反查機器（例如單號、員工編號）。

    只有設定檔列出的稽核人員可以查別人的資料，一般使用者請走 /api/my-vms。
    """
    if not is_auditor(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "沒有稽核權限")
    if field_key not in {f.key for f in all_fields()}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"沒有這個欄位：{field_key}")
    machines = await run_in_threadpool(vcenter.find_by_attribute, field_key, value)
    return {"field": field_key, "value": value, "machines": machines}


@app.delete("/api/deployments/{deployment_id}")
async def api_destroy(deployment_id: str, user: User = Depends(current_user)):
    """回收機器。

    ⚠️ vRA 端所有部署都掛同一個服務帳號，所以「這台是不是你的」必須由這裡把關，
       不能靠 vRA 的權限模型。
    """
    detail = await vra.deployment_detail(deployment_id)
    owners = {(m.get("attributes") or {}).get("requester") for m in detail.get("machines", [])}
    if user.upn not in owners:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "這不是你申請的機器")
    try:
        return await vra.destroy(deployment_id)
    except VraError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


# ------------------------------------------------------------------- health
@app.get("/healthz")
async def healthz(response: Response):
    """給負載平衡器探測用（US-22）。後端連不上就回 503，讓該節點被移出輪替。"""
    vra_ok = await vra.ping()
    vc_ok = await run_in_threadpool(vcenter.ping)
    try:
        all_fields()
        fields_ok = True
    except FieldConfigError:
        fields_ok = False

    ok = vra_ok and vc_ok and fields_ok
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if ok else "degraded",
        "vra": vra_ok, "vcenter": vc_ok, "fields": fields_ok,
        "ts": int(time.time()),
    }


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    """API 一律回 JSON；頁面 401 導回登入頁。"""
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


def _deployment_name(upn: str, attributes: dict[str, str]) -> str:
    """部署名稱要能一眼看出是誰、哪張單，且必須唯一。

    單號欄位可能被客戶改名或拿掉，所以取不到就只用帳號。
    """
    who = _NAME_SAFE.sub("-", upn.split("@")[0]).lower()
    ticket = _NAME_SAFE.sub("-", attributes.get("ticket", "")).lower().strip("-")
    prefix = f"{ticket}-" if ticket else ""
    return f"{prefix}{who}-{uuid.uuid4().hex[:6]}"
