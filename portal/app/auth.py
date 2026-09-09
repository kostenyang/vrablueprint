"""AD 登入（LDAP bind）＋ 無狀態 session。

三個關鍵設計：

1. 身分欄位只認 session。哪些欄位算「身分欄位」由 fields.json 決定
   （`source: ad`），前端傳什麼都不會被採用 —— 避免冒名申請與手打錯字。
2. session 不存在伺服器記憶體，而是簽章 JWT 放在 HttpOnly cookie。
   任一節點都能處理任何請求，掛在負載平衡器後面不需要 sticky session。
   🔴 前提：所有節點的 SESSION_SECRET 必須相同。
3. 要跟 AD 要哪些屬性，同樣由 fields.json 決定 —— 客戶要多帶一個 AD 欄位
   （例如廠區、成本中心），改設定檔即可，這支程式不用動。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field as dc_field

import jwt
from fastapi import HTTPException, Request, status
from ldap3 import ALL, SIMPLE, Connection, Server
from ldap3.core.exceptions import LDAPException

from .config import settings
from .fields import ad_attribute_names, ad_fields

COOKIE_NAME = "vmportal_session"
_ALG = "HS256"


@dataclass
class User:
    upn: str                # kosten.yang@vsmc.local
    display_name: str
    email: str
    # 依 fields.json 的 source=ad 欄位取回的值，key 是欄位 key
    attributes: dict[str, str] = dc_field(default_factory=dict)


class AuthError(Exception):
    """帳密錯誤或 AD 連不上，訊息可直接顯示在登入頁。"""


# ------------------------------------------------------------------ AD bind
def authenticate(username: str, password: str) -> User:
    """對 AD 做 simple bind 驗證，並取回 fields.json 指定的屬性。"""
    if not password:
        # AD 的 unauthenticated bind 會回成功，必須自己擋掉空密碼
        raise AuthError("請輸入密碼")

    upn = username if "@" in username else f"{username}{settings.ldap_upn_suffix}"
    server = Server(settings.ldap_url, use_ssl=settings.ldap_use_ssl, get_info=ALL)

    try:
        with Connection(server, user=upn, password=password,
                        authentication=SIMPLE, auto_bind=True) as conn:
            raw = _lookup(conn, upn)
    except LDAPException as exc:
        raise AuthError(_friendly(exc)) from exc

    # 把 AD 原始屬性轉成「欄位 key -> 值」
    attributes: dict[str, str] = {}
    for f in ad_fields():
        if f.ad_attribute == "userPrincipalName":
            attributes[f.key] = upn
            continue
        attributes[f.key] = raw.get(f.ad_attribute) or raw.get(f.ad_fallback) or ""

    return User(
        upn=upn,
        display_name=raw.get("displayName") or upn.split("@")[0],
        email=raw.get("mail") or upn,
        attributes=attributes,
    )


def _lookup(conn: Connection, upn: str) -> dict[str, str]:
    """讀使用者屬性。讀不到不算登入失敗 —— bind 成功身分就成立了。"""
    wanted = list(dict.fromkeys(("displayName", "mail", *ad_attribute_names())))
    try:
        conn.search(
            search_base=settings.ldap_base_dn,
            search_filter=f"(userPrincipalName={upn})",
            attributes=wanted,
        )
        if conn.entries:
            entry = conn.entries[0]
            return {a: str(entry[a].value) for a in wanted if a in entry and entry[a].value}
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


def is_auditor(user: User) -> bool:
    """稽核端點才需要。之後要改成看 AD 群組（memberOf），換掉這個函式即可。"""
    allowed = {u.strip().lower() for u in settings.auditor_upns.split(",") if u.strip()}
    return user.upn.lower() in allowed


# ------------------------------------------------------------ stateless session
def issue_token(user: User) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    return jwt.encode(
        {
            "sub": user.upn,
            "name": user.display_name,
            "email": user.email,
            "attrs": user.attributes,
            "iat": now,
            "exp": now + dt.timedelta(minutes=settings.session_ttl_minutes),
        },
        settings.session_secret,
        algorithm=_ALG,
    )


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
        email=claims.get("email", ""),
        attributes=dict(claims.get("attrs") or {}),
    )
