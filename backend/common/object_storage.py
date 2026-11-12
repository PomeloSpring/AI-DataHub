"""Object Storage — S3 兼容的文件存储抽象层(归档资产 + 工作空间会话产物)。

支持后端:
- MinIO / AWS S3 / 阿里云 OSS / 腾讯云 COS 等 S3 兼容接口
- 未配置时自动回退到本地磁盘(开发环境兼容,ADH_OBJECT_FALLBACK_DIR)

承载三类对象(首段一律是用户 ID,不受空间改名/迁移影响):
- 用户归档资产 user_assets: `{prefix}/{user_id}/assets/...`(见 build_asset_key)
- 工作空间会话产物: `{prefix}/{user_id}/sessions/{session_key}/workspace/...`
  (见 backend.modules.mind.execution.session_cloud.build_session_prefix)
- 报表正文: `{prefix}/{user_id}/reports/{report_id}`(见 build_report_content_key)
  —— 正文不落元数据库,库里只存 key,域名+签名读取时现拼

Usage:
    from backend.common.object_storage import build_asset_key, get_object_storage

    key = build_asset_key(user_id=9, asset_id="abc123...", filename="报告.pdf")
    # -> "ai-datahub/9/assets/abc123..._报告.pdf"
    storage = get_object_storage()
    storage.upload_bytes(key, data, content_type="application/pdf")
    data = storage.download_bytes(key)
    storage.delete(key)
    url = storage.get_presigned_url(key, expires=3600)
    storage.upload_file(key, "/path/a.xlsx")          # 大文件直传
    keys = storage.list_keys("ai-datahub/9/sessions/")  # 前缀清单(分页拉全)
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
    from backend.common.config import OBJECT_STORAGE_PREFIX
    safe = "".join(ch for ch in (filename or "asset") if ch.isprintable())
    safe = safe.replace("/", "_").replace("\\", "_").strip(" .") or "asset"
    return f"{OBJECT_STORAGE_PREFIX}/{int(user_id)}/assets/{asset_id}_{safe}"


def build_content_file_key(article_id: int, token: str, filename: str) -> str:
    """内容附件对象 key — 唯一构建点: {prefix}/content/{article_id}/{token}_{filename}。

    与用户资产(build_asset_key)的区别是归属主体: 附件属**内容**而非上传人, 发布后
    由全员下载, 所以首段是条目 id 而不是 user_id —— 作者离职或改名都不能让已发布
    内容的附件失效。token 由调用方给(uuid 片段), 保证同名文件不互相覆盖。
    任何读写内容附件的代码不得自行拼 key。
    """
    from backend.common.config import OBJECT_STORAGE_PREFIX
    safe = "".join(ch for ch in (filename or "attachment") if ch.isprintable())
    safe = safe.replace("/", "_").replace("\\", "_").strip(" .") or "attachment"
    return f"{OBJECT_STORAGE_PREFIX}/content/{int(article_id)}/{token}_{safe}"


def build_report_content_key(user_id: int, report_id: int) -> str:
    """报表正文对象 key — 唯一构建点: {prefix}/{user_id}/reports/{report_id}。

    报表正文不再落元数据库（adh_reports.content 退役为恒空串），库里只存这个 key，
    域名与签名在读取时现拼（见 report_service._content_access）。首段用 user_id 与
    归档资产同构：报表跟随属主，不受工作空间归属/改名影响。

    key 不带扩展名也不带版本：
    * 不带扩展名 —— markdown↔html 互转时不需要搬对象（渲染口径看库里的 format 列，
      前端取正文一律按文本读，对象 Content-Type 只是上传时的便利标注）；
    * 不带版本 —— 同一份报表的正文就地覆盖，历史版本是独立的 adh_reports 行与各自的 key。
    任何读写报表正文对象的代码不得自行拼 key（存量搬运脚本同用本函数）。
    """
    from backend.common.config import OBJECT_STORAGE_PREFIX
    return f"{OBJECT_STORAGE_PREFIX}/{int(user_id)}/reports/{int(report_id)}"


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
        from backend.common.config import (
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

    def list_keys(self, prefix: str) -> list[dict]:
        """列出前缀下全部对象 → [{"key", "size", "etag"}](分页拉全, 含自身前缀键)。

        失败一律外抛(调用方据此判定“同步状态不可得”), 绝不回空清单把“查不到”
        伪装成“云端没有文件”——那会让按需回落与同步标记双双判错。
        """
        if self._enabled:
            out: list[dict] = []
            token = None
            while True:
                params = {"Bucket": self._bucket, "Prefix": prefix, "MaxKeys": 1000}
                if token:
                    params["ContinuationToken"] = token
                resp = self._client.list_objects_v2(**params)
                for obj in resp.get("Contents") or []:
                    out.append({"key": obj["Key"], "size": int(obj.get("Size") or 0),
                                "etag": str(obj.get("ETag") or "").strip('"')})
                if not resp.get("IsTruncated"):
                    break
                token = resp.get("NextContinuationToken")
                if not token:
                    break
            return out
        root = Path(self._local_path(prefix))
        base = Path(self._fallback_dir)
        out = []
        if root.is_dir():
            for p in sorted(root.rglob("*")):
                if p.is_file() and not p.is_symlink():
                    rel = str(p.relative_to(base))
                    out.append({"key": rel, "size": p.stat().st_size, "etag": ""})
        return out

    def upload_file(self, key: str, path, content_type: str = "application/octet-stream") -> None:
        """上传本地文件(不整份读进内存)。失败外抛, 由调用方决定如何暴露。"""
        if self._enabled:
            self._client.upload_file(str(path), self._bucket, key,
                                     ExtraArgs={"ContentType": content_type})
            return
        dest = Path(self._local_path(key))
        dest.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copyfile(str(path), str(dest))

    def download_to_file(self, key: str, path) -> bool:
        """下载对象到本地文件;对象不存在返回 False(可诊断的“云端没有”), 其余失败外抛。

        与 download_bytes 的区别: 后者把异常也吞成 None, 会让“桶不可达”被当成
        “产物已丢失”——恢复路径必须能区分这两种事实, 因此单独提供本方法。
        """
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self._enabled:
            try:
                self._client.download_file(self._bucket, key, str(dest))
            except Exception as e:  # noqa: BLE001 — 先把“不存在”摘出来, 其余按失败外抛
                if not self.exists(key):
                    return False
                raise RuntimeError(f"对象存储下载失败 ({key}): {_describe_boto_error(e)}") from e
            return True
        src = Path(self._local_path(key))
        if not src.is_file():
            return False
        import shutil
        shutil.copyfile(str(src), str(dest))
        return True

    def delete_prefix(self, prefix: str) -> int:
        """删除前缀下全部对象, 返回删除个数。失败外抛(调用方需据此提醒/重试)。

        走单对象逐个删而不用 DeleteObjects 批量接口: 实机核验发现在阿里云 OSS 的
        S3 兼容入口上 DeleteObjects 报 `MissingArgument`(本客户端为规避 aws-chunked
        上传而关掉了默认 checksum, 批量删除必需的 Content-MD5 因此不发), 而单对象
        delete 可用。一个会话的产物数量本就是十几到几十个量级, 逐个删的代价可接受。
        """
        keys = [o["key"] for o in self.list_keys(prefix)]
        if self._enabled:
            for key in keys:
                self._client.delete_object(Bucket=self._bucket, Key=key)
            return len(keys)
        for key in keys:
            p = Path(self._local_path(key))
            if p.is_file():
                p.unlink()
        return len(keys)

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
