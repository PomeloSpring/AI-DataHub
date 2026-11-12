"""AES-256-GCM 加密（凭据类字段：数据源密码 / LLM api_key / 用户邮箱手机）。

密钥纪律（2026-11 存储审计发现）：密钥来自 ``ADH_SECRET_KEY``，历史上环境里留的是
脚手架占位值 ``change-me-in-production``，那等于「密钥是个公开常量」—— 字段虽然
存的是密文，但任何拿到仓库的人都能解开，加密形同混淆。因此本模块额外提供：

- :func:`key_is_placeholder`：识别已知占位值/空密钥；
- :func:`security_advisories`：启动与 ``/health`` 都要把它挂出去（可见提示，不静默）；
- 严格开关 ``ADH_SECRET_KEY_STRICT=1``：占位密钥直接拒绝启动（由人决策，不由代码默认
  阻断存量部署 —— 现网用占位值加密过的行要换密钥需先重加密，硬拦会把服务打死）。
"""

import os
import base64
import hashlib
import logging

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.common.config import ADH_SECRET_KEY

logger = logging.getLogger(__name__)

#: 脚手架/示例里的占位密钥（命中即视为"未配置真实密钥"）
_PLACEHOLDER_KEYS = {
    "", "change-me", "change-me-in-production", "secret", "changeme", "adh-secret-key",
    "your-secret-key", "replace-me",
}

ADVISORY_INSECURE_KEY = (
    "ADH_SECRET_KEY 为空或仍是脚手架占位值：「已加密」的凭据字段实际密钥可预测，等同于未加密。"
    "请在 backend/.env 把它换成真随机值（例如 python -c 'import secrets;print(secrets.token_urlsafe(48))'），"
    "换密钥前需先用旧密钥解密并重加密存量行（数据源密码 / LLM api_key / 用户邮箱手机），否则存量密文解不开。"
)


def key_is_placeholder() -> bool:
    """当前密钥是否空/占位（大小写与首尾空白不参与判定）。"""
    return ADH_SECRET_KEY.strip().lower() in _PLACEHOLDER_KEYS


def security_advisories() -> list:
    """启动与健康检查共用的安全告警清单（可见提示，不阻服务）。"""
    if not key_is_placeholder():
        return []
    if os.getenv("ADH_SECRET_KEY_STRICT", "").strip().lower() in ("1", "true", "yes", "on"):
        return ["[FATAL] " + ADVISORY_INSECURE_KEY + "（ADH_SECRET_KEY_STRICT=1，本进程拒绝服务）"]
    return [ADVISORY_INSECURE_KEY]


def assert_key_operational() -> None:
    """严格模式下的启动门禁：占位密钥拒绝启动；非严格模式由调用方打印告警。

    不在加密函数里抛：加密比明文好，把服务打死不是修复而是新的故障。
    """
    if key_is_placeholder() and os.getenv("ADH_SECRET_KEY_STRICT", "").strip().lower() in (
            "1", "true", "yes", "on"):
        raise RuntimeError(ADVISORY_INSECURE_KEY)


def _get_key() -> bytes:
    """Derive a 32-byte key from ADH_SECRET_KEY using SHA-256."""
    return hashlib.sha256(ADH_SECRET_KEY.encode()).digest()


def encrypt_password(plaintext: str) -> str:
    """Encrypt a password using AES-256-GCM.

    Args:
        plaintext: The password to encrypt.

    Returns:
        Base64-encoded string containing IV + ciphertext + GCM tag.
    """
    key = _get_key()
    aesgcm = AESGCM(key)
    # 12 bytes random IV (recommended for GCM)
    iv = os.urandom(12)
    ct = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    # Format: base64(iv + ciphertext_with_tag)
    return base64.b64encode(iv + ct).decode("ascii")


def decrypt_password(encrypted: str) -> str:
    """Decrypt an AES-256-GCM encrypted password.

    Args:
        encrypted: Base64-encoded encrypted password string.

    Returns:
        Decrypted plaintext password.

    Raises:
        ValueError: If decryption fails (wrong key or corrupted data).
    """
    key = _get_key()
    aesgcm = AESGCM(key)
    data = base64.b64decode(encrypted)
    if len(data) < 29:  # 12 (IV) + 16 (GCM tag) + 1 (min ciphertext)
        raise ValueError("Invalid encrypted data: too short")
    iv = data[:12]
    ct = data[12:]
    try:
        return aesgcm.decrypt(iv, ct, None).decode("utf-8")
    except Exception as e:
        raise ValueError(f"Decryption failed: {e}")


def is_encrypted(password: str) -> bool:
    """Check if a password string appears to be encrypted (base64 format).

    This is a heuristic check used during migration to avoid double-encrypting.
    """
    if not password:
        return False
    try:
        data = base64.b64decode(password)
        # Encrypted data should be at least 12 (IV) + 16 (GCM tag) + 1 = 29 bytes
        return len(data) >= 29
    except Exception:
        return False


# ── 敏感字段统一入口（email / phone 等；全平台仅此一份实现）──────────────
#
# 历史上 `backend/common/auth.py` 与 `backend/modules/auth/services/auth_service.py`
# 各写了一份同名助手，两份行为不一致：修好的那份没人调（测试打在其上是假绿），
# 真正挂在 /api/users 上的那份仍把加密失败当 warning 后回退明文。收敛到本模块，
# 调用方只转发，不得再本地写第二套（口径不一致 = 缺陷）。

DECRYPT_FAILED_HINT = "无法解密（密钥不匹配，请管理员处置）"


def encrypt_field(value: str) -> str:
    """加密敏感字段（空值→空串）。失败上抛 ValueError，绝不回退明文。

    no-silent-degradation §1：历史缺陷是 ``except`` 后 ``return value``，行以明文
    落库而用户与审计都以为已加密 —— 那比直接报错危害大得多。
    """
    if not value:
        return ""
    try:
        return encrypt_password(value)
    except Exception as exc:  # noqa: BLE001 — 带上原因上抛，不拿明文充数
        logger.error("敏感字段加密失败（email/phone 不落明文）: %s", exc, exc_info=True)
        raise ValueError(f"敏感信息加密失败，未能保存：{exc}") from exc


def decrypt_field(value: str) -> str:
    """解密敏感字段（空值→空串）。

    旧明文行（未加密的历史数据）原样返回是正确行为；但“密文却解不开”只能是
    密钥换了/密文损坏 —— 静默返回 "***" 会被当成"已脱敏"，必须给可诊断文案并 ERROR 留痕。
    """
    if not value:
        return ""
    try:
        if is_encrypted(value):
            return decrypt_password(value)
        return value  # 未加密的历史行：原样返回
    except Exception as exc:  # noqa: BLE001
        logger.error("敏感字段解密失败（密钥不匹配或密文损坏，需按新旧密钥重加密）: %s", exc)
        return DECRYPT_FAILED_HINT
