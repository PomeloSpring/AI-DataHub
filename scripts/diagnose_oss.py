"""OSS/S3 权限逐操作诊断: 定位 403 到底卡在哪一步、根因是什么。

不依赖服务,直接用 boto3(SigV4)按 object_storage.py 相同的客户端配置,
依次探测各类操作,对每一步打印阿里云 OSS 的错误码 / HTTP 状态 / request-id,
并给出可执行的结论。配置全部来自 services/.env(OBJECT_STORAGE_*)。

用法:
    venv/bin/python scripts/diagnose_oss.py                 # 用 .env 配置, 只读探测(head/list/get 探针)
    venv/bin/python scripts/diagnose_oss.py --write         # 额外验证 put/delete(会写一个临时对象再删掉)
    venv/bin/python scripts/diagnose_oss.py --key a/b.png   # 指定要 GET 探针的对象键
退出码: 全部通过=0; 任一步被拒/失败=1。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.shared.common import config as cfg  # noqa: E402
from services.shared.common.object_storage import _describe_boto_error  # noqa: E402

# 错误码 → 人话结论(把最可能的根因直接摊开,不用再去搜)
_HINTS = {
    "AccessDenied": "权限没给到: 核对 AK 对应的 RAM 用户是否附加了本桶授权、桶是否属于当前账号(跨账号需 Bucket Policy)、是否有显式 Deny",
    "SignatureDoesNotMatch": "签名不对: 多为 SK 错误 / 系统时钟漂移>15min / region 与 endpoint 不匹配",
    "InvalidAccessKeyId": "AK 无效: AccessKey 不存在、被禁用或填错",
    "NoSuchBucket": "桶不存在或名字/region 不对(注意: OSS 对无权限的桶也可能报此码以隐藏存在性)",
    "SecondLevelDomainForbidden": "寻址风格问题(非权限): OSS 拒二级域名(path 风格), 要求虚拟主机/三级域名。修复: 强制 s3={'addressing_style':'virtual'}",
    "NotImplemented": "协议兼容(非权限): 新版 boto3 默认用 aws-chunked STREAMING-UNSIGNED-PAYLOAD-TRAILER 上传, OSS 不支持; 需改用非 streaming 上传(如传 ContentMD5 或固定长度 payload)",
}


def _norm_endpoint(ep: str) -> str:
    """endpoint 归一化: boto3 必须带 scheme, 缺省补 https://(阿里云 OSS 常见误配)."""
    ep = (ep or "").strip()
    if ep and not ep.startswith(("http://", "https://")):
        ep = "https://" + ep
    return ep


def _mk_client(addressing: str = "auto"):
    """按 object_storage.py 同款配置建客户端; addressing 控制寻址风格。

    auto=None(不显式指定, 由 botocore 决定) / virtual=三级域名 / path=二级域名。
    阿里云 OSS 的 S3 兼容接口通常要求 virtual(虚拟主机)风格。
    """
    import boto3
    from botocore.config import Config as BotoConfig

    boto_cfg = {
        "signature_version": "s3v4",
        # 与修复后的生产 object_storage.py 保持一致: 关掉默认 flexible checksum, 避免 streaming trailer
        "request_checksum_calculation": "when_required",
    }
    if addressing in ("virtual", "path"):
        boto_cfg["s3"] = {"addressing_style": addressing}
    kwargs = {
        "endpoint_url": _norm_endpoint(cfg.OBJECT_STORAGE_ENDPOINT),
        "aws_access_key_id": cfg.OBJECT_STORAGE_ACCESS_KEY,
        "aws_secret_access_key": cfg.OBJECT_STORAGE_SECRET_KEY,
        "region_name": cfg.OBJECT_STORAGE_REGION or "us-east-1",
        "config": BotoConfig(**boto_cfg),
    }
    return boto3.client("s3", **kwargs)


def _probe(name: str, fn) -> bool:
    """执行一个探测, 打印结果; 返回是否通过。"""
    try:
        fn()
        print(f"  [PASS] {name}")
        return True
    except Exception as e:  # noqa: BLE001 — 诊断脚本, 所有异常都摊开
        detail = _describe_boto_error(e)
        code = ""
        resp = getattr(e, "response", None)
        if isinstance(resp, dict):
            code = str((resp.get("Error") or {}).get("Code", ""))
        print(f"  [FAIL] {name}  ->  {detail}")
        hint = _HINTS.get(code)
        if hint:
            print(f"         结论: {hint}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="OSS/S3 权限逐操作诊断")
    ap.add_argument("--write", action="store_true", help="额外探测 put/delete(写临时对象后删除)")
    ap.add_argument("--key", default="", help="GET 探针使用的对象键(留空则用 list 出来的第一个或跳过)")
    args = ap.parse_args()

    bucket = cfg.OBJECT_STORAGE_BUCKET
    raw_ep = cfg.OBJECT_STORAGE_ENDPOINT
    print("=== OSS 权限诊断 ===")
    print(f"endpoint = {raw_ep or '(未配置)'}")
    if raw_ep and not raw_ep.startswith(("http://", "https://")):
        print(f"  [警告] endpoint 缺 scheme, boto3 会直接报 Invalid endpoint(建不出客户端→静默回退本地);")
        print(f"         本次诊断自动补为 {_norm_endpoint(raw_ep)} 继续探测, 但请在 .env 显式写 https://")
    print(f"region   = {cfg.OBJECT_STORAGE_REGION or '(未设, 用 us-east-1)'}")
    print(f"bucket   = {bucket}")
    print(f"AK       = {cfg.OBJECT_STORAGE_ACCESS_KEY[:6]}****{cfg.OBJECT_STORAGE_ACCESS_KEY[-4:] if len(cfg.OBJECT_STORAGE_ACCESS_KEY) > 10 else ''}")
    if not (cfg.OBJECT_STORAGE_ENDPOINT and cfg.OBJECT_STORAGE_ACCESS_KEY):
        print("\n[skip] 未配置 OBJECT_STORAGE_ENDPOINT/ACCESS_KEY, 无对象存储可诊断。")
        return 0

    probe_key = args.key or f"__diag__/healthcheck-{os.getpid()}.txt"

    # 三种寻址风格逐个探测: 若只读探测(head/list)仅 virtual 能过而 auto/path 被拒(SecondLevelDomainForbidden),
    # 说明是寻址配置问题(生产客户端需强制 virtual), 不是 RAM 权限问题。
    # read_ok=只读探测(权限+寻址)结果; put_ok=上传探测结果(可能受协议兼容影响, 单独看)。
    read_ok, put_ok = {}, {}
    for mode in ("auto", "virtual", "path"):
        print(f"\n[寻址风格: {mode}]")
        client = _mk_client(mode)
        rok = True
        rok &= _probe("HeadBucket     (init 第一步, HEAD 无响应体故仅报 HTTP 码)", lambda: client.head_bucket(Bucket=bucket))

        listed_keys = []

        def _list():
            nonlocal listed_keys
            resp = client.list_objects_v2(Bucket=bucket, MaxKeys=1)
            listed_keys = [o["Key"] for o in resp.get("Contents", [])]

        rok &= _probe("ListObjectsV2  (列目录)", _list)

        get_key = args.key or (listed_keys[0] if listed_keys else "")
        if get_key:
            rok &= _probe(f"GetObject      (下载 {get_key})", lambda: client.get_object(Bucket=bucket, Key=get_key))
        else:
            print("  [skip] GetObject  —— 桶内无可探测对象且未指定 --key")
        read_ok[mode] = rok

        if args.write:
            data = b"oss-diagnose"
            pok = True
            pok &= _probe(f"PutObject      (上传探针 {probe_key})", lambda: client.put_object(Bucket=bucket, Key=probe_key, Body=data))
            pok &= _probe(f"DeleteObject   (删除探针 {probe_key})", lambda: client.delete_object(Bucket=bucket, Key=probe_key))
            put_ok[mode] = pok
        else:
            print("  [skip] Put/Delete —— 只读模式; 加 --write 验证上传/删除权限")

    def _verdict():
        if read_ok.get("virtual") and not read_ok.get("auto"):
            print("\n=== 根因判定: 寻址风格问题(非权限)。auto/path 风格被 OSS 拒(SecondLevelDomainForbidden), virtual 风格只读全部通过。")
            print("    修复: 给 object_storage.py 的 BotoConfig 加 s3={'addressing_style':'virtual'}。")
        elif not read_ok.get("virtual"):
            print("\n=== 根因判定: virtual 风格下只读仍失败 —— 才需怀疑 RAM 权限/跨账号/签名(见上方 FAIL 结论)。")
        if args.write and read_ok.get("virtual") and not put_ok.get("virtual"):
            print("    另: 上传(PutObject)在 virtual 下仍失败——若为 NotImplemented 则是 boto3/OSS 协议兼容问题, 与权限无关。")

    _verdict()
    ok = read_ok["auto"] and (put_ok.get("auto", True) if args.write else True)
    print("\n=== 结果:", "全部通过 ✅" if ok else "存在失败,见上方 FAIL/判定 ❌", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
