"""AD 登入（LDAP bind）＋ 無狀態 session。

兩個關鍵設計：

US-03  申請人身分只認 session，前端傳什麼都不算數 —— 避免冒名申請。
US-20  session 不存在伺服器記憶體，而是簽章過的 JWT 放在 HttpOnly cookie。
       因此任一節點都能處理任何 request，掛在 AVI 後面不需要 sticky session，
       節點掛掉使用者也不會被登出。
       🔴 前提：所有節點的 SESSION_SECRET 必須相同。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import jwt
from fastapi import HTTPException, Request, status
from ldap3 import ALL, SIMPLE, Connection, Server
from ldap3.core.exceptions import LDAPException

from .config import settings

COOKIE_NAME = "vmportal_session"
_ALG = "HS256"


@dataclass
class User:
    upn: str          # kosten.yang@vsmc.local
    display_name: str
    department: str
    email: str
    employee_id: str  # 從 AD 讀，不讓使用者自己填（US-02/US-03）


class AuthError(Exception):
    """帳密錯誤或 AD 連不上，訊息可直接顯示在登入頁。"""


# ------------------------------------------------------------------ AD bind
def authenticate(username: str, password: str) -> User:
    """對 AD 做 simple bind 驗證，順便讀回姓名 / 部門 / Email（US-02）。

    帳號可輸入 sAMAccountName 或完整 UPN，統一補成 UPN 再 bind。
    """
    if not password:
        # AD 的 unauthenticated bind 會回成功，必須自己擋掉空密碼
        raise AuthError("請輸入密碼")

    upn = username if "@" in username else f"{username}{settings.ldap_upn_suffix}"
    server = Server(settings.ldap_url, use_ssl=settings.ldap_use_ssl, get_info=ALL)

    try:
        with Connection(server, user=upn, password=password,
                        authentication=SIMPLE, auto_bind=True) as conn:
            attrs = _lookup(conn, upn)
    except LDAPException as exc:
        raise AuthError(_friendly(exc)) from exc

    return User(
        upn=upn,
        display_name=attrs.get("displayName") or upn.split("@")[0],
        department=attrs.get("department") or "",
        email=attrs.get("mail") or upn,
        # 不同 AD 架構用的欄位不一樣，兩個都試
        employee_id=attrs.get("employeeID") or attrs.get("employeeNumber") or "",
    )


# 各家 AD 放員工編號的欄位不一定相同，需要時可在這裡加
_USER_ATTRS = ("displayName", "department", "mail", "employeeID", "employeeNumber")


def _lookup(conn: Connection, upn: str) -> dict[str, str]:
    """讀使用者屬性。讀不到不算登入失敗 —— bind 成功身分就成立了。"""
    try:
        conn.search(
            search_base=settings.ldap_base_dn,
            search_filter=f"(userPrincipalName={upn})",
            attributes=list(_USER_ATTRS),
        )
        if conn.entries:
            entry = conn.entries[0]
            return {
                a: str(entry[a].value)
                for a in _USER_ATTRS
                if a in entry and entry[a].value
            }
    except LDAPException:
        pass
    return {}


def _friendly(exc: LDAPException) -> str:
    text = str(exc).lower()
    if "invalidcredentials" in text or "data 52e" in text:
        return "帳號或密碼錯誤"
    if "data 533" in text or "data 701" in text:
        return "帳號已停用或已過期"
    return "無法連線至 AD，請聯絡管理員"


# ------------------------------------------------------------ stateless session
def issue_token(user: User) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": user.upn,
        "name": user.display_name,
        "dept": user.department,
        "email": user.email,
        "eid": user.employee_id,
        "iat": now,
        "exp": now + dt.timedelta(minutes=settings.session_ttl_minutes),
    }
    return jwt.encode(payload, settings.session_secret, algorithm=_ALG)


def is_auditor(user: User) -> bool:
    """稽核端點才需要，一般申請流程用不到。

    目前用設定檔列 UPN；之後若要改成看 AD 群組（memberOf），只要換掉這一個函式。
    """
    allowed = {u.strip().lower() for u in settings.auditor_upns.split(",") if u.strip()}
    return user.upn.lower() in allowed


def current_user(request: Request) -> User:
    """FastAPI dependency：沒有有效 session 就擋下來。"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "尚未登入")
    try:
        claims = jwt.decode(token, settings.session_secret, algorithms=[_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登入已逾時，請重新登入") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登入資訊無效") from None

    return User(
        upn=claims["sub"],
        display_name=claims.get("name", ""),
        department=claims.get("dept", ""),
        email=claims.get("email", ""),
        employee_id=claims.get("eid", ""),
    )
