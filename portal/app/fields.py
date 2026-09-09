"""自訂屬性欄位定義 —— 整個系統唯一決定「有哪些欄位」的地方。

客戶要增減欄位時只改 fields.json：
  - 藍圖不用改（屬性以單一 attributes 物件整包傳入 vRA）
  - 這支程式不用改
  - 網頁樣板不用改（表單依定義動態產生）

只有新增「決策維度」（例如未來要讓使用者選儲存等級）才需要動藍圖。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import settings


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    source: str                      # "ad" | "user"
    vc_attribute: str = ""
    ad_attribute: str = ""
    ad_fallback: str = ""
    type: str = "text"               # text | textarea | select
    required: bool = False
    placeholder: str = ""
    pattern: str = ""
    pattern_hint: str = ""
    max_length: int = 0
    options: tuple = dc_field(default_factory=tuple)

    @property
    def from_ad(self) -> bool:
        return self.source == "ad"


class FieldConfigError(RuntimeError):
    """設定檔寫錯時要在啟動階段就炸掉，不要拖到使用者送單才發現。"""


def _parse(raw: dict[str, Any]) -> list[Field]:
    out: list[Field] = []
    seen: set[str] = set()
    for i, f in enumerate(raw.get("fields", [])):
        key = f.get("key")
        if not key:
            raise FieldConfigError(f"第 {i + 1} 個欄位缺少 key")
        if key in seen:
            raise FieldConfigError(f"欄位 key 重複：{key}")
        seen.add(key)

        source = f.get("source", "user")
        if source not in ("ad", "user"):
            raise FieldConfigError(f"{key}: source 只能是 ad 或 user")

        ftype = f.get("type", "text")
        if source == "user" and ftype not in ("text", "textarea", "select"):
            raise FieldConfigError(f"{key}: type 只能是 text / textarea / select")
        if ftype == "select" and not f.get("options"):
            raise FieldConfigError(f"{key}: select 欄位必須提供 options")

        pattern = f.get("pattern", "")
        if pattern:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise FieldConfigError(f"{key}: pattern 不是合法的正規表示式（{exc}）") from exc

        options = tuple(
            (o if isinstance(o, dict) else {"value": o, "label": str(o)})
            for o in f.get("options", [])
        )

        out.append(Field(
            key=key,
            label=f.get("label", key),
            source=source,
            vc_attribute=f.get("vcAttribute", ""),
            ad_attribute=f.get("adAttribute", ""),
            ad_fallback=f.get("adFallback", ""),
            type=ftype,
            required=bool(f.get("required", False)),
            placeholder=f.get("placeholder", ""),
            pattern=pattern,
            pattern_hint=f.get("patternHint", ""),
            max_length=int(f.get("maxLength", 0) or 0),
            options=options,
        ))
    return out


@lru_cache(maxsize=1)
def all_fields() -> tuple[Field, ...]:
    path = Path(settings.fields_file)
    if not path.exists():
        raise FieldConfigError(f"找不到欄位定義檔：{path}")
    return tuple(_parse(json.loads(path.read_text(encoding="utf-8"))))


def user_fields() -> tuple[Field, ...]:
    """要顯示在表單上讓使用者填的欄位。"""
    return tuple(f for f in all_fields() if f.source == "user")


def ad_fields() -> tuple[Field, ...]:
    """由登入身分自動帶入的欄位 —— 使用者填不了，也就冒用不了。"""
    return tuple(f for f in all_fields() if f.source == "ad")


def ad_attribute_names() -> tuple[str, ...]:
    """登入時要跟 AD 要哪些屬性。"""
    names: list[str] = []
    for f in ad_fields():
        for n in (f.ad_attribute, f.ad_fallback):
            if n and n not in names:
                names.append(n)
    return tuple(names)


def vc_mapping() -> dict[str, str]:
    """欄位 key -> vCenter 自訂屬性名稱（沒填的不寫到 vCenter）。"""
    return {f.key: f.vc_attribute for f in all_fields() if f.vc_attribute}


def form_schema() -> list[dict[str, Any]]:
    """給前端畫表單用；只吐使用者要填的欄位。"""
    return [
        {
            "key": f.key,
            "label": f.label,
            "type": f.type,
            "required": f.required,
            "placeholder": f.placeholder,
            "pattern": f.pattern,
            "patternHint": f.pattern_hint,
            "maxLength": f.max_length,
            "options": [dict(o) for o in f.options],
        }
        for f in user_fields()
    ]


def validate(submitted: dict[str, Any]) -> dict[str, str]:
    """依設定檔驗證使用者填的值，回傳清理後的結果。

    錯誤訊息帶欄位中文名稱，前端可以直接顯示。
    """
    cleaned: dict[str, str] = {}
    for f in user_fields():
        value = str(submitted.get(f.key, "") or "").strip()

        if not value:
            if f.required:
                raise ValueError(f"「{f.label}」為必填")
            continue

        if f.max_length and len(value) > f.max_length:
            raise ValueError(f"「{f.label}」不可超過 {f.max_length} 個字")

        if f.pattern and not re.fullmatch(f.pattern, value):
            hint = f"（{f.pattern_hint}）" if f.pattern_hint else ""
            raise ValueError(f"「{f.label}」格式不正確{hint}")

        if f.type == "select":
            allowed = {o["value"] for o in f.options}
            if value not in allowed:
                raise ValueError(f"「{f.label}」不是有效的選項")

        cleaned[f.key] = value
    return cleaned
