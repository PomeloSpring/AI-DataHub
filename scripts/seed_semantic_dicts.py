#!/usr/bin/env python3
"""语义字典补充脚本: 术语/指标/维度/标签打标(ws=1, datasource_id=0)。

数据来源均已核实:
- 表/列: 数据源 test-alb(1780478236183) 的 adh_table_info / adh_column_metadata 快照
- 本体对象键: adh_ontology_bindings(bind_kind='primary', status='active')
  case→t_case_records, company→t_user_company, user→t_user_customer,
  equipment→t_product_equipment, work_order→t_product_work_order,
  order→t_order_records, ai_conversation→t_ai_conversation, isv→open_isv_info …
幂等: 全部先查存在再插; 修正类 UPDATE 按旧值匹配可重复执行。
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'services/.env'))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

from services.shared.common.db.metadata_db import get_metadata_conn

WS = 1


def _rid():
    return int(time.time() * 1000000)


def _next_id(cur, table):
    # adh_metrics/adh_dimensions 主键是 INT, 时间戳会溢出, 用 max+1
    cur.execute(f"SELECT COALESCE(MAX(id), 0) + 1 AS nid FROM {table}")
    return cur.fetchone()['nid']


def _now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


# ─────────────────────────────────────────────────────────────
# 1. 修正既有记录指向的不存在的表(历史 demo/旧数据源遗留)
# ─────────────────────────────────────────────────────────────
TERM_TABLE_FIXES = [
    # (旧表, 新表, 旧列或None[按术语名单独处理])
    ('dim_case', 't_case_records', None),
    ('t_equipment', 't_product_equipment', None),
]
# 换表后新表无此列的术语 → 清掉列引用只留表级关联(站点/区域原指 dim_case.site, 设备型号原指 t_equipment.model)
TERM_COL_BLANKOUT = [('站点', 'site'), ('区域', 'site'), ('设备型号', 'model')]

METRIC_FIXES = [
    # 订单量: demo 表 orders → 真实表 t_order_records, 桥接本体 order
    dict(name='订单量', target_table='t_order_records', target_column='',
         formula='COUNT(*)', agg_type='COUNT', default_agg='COUNT',
         bound_object_key='order', category='订单',
         description='销售订单记录数(订单记录新表 t_order_records)。原指向演示表 orders, 已改绑真实表'),
]


def fix_existing(cur):
    n = 0
    for old, new, _ in TERM_TABLE_FIXES:
        cur.execute("UPDATE adh_business_terms SET target_table=%s, updated_at=%s "
                    "WHERE target_table=%s AND workspace_id=%s", (new, _now(), old, WS))
        n += cur.rowcount
    for cn, old_col in TERM_COL_BLANKOUT:
        cur.execute("UPDATE adh_business_terms SET target_column='', updated_at=%s "
                    "WHERE term_cn=%s AND workspace_id=%s AND target_column=%s",
                    (_now(), cn, WS, old_col))
    for m in METRIC_FIXES:
        cur.execute("UPDATE adh_metrics SET target_table=%(target_table)s, target_column=%(target_column)s, "
                    "formula=%(formula)s, agg_type=%(agg_type)s, default_agg=%(default_agg)s, "
                    "bound_object_key=%(bound_object_key)s, category=%(category)s, "
                    "description=%(description)s, updated_at=%(now)s "
                    "WHERE name=%(name)s AND workspace_id=%(ws)s AND target_table IN ('orders','user_events')",
                    {**m, 'now': _now(), 'ws': WS})
        n += cur.rowcount
    return n


# ─────────────────────────────────────────────────────────────
# 2. 新增术语(term_cn 唯一)
# ─────────────────────────────────────────────────────────────
TERMS = [
    # cn, en, aliases, type, table, column, calculation, description
    ('企业', 'company', '公司,商户,企业客户', 'dimension', 't_user_company', 'company_code', '', '企业(医院/技工所/内部企业)主数据, company_type 区分: 1内部企业 2医院 3技工所'),
    ('部门', 'department', '', 'dimension', 't_user_customer', 'department_code', '', '用户所属部门编码, 部门主数据表 t_user_company_department'),
    ('患者档案', 'patient_file', '病人,患者', 'dimension', 't_case_records', 'patient_file_code', '', '案例关联的患者档案编码, 档案主数据在病人档案表 t_user_hospital_patient'),
    ('诊断医生', 'doctor', '医生,医师', 'dimension', 't_case_records', 'doctor_user_code', '', '案例关联的诊断医师, 姓名列为 doctor_user_name'),
    ('治疗方案', 'treatment_plan', '方案', 'dimension', 't_case_treatments', 'treatment_code', '', '案例的治疗方案(临床类型/牙位/材料/种植等), 一行一个方案'),
    ('影像资料', 'case_file', '文件,扫描文件', 'dimension', 't_case_files', 'file_code', '', '案例上传的影像/附件文件, type 区分图片/录像/音频/文件'),
    ('工单', 'work_order', '', 'dimension', 't_product_work_order', '', '', '设备售后工单(维修/检测等), 明细在 t_product_work_order_detail'),
    ('软件版本', 'software_version', '', 'dimension', 't_software_upgrade_version', '', '', '软件下载/升级版本; 案例表内 c_version/s_version/f_version 记录下单时的客户端版本'),
    ('AI会话', 'ai_conversation', '对话,会话', 'dimension', 't_ai_conversation', 'conversation_id', '', 'AI 对话会话, 业务主键 conversation_id, del_flag=0 表示未删除'),
    ('ISV商户', 'isv', '商户,开放平台商户', 'dimension', 'open_isv_info', 'appid', '', '开放平台接入商户, 以 appid 标识, status 1启用 2禁用'),
    ('种植修复', 'implant_restoration', '种植', 'dimension', 't_case_records', 'is_plant', '', '是否种植修复: 0否 1是'),
    ('案例状态', 'case_status', '', 'dimension', 't_case_records', 'case_status', '', '案例流转状态码(case_status)'),
    ('逻辑删除', 'del_flag', '删除标记', 'dimension', 't_case_records', 'del_flag', '', '逻辑删除标记。注意: 多数表列注释为 0=已删除/1=未删除, 与部分既有指标口径存在出入, 统计前先用样例数据核对'),
    ('临床类型', 'clinical_type', '', 'dimension', 't_case_treatments', 'clinical_type', '', '临床类型: 0 Restoration(修复) 1 Orthodontics(正畸)'),
    ('牙位', 'tooth_position', '牙齿位置', 'dimension', 't_case_treatments', 'number', '', '治疗方案对应的牙齿位置编号'),
    ('下单渠道', 'order_channel', '渠道', 'dimension', 't_case_records', 'app_code', '', '案例下单渠道编码; 商户域另有 appmanager(域)字段'),
]


def seed_terms(cur):
    added = 0
    for cn, en, alias, ttype, tbl, col, calc, desc in TERMS:
        cur.execute("SELECT id FROM adh_business_terms WHERE term_cn=%s AND workspace_id=%s", (cn, WS))
        if cur.fetchone():
            continue
        cur.execute(
            "INSERT INTO adh_business_terms (id, datasource_id, workspace_id, term_cn, term_en, "
            "term_aliases, term_type, target_table, target_column, calculation, description, "
            "usage_count, is_active, created_at, updated_at) "
            "VALUES (%s,0,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,1,%s,%s)",
            (_rid(), WS, cn, en, alias, ttype, tbl, col, calc, desc, _now(), _now()))
        added += 1
    return added


# ─────────────────────────────────────────────────────────────
# 3. 新增指标(name=中文业务名, name 同时是语义层 run_semantic_query 的指标键)
# ─────────────────────────────────────────────────────────────
METRICS = [
    # name, name_en, formula, unit, target_table, target_column, bound, category, description
    ('案例文件数', 'case_file_count', 'COUNT(*)', '个', 't_case_files', '', 'case_file', '案例', '案例关联的影像/附件文件数量'),
    ('治疗方案数', 'treatment_plan_count', 'COUNT(*)', '个', 't_case_treatments', '', 'case_treatment', '案例', '案例治疗方案条数'),
    ('企业数', 'company_count', 'COUNT(DISTINCT company_code)', '个', 't_user_company', 'company_code', 'company', '客户', '平台企业(含医院/技工所/内部企业)总数, 去重 company_code'),
    ('医院数', 'hospital_count', 'COUNT(DISTINCT company_code)', '个', 't_user_company', 'company_code', 'company', '客户', '企业类型为医院(company_type=2)的企业数, 查询时需带该过滤'),
    ('用户数', 'user_count', 'COUNT(DISTINCT user_code)', '个', 't_user_customer', 'user_code', 'user', '客户', '平台注册用户数, 去重工号 user_code'),
    ('启用用户数', 'active_user_count', 'COUNT(DISTINCT user_code)', '个', 't_user_customer', 'user_code', 'user', '客户', '账号状态正常(status=1)的用户数, 查询时需带该过滤'),
    ('设备总数', 'equipment_count', 'COUNT(*)', '台', 't_product_equipment', '', 'equipment', '设备', '口扫等设备总量(设备信息表)'),
    ('工单数', 'work_order_count', 'COUNT(*)', '单', 't_product_work_order', '', 'work_order', '设备', '设备售后工单总量'),
    ('订单数', 'order_count', 'COUNT(*)', '单', 't_order_records', '', 'order', '订单', '销售订单记录数(订单记录新表)'),
    ('AI会话数', 'ai_conversation_count', 'COUNT(DISTINCT conversation_id)', '个', 't_ai_conversation', 'conversation_id', 'ai_conversation', 'AI', 'AI 对话会话数, 有效会话需过滤 del_flag=0(0为未删除)'),
    ('患者档案数', 'patient_count', 'COUNT(*)', '人', 't_user_hospital_patient', '', 'patient', '客户', '医院侧患者档案总数(病人档案表)'),
    ('ISV商户数', 'isv_count', 'COUNT(DISTINCT appid)', '个', 'open_isv_info', 'appid', 'isv', '开放平台', '开放平台接入且启用(status=1)的商户数, 查询时需带该过滤'),
]


def seed_metrics(cur):
    added = 0
    for name, en, formula, unit, tbl, col, bound, cat, desc in METRICS:
        cur.execute("SELECT id FROM adh_metrics WHERE (name=%s OR name_en=%s) AND workspace_id=%s",
                    (name, en, WS))
        if cur.fetchone():
            continue
        cur.execute(
            "INSERT INTO adh_metrics (id, workspace_id, name, name_en, formula, unit, agg_type, "
            "target_table, target_column, description, category, datasource_id, is_active, "
            "created_at, updated_at, default_agg, bound_object_key) "
            "VALUES (%s,%s,%s,%s,%s,%s,'COUNT',%s,%s,%s,%s,0,1,%s,%s,'COUNT',%s)",
            (_next_id(cur, 'adh_metrics'), WS, name, en, formula, unit, tbl, col, desc, cat, _now(), _now(), bound))
        added += 1
    return added


# ─────────────────────────────────────────────────────────────
# 4. 新增维度(adh_dimensions, 供语义层 group_by 使用; 列已核实存在)
# ─────────────────────────────────────────────────────────────
DIMENSIONS = [
    # name, name_en, target_table, target_column, bound, category
    ('案例状态', 'case_status', 't_case_records', 'case_status', 'case', '状态'),
    ('案例类型', 'case_type', 't_case_records', 'case_type', 'case', '属性'),
    ('就诊医院', 'hospital_name', 't_case_records', 'company_name', 'case', '组织'),
    ('诊断医生', 'doctor_name', 't_case_records', 'doctor_user_name', 'case', '组织'),
    ('用户类型', 'user_type', 't_user_customer', 'user_type', 'user', '属性'),
    ('账号状态', 'account_status', 't_user_customer', 'status', 'user', '状态'),
    ('性别', 'gender', 't_user_customer', 'sex', 'user', '属性'),
    ('用户省份', 'user_province', 't_user_customer', 'province', 'user', '地理'),
    ('用户城市', 'user_city', 't_user_customer', 'city', 'user', '地理'),
    ('企业类型', 'company_type', 't_user_company', 'company_type', 'company', '属性'),
    ('企业省份', 'company_province', 't_user_company', 'address_province', 'company', '地理'),
    ('企业城市', 'company_city', 't_user_company', 'address_city', 'company', '地理'),
    ('文件类型', 'file_type', 't_case_files', 'type', 'case_file', '属性'),
    ('临床类型', 'clinical_type', 't_case_treatments', 'clinical_type', 'case_treatment', '属性'),
    ('牙位', 'tooth_position', 't_case_treatments', 'number', 'case_treatment', '属性'),
    ('商户状态', 'isv_status', 'open_isv_info', 'status', 'isv', '状态'),
]


def seed_dimensions(cur):
    added = 0
    for name, en, tbl, col, bound, cat in DIMENSIONS:
        cur.execute("SELECT id FROM adh_dimensions WHERE name=%s OR name_en=%s", (name, en))
        if cur.fetchone():
            continue
        cur.execute(
            "INSERT INTO adh_dimensions (id, name, name_en, level, target_table, target_column, "
            "description, category, datasource_id, is_active, created_at, updated_at, bound_object_key) "
            "VALUES (%s,%s,%s,0,%s,%s,%s,%s,0,1,%s,%s,%s)",
            (_next_id(cur, 'adh_dimensions'), name, en, tbl, col, f'物理列 {tbl}.{col}, 语义层维度', cat, _now(), _now(), bound))
        added += 1
    return added


# ─────────────────────────────────────────────────────────────
# 5. 标签: 分类 + 标签 + 表级打标(entity_type='table', entity_id=物理表名)
# ─────────────────────────────────────────────────────────────
TAG_CATEGORIES = ['业务域', '数据分层', '敏感级别']

TAGS = {
    '业务域': ['客户域', '案例域', '订单域', '设备域', '软件域', 'AI域', '开放平台域'],
    '数据分层': ['主数据', '业务过程', '配置字典'],
    '敏感级别': ['含个人信息', '一般业务'],
}

# entity_id(物理表) → 标签名列表; 表均在本体 active 绑定或快照表清单内
TAG_ASSIGNMENTS = {
    't_user_customer': ['客户域', '主数据', '含个人信息'],
    't_user_company': ['客户域', '主数据', '含个人信息'],
    't_user_hospital_patient': ['客户域', '主数据', '含个人信息'],
    't_user_company_department': ['客户域', '主数据'],
    't_user_partner': ['客户域', '主数据'],
    't_user_role': ['客户域', '配置字典'],
    't_case_records': ['案例域', '业务过程', '含个人信息'],
    't_case_files': ['案例域', '业务过程'],
    't_case_treatments': ['案例域', '业务过程'],
    't_case_recycle_bin': ['案例域', '业务过程'],
    't_order_records': ['订单域', '业务过程'],
    't_order_record': ['订单域', '业务过程'],
    't_product_equipment': ['设备域', '主数据'],
    't_product_work_order': ['设备域', '业务过程'],
    't_software_upgrade_version': ['软件域', '配置字典'],
    't_ai_conversation': ['AI域', '业务过程'],
    'open_isv_info': ['开放平台域', '配置字典'],
}


def seed_tags(cur):
    cat_ids = {}
    for i, cname in enumerate(TAG_CATEGORIES):
        cur.execute("SELECT id FROM adh_tag_categories WHERE name=%s AND workspace_id=%s", (cname, WS))
        hit = cur.fetchone()
        if hit:
            cat_ids[cname] = hit['id']
        else:
            cid = _rid() + i
            cur.execute("INSERT INTO adh_tag_categories (id, workspace_id, name, description, "
                        "sort_order, is_active, created_at) VALUES (%s,%s,%s,%s,%s,1,%s)",
                        (cid, WS, cname, f'表级{cname}分类', i, _now()))
            cat_ids[cname] = cid

    tag_ids = {}
    for cname, names in TAGS.items():
        for tn in names:
            cur.execute("SELECT id FROM adh_tags WHERE name=%s AND workspace_id=%s", (tn, WS))
            hit = cur.fetchone()
            if hit:
                tag_ids[tn] = hit['id']
                continue
            tid = _rid() + len(tag_ids)
            cur.execute("INSERT INTO adh_tags (id, workspace_id, category_id, name, tag_type, "
                        "entity_type, data_type, description, is_active, created_at, updated_at) "
                        "VALUES (%s,%s,%s,%s,'manual','table','string',%s,1,%s,%s)",
                        (tid, WS, cat_ids[cname], tn, f'{cname}标签: {tn}', _now(), _now()))
            tag_ids[tn] = tid

    linked = 0
    for table, tnames in TAG_ASSIGNMENTS.items():
        for tn in tnames:
            cur.execute("SELECT id FROM adh_tag_values WHERE tag_id=%s AND entity_id=%s",
                        (tag_ids[tn], table))
            if cur.fetchone():
                continue
            cur.execute("INSERT INTO adh_tag_values (id, workspace_id, tag_id, entity_id, value, "
                        "confidence, source, created_at, updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,1.00,'manual',%s,%s)",
                        (_rid(), WS, tag_ids[tn], table, tn, _now(), _now()))
            linked += 1
    return len(cat_ids), len(tag_ids), linked


def main():
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            fixed = fix_existing(cur)
            conn.commit()
            terms = seed_terms(cur)
            conn.commit()
            metrics = seed_metrics(cur)
            conn.commit()
            dims = seed_dimensions(cur)
            conn.commit()
            cats, tags, links = seed_tags(cur)
            conn.commit()
        print(f'[OK] 修正既有指向: {fixed} 行')
        print(f'[OK] 新增术语: {terms} (累计 {len(TERMS)} 项清单)')
        print(f'[OK] 新增指标: {metrics}')
        print(f'[OK] 新增维度: {dims}')
        print(f'[OK] 标签分类: {cats}, 标签: {tags}, 新增打标关系: {links}')
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
