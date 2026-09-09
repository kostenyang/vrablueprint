"""把申請資訊寫成 vCenter 上的**自訂屬性（Custom Attributes）**，並用它反查機器。

為什麼需要這一層：
vRA 藍圖裡的自訂屬性只存在 vRA 自己的資料裡，**不會**自動出現在 vCenter 上
——已實測：由藍圖部署出來的 VM，vCenter 端 `customValue` 是空的。

寫哪些欄位由 fields.json 決定（`vcAttribute`），這支程式不需要知道欄位名稱。

放在入口這一層的理由：vRA 端維持最薄，不必為此導入 ABX / vRO。
⚠️ 代價：**只有透過本入口申請的機器才會有自訂屬性**。若要求不論從哪個入口
   部署都要有，就得改用 vRA 的 ABX action 訂閱 post-provisioning 事件。

自訂屬性是 SOAP 介面（CustomFieldsManager），vSphere REST API 沒有對應端點，
因此這裡用 pyVmomi。
"""
from __future__ import annotations

import logging
import ssl
from typing import Any

from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim, vmodl

from .config import settings
from .fields import vc_mapping

log = logging.getLogger(__name__)


def _connect():
    ctx = ssl._create_unverified_context() if not settings.vc_verify_tls else None
    return SmartConnect(
        host=settings.vc_host,
        user=settings.vc_username,
        pwd=settings.vc_password,
        sslContext=ctx,
    )


def enabled() -> bool:
    return bool(settings.vc_host)


# ------------------------------------------------------------------- 寫入
def apply_custom_attributes(moref: str, values: dict[str, Any]) -> dict[str, str]:
    """把 values 依 fields.json 的對應寫到 moref 指向的 VM 上。

    冪等：重複呼叫只是覆寫成相同的值，可以安全地在每次查詢狀態時重跑。
    寫入失敗不應該讓整個申請流程失敗 —— 只記錄，不中斷。
    """
    if not moref or not enabled():
        return {}

    mapping = vc_mapping()
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

        existing = _vm_fields(cfm)
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
                existing[name] = cfm.AddCustomFieldDef(name=name, moType=vim.VirtualMachine).key
            cfm.SetField(entity=vm, key=existing[name], value=value)
            written[name] = value
        return written
    except Exception as exc:  # noqa: BLE001 - 寫自訂屬性失敗不該影響申請流程
        log.warning("寫入 vCenter 自訂屬性失敗：%s", exc)
        return {}
    finally:
        _close(si)


# ------------------------------------------------------------------- 查詢
def find_by_attribute(field_key: str, value: str) -> list[dict[str, Any]]:
    """依自訂屬性反查機器 —— 「我的機器」與稽核查詢都走這裡。

    自訂屬性沒有伺服器端的篩選 API，所以用 PropertyCollector 一次撈回
    所有 VM 的 name / customValue / 電源 / IP 再在本地過濾。
    只取這幾個屬性，不會把整份 inventory 拉下來。
    """
    name = vc_mapping().get(field_key)
    if not name or not enabled() or not value:
        return []

    si = None
    try:
        si = _connect()
        content = si.RetrieveContent()
        key = _vm_fields(content.customFieldsManager).get(name)
        if key is None:
            log.warning("vCenter 沒有自訂屬性欄位 %s", name)
            return []

        rev = {v: k for k, v in _vm_fields(content.customFieldsManager).items()}
        out: list[dict[str, Any]] = []
        for props in _collect(content, ["name", "customValue", "runtime.powerState", "guest.ipAddress"]):
            cv = {rev.get(c.key): c.value for c in (props.get("customValue") or [])}
            if cv.get(name) != value:
                continue
            out.append({
                "name": props.get("name"),
                "address": props.get("guest.ipAddress"),
                "powerState": str(props.get("runtime.powerState") or ""),
                "attributes": {k: v for k, v in cv.items() if k},
            })
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("查詢 vCenter 自訂屬性失敗：%s", exc)
        return []
    finally:
        _close(si)


def ping() -> bool:
    """給 /healthz 用。未設定 vc_host 時視為「沒啟用」而非「壞掉」。"""
    if not enabled():
        return True
    si = None
    try:
        si = _connect()
        return si.RetrieveContent() is not None
    except Exception:  # noqa: BLE001
        return False
    finally:
        _close(si)


# --------------------------------------------------------------------- utils
def _vm_fields(cfm) -> dict[str, int]:
    """VM 適用的自訂屬性欄位：名稱 -> key。"""
    if cfm is None:
        return {}
    return {
        f.name: f.key
        for f in (cfm.field or [])
        if f.managedObjectType in (None, vim.VirtualMachine)
    }


def _vm_by_moref(content, moref: str):
    """moref 形如 'VirtualMachine:vm-17739'，直接組出受管物件，不用掃整個 inventory。"""
    value = moref.split(":", 1)[1] if ":" in moref else moref
    vm = vim.VirtualMachine(value, content._stub)  # noqa: SLF001 - pyVmomi 慣用作法
    try:
        _ = vm.name  # 觸發一次讀取，確認 moref 真的存在
    except vim.fault.ManagedObjectNotFound:
        return None
    return vm


def _collect(content, paths: list[str]):
    """PropertyCollector：一次取回所有 VM 的指定屬性。"""
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    try:
        spec = vmodl.query.PropertyCollector.FilterSpec(
            objectSet=[vmodl.query.PropertyCollector.ObjectSpec(
                obj=view,
                skip=False,
                selectSet=[vmodl.query.PropertyCollector.TraversalSpec(
                    name="view", type=vim.view.ContainerView, path="view", skip=False)],
            )],
            propSet=[vmodl.query.PropertyCollector.PropertySpec(
                type=vim.VirtualMachine, all=False, pathSet=paths)],
        )
        for obj in content.propertyCollector.RetrieveContents([spec]):
            yield {p.name: p.val for p in obj.propSet}
    finally:
        try:
            view.Destroy()
        except Exception:  # noqa: BLE001
            pass


def _close(si) -> None:
    if si is None:
        return
    try:
        Disconnect(si)
    except Exception:  # noqa: BLE001
        pass
