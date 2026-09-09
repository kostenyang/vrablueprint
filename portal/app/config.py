"""組態一律從環境變數讀，不寫死在程式裡。

多節點部署時每台的 .env 應該一致，特別是 SESSION_SECRET。
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 本站 session
    session_secret: str = "change-me"
    session_ttl_minutes: int = 480

    # AD
    ldap_url: str = "ldap://10.0.0.200:389"
    ldap_base_dn: str = "DC=home,DC=lab"
    ldap_upn_suffix: str = "@home.lab"
    ldap_bind_user: str = ""
    ldap_bind_password: str = ""
    ldap_use_ssl: bool = False

    # 可查稽核端點的人（UPN，逗號分隔）。留空 = 沒有人可以查別人的資料。
    # 之後要改成用 AD 群組判斷的話，換掉 auth.is_auditor 即可。
    auditor_upns: str = ""

    # vRA
    vra_url: str = "https://vra.home.lab"
    vra_username: str = ""
    vra_password: str = ""
    vra_domain: str = "System Domain"
    vra_project_id: str = ""
    vra_blueprint_id: str = ""
    vra_verify_tls: bool = False

    # vCenter：把申請資訊寫成 VM 的自訂屬性（Custom Attributes）
    # vc_host 留空 = 停用這個功能
    vc_host: str = ""
    vc_username: str = ""
    vc_password: str = ""
    vc_verify_tls: bool = False
    # portal 欄位 -> vCenter 自訂屬性名稱，JSON 字串；留空用 vcenter._DEFAULT_MAP
    vc_field_map: str = ""
    # 欄位不存在時是否自動建立（需要 vCenter 的 Global.ManageCustomFields 權限）
    vc_create_fields: bool = False


settings = Settings()
