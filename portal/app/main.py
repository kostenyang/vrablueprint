"""VM 申請入口 — FastAPI 應用程式。

無狀態設計：這支程式不保存任何 session 或申請紀錄，
所有狀態都在 vRA 或簽章 cookie 裡，因此可以直接多開幾個節點掛在 AVI 後面（US-19/US-20）。
"""
from __future__ import annotations

import re
import time
import uuid

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.concurrency import run_in_threadpool
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .auth import (COOKIE_NAME, AuthError, User, authenticate, current_user,
                   is_auditor, issue_token)
from .config import settings
from .vcenter import apply_custom_attributes
from .vra import VraError, vra

app = FastAPI(title="VM Request Portal", docs_url="/api/docs")
templates = Jinja2Templates(directory="templates")

_NAME_SAFE = re.compile(r"[^a-zA-Z0-9-]")


class VmRequest(BaseModel):
    """使用者在表單上能決定的東西。

    注意這裡**沒有** requester、也沒有 employeeId ——
    申請人與員工編號一律由 session 決定（US-03），前端就算硬塞也不會被採用。
    單號則相反：它是既有 ITSM 工單的號碼，只有使用者知道，所以要填。
    """

    os: str = Field(min_length=1)
    size: str = Field(min_length=1)
    cluster: str = Field(min_length=1)
    zone: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    purpose_note: str = ""
    # 單號格式依客戶 ITSM 而定，這裡先只擋明顯錯誤（空白、奇怪字元、過長）
    ticket: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _build_inputs(user: User, req: VmRequest) -> dict[str, str]:
    """組藍圖 inputs。

    requester / employeeId 來自 session，ticket 來自表單 —— 三者都會一路帶到 VM 上，
    稽核時可以回答「這張單、這個人、開了哪些機器」（US-17）。
    """
    return {
        "requester": user.upn,            # ← 只認 session
        "employeeId": user.employee_id,   # ← 只認 session（AD 讀來的）
        "ticket": req.ticket,
        "purpose": req.purpose,
        "purposeNote": req.purpose_note,
        "os": req.os,
        "size": req.size,
        "cluster": req.cluster,
        "zone": req.zone,
    }


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
@app.get("/api/me")
async def api_me(user: User = Depends(current_user)):
    """前端拿來顯示「以 XXX 的身分申請」，申請人欄位不可編輯（US-02）。"""
    return {
        "upn": user.upn,
        "displayName": user.display_name,
        "department": user.department,
        "email": user.email,
        "employeeId": user.employee_id,
    }


@app.get("/api/catalog")
async def api_catalog(_: User = Depends(current_user)):
    """選單內容直接反映 vRA 現況，平台管理員加選項不用改前端（US-16）。"""
    try:
        return await vra.catalog()
    except VraError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@app.post("/api/requests", status_code=status.HTTP_202_ACCEPTED)
async def api_create_request(req: VmRequest, user: User = Depends(current_user)):
    """送出申請。回 deploymentId，前端再輪詢狀態（部署是非同步的）。"""
    name = _deployment_name(user.upn, req.ticket)
    try:
        result = await vra.deploy(deployment_name=name, inputs=_build_inputs(user, req))
    except VraError as exc:
        # vRA 的訊息夠具體（例如「找不到符合 zone:fdc 的網段」），直接透出（US-10）
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return {"deploymentId": result.get("deploymentId"), "deploymentName": name}


@app.post("/api/requests/validate")
async def api_validate_request(req: VmRequest, user: User = Depends(current_user)):
    """送出前的 dry-run（plan=true），不會真的開機（US-09）。"""
    try:
        result = await vra.validate(
            deployment_name=_deployment_name(user.upn, req.ticket),
            inputs=_build_inputs(user, req),
        )
    except VraError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"ok": result.get("status") == "FINISHED", "status": result.get("status")}


@app.get("/api/requests/{deployment_id}")
async def api_request_status(deployment_id: str, _: User = Depends(current_user)):
    """輪詢進度（US-11/US-12）。失敗時 message 會帶 vRA 的具體原因。

    部署成功時順手把申請資訊寫成 vCenter 的自訂屬性 —— vRA 的自訂欄位不會自己
    跑到 vCenter 上（已實測），要有人主動寫。寫入是冪等的，重複輪詢不會有副作用。
    """
    try:
        detail = await vra.deployment_detail(deployment_id)
    except VraError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    if detail.get("status") == "CREATE_SUCCESSFUL":
        for machine in detail.get("machines", []):
            # 在執行緒池裡跑：pyVmomi 是同步的，別擋住 event loop
            machine["customAttributes"] = await run_in_threadpool(
                apply_custom_attributes, machine.get("moref") or "", machine
            )
    return detail


@app.get("/api/my-vms")
async def api_my_vms(user: User = Depends(current_user)):
    """US-14：只回登入者自己申請的機器，靠 vRA 的 requester tag 查。"""
    try:
        return {"machines": await vra.machines_of(user.upn)}
    except VraError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@app.get("/api/audit/tickets/{ticket}")
async def api_audit_ticket(ticket: str, user: User = Depends(current_user)):
    """US-17 稽核：這張單開出了哪些機器。

    只有設定檔列出的稽核人員可以查別人的資料，一般使用者請走 /api/my-vms。
    """
    if not is_auditor(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "沒有稽核權限")
    try:
        return {"ticket": ticket, "machines": await vra.machines_by_ticket(ticket)}
    except VraError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@app.delete("/api/deployments/{deployment_id}")
async def api_destroy(deployment_id: str, user: User = Depends(current_user)):
    """回收機器。

    ⚠️ vRA 端所有部署都掛同一個服務帳號，所以「這台是不是你的」必須由這裡把關，
       不能靠 vRA 的權限模型。
    """
    detail = await vra.deployment_detail(deployment_id)
    owners = {m.get("requester") for m in detail.get("machines", [])}
    if user.upn not in owners:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "這不是你申請的機器")
    try:
        return await vra.destroy(deployment_id)
    except VraError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


# ------------------------------------------------------------------- health
@app.get("/healthz")
async def healthz(response: Response):
    """給 AVI 探測用（US-22）。

    vRA 連不上就回 503，讓負載平衡器把這個節點移出輪替。
    """
    ok = await vra.ping()
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "degraded", "vra": ok, "ts": int(time.time())}


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    """API 一律回 JSON；頁面 401 導回登入頁。"""
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


def _deployment_name(upn: str, ticket: str) -> str:
    """部署名稱要能一眼看出是哪張單、誰申請的，且必須唯一。

    同一張單可能申請多台，所以後面仍要補一段亂數。
    """
    who = _NAME_SAFE.sub("-", upn.split("@")[0]).lower()
    tkt = _NAME_SAFE.sub("-", ticket).lower()
    return f"{tkt}-{who}-{uuid.uuid4().hex[:6]}"
