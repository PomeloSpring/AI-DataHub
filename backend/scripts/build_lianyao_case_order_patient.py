#!/usr/bin/env python3
"""联耀业务本体：案例 / 订单 / 患者域口径补齐（指标 + 维度 + PII 敏感基线）。

## 由来

口径依据《本体指标维度设计-案例订单患者域.xlsx》的「② 指标设计」「③ 维度设计」两页：
79 条指标、79 条维度，其中原稿存在多处错误口径（订单未带删除过滤、取消=9、
未发送=待确认、案例已下单=case_status=4、拆牙请求=1、患者平均年龄=AVG(age)…），
本脚本按「枚举类为权威源 + 实库列为准」逐条纠正并登记。

## 三类写入，各有唯一入口（不绕服务层自造第二套治理）

1. **存量行修正/改名/退役** 走 `metrics_service.update_metric` / `update_dimension`;
   字典名全局唯一（`name`/`name_en` 双查重且**不过滤 is_active**），所以「退役旧行 +
   新建中文行」会因 name_en 撞车而建不出来 —— 1:1 对得上的存量行一律**改名复用**
   （update_metric 支持 display_name），只有真正的重复行/坏行才退役。
2. **新指标/新维度** 写进 doc 顶层 `metrics[]`/`dimensions[]`，由
   `ontology_service.save_draft` → `absorb_doc_semantic_assets` 收编成字典行
   （收编即走 create_metric/create_dimension，含「公式列必须在归属对象物理列里」的校验）。
   顶层只放字典里**不存在**的名字，否则会与存量同义行分裂成两条口径。
3. **PII** 落 `adh_sensitive_fields`（该表没有服务层，接口层就是直写，此处同口径）。
   `phone/email/real_name/nick_name` 已被全局基线（datasource_id=0, table_name=''）
   block，且策略取「全局 ∪ 表级」并集、block 优先 —— 表级 partial 压不住全局 block，
   故只补 `avatar/id_no/address_detail` 三列，不重复登记全局已覆盖的列。

## 口径统一规则（Excel 的「具体检索SQL」是带 WHERE 的整句，指标公式改写成单表达式）

- 案例域 `del_flag = 0`；订单域 `clinic_del_flag = 0`（技工所侧单列 `lab_del_flag = 0`）；
  患者域 `del_flag = 0`（按拍板口径：该表虽有 del_flag，仍统一排除已删除档案）。
- 比率类一律 `NULLIF(分母, 0)`，且**分母同口径**（不沿用 Excel 里 `COUNT(*)` 的分母，
  否则已删记录会虚增分母）。
- `metric_formula.metric_safe_target` 只接受「以聚合函数开头、不含 SELECT/WHERE、
  无跨表限定」的公式，所以 Excel 的 `COUNT(*) WHERE x` 统一改写
  `COUNT(CASE WHEN x THEN 1 END)`。

## 明确不登记（Excel 有、实库或护栏不允许）

- 「订单金额」：`t_order_records` 无金额列（真实源 `t_order_bill*`，下期）。
- 「待OSS清理案例数」：`t_case_records` 无 `file_clean_time` 列。
- 案例「报告牙医」维度：`t_case_records` 无 `dentist_name` 列（订单表才有）。
- 「跨院重复档案患者数」「档案案例数不一致患者数」：需要 GROUP BY 子查询 / 跨表 JOIN，
  不是单表聚合，登记后也编译不出来（取了也是错数）。
- 「手机号缺失患者数」：`phone` 在全局 block 基线里（实测 `get_blocked_columns` 命中），
  编译期的屏蔽列闸门会硬拒，登记了等于挂一条永远查不了的口径。
- 案例域「案例患者平均年龄」：`t_case_records.patient_age` 在 `adh_sensitive_fields` 里
  是 `partial` 掩码列，`permission_enforcer` 对掩码列只允许直接输出、拒绝任何派生
  计算（`backend/core/enforcer.py` 「受保护字段不支持派生计算」），`AVG(...)` 必然失败；
  患者现龄口径由患者域「患者平均年龄」（birthday 现算）承载。
- **只有仍处于掩码状态的列上的维度才退役**（2026-11-24 二次拍板）：`doctor_user_name`
  （诊断医生）仍是 `partial` 掩码列，分组值只会打出 `****`，登记了没有分析价值 →
  存量行 #10 退役，诊断医生口径走未掩码的「诊断医生编码」(`doctor_user_code`)。
  性别/年龄已由用户移出敏感策略（不再涉及隐私口径），`patient_sex`/`patient_age`
  分组可出真实值，故「病人性别」(#289)/「病人年龄」(#240) **不退役**并各自按
  `is_active=1` 复活。前提是 `adh_sensitive_fields` 里对应行确实已删：apply 收尾会
  显式核对，若登记仍在则打印告警（此时分组值会是掩码占位 `***`，不是真实取值）。
- 存量指标「待OSS清理案例数」(#244)：公式引用 `file_clean_time`，该列在 `t_case_records`
  不存在，执行必报 `Unknown column` —— 已列入退役（线下清理数改由文件存储侧统计）。
- 维度「案例步骤」「产品编码」（多值列）：无列 / 不可 GROUP BY。

## 用法

    venv/bin/python -m backend.scripts.build_lianyao_case_order_patient            # dry-run（默认，只打印计划）
    venv/bin/python -m backend.scripts.build_lianyao_case_order_patient --apply    # 真写

## 幂等边界

- 存量修正按 `id + 当前名` 匹配（改名后第二次跑仍能命中：`expect` 是名字元组），
  重复执行写的是同一份 patch；退役行已是 0 时只打印「已退役」。
- 顶层新资产重复提交会被 absorb 判为 merged（只补空字段），不会产生第二行。
- doc 属性删除只在「脏属性还在」时才声明 `_declared_removals`，清理干净后不再声明。
- PII 按 `datasource_id + table_name + column_name` 查存在再插。
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime

# 直接以脚本路径运行（非 -m）时把仓库根放回 sys.path，两种跑法都能 import backend
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

MODEL_NAME = "联耀业务本体"

# 五站部署（doc.deploy_sites）：站点 → 数据源业务名
SITE_DATASOURCES = [
    ("cn", "prd-cn"),
    ("int", "prd-en"),
    ("eu", "prd-eu"),
    ("uk", "prd-uk"),
    ("jp", "prd-jp"),
]

TABLES = {
    "case": "t_case_records",
    "order": "t_order_records",
    "patient": "t_user_hospital_patient",
}

# 与对象属性列比对时忽略的列（策略：无注释/测试开关列不登记为业务属性）
SKIP_COLUMNS = {"test_flag"}


# ── 1. 存量指标修正（按 id + 当前名匹配；改名走 display_name） ────────────────
# patch 键沿用 update_metric 的前端键：display_name=业务名, formula, aliases, unit,
# category, calculation_type=聚合类型
METRIC_FIXES: list[dict] = [
    # ── 案例域 ──
    dict(id=6, expect=("案例数量",), patch=dict(
        formula="COUNT(CASE WHEN del_flag = 0 THEN id END)",
        calculation_type="count", unit="个", category="业务",
        aliases=["case数", "total_cases", "口扫案例数", "病例数", "病例总量", "检查数", "检查次数"],
        description="有效案例(病例)记录数，排除已删除 del_flag=0")),
    dict(id=151, expect=("已完成案例数",), patch=dict(
        formula="COUNT(CASE WHEN case_status = 5 AND del_flag = 0 THEN 1 END)",
        calculation_type="count", unit="个", category="业务",
        aliases=["completed_cases", "completed_cases_wiki", "已结案数", "结案数"],
        description="案例状态为已完成(5)且未删除的案例数")),
    dict(id=39, expect=("pending_cases", "待处理案例数"), patch=dict(
        display_name="待处理案例数",
        formula="COUNT(CASE WHEN case_status IN (0, 1, 2) AND del_flag = 0 THEN 1 END)",
        calculation_type="count", unit="个", category="业务",
        aliases=["pending_cases", "未结案案例数"],
        description="尚未上传(case_status 0/1/2)的有效案例数")),
    dict(id=109, expect=("已上传案例数",), patch=dict(
        formula="COUNT(CASE WHEN case_status = 3 AND del_flag = 0 THEN 1 END)",
        calculation_type="count", unit="个", category="业务",
        aliases=["uploaded_cases", "云端案例数"],
        description="已上传到云端(3)的有效案例数")),
    dict(id=110, expect=("已下单案例数",), patch=dict(
        formula="COUNT(CASE WHEN has_order = 1 AND del_flag = 0 THEN 1 END)",
        calculation_type="count", unit="个", category="业务",
        aliases=["ordered_cases", "转订单案例数", "已发起订单案例数"],
        description="has_order=1 的有效案例数；下单标记由订单创建回写(不是 case_status=4)")),
    dict(id=111, expect=("拆牙请求案例数", "排牙请求案例数"), patch=dict(
        display_name="排牙请求案例数",
        formula="COUNT(CASE WHEN split_teeth_status IN (1, 2) AND del_flag = 0 THEN 1 END)",
        calculation_type="count", unit="个", category="业务",
        aliases=["split_teeth_cases", "拆牙请求数", "拆牙请求案例数", "需要排牙案例数"],
        description="已发起排牙/三方排牙请求(split_teeth_status 1/2)的案例数")),
    dict(id=112, expect=("种植修复案例数",), patch=dict(
        formula="COUNT(CASE WHEN is_plant = 1 AND del_flag = 0 THEN 1 END)",
        calculation_type="count", unit="个", category="业务",
        aliases=["implant_cases", "种植案例", "种植体病例数"],
        description="种植修复案例数。权威口径是 is_plant=1；不可用 case_type=101(代码永不落库)")),
    dict(id=150, expect=("上锁案例数", "锁定案例数"), patch=dict(
        display_name="锁定案例数",
        formula="COUNT(CASE WHEN locked = 1 AND del_flag = 0 THEN 1 END)",
        calculation_type="count", unit="个", category="业务",
        aliases=["locked_cases", "上锁案例数", "锁定的病例数"],
        description="处于锁定状态的有效案例数")),
    dict(id=40, expect=("total_case_storage_gb", "案例总存储GB"), patch=dict(
        display_name="案例总存储GB",
        formula="SUM(CASE WHEN del_flag = 0 THEN occupied_space_size END) / 1073741824.0",
        calculation_type="sum", unit="GB", category="成本",
        aliases=["total_case_storage_gb", "案例存储空间"],
        description="有效案例占用云端存储(GB)，occupied_space_size 单位为字节；排除已删除案例")),
    dict(id=41, expect=("avg_case_cycle_hours", "平均案例处理时长小时"), patch=dict(
        display_name="平均案例处理时长小时",
        formula="AVG(CASE WHEN case_status = 5 AND del_flag = 0 "
                         "THEN TIMESTAMPDIFF(HOUR, create_time, update_time) END)",
        calculation_type="avg", unit="小时", category="业务",
        aliases=["avg_case_cycle_hours", "cycle_time_hours", "案例周期"],
        description="近似口径：本表无完成时间列，update_time 会被后续任意更新(含文件清理)刷新；"
                    "精确完成时长需状态流转日志，本期不纳入")),
    dict(id=42, expect=("cases_with_orders_ratio", "有订单案例占比"), patch=dict(
        display_name="有订单案例占比",
        formula="SUM(CASE WHEN has_order = 1 AND del_flag = 0 THEN 1 ELSE 0 END) "
                "/ NULLIF(SUM(CASE WHEN del_flag = 0 THEN 1 ELSE 0 END), 0)",
        calculation_type="sum", unit="比例", category="业务",
        aliases=["cases_with_orders_ratio", "案例下单转化率", "转订单率", "成单率"],
        description="已发起过订单的案例占全部**有效**案例(排除已删除)的比例")),
    # ── 订单域（全部补 clinic_del_flag=0；弃用不存在的 del_flag 过滤） ──
    dict(id=15, expect=("订单数",), patch=dict(
        formula="COUNT(CASE WHEN clinic_del_flag = 0 THEN 1 END)",
        calculation_type="count", unit="个", category="订单",
        aliases=["order_count", "order count", "下单量", "全部订单数", "订单量"],
        description="诊所侧有效订单数。本表没有单一删除标记：诊所侧看 clinic_del_flag、"
                    "技工所侧看 lab_del_flag，不能混用")),
    dict(id=46, expect=("completed_orders", "已完成订单数"), patch=dict(
        display_name="已完成订单数",
        formula="COUNT(CASE WHEN order_status = 6 AND clinic_del_flag = 0 THEN 1 END)",
        calculation_type="count", unit="个", category="订单",
        aliases=["completed_orders", "结案订单数"],
        description="状态为已完成(order_status=6)的诊所侧有效订单数")),
    dict(id=115, expect=("待确认订单数", "未发送订单数"), patch=dict(
        display_name="未发送订单数",
        formula="COUNT(CASE WHEN order_status = 1 AND clinic_del_flag = 0 THEN 1 END)",
        calculation_type="count", unit="个", category="订单",
        aliases=["pending_confirm_orders", "is_pending", "待发送订单数", "草稿订单数"],
        description="已创建但未发送给技工所的订单数(order_status=1 未发送)。"
                    "原稿把 1=未发送 与 9=处理中 合成「待处理」，两端状态无业务意义，已拆开")),
    dict(id=116, expect=("已收货订单数", "已发货订单数"), patch=dict(
        display_name="已发货订单数",
        formula="COUNT(CASE WHEN order_status = 5 AND clinic_del_flag = 0 THEN 1 END)",
        calculation_type="count", unit="个", category="订单",
        aliases=["received_orders", "shipped订单数", "出库订单数"],
        description="已发货(order_status=5)的订单数")),
    dict(id=117, expect=("已取消订单数",), patch=dict(
        formula="COUNT(CASE WHEN order_status = 7 AND clinic_del_flag = 0 THEN 1 END)",
        calculation_type="count", unit="个", category="订单",
        aliases=["cancelled_orders", "取消订单", "cancel订单数"],
        description="已取消(order_status=7 CANCEL)的订单数。原稿写 9 是错的：9 是处理中(READY)")),
    dict(id=156, expect=("诊所侧已删除订单数",), patch=dict(
        formula="COUNT(CASE WHEN clinic_del_flag = 1 THEN 1 END)",
        calculation_type="count", unit="个", category="订单",
        aliases=["clinic_deleted_orders"],
        description="诊所侧删除标识=是（1）的订单数（回收站口径，与有效订单互斥）")),
    dict(id=157, expect=("技工所侧已删除订单数",), patch=dict(
        formula="COUNT(CASE WHEN lab_del_flag = 1 THEN 1 END)",
        calculation_type="count", unit="个", category="订单",
        aliases=["lab_deleted_orders"],
        description="技工所侧删除标识=是（1）的订单数（回收站口径，与有效订单互斥）")),
    # ── 患者域（统一 del_flag = 0） ──
    dict(id=17, expect=("患者档案数",), patch=dict(
        formula="COUNT(CASE WHEN del_flag = 0 THEN 1 END)",
        calculation_type="count", unit="条", category="客户",
        aliases=["patient_count", "total_patients", "病人档案数", "建档数"],
        description="患者档案记录条数(同一人在 N 家医院有 N 条)，排除已删除；"
                    "「人数」需按 patient_uid 去重，见「患者人数」")),
    dict(id=148, expect=("患者平均年龄",), patch=dict(
        formula="AVG(CASE WHEN del_flag = 0 AND birthday IS NOT NULL "
                         "THEN TIMESTAMPDIFF(YEAR, birthday, CURDATE()) END)",
        calculation_type="avg", unit="岁", category="客户",
        aliases=["avg_patient_age", "平均年龄"],
        description="以出生日(birthday)现算的平均年龄；不用 age 快照列(建档时写入、不随年份刷新)")),
    dict(id=147, expect=("患者关联案例总数",), patch=dict(
        formula="SUM(CASE WHEN del_flag = 0 THEN case_count END)",
        calculation_type="sum", unit="个", category="客户",
        aliases=["patient_total_cases", "关联案例总数"],
        description="患者档案关联案例数量列(case_count 冗余列)求和，排除已删除档案")),
]

# ── 2. 退役（is_active=0）：无看板/数据集引用，已核 ────────────────────────
METRIC_RETIRE: list[dict] = [
    dict(id=35, expect=("storage_mb",), reason="非聚合标量(occupied_space_size / 1048576.0)，已由「单案例平均存储MB」替代"),
    dict(id=36, expect=("is_completed",), reason="布尔派生规则不是度量，已由「已完成案例数」承载"),
    dict(id=38, expect=("completed_cases",), reason="与「已完成案例数」(id 151) 同口径重复行，英文名已并入其别名"),
    dict(id=44, expect=("is_pending",), reason="布尔规则 order_status IN (1,9) 无业务意义，已拆成「未发送/处理中订单数」"),
    dict(id=45, expect=("total_orders",), reason="与「订单数」(id 15) 同口径重复行，且未带删除过滤"),
    dict(id=3, expect=("订单量",), reason="与「订单数」(id 15) 同口径重复行；「订单量」已并入其别名"),
    dict(id=31, expect=("is_frequent_visitor",), reason="布尔规则 case_count>=3，已由「常访患者数」承载"),
    dict(id=32, expect=("total_patients",), reason="与「患者档案数」(id 17) 同口径重复行；英文名已并入其别名"),
    dict(id=25, expect=("total_cases",), reason="案例数跨表口径(COUNT(Case.id))挂在 company 对象上，已由案例域「案例数量」承载"),
    dict(id=149, expect=("案例患者平均年龄",),
         reason="patient_age 在敏感字段基线里是 partial 掩码列，AVG 派生被 enforcer 硬拒"
                "（受保护字段不支持派生计算）；患者现龄口径由患者域「患者平均年龄」承载"),
    dict(id=244, expect=("待OSS清理案例数",),
         reason="公式引用 file_clean_time，t_case_records 无此列（实库 49 列已复核），"
                "执行必报 Unknown column；线下清理数改由文件存储侧统计"),
]

# ── 3. 新指标（doc 顶层收编；单表聚合、无 WHERE、无跨表限定） ──────────────
NEW_METRICS: list[dict] = [
    # 案例域
    dict(object="case", biz="未扫描案例数", en="unscanned_case_count",
         formula="COUNT(CASE WHEN case_status = 0 AND del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="业务",
         aliases=["空案例数", "仅表单案例数"],
         desc="建档但未做口扫的案例数(case_status=0，仅表单)"),
    dict(object="case", biz="扫描未优化案例数", en="unoptimized_case_count",
         formula="COUNT(CASE WHEN case_status = 1 AND del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="业务",
         aliases=["待优化案例数"],
         desc="扫描完但未完成 ScanPro 优化(case_status=1)的案例数"),
    dict(object="case", biz="正畸案例数", en="ortho_case_count",
         formula="COUNT(CASE WHEN case_type = 200 AND del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="业务",
         aliases=["正畸病例数", "牙齿矫正案例数"],
         desc="案例类型为正畸(200)的有效案例数"),
    dict(object="case", biz="修复案例数", en="repair_case_count",
         formula="COUNT(CASE WHEN case_type = 100 AND del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="业务",
         aliases=["修复病例数"],
         desc="案例类型为修复(100)的有效案例数；其中含种植(is_plant=1)"),
    dict(object="case", biz="案例类型未设置数", en="case_type_null_count",
         formula="COUNT(CASE WHEN case_type IS NULL AND del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="业务",
         aliases=["无类型案例数"],
         desc="未传治疗方案时 case_type 保持 NULL；该桶占比高说明按类型分组的指标不可信"),
    dict(object="case", biz="排牙成功案例数", en="split_teeth_success_count",
         formula="COUNT(CASE WHEN split_teeth_status = 3 AND del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="业务",
         aliases=["拆牙成功数"],
         desc="三方拆牙返回成功(split_teeth_status=3)的案例数"),
    dict(object="case", biz="排牙失败案例数", en="split_teeth_failed_count",
         formula="COUNT(CASE WHEN split_teeth_status = 4 AND del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="业务",
         aliases=["拆牙失败数"],
         desc="三方拆牙返回失败(split_teeth_status=4)的案例数"),
    dict(object="case", biz="排牙成功率", en="split_teeth_success_rate",
         formula="SUM(CASE WHEN split_teeth_status = 3 AND del_flag = 0 THEN 1 ELSE 0 END) "
                 "/ NULLIF(SUM(CASE WHEN split_teeth_status IN (1, 2, 3, 4) "
                 "AND del_flag = 0 THEN 1 ELSE 0 END), 0)",
         agg="sum", unit="比例", category="业务",
         aliases=["拆牙成功率"],
         desc="分母只取已发起过排牙请求(1/2/3/4)的案例，不含未排牙(0)"),
    dict(object="case", biz="数据收集完成案例数", en="collection_done_case_count",
         formula="COUNT(CASE WHEN collection_status = 2 AND del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="业务",
         aliases=["已收集案例数"],
         desc="研发数据收集状态为已上传(collection_status=2)的案例数"),
    dict(object="case", biz="数据收集忽略案例数", en="collection_ignored_case_count",
         formula="COUNT(CASE WHEN collection_status = 4 AND del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="业务",
         aliases=["忽略收集数"],
         desc="被标记忽略收集(collection_status=4)的案例数"),
    dict(object="case", biz="双颌案例数", en="dual_jaw_case_count",
         formula="COUNT(CASE WHEN is_upper = 1 AND is_lower = 1 AND del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="业务",
         aliases=["上下颌都有案例数"],
         desc="同时包含上颌与下颌扫描的案例数"),
    dict(object="case", biz="单颌案例数", en="single_jaw_case_count",
         formula="SUM(CASE WHEN del_flag = 0 AND ((is_upper = 1 AND is_lower = 0) "
                 "OR (is_upper = 0 AND is_lower = 1)) THEN 1 ELSE 0 END)",
         agg="sum", unit="个", category="业务",
         aliases=["仅上颌或仅下颌数"],
         desc="只有上颌或只有下颌的案例数"),
    dict(object="case", biz="单案例平均存储MB", en="avg_case_storage_mb",
         formula="AVG(CASE WHEN del_flag = 0 THEN occupied_space_size END) / 1048576.0",
         agg="avg", unit="MB", category="成本",
         aliases=["平均案例大小"],
         desc="单个有效案例的平均占用(MB)；occupied_space_size 单位为字节"),
    dict(object="case", biz="活跃口扫设备数", en="active_device_count",
         formula="COUNT(DISTINCT CASE WHEN del_flag = 0 THEN serial_number END)",
         agg="count_distinct", unit="台", category="设备",
         aliases=["有案例的设备数"],
         desc="统计周期内产生过案例的设备序列号去重数"),
    dict(object="case", biz="活跃诊断医生数", en="active_doctor_count",
         formula="COUNT(DISTINCT CASE WHEN del_flag = 0 THEN doctor_user_code END)",
         agg="count_distinct", unit="人", category="客户",
         aliases=["有案例医生数", "开单医生数"],
         desc="去重诊断医生账号数；键用 doctor_user_code(姓名可改，仅作展示)"),
    dict(object="case", biz="产生案例的诊所数", en="active_clinic_count",
         formula="COUNT(DISTINCT CASE WHEN del_flag = 0 THEN company_code END)",
         agg="count_distinct", unit="家", category="客户",
         aliases=["活跃医院数"],
         desc="去重机构编码数；键用 company_code 而非 company_name"),
    dict(object="case", biz="第三方接入案例数", en="isv_case_count",
         formula="COUNT(CASE WHEN appid IS NOT NULL AND appid <> '' AND del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="开放平台",
         aliases=["ISV案例数", "开放平台案例数"],
         desc="由 ISV/第三方 app 写入的案例数(appid 非空)"),
    dict(object="case", biz="案例关联患者数", en="case_patient_count",
         formula="COUNT(DISTINCT CASE WHEN del_flag = 0 THEN patient_file_code END)",
         agg="count_distinct", unit="人", category="客户",
         aliases=["涉及病人数"],
         desc="案例侧去重患者档案号数；与患者域「患者人数」交叉校验口径差异"),
    # 订单域
    dict(object="order", biz="技工所侧有效订单数", en="lab_valid_order_count",
         formula="COUNT(CASE WHEN lab_del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="订单",
         aliases=["lab侧订单数", "加工厂可见订单数"],
         desc="技工所侧未删除(lab_del_flag=0)的订单数，与诊所侧口径分开，不能混用"),
    dict(object="order", biz="新增订单数", en="new_order_count",
         formula="COUNT(CASE WHEN clinic_del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="订单",
         aliases=["新订单数", "下单数"],
         desc="统计周期内创建的订单数(时间窗由查询的时间范围决定，默认近30天)；"
              "记录时间轴用 create_time，业务时间轴用 order_time"),
    dict(object="order", biz="处理中订单数", en="processing_order_count",
         formula="COUNT(CASE WHEN order_status = 9 AND clinic_del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="订单",
         aliases=["加工中订单数", "READY订单数"],
         desc="技工所已开始加工(order_status=9 处理中)的订单数"),
    dict(object="order", biz="已发送待接单订单数", en="sent_pending_order_count",
         formula="COUNT(CASE WHEN order_status = 2 AND clinic_del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="订单",
         aliases=["待接单订单数"],
         desc="已发送但技工所尚未处置(order_status=2)的订单数"),
    dict(object="order", biz="已接单订单数", en="accepted_order_count",
         formula="COUNT(CASE WHEN order_status IN (3, 9, 5, 6, 8) AND clinic_del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="订单",
         aliases=["接受订单数"],
         desc="已接受及其后续状态(3/9/5/6/8)的订单数"),
    dict(object="order", biz="已拒绝订单数", en="rejected_order_count",
         formula="COUNT(CASE WHEN order_status = 4 AND clinic_del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="订单",
         aliases=["拒单数", "reject订单数"],
         desc="技工所拒单(order_status=4)的订单数"),
    dict(object="order", biz="已归档订单数", en="archived_order_count",
         formula="COUNT(CASE WHEN order_status = 8 AND clinic_del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="订单",
         aliases=["归档订单数"],
         desc="已归档(order_status=8)的订单数"),
    dict(object="order", biz="接单率", en="accept_rate",
         formula="SUM(CASE WHEN order_status IN (3, 9, 5, 6, 8) AND clinic_del_flag = 0 "
                 "THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN order_status IN (2, 3, 4, 9, 5, 6, 8) "
                 "AND clinic_del_flag = 0 THEN 1 ELSE 0 END), 0)",
         agg="sum", unit="比例", category="订单",
         aliases=["接受率", "技工所接单率"],
         desc="分母为已进入技工所处置环节(已发送及之后，不含诊所侧取消7)的订单"),
    dict(object="order", biz="拒单率", en="reject_rate",
         formula="SUM(CASE WHEN order_status = 4 AND clinic_del_flag = 0 THEN 1 ELSE 0 END) "
                 "/ NULLIF(SUM(CASE WHEN order_status IN (2, 3, 4, 9, 5, 6, 8) "
                 "AND clinic_del_flag = 0 THEN 1 ELSE 0 END), 0)",
         agg="sum", unit="比例", category="订单",
         aliases=["拒绝率"],
         desc="与接单率同分母，便于对比"),
    dict(object="order", biz="订单完成率", en="order_completion_rate",
         formula="SUM(CASE WHEN order_status = 6 AND clinic_del_flag = 0 THEN 1 ELSE 0 END) "
                 "/ NULLIF(SUM(CASE WHEN clinic_del_flag = 0 THEN 1 ELSE 0 END), 0)",
         agg="sum", unit="比例", category="订单",
         aliases=["完成率"],
         desc="已完成订单占全部有效订单比例"),
    dict(object="order", biz="订单取消率", en="order_cancel_rate",
         formula="SUM(CASE WHEN order_status = 7 AND clinic_del_flag = 0 THEN 1 ELSE 0 END) "
                 "/ NULLIF(SUM(CASE WHEN clinic_del_flag = 0 THEN 1 ELSE 0 END), 0)",
         agg="sum", unit="比例", category="订单",
         aliases=["取消率"],
         desc="已取消(order_status=7)订单占全部有效订单比例"),
    dict(object="order", biz="待发货积压订单数", en="backlog_order_count",
         formula="COUNT(CASE WHEN order_status IN (2, 3, 9) AND ship_date IS NULL "
                 "AND clinic_del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="订单",
         aliases=["未发货订单数", "积压订单数"],
         desc="已接单但尚未登记发货(ship_date 为空)的订单数，供应链卡点指标"),
    dict(object="order", biz="平均发货时效小时", en="avg_ship_hours",
         formula="AVG(CASE WHEN clinic_del_flag = 0 AND ship_date IS NOT NULL "
                 "AND order_time IS NOT NULL THEN TIMESTAMPDIFF(HOUR, order_time, ship_date) END)",
         agg="avg", unit="小时", category="订单",
         aliases=["平均出货时长", "发货周期"],
         desc="从下单(order_time)到发货(ship_date)的平均小时数；两列均在本表，口径闭合"),
    dict(object="order", biz="按期发货率", en="on_time_ship_rate",
         formula="SUM(CASE WHEN clinic_del_flag = 0 AND ship_date IS NOT NULL "
                 "AND expected_arrival_date IS NOT NULL AND ship_date <= expected_arrival_date "
                 "THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN clinic_del_flag = 0 "
                 "AND ship_date IS NOT NULL AND expected_arrival_date IS NOT NULL "
                 "THEN 1 ELSE 0 END), 0)",
         agg="sum", unit="比例", category="订单",
         aliases=["承诺达成率", "准时发货率"],
         desc="以本表 expected_arrival_date(约定到达日)为基准的达成率；本表无实际签收时间列"),
    dict(object="order", biz="逾期发货订单数", en="late_ship_order_count",
         formula="COUNT(CASE WHEN ship_date > expected_arrival_date AND clinic_del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="订单",
         aliases=["超期发货数"],
         desc="发货日期晚于约定到达日期的订单数(NULL 自动不计入)"),
    dict(object="order", biz="承诺周期天数均值", en="avg_commit_days",
         formula="AVG(CASE WHEN clinic_del_flag = 0 AND req_delivery_date IS NOT NULL "
                 "AND order_time IS NOT NULL "
                 "THEN TIMESTAMPDIFF(DAY, order_time, req_delivery_date) END)",
         agg="avg", unit="天", category="订单",
         aliases=["要求交付周期"],
         desc="下单到客户要求交付日的天数，反映交付压力分布"),
    dict(object="order", biz="已发货未登记物流单号订单数", en="shipped_no_tracking_count",
         formula="COUNT(CASE WHEN order_status IN (5, 6, 8) "
                 "AND (track_number IS NULL OR track_number = '') "
                 "AND clinic_del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="运维",
         aliases=["物流单号缺失数"],
         desc="数据质量巡检：状态已发货但 track_number 为空的订单"),
    dict(object="order", biz="活跃承接技工所数", en="active_lab_count",
         formula="COUNT(DISTINCT CASE WHEN clinic_del_flag = 0 THEN laboratory_company_code END)",
         agg="count_distinct", unit="家", category="客户",
         aliases=["接单技工所数", "活跃加工厂数"],
         desc="去重承接技工所编码数"),
    dict(object="order", biz="活跃下单诊所数", en="active_order_clinic_count",
         formula="COUNT(DISTINCT CASE WHEN clinic_del_flag = 0 THEN company_code END)",
         agg="count_distinct", unit="家", category="客户",
         aliases=["下单医院数"],
         desc="去重下单机构编码数"),
    dict(object="order", biz="诊所-技工所活跃配对数", en="clinic_lab_pair_count",
         formula="COUNT(DISTINCT CASE WHEN clinic_del_flag = 0 "
                 "AND laboratory_company_code IS NOT NULL AND laboratory_company_code <> '' "
                 "THEN CONCAT(company_code, '#', laboratory_company_code) END)",
         agg="count_distinct", unit="对", category="业务",
         aliases=["合作下单对数"],
         desc="实际发生下单的配对数，可与 partnership 域的登记合作总数对比算激活率"),
    dict(object="order", biz="订单关联案例数", en="order_case_count",
         formula="COUNT(DISTINCT CASE WHEN clinic_del_flag = 0 THEN case_code END)",
         agg="count_distinct", unit="个", category="业务",
         aliases=["涉及病例数"],
         desc="订单侧去重案例编码；跨域引用案例域主键，不重复建模 Case 对象"),
    dict(object="order", biz="第三方来源订单数", en="isv_order_count",
         formula="COUNT(CASE WHEN appid IS NOT NULL AND appid <> '' AND clinic_del_flag = 0 THEN 1 END)",
         agg="count", unit="个", category="开放平台",
         aliases=["ISV订单数"],
         desc="由第三方 app 创建的订单数"),
    # 患者域（统一 del_flag = 0）
    dict(object="patient", biz="患者人数", en="unique_patient_count",
         formula="COUNT(DISTINCT CASE WHEN del_flag = 0 THEN patient_uid END)",
         agg="count_distinct", unit="人", category="客户",
         aliases=["去重患者数", "病人数", "自然人患者数"],
         desc="按 patient_uid(跨院患者身份列)去重的自然人数；不能用 file_code——"
              "实测该列逐行唯一，去重后等于档案数，与档案数之差即跨院重复程度"),
    dict(object="patient", biz="新增患者数", en="new_patient_count",
         formula="COUNT(CASE WHEN del_flag = 0 THEN 1 END)",
         agg="count", unit="条", category="客户",
         aliases=["新登记患者", "新建档数"],
         desc="统计周期内新登记的档案数(时间窗由查询的时间范围决定，默认近30天)；"
              "时间轴固定用 registration_date(登记日)，不用 create_time(入库日)"),
    dict(object="patient", biz="有就诊记录患者数", en="visited_patient_count",
         formula="COUNT(CASE WHEN recent_visit_date IS NOT NULL AND del_flag = 0 THEN 1 END)",
         agg="count", unit="人", category="客户",
         aliases=["就诊患者数", "已随访患者数"],
         desc="有过最近就诊日期(recent_visit_date)的患者数"),
    dict(object="patient", biz="沉默患者数", en="silent_patient_count",
         formula="COUNT(CASE WHEN (recent_visit_date IS NULL "
                 "OR recent_visit_date < DATE_SUB(CURDATE(), INTERVAL 180 DAY)) "
                 "AND del_flag = 0 THEN 1 END)",
         agg="count", unit="人", category="客户",
         aliases=["流失患者数", "超期未就诊患者数"],
         desc="180 天(阈值默认 180，可按需调整)未就诊或从未就诊的患者数"),
    dict(object="patient", biz="常访患者数", en="frequent_patient_count",
         formula="COUNT(CASE WHEN case_count >= 3 AND del_flag = 0 THEN 1 END)",
         agg="count", unit="人", category="客户",
         aliases=["高频患者", "复诊患者数"],
         desc="关联案例数≥3 的患者数；case_count 是档案表上的冗余计数列"),
    dict(object="patient", biz="患者平均案例数", en="avg_patient_cases",
         formula="AVG(CASE WHEN del_flag = 0 THEN case_count END)",
         agg="avg", unit="个", category="客户",
         aliases=["人均案例数", "人均就诊次数"],
         desc="每位患者的平均关联案例数(基于冗余列 case_count)"),
    dict(object="patient", biz="未成年患者数", en="minor_patient_count",
         formula="COUNT(CASE WHEN birthday IS NOT NULL "
                 "AND TIMESTAMPDIFF(YEAR, birthday, CURDATE()) < 18 AND del_flag = 0 THEN 1 END)",
         agg="count", unit="人", category="客户",
         aliases=["儿童患者数", "18岁以下患者数"],
         desc="现龄小于 18 岁的患者数"),
    dict(object="patient", biz="男性患者数", en="male_patient_count",
         formula="COUNT(CASE WHEN sex = 1 AND del_flag = 0 THEN 1 END)",
         agg="count", unit="人", category="客户",
         aliases=["男患者数"],
         desc="性别为男(1)的患者数；取值权威源 UserSexEnEnum"),
    dict(object="patient", biz="女性患者数", en="female_patient_count",
         formula="COUNT(CASE WHEN sex = 2 AND del_flag = 0 THEN 1 END)",
         agg="count", unit="人", category="客户",
         aliases=["女患者数"],
         desc="性别为女(2)的患者数"),
    dict(object="patient", biz="建档医院数", en="patient_hospital_count",
         formula="COUNT(DISTINCT CASE WHEN del_flag = 0 THEN company_code END)",
         agg="count_distinct", unit="家", category="客户",
         aliases=["有患者的医院数"],
         desc="有患者档案的机构编码去重数"),
]

# ── 4. 存量维度修正（维度没有改名白名单 → 保留原名 + 补 aliases + 重写枚举标签） ──
DIM_FIXES: list[dict] = [
    dict(id=6, expect=("创建日期",), patch=dict(
        aliases=["创建时间", "create_time", "建档日期"],
        description="案例记录创建时间(平台侧入库时间)；语义查询按 time_grain 分桶")),
    dict(id=7, expect=("案例状态",), patch=dict(
        aliases=["case_status", "病例状态", "案例进展"],
        value_labels={"0": "仅表单(创建未扫描)", "1": "需处理(扫描完未优化)",
                      "2": "扫描完成(ScanPro优化并Finish)", "3": "已上传",
                      "4": "已下单", "5": "已完成"},
        description="案例生命周期状态(CaseStatusEnum 0-5)，以枚举类为权威源")),
    dict(id=8, expect=("案例类型",), patch=dict(
        aliases=["case类型", "case_type", "业务类型", "修复/种植/正畸"],
        value_labels={"0": "其他", "100": "修复", "101": "种植(代码永不产生,勿用)", "200": "正畸"},
        description="案例业务类型(OrderTypeEnum)：值域是 100/101/200；101 永不落库，"
                    "种植口径请用 is_plant；NULL 表示未传治疗方案")),
    dict(id=9, expect=("就诊医院",), patch=dict(
        aliases=["医院/诊所名称", "医院名称", "机构", "company_name"],
        description="案例所属机构名称(展示用)；分组分析优先用「就诊医院编码」(company_code)")),
    dict(id=29, expect=("拆牙状态",), patch=dict(
        aliases=["split_teeth_status", "排牙状态", "三方拆牙", "拆牙状态"],
        value_labels={"0": "不排牙", "1": "请求排牙", "2": "已发起三方请求",
                      "3": "排牙成功", "4": "排牙失败"},
        description="三方拆牙流程状态(SplitTeethStatusEnum 0-4)，以代码枚举为权威源")),
    dict(id=72, expect=("应用域标识",), patch=dict(
        aliases=["应用域", "appmanager", "站点", "域", "所属站点"],
        description="案例所属应用域/站点标识")),
    dict(id=108, expect=("是否种植修复",), patch=dict(
        aliases=["is_plant", "是否种植", "种植"],
        description="种植修复标记(权威口径，优于 case_type=101)")),
    dict(id=109, expect=("案例下单渠道",), patch=dict(
        aliases=["下单渠道", "app_code", "渠道", "来源渠道", "下单渠道编码"],
        description="案例来源渠道编码(app_code)，如 CONNECT/第三方")),
    dict(id=110, expect=("是否锁定",), patch=dict(
        aliases=["locked", "锁定", "是否上锁"],
        description="案例是否处于锁定状态")),
    dict(id=111, expect=("产品改善计划状态",), patch=dict(
        aliases=["数据收集状态", "collection_status", "收集状态", "数据回收"],
        value_labels={"0": "未标记(未收集)", "1": "已标记(收集中)", "2": "已上传(已收集)",
                      "3": "已下载", "4": "忽略收集"},
        description="研发数据收集状态(CollectionStatusEnum 0-4)")),
    dict(id=122, expect=("要求送达时间段",), patch=dict(
        target_table="t_order_records", target_column="req_delivery_time_period_code",
        aliases=["要求交付时段", "req_delivery_time_period_code", "时段"],
        description="客户要求的交付时段编码(req_delivery_time_period_code)")),
    dict(id=123, expect=("接单技工所",), patch=dict(
        aliases=["承接技工所", "技工所", "加工厂", "实验室", "laboratory",
                 "laboratory_company_name"],
        description="承接加工任务的技工所名称(展示)；稳定键用「承接技工所编码」")),
    dict(id=157, expect=("是否展示牙医签名",), patch=dict(
        aliases=["是否显示牙医签名", "show_dentist_signature", "签名"],
        description="订单是否展示牙医签名")),
    dict(id=158, expect=("是否显示患者姓名",), patch=dict(
        aliases=["is_show_patient_name", "匿名下单"],
        description="合规维度：控制订单在技工所侧是否暴露患者姓名")),
    dict(id=159, expect=("诊所删除标识",), patch=dict(
        aliases=["诊所侧删除标记", "clinic_del_flag", "诊所删除"],
        value_labels={"0": "未删除(有效)", "1": "已删除(回收站)"},
        description="诊所(下单方)侧删除标记；订单域是双角色表，必须区分两侧口径")),
    dict(id=160, expect=("技工所删除标识",), patch=dict(
        aliases=["技工所侧删除标记", "lab_del_flag", "加工厂删除"],
        value_labels={"0": "未删除(有效)", "1": "已删除(回收站)"},
        description="技工所(承接方)侧删除标记")),
    dict(id=181, expect=("Order创建日期",), patch=dict(
        aliases=["创建时间", "记录创建", "order_create_date", "create_time"],
        description="平台入库时间；仅用于记录增长类统计，不用于业务时效(业务时间轴用「下单时间」)")),
    dict(id=189, expect=("订单是否通知企业",), patch=dict(
        aliases=["是否通知企业", "is_notification"],
        description="订单是否通知企业")),
    dict(id=190, expect=("订单联耀正畸模拟状态",), patch=dict(
        aliases=["联耀正畸模拟状态", "new_ortho_sim_status"],
        description="订单正畸模拟状态")),
    dict(id=25, expect=("sex",), patch=dict(
        aliases=["患者性别", "病人性别", "性别", "sex"],
        value_labels={"0": "other 未知", "1": "male 男", "2": "female 女", "3": "intersex 双性"},
        description="患者性别(UserSexEnEnum 0=other/1=male/2=female/3=intersex)")),
    dict(id=107, expect=("患者来源渠道",), patch=dict(
        aliases=["来源渠道", "渠道来源", "channel"],
        description="患者来源渠道(取值需按数据分布补枚举)")),
    dict(id=175, expect=("Patient创建日期",), patch=dict(
        aliases=["创建时间", "patient_create_date"],
        description="平台入库时间；患者域首选时间轴是「登记日期」(registration_date)")),
    # 性别/年龄已移出敏感策略（2026-11-24），恢复生效；若曾按「敏感列」退役过则在此复活
    dict(id=240, expect=("病人年龄",), patch=dict(
        is_active=1,
        aliases=["患者年龄", "年龄", "patient_age"],
        description="案例患者年龄(record 快照值)；按患者现龄统计请用患者域「患者年龄」(birthday 现算)")),
    dict(id=289, expect=("病人性别",), patch=dict(
        is_active=1,
        aliases=["患者性别", "性别", "patient_sex"],
        value_labels={"0": "未知", "1": "男", "2": "女"},
        description="案例患者的性别快照(0/1/2)；按患者档案统计请用患者域「患者性别」(patient.sex)")),
]

# ── 5. 退役维度：列不存在(#27) / 仍被掩码且分组无意义(#10) ──────────────────
DIM_RETIRE: list[dict] = [
    dict(id=27, expect=("案例步骤",), reason="t_case_records 无 case_step 列；该概念已由「案例状态」枚举表达"),
    dict(id=10, expect=("诊断医生",),
         reason="doctor_user_name 是 partial 掩码列，enforcer 不允许掩码列 GROUP BY/派生，"
                "登记了永远查不了；诊断医生口径走未掩码的「诊断医生编码」(doctor_user_code)"),
]

# ── 6. 新维度（doc 顶层收编；name_en 全局唯一，沿用 case_/order_/patient_ 前缀风格） ──
_BOOL_LABELS = {"0": "否", "1": "是"}
_DEL_LABELS = {"0": "未删除(有效)", "1": "已删除(回收站)"}
_CASE_TYPE_LABELS = {"0": "其他", "100": "修复", "101": "种植(代码永不产生,勿用)", "200": "正畸"}

NEW_DIMENSIONS: list[dict] = [
    # 案例域
    dict(object="case", biz="就诊医院编码", en="case_company_code", column="company_code",
         category="组织", aliases=["机构编码", "医院编码", "company_code"],
         desc="机构稳定编码，与「就诊医院」(名称) 是同一概念的两种物理实现"),
    dict(object="case", biz="诊断医生编码", en="case_doctor_user_code", column="doctor_user_code",
         category="组织", aliases=["医生账号", "doctor_code"],
         desc="诊断医生账号编码(稳定键)；与 dentist_name 不是同一角色，勿混用"),
    dict(object="case", biz="扫描时间", en="case_scan_time", column="scan_time",
         category="时间", aliases=["扫描日期", "口扫时间", "scan_time"],
         desc="本地端口扫发生时间(业务时间轴)"),
    dict(object="case", biz="本地修改时间", en="case_local_modify_date", column="local_modify_date",
         category="时间", aliases=["local_modify_date", "端上修改时间"],
         desc="本地客户端侧的最后修改时间(设备所在时区的业务时间)，跨时区口径纠偏用"),
    dict(object="case", biz="更新时间", en="case_update_time", column="update_time",
         category="时间", aliases=["最后更新时间", "update_time"],
         desc="记录最后更新时间；会被文件清理任务刷新，不能当「完成时间」"),
    dict(object="case", biz="上级应用域", en="case_parent_appmanager", column="parent_appmanager",
         category="属性", aliases=["parent_appmanager", "父域"],
         desc="上级/父应用域，用于跨站汇总口径"),
    dict(object="case", biz="第三方应用", en="case_appid", column="appid",
         category="属性", aliases=["appid", "ISV应用", "接入应用"],
         desc="写入该案例的第三方 app 标识；空值表示非开放平台来源"),
    dict(object="case", biz="Connect版本", en="case_c_version", column="c_version",
         category="属性", aliases=["connect版本", "c_version", "客户端版本"],
         desc="Connect 客户端版本号(字符串，勿用大小比较做版本筛选)"),
    dict(object="case", biz="ScanPro版本", en="case_s_version", column="s_version",
         category="属性", aliases=["scanpro版本", "s_version"],
         desc="ScanPro 版本号"),
    dict(object="case", biz="案例固件版本", en="case_f_version", column="f_version",
         category="属性", aliases=["f_version", "设备固件", "固件版本"],
         desc="扫描设备固件版本号(与设备域「固件版本」同名列不同表)"),
    dict(object="case", biz="扫描设备", en="case_serial_number", column="serial_number",
         category="设备", aliases=["设备序列号", "serial_number", "口扫仪"],
         desc="产生案例的设备序列号；跨域引用设备域对象，不在此重复建模 Equipment"),
    dict(object="case", biz="患者档案号", en="case_patient_file_code", column="patient_file_code",
         category="客户", aliases=["病人档案号", "patient_file_code", "档案编码"],
         desc="案例关联的患者档案编号，与患者域 file_code 对应"),
    dict(object="case", biz="外部患者号", en="case_out_patient_id", column="out_patient_id",
         category="客户", aliases=["out_patient_id", "HIS患者号"],
         desc="外部系统(HIS)患者标识，用于跨系统对账"),
    dict(object="case", biz="案例是否有上颌", en="case_is_upper", column="is_upper",
         category="属性", aliases=["是否有上颌", "上颌", "is_upper"],
         labels=_BOOL_LABELS, desc="是否含上颌扫描"),
    dict(object="case", biz="案例是否有下颌", en="case_is_lower", column="is_lower",
         category="属性", aliases=["是否有下颌", "下颌", "is_lower"],
         labels=_BOOL_LABELS, desc="是否含下颌扫描"),
    dict(object="case", biz="是否已下单", en="case_has_order", column="has_order",
         category="属性", aliases=["有订单", "is_ordered", "has_order", "已下单"],
         labels=_BOOL_LABELS, desc="案例是否已发起过订单(下单标记由订单创建回写)"),
    dict(object="case", biz="用户时区", en="case_up_time_zone", column="up_time_zone",
         category="属性", aliases=["up_time_zone", "时区"],
         desc="案例创建方所在时区；跨时区按天分桶必须先声明此维度或转 UTC"),
    dict(object="case", biz="删除标记", en="case_del_flag", column="del_flag",
         category="状态", aliases=["del_flag", "已删除", "回收"],
         labels=_DEL_LABELS,
         desc="逻辑删除标记；登记为可筛维度，防止「有效/全部」口径歧义"),
    # 订单域
    dict(object="order", biz="下单诊所", en="order_company_name", column="company_name",
         category="组织", aliases=["诊所", "医院", "客户机构", "company_name"],
         desc="下单机构名称(展示)；分组优先用「下单诊所编码」"),
    dict(object="order", biz="下单诊所编码", en="order_company_code", column="company_code",
         category="组织", aliases=["company_code", "机构编码"],
         desc="下单机构稳定编码"),
    dict(object="order", biz="承接技工所编码", en="order_laboratory_company_code",
         column="laboratory_company_code", category="组织",
         aliases=["laboratory_company_code", "加工厂编码"],
         desc="承接技工所稳定编码，跨域引用 company 域"),
    dict(object="order", biz="技工所接单人", en="order_laboratory_user_name",
         column="laboratory_user_name", category="组织",
         aliases=["laboratory_user_name", "加工师"],
         desc="技工所侧处理人姓名"),
    dict(object="order", biz="订单诊断医生", en="order_doctor_user_name", column="doctor_user_name",
         category="组织", aliases=["医生", "doctor_user_name", "诊断医生"],
         desc="订单上的诊断医生账号名"),
    dict(object="order", biz="报告牙医", en="order_dentist_name", column="dentist_name",
         category="组织", aliases=["牙医", "dentist_name", "dentist"],
         desc="订单上的牙医姓名(案例表无此列，故只登记在订单域)"),
    dict(object="order", biz="订单设备序列号", en="order_serial_number", column="serial_number",
         category="设备", aliases=["serial_number", "扫描仪", "设备序列号"],
         desc="下单来源设备序列号(与设备域的「设备序列号」同名列不同表)"),
    dict(object="order", biz="关联案例编码", en="order_case_code", column="case_code",
         category="业务", aliases=["case_code", "病例号"],
         desc="订单对应的案例编码(跨域逻辑外键，不重复建模 Case)"),
    dict(object="order", biz="订单案例类型", en="order_case_type", column="case_type",
         category="属性", aliases=["case_type", "业务类型", "案例类型"],
         labels=_CASE_TYPE_LABELS,
         desc="订单冗余存储的案例类型，与案例域同枚举"),
    dict(object="order", biz="订单应用域", en="order_appmanager", column="appmanager",
         category="属性", aliases=["appmanager", "站点", "域"],
         desc="订单所属应用域/站点"),
    dict(object="order", biz="订单第三方应用", en="order_appid", column="appid",
         category="属性", aliases=["appid"],
         desc="创建订单的第三方 app 标识"),
    dict(object="order", biz="下单时间", en="order_time", column="order_time",
         category="时间", aliases=["订单时间", "业务下单时间", "order_time"],
         desc="业务口径的下单时间(订单域首选时间轴)"),
    dict(object="order", biz="发货日期", en="order_ship_date", column="ship_date",
         category="时间", aliases=["ship_date", "出货日期"],
         desc="实际发货日期"),
    dict(object="order", biz="约定到达日期", en="order_expected_arrival_date",
         column="expected_arrival_date", category="时间",
         aliases=["expected_arrival_date", "预计到达"],
         desc="与客户约定的到达日期，是达成率基准"),
    dict(object="order", biz="要求交付日期", en="order_req_delivery_date",
         column="req_delivery_date", category="时间",
         aliases=["req_delivery_date", "客户期望交付日"],
         desc="客户提出的要求交付日期"),
    dict(object="order", biz="订单用户时区", en="order_up_time_zone", column="up_time_zone",
         category="属性", aliases=["up_time_zone", "时区"],
         desc="下单方时区，跨时区按天分桶用"),
    # 患者域
    dict(object="patient", biz="登记日期", en="patient_registration_date",
         column="registration_date", category="时间",
         aliases=["建档日期", "registration_date", "入档时间"],
         desc="患者档案登记日期(患者域首选时间轴)"),
    dict(object="patient", biz="最近就诊日期", en="patient_recent_visit_date",
         column="recent_visit_date", category="时间",
         aliases=["最近就诊", "recent_visit_date", "复诊日期"],
         desc="最近一次就诊日期；沉默/流失分析的唯一可用时间轴(本表无逐次就诊明细)"),
    dict(object="patient", biz="出生日期", en="patient_birthday", column="birthday",
         category="时间", aliases=["生日", "birthday"],
         desc="出生日(年龄的权威源，按月/年可分桶)"),
    dict(object="patient", biz="患者年龄", en="patient_age", column="age",
         category="属性", aliases=["age", "登记年龄"],
         desc="建档时写入的年龄快照，仅用于按年龄段分组，不用于现龄统计"),
    dict(object="patient", biz="患者省份", en="patient_province", column="province",
         category="地理", aliases=["省份", "province"],
         desc="患者所在省份"),
    dict(object="patient", biz="患者城市", en="patient_city", column="city",
         category="地理", aliases=["城市", "city"],
         desc="患者所在城市"),
    dict(object="patient", biz="患者区县", en="patient_district", column="district",
         category="地理", aliases=["区县", "district"],
         desc="患者所在区县"),
    dict(object="patient", biz="患者乡镇", en="patient_town", column="town",
         category="地理", aliases=["乡镇", "街道", "town"],
         desc="患者所在乡镇/街道，最细地理粒度"),
    dict(object="patient", biz="转介码", en="patient_referral_code", column="referral_code",
         category="属性", aliases=["referral_code", "推荐码"],
         desc="转介绍编码，用于口碑传播分析"),
    dict(object="patient", biz="建档机构", en="patient_company_code", column="company_code",
         category="组织", aliases=["company_code", "所属医院", "诊所"],
         desc="档案归属机构编码(跨域引用 company 域)；本表无机构名称列，展示需 JOIN"),
    dict(object="patient", biz="患者档案编号", en="patient_file_code", column="file_code",
         category="客户", aliases=["file_code", "档案编号", "患者档案号", "档案编码"],
         desc="患者档案业务编号，人数去重键"),
    dict(object="patient", biz="患者唯一标识", en="patient_uid", column="patient_uid",
         category="客户", aliases=["patient_uid", "uid"],
         desc="患者主键 UID(技术键，报表优先用 file_code)"),
    dict(object="patient", biz="患者外部患者号", en="patient_out_patient_id",
         column="out_patient_id", category="客户",
         aliases=["out_patient_id", "HIS号"],
         desc="外部 HIS 系统患者号"),
    dict(object="patient", biz="建档人", en="patient_user_code", column="user_code",
         category="组织", aliases=["user_code", "录入人"],
         desc="创建该档案的用户账号编码"),
    dict(object="patient", biz="患者本地修改时间", en="patient_local_modify_date",
         column="local_modify_date", category="时间",
         aliases=["local_modify_date", "端上修改时间"],
         desc="本地端修改时间，跨时区口径纠偏用"),
    dict(object="patient", biz="患者删除标记", en="patient_del_flag", column="del_flag",
         category="状态", aliases=["del_flag", "已删除"],
         labels=_DEL_LABELS,
         desc="逻辑删除标记(0 未删除 / 1 已删除)；患者域指标统一按 del_flag=0 计有效档案"),
]

# ── 7. 患者域 PII 敏感基线（全局基线已 block 的列不重复登记） ─────────────
PII_COLUMNS: list[dict] = [
    dict(column="id_no", level="critical", desc="敏感识别建议采纳(患者身份证号)｜患者域 PII 合规屏蔽"),
    dict(column="address_detail", level="high", desc="敏感识别建议采纳(患者详细地址)｜患者域 PII 合规屏蔽"),
    dict(column="avatar", level="medium", desc="敏感识别建议采纳(患者头像)｜患者域 PII 合规屏蔽"),
]


# ── 执行 ──────────────────────────────────────────────────────────────
def _log(msg: str = "") -> None:
    print(msg, flush=True)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fetch_model() -> dict:
    from backend.common.db import execute_query
    rows = execute_query(
        "SELECT id, name, status, kind, datasource_id, json_content, deploy_sites "
        "FROM adh_ontology_models WHERE name = %s AND status = 'active' "
        "ORDER BY id DESC LIMIT 1", (MODEL_NAME,))
    if not rows:
        raise SystemExit(f"[fail] 找不到生效模型：{MODEL_NAME}")
    return rows[0]


def _fetch_metric_rows() -> dict:
    from backend.common.db import execute_query
    rows = execute_query(
        "SELECT id, name, name_en, formula, agg_type, unit, category, aliases, "
        "       bound_object_key, target_table, is_active, description "
        "FROM adh_metrics")
    return {int(r["id"]): r for r in rows}


def _fetch_dim_rows() -> dict:
    from backend.common.db import execute_query
    rows = execute_query(
        "SELECT id, name, name_en, target_table, target_column, category, value_labels, "
        "       aliases, bound_object_key, is_active, description "
        "FROM adh_dimensions")
    return {int(r["id"]): r for r in rows}


_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def _clean_aliases(items) -> list:
    """别名归一：去首尾空白、中文别名内的空白直接删除（Excel 排版残留）。

    库里存在「机构 编码」「上 颌」「最近 就诊」这类带断行空格的别名（上一轮从 Excel
    直接抄入）：用户/AI 不会带空格提问，留着只会多一条永远命不中的别名。中文别名
    去空白后与干净写法重名时以后者为准；纯英文别名（如 connect version）保留空格。
    """
    out: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        s = str(item or "").strip()
        if not s:
            continue
        if _CJK_RE.search(s):
            s = re.sub(r"\s+", "", s)
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _as_list(raw) -> list:
    """字典行的 aliases 列：服务层读出是 list，这里拿到的是库里的 JSON 字符串，统一成 list。"""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x)]
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return [str(raw)]
    return [str(x) for x in val] if isinstance(val, list) else []


def _as_dict(raw) -> dict:
    """value_labels 列同上：JSON 字符串 → dict（枚举码 → 业务标签）。"""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return {str(k): str(v) for k, v in val.items()} if isinstance(val, dict) else {}


def _match(rows: dict, item: dict, kind: str) -> dict | None:
    """按 id + 当前名（元组，含改名前后两种写法）定位存量行。"""
    row = rows.get(int(item["id"]))
    if not row:
        return None
    if str(row.get("name") or "") not in item["expect"]:
        raise SystemExit(
            f"[fail] {kind} id={item['id']} 当前名「{row.get('name')}」不在预期 {item['expect']}，"
            f"拒绝盲改（字典可能已被他人调整）")
    return row


def _live_columns(model: dict) -> dict[str, list[str]]:
    from backend.common.db.datasource_db import get_datasource_by_id, get_datasource_conn
    ds = get_datasource_by_id(int(model["datasource_id"]))
    conn = get_datasource_conn(ds["db_type"], ds["host"], ds["port"],
                               ds["username"], ds.get("password"), ds.get("database_name"))
    out: dict[str, list[str]] = {}
    try:
        with conn.cursor() as cur:
            for key, table in TABLES.items():
                cur.execute(f"SHOW COLUMNS FROM {table}")
                out[key] = [r["Field"] for r in cur.fetchall()]
    finally:
        conn.close()
    return out


def _datasource_ids() -> list[tuple[str, str, int]]:
    from backend.common.db import execute_query
    names = [n for _, n in SITE_DATASOURCES]
    ph = ", ".join(["%s"] * len(names))
    rows = execute_query(f"SELECT id, name FROM adh_datasources WHERE name IN ({ph})", tuple(names))
    by_name = {str(r["name"]): int(r["id"]) for r in rows}
    missing = [n for n in names if n not in by_name]
    if missing:
        raise SystemExit(f"[fail] 数据源未登记：{', '.join(missing)}")
    return [(site, name, by_name[name]) for site, name in SITE_DATASOURCES]


def _doc_objects(doc: dict) -> dict:
    return {str(o.get("key") or ""): o for o in (doc.get("objects") or [])}


def _plan_property_cleanup(doc: dict, live: dict) -> tuple[list, list]:
    """返回 (要删的脏属性, 实库缺列提示)。只报告，不擅自补列。"""
    objs = _doc_objects(doc)
    dirty: list[tuple[str, str]] = []
    missing: list[tuple[str, str]] = []
    for key, table in TABLES.items():
        obj = objs.get(key)
        if not obj:
            raise SystemExit(f"[fail] 模型里没有对象「{key}」")
        props = obj.get("properties") or []
        for p in props:
            col = str((p or {}).get("column") or "").strip()
            if not col:
                dirty.append((key, str(p.get("name") or "(无名)")))
        have = {str(p.get("column") or "").split(".")[-1].strip() for p in props}
        for col in live[key]:
            if col in have or col in SKIP_COLUMNS:
                continue
            missing.append((table, col))
    return dirty, missing


def _build_doc(doc: dict, metric_rows: dict, dim_rows: dict, dirty: list,
               new_metrics: list, new_dimensions: list) -> tuple[str, dict]:
    """构造待保存 doc：清脏属性 + 重写对象 metrics[] + 顶层待收编新资产。"""
    objs = _doc_objects(doc)
    # 1) 清脏属性（列名为空 = 建模残渣，编译不出 SQL），用显式删除声明点名
    declared: dict[str, list[str]] = {}
    for key, prop_name in dirty:
        obj = objs[key]
        before = len(obj.get("properties") or [])
        obj["properties"] = [p for p in (obj.get("properties") or [])
                             if str((p or {}).get("column") or "").strip()]
        if len(obj["properties"]) != before:
            declared.setdefault(key, []).append(prop_name)
    if declared:
        doc["_declared_removals"] = declared

    # 2) 对象 metrics[] 重写为字典里的生效中文名（canonical：仅名字字符串），
    #    移除已退役的英文旧名与带空格的脏引用
    for key in TABLES:
        names = [str(r["name"]) for r in sorted(metric_rows.values(), key=lambda x: int(x["id"]))
                 if str(r["bound_object_key"] or "") == key and int(r["is_active"]) == 1]
        objs[key]["metrics"] = names

    # 3) 顶层新资产：只放字典里还不存在的名字（已收编的由 absorb 转入字典，
    #    存量修正走 update_metric/update_dimension，重复提交只会被判 merged）
    doc["metrics"] = [
        {"display_name": m["biz"], "name_en": m["en"], "formula": m["formula"],
         "agg_type": m["agg"], "unit": m["unit"], "category": m["category"],
         "description": m["desc"], "aliases": m["aliases"], "bound_object_key": m["object"]}
        for m in new_metrics
    ]
    doc["dimensions"] = [
        {"display_name": d["biz"], "name_en": d["en"], "target_column": d["column"],
         "category": d["category"], "description": d["desc"], "aliases": d["aliases"],
         "value_labels": d.get("labels"), "bound_object_key": d["object"]}
        for d in new_dimensions
    ]
    return json.dumps(doc, ensure_ascii=False), declared


def _pii_plan(ds_ids: list[tuple[str, str, int]]) -> tuple[list, list]:
    from backend.common.db import execute_query
    table = TABLES["patient"]
    todo: list[tuple[str, str, int, str]] = []
    done: list[tuple[str, str]] = []
    for site, ds_name, ds_id in ds_ids:
        for item in PII_COLUMNS:
            rows = execute_query(
                "SELECT id, mask_type FROM adh_sensitive_fields "
                "WHERE datasource_id = %s AND table_name = %s AND column_name = %s",
                (ds_id, table, item["column"]))
            if rows:
                done.append((ds_name, f"{item['column']}={rows[0]['mask_type']}"))
                continue
            todo.append((site, ds_name, ds_id, item["column"]))
    return todo, done


def main() -> None:
    apply = "--apply" in sys.argv
    _log(f"=== 联耀案例/订单/患者域口径补齐（{'APPLY' if apply else 'DRY-RUN'}）{_now()}")

    model = _fetch_model()
    model_id = int(model["id"])
    doc = json.loads(model["json_content"])
    _log(f"模型：{model['name']} id={model_id} status={model['status']} ds={model['datasource_id']}")

    live = _live_columns(model)
    metric_rows = _fetch_metric_rows()
    dim_rows = _fetch_dim_rows()

    dirty, missing = _plan_property_cleanup(doc, live)
    _log(f"\n[1] 属性核对：三张表实存列 {', '.join(f'{TABLES[k]}={len(v)}' for k, v in live.items())}")
    _log(f"    脏属性(column 为空) → 删除 {len(dirty)} 条: {dirty or '无'}")
    _log(f"    实库有列但未登记为属性 {len(missing)} 条: {missing or '无'}")
    if missing:
        _log("    （SKIP_COLUMNS 内的测试开关列按策略不登记；其余为待补项，本脚本不改）")

    # ── 存量指标修正 ──
    _log(f"\n[2] 存量指标修正 {len(METRIC_FIXES)} 条")
    for item in METRIC_FIXES:
        row = _match(metric_rows, item, "指标")
        if row is None:
            raise SystemExit(f"[fail] 指标 id={item['id']} 不存在")
        patch = item["patch"]
        rename = patch.get("display_name")
        _log(f"    #{item['id']} {row['name']}"
             + (f" → {rename}" if rename else "")
             + (f" | 公式: {patch['formula']}" if "formula" in patch else "")
             + (f" | 别名 +{len(patch['aliases'])}" if "aliases" in patch else ""))

    # ── 指标退役 ──
    _log(f"\n[3] 指标退役 {len(METRIC_RETIRE)} 条（is_active=0）")
    for item in METRIC_RETIRE:
        row = _match(metric_rows, item, "指标")
        if row is None:
            raise SystemExit(f"[fail] 指标 id={item['id']} 不存在")
        state = "已退役" if int(row["is_active"]) == 0 else "待退役"
        _log(f"    #{item['id']} {row['name']} [{state}] — {item['reason']}")

    # ── 维度修正 / 退役 ──
    _log(f"\n[4] 存量维度修正 {len(DIM_FIXES)} 条")
    for item in DIM_FIXES:
        row = _match(dim_rows, item, "维度")
        if row is None:
            raise SystemExit(f"[fail] 维度 id={item['id']} 不存在")
        patch = item["patch"]
        bits = []
        if "target_column" in patch:
            bits.append(f"列 {row['target_column']}→{patch['target_column']}")
        if "value_labels" in patch:
            bits.append(f"枚举 {len(patch['value_labels'])} 项")
        if "aliases" in patch:
            bits.append(f"别名 +{len(patch['aliases'])}")
        _log(f"    #{item['id']} {row['name']} | " + ("; ".join(bits) or "仅描述"))
    _log(f"\n[5] 维度退役 {len(DIM_RETIRE)} 条")
    for item in DIM_RETIRE:
        row = _match(dim_rows, item, "维度")
        if row is None:
            raise SystemExit(f"[fail] 维度 id={item['id']} 不存在")
        _log(f"    #{item['id']} {row['name']} [is_active=0] — {item['reason']}")

    # ── 新增资产查重（幂等：本脚本上一轮已收编的同名同英文同行视为已落库） ──
    _log(f"\n[6] 新增指标 {len(NEW_METRICS)} 条 / 新增维度 {len(NEW_DIMENSIONS)} 条")
    metric_by_name = {str(r["name"]): r for r in metric_rows.values()}
    metric_ens = {str(r["name_en"] or "").lower() for r in metric_rows.values()}
    refresh_m: list[tuple[int, str, dict]] = []
    pending_m, absorbed_m = [], []
    for m in NEW_METRICS:
        row = metric_by_name.get(m["biz"])
        if row is not None:
            # 等价锚 = 业务名（全局唯一）+ 归属对象。name_en 是解析别名之一（`name_en+aliases`
            # 都算合法写法），存量行可能用别的英文写法；用脚本里的 en 覆盖它等于删掉
            # 用户/AI 已经能命中的别名（违反只增不减）→ 只补空，不覆盖。
            same = str(row["bound_object_key"] or "") == m["object"]
            if not same:
                raise SystemExit(
                    f"[fail] 指标名已被他人占用（归属对象不符），拒绝覆盖：{m['biz']}"
                    f"  现有 object={row['bound_object_key']}（脚本: {m['object']}）")
            absorbed_m.append(m["biz"])
            # 已收编但公式/单位/分类漂移（上一轮写错或本次修口径）→ 改走 update_metric
            drift = {k: m[k] for k in ("formula", "unit", "category")
                     if str(row.get(k) or "") != str(m.get(k) or "")}
            if not str(row.get("name_en") or "").strip():
                drift["name_en"] = m["en"]
            have = _as_list(row.get("aliases"))
            merged = _clean_aliases(have + list(m["aliases"] or []))
            if merged != _clean_aliases(have):
                drift["aliases"] = merged
            if int(row.get("is_active") or 0) != 1:
                drift["is_active"] = 1
            if drift:
                refresh_m.append((int(row["id"]), m["biz"], drift))
            continue
        if m["en"].lower() in metric_ens:
            raise SystemExit(f"[fail] 指标英文标识已存在（全局查重不过滤 is_active）：{m['en']}")
        pending_m.append(m)

    dim_by_name = {str(r["name"]): r for r in dim_rows.values()}
    dim_ens = {str(r["name_en"] or "").lower(): r for r in dim_rows.values() if str(r["name_en"] or "")}
    refresh_d: list[tuple[int, str, dict]] = []
    pending_d, absorbed_d = [], []
    for d in NEW_DIMENSIONS:
        row = dim_by_name.get(d["biz"])
        if row is not None:
            # 等价锚 = 业务名（全局唯一）+ 归属对象 + 物理列。name_en 不参与等价判定：
            # 存量字典的英文标识多是裸列名（company_code / del_flag…），它是解析别名之一，
            # 改成对象前缀等于删掉用户/AI 已能命中的写法（只增不减）。脚本里的带前缀 en
            # 改为「库为空时补 name_en，否则并入 aliases」，两种写法都能命中原行。
            cur_col = str(row.get("target_column") or "").split(".")[-1].strip()
            same = (str(row.get("bound_object_key") or "") == d["object"]
                    and cur_col == str(d["column"] or "").split(".")[-1].strip())
            if not same:
                raise SystemExit(
                    f"[fail] 维度名已被他人占用（归属对象/物理列不符），拒绝覆盖：{d['biz']}"
                    f"  现有 object={row['bound_object_key']} column={row['target_column']}"
                    f"（脚本: object={d['object']} column={d['column']}）")
            absorbed_d.append(d["biz"])
            drift: dict = {}
            if str(row.get("category") or "") != str(d["category"] or ""):
                drift["category"] = d["category"]
            cur_en = str(row.get("name_en") or "").strip()
            have_aliases = _as_list(row.get("aliases"))
            want_aliases = list(d["aliases"] or [])
            if not cur_en:
                drift["name_en"] = d["en"]
            elif cur_en.lower() != d["en"].lower() and d["en"] not in have_aliases:
                want_aliases.append(d["en"])
            merged_aliases = _clean_aliases(have_aliases + want_aliases)
            if merged_aliases != _clean_aliases(have_aliases):
                drift["aliases"] = merged_aliases
            want_labels = d.get("labels") or {}
            cur_labels = _as_dict(row.get("value_labels"))
            if want_labels:
                # 枚举码→标签：库已有值优先（人工改过的口径不被脚本抬回去），只补缺码
                merged_labels = {**want_labels, **cur_labels}
                if merged_labels != cur_labels:
                    drift["value_labels"] = merged_labels
            if int(row.get("is_active") or 0) != 1:
                drift["is_active"] = 1
            if drift:
                refresh_d.append((int(row["id"]), d["biz"], drift))
            continue
        owner = dim_ens.get(d["en"].lower())
        if owner is not None:
            raise SystemExit(
                f"[fail] 维度英文标识已被「{owner['name']}」(#{owner['id']}) 占用"
                f"（全局查重不过滤 is_active）：{d['en']}")
        pending_d.append(d)
    _log(f"    待写：指标 {len(pending_m)} 条 / 维度 {len(pending_d)} 条；"
         f"上一轮已收编：指标 {len(absorbed_m)} 条 / 维度 {len(absorbed_d)} 条")
    if refresh_m or refresh_d:
        for rid, name, drift in refresh_m:
            _log(f"    ~ #{rid} {name} 已收编但口径漂移 → update_metric: {drift}")
        for rid, name, drift in refresh_d:
            _log(f"    ~ #{rid} {name} 已收编但绑定漂移 → update_dimension: {drift}")

    # ── PII ──
    ds_ids = _datasource_ids()
    pii_todo, pii_done = _pii_plan(ds_ids)
    _log(f"\n[7] 患者域 PII 敏感基线：待登记 {len(pii_todo)} 行，已存在 {len(pii_done)} 行")
    for site, ds_name, ds_id, column in pii_todo:
        _log(f"    + {ds_name}({ds_id}) {TABLES['patient']}.{column} = block")
    if pii_done:
        _log(f"    已存在: {pii_done}")
    _log("    说明：phone/email/real_name/nick_name 已被全局基线(datasource_id=0) block，"
         "策略取并集且 block 优先，表级 partial 压不住 → 不重复登记")

    # ── doc 预览 ──
    doc_json, declared = _build_doc(doc, metric_rows, dim_rows, dirty, pending_m, pending_d)
    _log(f"\n[8] 本体文档：删除声明 {declared or '无'}；顶层写入 "
         f"metrics={len(pending_m)} dimensions={len(pending_d)}")

    if not apply:
        _log("\n=== DRY-RUN 结束（未写任何一行）。加 --apply 落库。")
        return

    # ── 落库 ──
    from backend.modules.catalog.services import metrics_service as ms
    from backend.modules.catalog.services import ontology_service as osvc

    _log("\n=== APPLY：存量指标修正")
    for item in METRIC_FIXES:
        ms.update_metric(int(item["id"]), dict(item["patch"]))
    _log(f"    完成 {len(METRIC_FIXES)} 条")

    if refresh_m:
        _log(f"=== APPLY：已收编指标口径重对齐 {len(refresh_m)} 条")
        for rid, name, drift in refresh_m:
            ms.update_metric(rid, dict(drift))
            _log(f"    #{rid} {name}: {drift}")

    _log("=== APPLY：指标退役")
    for item in METRIC_RETIRE:
        ms.update_metric(int(item["id"]), {"is_active": 0})
    _log(f"    完成 {len(METRIC_RETIRE)} 条")

    _log("=== APPLY：存量维度修正")
    for item in DIM_FIXES:
        ms.update_dimension(int(item["id"]), dict(item["patch"]))
    _log(f"    完成 {len(DIM_FIXES)} 条")

    if refresh_d:
        _log(f"=== APPLY：已收编维度绑定重对齐 {len(refresh_d)} 条")
        for rid, name, drift in refresh_d:
            ms.update_dimension(rid, dict(drift))
            _log(f"    #{rid} {name}: {drift}")

    _log("=== APPLY：维度退役")
    for item in DIM_RETIRE:
        ms.update_dimension(int(item["id"]), {"is_active": 0})
    _log(f"    完成 {len(DIM_RETIRE)} 条")

    # 修正后再取一次字典，避免用修正前的名字回写对象 metrics[]
    metric_rows = _fetch_metric_rows()
    doc_json, declared = _build_doc(doc, metric_rows, dim_rows, dirty, pending_m, pending_d)

    _log("=== APPLY：保存本体文档（触发级联 + 字典收编）")
    saved = osvc.save_draft(model_id, doc_json)
    warnings = saved.get("validation_warnings") or []
    if warnings:
        _log(f"    告警 {len(warnings)} 条：")
        for w in warnings[:20]:
            _log(f"      ! {w}")

    _log("=== APPLY：患者域 PII 敏感基线")
    from backend.common.db import execute_write
    inserted = 0
    for site, ds_name, ds_id, column in pii_todo:
        meta = next(it for it in PII_COLUMNS if it["column"] == column)
        execute_write(
            "INSERT INTO adh_sensitive_fields "
            "(workspace_id, datasource_id, table_name, column_name, sensitivity_level, "
            " mask_type, mask_config, description) VALUES (0, %s, %s, %s, %s, 'block', NULL, %s)",
            (ds_id, TABLES["patient"], column, meta["level"], meta["desc"]))
        inserted += 1
    _log(f"    新增 {inserted} 行")

    # ── 收尾核对 ──
    metric_rows = _fetch_metric_rows()
    dim_rows = _fetch_dim_rows()
    active_m = [r for r in metric_rows.values() if int(r["is_active"]) == 1]
    active_d = [r for r in dim_rows.values() if int(r["is_active"]) == 1]
    _log("\n=== 落库后核对")
    for key in TABLES:
        n_m = sum(1 for r in active_m if str(r["bound_object_key"] or "") == key)
        n_d = sum(1 for r in active_d if str(r["bound_object_key"] or "") == key)
        _log(f"    {key}: 生效指标 {n_m} 条，生效维度 {n_d} 条")
    want_m = {m["biz"]: m for m in NEW_METRICS}
    miss_m = [k for k in want_m if k not in {r["name"] for r in active_m}]
    want_d = {d["biz"]: d for d in NEW_DIMENSIONS}
    miss_d = [k for k in want_d if k not in {r["name"] for r in active_d}]
    _log(f"    新指标未落库: {miss_m or '无'}")
    _log(f"    新维度未落库: {miss_d or '无'}")
    no_target = [r["name"] for r in active_m
                 if str(r["bound_object_key"] or "") in TABLES and not str(r["target_table"] or "")]
    _log(f"    生效但 target_table 为空(planner 加载不到): {no_target or '无'}")
    # 复活维度(#240/#289)的可用性前提：对应列已不在敏感基线里，否则 GROUP BY 只会得到
    # 掩码占位(***)，用户看到「性别/年龄分布」全是星号。此处显式核对，不静默放过。
    from backend.common.db import execute_query as _eq
    still_masked = _eq(
        "SELECT datasource_id, column_name, mask_type FROM adh_sensitive_fields "
        "WHERE table_name = %s AND column_name IN ('patient_sex', 'patient_age')",
        (TABLES["case"],))
    if still_masked:
        _log(f"    [warn] 性别/年龄列仍登记着敏感策略（分组值会是掩码占位）：{still_masked}")
    else:
        _log("    性别/年龄列无敏感登记：案例域「病人性别」/「病人年龄」可直接分组")
    if miss_m or miss_d or no_target:
        raise SystemExit("[fail] 收尾核对不通过，见上")


if __name__ == "__main__":
    main()