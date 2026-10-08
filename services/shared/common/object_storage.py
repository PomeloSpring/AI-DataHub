"""Object Storage — S3 兼容的文件存储抽象层(归档资产专用)。

支持后端:
- MinIO / AWS S3 / 阿里云 OSS / 腾讯云 COS 等 S3 兼容接口
- 未配置时自动回退到本地磁盘(开发环境兼容,ADH_OBJECT_FALLBACK_DIR)

仅承载用户归档资产(user_assets, 资产跟随用户跨工作空间);聊天附件是会话工作区文件,
不经本模块(见 services.datamind.multimodal.loader)。

Usage:
    from services.shared.common.object_storage import build_asset_key, get_object_storage

    key = build_asset_key(user_id=9, asset_id="abc123...", filename="报告.pdf")
    # -> "ai-datahub/9/assets/abc123..._报告.pdf"
    storage = get_object_storage()
    storage.upload_bytes(key, data, content_type="application/pdf")
    data = storage.download_bytes(key)
    storage.delete(key)
    url = storage.get_presigned_url(key, expires=3600)
"""

import io
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def build_asset_key(user_id: int, asset_id: str, filename: str) -> str:
    """资产对象 key — 唯一构建点: {prefix}/{user_id}/assets/{asset_id}_{filename}。

    资产跟随用户: 首段用用户 ID(不受工作空间/改名影响); asset_id 前缀保证
    同名文件不互相覆盖; 文件名做路径安全清洗(去斜杠/控制字符)。
    任何读写资产对象的代码不得自行拼 key(迁移脚本同用本函数)。
    """
    from services.shared.common.config import OBJECT_STORAGE_PREFIX
    safe = "".join(ch for ch in (filename or "asset") if ch.isprintable())
    safe = safe.replace("/", "_").replace("\\", "_").strip(" .") or "asset"
    return f"{OBJECT_STORAGE_PREFIX}/{int(user_id)}/assets/{asset_id}_{safe}"


def _describe_boto_error(e: Exception) -> str:
    """从 botocore ClientError 中抽取阿里云 OSS 排障关键字段(错误码/HTTP 状态/请求 ID).

    仅提取可诊断信息,不含 AK/SK 等机密;无法解析时回退 str(e).
    """
    resp = getattr(e, "response", None)
    if not isinstance(resp, dict):
        return f"{type(e).__name__}: {e}"
    err = resp.get("Error", {}) or {}
    meta = resp.get("ResponseMetadata", {}) or {}
    headers = meta.get("HTTPHeaders", {}) or {}
    code = err.get("Code") or "Unknown"
    status = meta.get("HTTPStatusCode", "?")
    req_id = headers.get("x-oss-request-id") or headers.get("x-amz-request-id") or "-"
    host_id = headers.get("x-oss-errormessage") or err.get("Message") or ""
    msg = f"Code={code} HTTP={status} request-id={req_id}"
    if host_id:
        msg += f" message={host_id}"
    return msg


class ObjectStorage:
    """S3 兼容对象存储客户端,未配置时回退本地磁盘."""

    def __init__(self):
        from services.shared.common.config import (
            OBJECT_STORAGE_ENABLED,
            OBJECT_STORAGE_ENDPOINT,
            OBJECT_STORAGE_ACCESS_KEY,
            OBJECT_STORAGE_SECRET_KEY,
            OBJECT_STORAGE_BUCKET,
            OBJECT_STORAGE_REGION,
            ADH_OBJECT_FALLBACK_DIR,
        )
        self._enabled = OBJECT_STORAGE_ENABLED
        self._bucket = OBJECT_STORAGE_BUCKET
        self._fallback_dir = ADH_OBJECT_FALLBACK_DIR
        self._client = None

        if self._enabled:
            try:
                import boto3
                from botocore.config import Config as BotoConfig

                # boto3 必须带 scheme, 缺省补 https://(阿里云 OSS endpoint 常见误配, 否则直接 Invalid endpoint)
                endpoint = OBJECT_STORAGE_ENDPOINT or ""
                if endpoint and not endpoint.startswith(("http://", "https://")):
                    endpoint = "https://" + endpoint
                kwargs = {
                    "endpoint_url": endpoint,
                    "aws_access_key_id": OBJECT_STORAGE_ACCESS_KEY,
                    "aws_secret_access_key": OBJECT_STORAGE_SECRET_KEY,
                    # 阿里云 OSS 的 S3 兼容接口拒二级域名(path)风格, 必须用虚拟主机(virtual/三级域名)风格,
                    # 否则报 403 SecondLevelDomainForbidden(与 RAM 权限无关);
                    # request_checksum_calculation=when_required 关掉新版 botocore 默认的 flexible checksum,
                    # 否则上传会带 aws-chunked STREAMING-UNSIGNED-PAYLOAD-TRAILER, OSS 不支持报 NotImplemented
                    "config": BotoConfig(
                        signature_version="s3v4",
                        s3={"addressing_style": "virtual"},
                        request_checksum_calculation="when_required",
                    ),
                }
                if OBJECT_STORAGE_REGION:
                    kwargs["region_name"] = OBJECT_STORAGE_REGION
                self._client = boto3.client("s3", **kwargs)
                self._ensure_bucket()
                logger.info(
                    "ObjectStorage initialized: endpoint=%s, bucket=%s",
                    OBJECT_STORAGE_ENDPOINT, self._bucket,
                )
            except Exception as e:
                logger.error(
                    "ObjectStorage init failed, falling back to local: %s | %s",
                    _describe_boto_error(e), e,
                )
                self._enabled = False
                self._client = None
        else:
            logger.info("ObjectStorage not configured, using local fallback: %s", self._fallback_dir)

    def _ensure_bucket(self):
        """确保 bucket 存在,不存在则创建.

        head_bucket 失败时先解析错误码:权限/签名/凭证类错误(AccessDenied 等)直接显式报错
        提醒(此时 create_bucket 也必然 403,再试只会把根因掩成"建桶失败"),
        仅当确属 bucket 不存在(404/NoSuchBucket)时才尝试建桶.
        """
        if not self._client:
            return
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except Exception as e:
            detail = _describe_boto_error(e)
            resp = getattr(e, "response", None) or {}
            err = resp.get("Error", {}) if isinstance(resp, dict) else {}
            code = str(err.get("Code", ""))
            status = (resp.get("ResponseMetadata", {}) or {}).get("HTTPStatusCode") if isinstance(resp, dict) else None
            is_missing = code in ("404", "NoSuchBucket", "NotFound") or status == 404
            if not is_missing:
                # 权限/签名/凭证问题:不掩盖,显式暴露根因(多为 AK 未绑定该 RAM 用户 / 跨账号 bucket / 显式 Deny)
                logger.error(
                    "ObjectStorage head_bucket 被拒绝 (bucket=%s): %s —— 请核对 AK 对应身份是否已附加该桶的 OSS 授权、"
                    "桶是否属于当前账号、是否存在显式 Deny 或签名/region 不匹配",
                    self._bucket, detail,
                )
                return
            try:
                self._client.create_bucket(Bucket=self._bucket)
                logger.info("Created bucket: %s", self._bucket)
            except Exception as ce:
                logger.warning(
                    "Create bucket failed (bucket=%s): %s | %s",
                    self._bucket, _describe_boto_error(ce), ce,
                )

    @property
    def is_object_storage(self) -> bool:
        """当前是否使用对象存储(而非本地磁盘)."""
        return self._enabled

    def upload_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        """上传字节数据.

        Args:
            key: 对象键(如 "ai-datahub/9/assets/abc_report.pdf", 见 build_asset_key)
            data: 文件字节内容
            content_type: MIME 类型
        """
        if self._enabled:
            # 显式传 ContentMD5: 强制走非 streaming 上传, 规避新版 boto3 默认的
            # aws-chunked STREAMING-UNSIGNED-PAYLOAD-TRAILER(阿里云 OSS 不支持, 报 NotImplemented)
            import base64
            import hashlib

            content_md5 = base64.b64encode(hashlib.md5(data).digest()).decode("utf-8")
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                ContentMD5=content_md5,
            )
        else:
            path = self._local_path(key)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)

    def download_bytes(self, key: str) -> Optional[bytes]:
        """下载对象为字节,不存在返回 None."""
        if self._enabled:
            try:
                resp = self._client.get_object(Bucket=self._bucket, Key=key)
                return resp["Body"].read()
            except self._client.exceptions.NoSuchKey:
                return None
            except Exception as e:
                logger.warning("Download failed (%s): %s", key, e)
                return None
        else:
            path = self._local_path(key)
            if not os.path.exists(path):
                return None
            with open(path, "rb") as f:
                return f.read()

    def delete(self, key: str) -> bool:
        """删除对象,成功返回 True."""
        if self._enabled:
            try:
                self._client.delete_object(Bucket=self._bucket, Key=key)
                return True
            except Exception as e:
                logger.warning("Delete failed (%s): %s", key, e)
                return False
        else:
            path = self._local_path(key)
            if os.path.exists(path):
                try:
                    os.remove(path)
                    return True
                except OSError as e:
                    logger.warning("Local delete failed (%s): %s", path, e)
                    return False
            return False

    def exists(self, key: str) -> bool:
        """检查对象是否存在."""
        if self._enabled:
            try:
                self._client.head_object(Bucket=self._bucket, Key=key)
                return True
            except Exception:
                return False
        else:
            return os.path.exists(self._local_path(key))

    def get_presigned_url(self, key: str, expires: int = 3600,
                          download_filename: str = "") -> Optional[str]:
        """生成预签名下载 URL(有效期默认 1 小时), 浏览器直连对象存储下载。

        download_filename 非空时以附件名下载(RFC 5987 声明 UTF-8 原名)。
        本地回退模式下返回 None(由调用方走服务端代理)。
        """
        if not self._enabled:
            return None
        try:
            params = {"Bucket": self._bucket, "Key": key}
            if download_filename:
                from urllib.parse import quote
                quoted = quote(download_filename)
                params["ResponseContentDisposition"] = (
                    f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}")
            url = self._client.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=expires,
            )
            return url
        except Exception as e:
            logger.warning("Presigned URL failed (%s): %s", key, e)
            return None

    def _local_path(self, key: str) -> str:
        """本地回退模式下的文件路径."""
        return os.path.join(self._fallback_dir, key)


# ── 全局单例 ──────────────────────────────────────────────────────

_storage: Optional[ObjectStorage] = None


def get_object_storage() -> ObjectStorage:
    """获取全局 ObjectStorage 单例."""
    global _storage
    if _storage is None:
        _storage = ObjectStorage()
    return _storage
