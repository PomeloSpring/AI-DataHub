"""轮换 ``ADH_SECRET_KEY`` 并重加密存量密文（dry-run 默认，不改数据）。

为什么需要它（2026-11 存储审计 P0-2）：环境里留的是脚手架占位值
``change-me-in-production``，等于"密钥是个公开常量"——拿到仓库的人就能解开所有
密文。换成真随机密钥是必须的，但**直接改 .env 会让存量密文全部解不开**
（数据源密码、模型 api_key、用户邮箱/手机），所以必须配套把存量行按
"旧密钥解密 → 新密钥重加密"迁一遍。

口径：
  1. **两段式且先验后写**：先用旧密钥把全部目标值解出来（解不开就中止，一行都不写），
     再用新密钥重加密落库并逐行回读校验。半途失败不会留下"一半旧密钥一半新密钥"的库。
  2. **新密钥不走命令行**（会进 shell history）：要么由 ``--new-key-env`` 指定环境变量提供，
     要么由脚本 ``secrets`` 生成并直接写进 ``backend/.env``（旧值备份到 ``.env.bak-<ts>``，
     脚本不打印任何密钥明文）。
  3. **只动看起来已加密的值**：``is_encrypted`` 不成立的行（历史明文）原样保留并在报告里
     列出 —— 把明文"顺手加密"会改变读路径预期，那是另一件事。
  4. **幂等可重跑**：已经是新密钥密文的行（用新密钥可解、旧密钥不可解）跳过。

用法（repo 根目录）:
    venv/bin/python -m backend.scripts.rotate_secret_key                 # dry-run 体检
    venv/bin/python -m backend.scripts.rotate_secret_key --apply         # 生成新密钥并迁移
    ADH_NEW_SECRET_KEY=xxx venv/bin/python -m backend.scripts.rotate_secret_key \
        --apply --new-key-env ADH_NEW_SECRET_KEY                          # 自带密钥
    # 迁移完成后必须重启服务（新密钥只在 .env 里，进程不会自动重读）
"""

import argparse
import base64
import hashlib
import os
import re
import secrets
import sys
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENV_PATH = os.path.join("backend", ".env")

#: 密文落点（表, 主键列, 待迁移列）。新增加密列时在这里登记，别在别处另写清单。
TARGETS = (
    ("adh_datasources", "id", "password"),
    ("adh_llm_models", "id", "api_key"),
    ("adh_users", "id", "email"),
    ("adh_users", "id", "phone"),
)


def _key(secret: str) -> bytes:
    """与 crypto._get_key 同派生方式（SHA-256 → 32 字节）。"""
    return hashlib.sha256(secret.encode()).digest()


def decrypt(value: str, secret: str) -> str:
    data = base64.b64decode(value)
    if len(data) < 29:
        raise ValueError("密文长度不足")
    return AESGCM(_key(secret)).decrypt(data[:12], data[12:], None).decode("utf-8")


def encrypt(value: str, secret: str) -> str:
    iv = os.urandom(12)
    return base64.b64encode(iv + AESGCM(_key(secret)).encrypt(iv, value.encode("utf-8"), None)).decode("ascii")


def looks_encrypted(value: str) -> bool:
    if not value:
        return False
    try:
        return len(base64.b64decode(value)) >= 29
    except Exception:
        return False


def _read_current_secret() -> str:
    from backend.common.config import ADH_SECRET_KEY
    return ADH_SECRET_KEY


def _rows(conn, table: str, pk: str, column: str):
    with conn.cursor() as cur:
        cur.execute(f"SELECT {pk}, {column} AS v FROM {table} WHERE {column} IS NOT NULL AND {column} <> ''")
        return [(r[pk], r["v"]) for r in cur.fetchall() or []]


def collect(old_secret: str, new_secret: str) -> dict:
    """逐列分类：待迁移 / 已是新密钥 / 明文 / 解不开（阻断）。"""
    from backend.common.db import get_metadata_conn

    plan: dict = {"migrate": [], "already_new": [], "plaintext": [], "undecryptable": []}
    with get_metadata_conn() as conn:
        for table, pk, column in TARGETS:
            try:
                rows = _rows(conn, table, pk, column)
            except Exception as exc:                      # 表/列不存在 = 环境差异，如实报告
                print(f"  ! {table}.{column} 读取失败，跳过：{exc}")
                continue
            for rid, value in rows:
                ref = f"{table}.{column}#{rid}"
                if not looks_encrypted(value):
                    plan["plaintext"].append(ref)
                    continue
                try:
                    plain = decrypt(value, old_secret)
                except Exception:
                    try:                                  # 旧密钥解不开 → 是否已是新密钥？
                        decrypt(value, new_secret)
                        plan["already_new"].append(ref)
                    except Exception:
                        plan["undecryptable"].append(ref)
                    continue
                plan["migrate"].append((table, pk, rid, column, plain))
    return plan


def apply_migration(plan: dict, new_secret: str) -> None:
    from backend.common.db import get_metadata_conn

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            for table, pk, rid, column, plain in plan["migrate"]:
                cur.execute(f"UPDATE {table} SET {column} = %s WHERE {pk} = %s",
                            (encrypt(plain, new_secret), rid))
            conn.commit()
        # 回读校验：任何一行用新密钥解不开都是事故，必须显式报错而不是"看起来写完了"
        with conn.cursor() as cur:
            for table, pk, rid, column, _plain in plan["migrate"]:
                cur.execute(f"SELECT {column} AS v FROM {table} WHERE {pk} = %s", (rid,))
                row = cur.fetchone() or {}
                decrypt(row.get("v") or "", new_secret)


def swap_env_file(new_secret: str) -> str:
    """把新密钥写进 backend/.env，旧文件备份为 .env.bak-<ts>（0600）。"""
    text = open(ENV_PATH, encoding="utf-8").read() if os.path.exists(ENV_PATH) else ""
    backup = f"{ENV_PATH}.bak-{time.strftime('%Y%m%d%H%M%S')}"
    with open(backup, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(backup, 0o600)
    line = f"ADH_SECRET_KEY={new_secret}"
    if re.search(r"^ADH_SECRET_KEY=", text, re.M):
        text = re.sub(r"^ADH_SECRET_KEY=.*$", line, text, flags=re.M)
    else:
        text = (text.rstrip("\n") + "\n" if text.strip() else "") + line + "\n"
    with open(ENV_PATH, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(ENV_PATH, 0o600)
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description="轮换 ADH_SECRET_KEY 并重加密存量密文")
    parser.add_argument("--apply", action="store_true", help="真正迁移（默认 dry-run 只体检）")
    parser.add_argument("--new-key-env", default="", help="从该环境变量读取新密钥（不提供则生成）")
    args = parser.parse_args()

    old_secret = _read_current_secret()
    if args.new_key_env:
        new_secret = os.getenv(args.new_key_env, "").strip()
        if not new_secret:
            print(f"环境变量 {args.new_key_env} 为空，拒绝执行")
            return 2
    else:
        new_secret = secrets.token_urlsafe(48)

    if new_secret == old_secret:
        print("新密钥与当前密钥相同，无需轮换")
        return 0

    print(f"当前密钥形态：{'占位/空（不安全）' if old_secret.strip().lower() in ('', 'change-me-in-production') else '已配置'}")
    plan = collect(old_secret, new_secret)
    print(f"待迁移 {len(plan['migrate'])} 行 | 已是新密钥 {len(plan['already_new'])} | "
          f"明文（不动）{len(plan['plaintext'])} | 解不开 {len(plan['undecryptable'])}")
    for ref in plan["undecryptable"]:
        print(f"  ✗ 旧新密钥都解不开：{ref}")
    for ref in plan["already_new"]:
        print(f"  · 已是新密钥密文，跳过：{ref}")
    for ref in plan["plaintext"]:
        print(f"  · 未加密的历史值，原样保留：{ref}")

    if plan["undecryptable"]:
        print("\n有密文无法解密，中止（一行都没写）。请先确认当前密钥与库内密文同源，"
              "或人工处置这些行后再重试。")
        return 1

    if not args.apply:
        print("\ndry-run：未做任何修改。确认无误后加 --apply 执行。")
        return 0

    if not plan["migrate"] and not os.path.exists(ENV_PATH):
        print("没有待迁移行且 .env 不存在 —— 只换密钥会解不开任何存量密文，请人工确认。")
        return 1

    apply_migration(plan, new_secret)
    backup = swap_env_file(new_secret)
    print(f"\n已迁移 {len(plan['migrate'])} 行并逐行回读校验通过；"
          f"新密钥已写入 {ENV_PATH}（旧文件备份 {backup}，密钥明文不打印）")
    print("下一步：重启服务让新密钥生效 —— ./stop-all.sh web && ./start-all.sh -d")
    return 0


if __name__ == "__main__":
    sys.exit(main())
