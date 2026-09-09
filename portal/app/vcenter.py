"""把申請資訊寫成 vCenter 上的**自訂屬性（Custom Attributes）**。

為什麼需要這一層：
vRA 藍圖裡寫的自訂欄位（requester / employeeId / ticket …）只會存在 vRA 自己的
custom properties 裡，**不會**自動出現在 vCenter 的自訂屬性上 —— 已實測確認：
由藍圖部署出來的 VM，vCenter 端 `customValue` 是空的。
所以要有人主動去寫，這支模組就是做這件事。

放在 portal 這一層的理由：vRA 端保持最薄，不必為了這件事導入 ABX / vRO。
⚠️ 代價：**只有透過本入口申請的機器才會有自訂屬性**。
   若有人直接在 vRA 部署藍圖，就不會經過這裡 —— 那種情況要改用 vRA 的
   ABX action（訂閱 post-provisioning 事件）寫，才不受入口影響。

自訂屬性是 SOAP 介面（CustomFieldsManager），vSphere REST API 沒有對應端點，
因此這裡用 pyVmomi 而不是 httpx。
"""
from __future__ import annotations

import json
import logging
import ssl
from typing import Any

from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim

from .config import settings

log = logging.getLogger(__name__)

# portal 欄位 -> vCenter 自訂屬性名稱。用 .env 的 VC_FIELD_MAP 覆寫成客戶實際的欄位名。
_DEFAULT_MAP = {
    "requester": "customOwner",
    "employeeId": "customEmployeeId",
    "ticket": "customTicket",
    "purpose": "customPurpose",
    "purposeNote": "customNote",
}


def field_map() -> dict[str, str]:
    if not settings.vc_field_map:
        return dict(_DEFAULT_MAP)
    try:
        return json.loads(settings.vc_field_map)
    except json.JSONDecodeError:
        log.warning("VC_FIELD_MAP 不是合法 JSON，改用預設對應")
        return dict(_DEFAULT_MAP)


def _connect():
    ctx = None
    if not settings.vc_verify_tls:
        ctx = ssl._create_unverified_context()
    return SmartConnect(
        host=settings.vc_host,
        user=settings.vc_username,
        pwd=settings.vc_password,
        sslContext=ctx,
    )


def apply_custom_attributes(moref: str, values: dict[str, Any]) -> dict[str, str]:
    """把 values 寫到 moref 指向的 VM 上，回傳實際寫入的欄位。

    冪等：重複呼叫只是覆寫成相同的值，所以可以在每次查詢狀態時安全地重跑。
    寫入失敗不應該讓整個申請流程失敗 —— 呼叫端只記錄、不中斷。
    """
    if not moref or not settings.vc_host:
        return {}

    mapping = field_map()
    payload = {
        mapping[k]: str(v)
        for k, v in values.items()
        if k in mapping and v not in (None, "")
    }
    if not payload:
        return {}

    si = None
    try:
        si = _connect()
        content = si.RetrieveContent()
        cfm = content.customFieldsManager
        if cfm is None:
            log.warning("這個 vCenter 沒有 customFieldsManager")
            return {}

        existing = {f.name: f.key for f in (cfm.field or []) if f.managedObjectType in (None, vim.VirtualMachine)}
        vm = _vm_by_moref(content, moref)
        if vm is None:
            log.warning("找不到 VM %s", moref)
            return {}

        written: dict[str, str] = {}
        for name, value in payload.items():
            if name not in existing:
                if not settings.vc_create_fields:
                    # 欄位不存在又不允許自動建立時跳過，不要讓整批失敗
                    log.warning("vCenter 沒有自訂屬性欄位 %s，已略過", name)
                    continue
                field = cfm.AddCustomFieldDef(name=name, moType=vim.VirtualMachine)
                existing[name] = field.key
            cfm.SetField(entity=vm, key=existing[name], value=value)
            written[name] = value
        return written
    except Exception as exc:  # noqa: BLE001 - 寫自訂屬性失敗不該影響申請流程
        log.warning("寫入 vCenter 自訂屬性失敗：%s", exc)
        return {}
    finally:
        if si is not None:
            try:
                Disconnect(si)
            except Exception:  # noqa: BLE001
                pass


def _vm_by_moref(content, moref: str):
    """moref 形如 'VirtualMachine:vm-17717'，直接組出受管物件，不用整個 inventory 掃一遍。"""
    value = moref.split(":", 1)[1] if ":" in moref else moref
    vm = vim.VirtualMachine(value, content._stub)  # noqa: SLF001 - pyVmomi 慣用作法
    try:
        _ = vm.name  # 觸發一次讀取，確認這個 moref 真的存在
    except vim.fault.ManagedObjectNotFound:
        return None
    return vm
