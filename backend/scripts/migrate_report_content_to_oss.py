"""把存量报表正文从 MySQL 搬到对象存储（幂等迁移脚本，dry-run 默认，--apply 才写）。

背景：``adh_reports.content``（LONGTEXT）原先整份存正文。正文转对象存储后，
库里只存 ``content_object_key``，读取时现拼「域名 + 签名」（见
``report_service.content_access``）。本脚本负责搬运存量，让新代码看不到"空 key"。

**执行顺序**（顺序错了报表中心会打不开）：
    1. ``docker/mysql/report_content_oss_migration.sql`` 建列；
    2. 本脚本 dry-run 看清单 → ``--apply`` 真搬；
    3. 发布应用代码（新代码只认 key，不回退读库正文）。

安全边界：
- 只处理 ``content_object_key='' AND content<>''`` 的行 —— 已经搬过的行不会被重复搬，
  正文为空的行（排队中/失败）本来就没有对象，不产空对象；
- 写回带 ``AND content_object_key=''`` 条件：并发下别的进程先搬完了就跳过，不覆盖；
- 上传失败**不写库**（宁可留 pending 也不制造"行说搬了、对象却没了"的死链），
  失败清单逐条打印并以非 0 退出码收尾；
- ``--verify`` 反向核验：key 非空但对象取不到的行（正文真丢了，必须显式暴露）。

用法：
    PYTHONPATH=. venv/bin/python -m backend.scripts.migrate_report_content_to_oss
    PYTHONPATH=. venv/bin/python -m backend.scripts.migrate_report_content_to_oss --apply
    PYTHONPATH=. venv/bin/python -m backend.scripts.migrate_report_content_to_oss --verify
"""
from __future__ import annotations

import sys

from backend.common.db.metadata_db import get_metadata_conn
from backend.common.object_storage import build_report_content_key, get_object_storage

_PENDING_WHERE = ("content_object_key='' AND content IS NOT NULL AND content<>''")


def _rows(cur, where: str, cols: str) -> list[dict]:
    cur.execute(f"SELECT {cols} FROM adh_reports WHERE {where} ORDER BY id")  # noqa: S608
    return list(cur.fetchall())


def _pending(cur) -> list[dict]:
    return _rows(cur, _PENDING_WHERE, "id, owner_id, format, LENGTH(content) AS bytes")


def migrate(apply: bool) -> int:
    """搬运存量正文；返回未处理行数（0 = 全部搬完）。"""
    storage = get_object_storage()
    mode = "APPLY" if apply else "DRY-RUN"
    failures: list[tuple[int, str]] = []
    moved = skipped = 0

    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            rows = _pending(cur)
            print(f"[{mode}] 待搬迁正文: {len(rows)} 行, "
                  f"合计 {sum(int(r['bytes'] or 0) for r in rows):,} 字节")
            if not rows:
                print("没有需要搬迁的正文（已搬完或库中无正文）。")
            for row in rows:
                rid, owner = int(row["id"]), int(row["owner_id"] or 0)
                if owner <= 0:
                    # 没有属主就无法定位 key（key 首段是 user_id）—— 不猜，显式跳过
                    failures.append((rid, "owner_id 缺失，无法构建对象 key"))
                    continue
                if not apply:
                    print(f"  - report {rid}: owner={owner} "
                          f"→ {build_report_content_key(owner, rid)} ({row['bytes']}B)")
                    skipped += 1
                    continue
                body = _body(cur, rid)
                if body is None:
                    failures.append((rid, "正文在读取前已变化，跳过（可重跑）"))
                    continue
                key = build_report_content_key(owner, rid)
                try:
                    storage.upload_bytes(key, body.encode("utf-8"),
                                         content_type="text/plain; charset=utf-8")
                except Exception as exc:                      # 上传失败 → 不写库，留 pending
                    failures.append((rid, f"上传失败: {type(exc).__name__}: {exc}"))
                    continue
                # 条件写：并发下已被别人搬完就不覆盖
                n = cur.execute(
                    "UPDATE adh_reports SET content_object_key=%s, content_size=%s, content='' "
                    "WHERE id=%s AND content_object_key=''",
                    (key, len(body.encode("utf-8")), rid))
                if n != 1:
                    storage.delete(key)                       # 没抢到：收回刚上传的对象
                    skipped += 1
                    continue
                moved += 1
            conn.commit()
            left = len(_pending(cur))

    print(f"[{mode}] 搬迁完成: moved={moved} skipped={skipped} failed={len(failures)}")
    for rid, why in failures:
        print(f"  ✗ report {rid}: {why}")
    print(f"剩余未搬迁: {left} 行")
    if failures:
        print("存在失败行 —— 修好对象存储后重跑本脚本（幂等，已搬的不会重复搬）。")
    return len(failures) or left


def _body(cur, report_id: int) -> str | None:
    """取那一份正文；只取还没搬的行（key 非空说明已被并发搬走，回 None 让调用方跳过）。"""
    cur.execute(f"SELECT content FROM adh_reports WHERE id=%s AND {_PENDING_WHERE}", (report_id,))
    row = cur.fetchone()
    return (row.get("content") or "") if row else None


def verify() -> int:
    """反向核验：key 非空但对象取不到 = 正文真丢了（必须显式暴露，不能等用户点开才 500）。"""
    storage = get_object_storage()
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            rows = _rows(cur, "content_object_key<>''", "id, owner_id, content_object_key, content_size")
    print(f"[VERIFY] 已指向对象存储的正文: {len(rows)} 行")
    missing = [r for r in rows if not storage.exists(str(r["content_object_key"]))]
    for r in missing:
        print(f"  ✗ report {r['id']}: 对象不存在 key={r['content_object_key']}")
    mismatch = [r for r in rows if int(r.get("content_size") or 0) == 0]
    for r in mismatch:
        print(f"  ! report {r['id']}: content_size=0（搬迁未记录字节数）")
    print(f"[VERIFY] 对象缺失 {len(missing)} 行 / 字节数未记 {len(mismatch)} 行")
    return 1 if missing else 0


def main(argv: list[str]) -> int:
    if "--verify" in argv:
        return verify()
    return migrate(apply="--apply" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
