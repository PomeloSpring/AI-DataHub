#!/usr/bin/env python3
"""初始化演示数据 — 远程元数据库 + 本地业务数据源

架构:
- 远程 MySQL (120.55.243.161): 系统元数据（用户、角色、权限、工作空间）
- 本地 MySQL (localhost): 演示业务数据（orders, users, products）— 作为数据源

使用方式:
    python scripts/init_demo_data_remote.py
"""

import sys
import os
import random
import time
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pymysql

# ============================================================================
# 数据库连接配置
# ============================================================================

# 远程 MySQL — 系统元数据
REMOTE_DB_CONFIG = {
    "host": "120.55.243.161",
    "port": 3306,
    "user": "root",
    "password": "adh_test_2024",
    "database": "adh",
}

# 本地 MySQL — 演示业务数据
LOCAL_DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "",
    "database": "demo_business",
}

# ============================================================================
# 演示数据配置
# ============================================================================

REGIONS = ["华东", "华南", "华北", "西南"]
CITIES = {
    "华东": ["上海", "杭州", "南京", "苏州", "宁波"],
    "华南": ["广州", "深圳", "东莞", "佛山", "珠海"],
    "华北": ["北京", "天津", "石家庄", "济南", "青岛"],
    "西南": ["成都", "重庆", "昆明", "贵阳", "西安"],
}
DEPARTMENTS = ["技术部", "市场部", "销售部", "财务部", "运营部", "产品部"]
PRODUCT_CATEGORIES = ["电子产品", "服装", "食品", "家居", "图书"]
PAYMENT_METHODS = ["支付宝", "微信", "银行卡", "现金"]

DEMO_NAMES = [
    "张伟", "李娜", "王芳", "刘洋", "陈明", "杨静", "赵磊", "黄丽",
    "周强", "吴敏", "徐超", "孙艳", "马军", "朱琳", "胡斌", "郭婷",
    "何勇", "高雪", "林涛", "罗慧", "梁杰", "宋颖", "唐浩", "韩璐",
    "冯刚", "董梅", "程鹏", "曹倩", "袁明", "邓丽", "许超", "傅敏",
    "沈洋", "曾艳", "彭磊", "吕琳", "苏军", "卢婷", "蒋勇", "蔡雪",
    "贾涛", "丁慧", "魏杰", "薛颖", "叶浩", "阎璐", "余刚", "潘梅",
    "杜鹏", "戴倩",
]


def get_connection(config):
    """Get database connection."""
    return pymysql.connect(
        host=config["host"],
        port=config["port"],
        user=config["user"],
        password=config["password"],
        database=config["database"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def gen_id():
    """Generate a unique ID."""
    return int(uuid.uuid4().int % (10**15))


# ============================================================================
# Step 1: 在远程 MySQL 创建演示用户
# ============================================================================

def create_demo_users():
    """Create demo users in remote MySQL."""
    print("\n👤 Step 1: Creating demo users in remote MySQL...")

    conn = get_connection(REMOTE_DB_CONFIG)
    try:
        # 检查用户是否已存在
        with conn.cursor() as cur:
            cur.execute("SELECT id, username FROM adh_users")
            existing = {r["username"]: r["id"] for r in cur.fetchall()}
            print(f"  ℹ️  现有用户: {list(existing.keys())}")

        # 创建新用户
        users_to_create = [
            (10, "zhangsan", "张三", "zhangsan@example.com", "analyst"),
            (11, "lisi", "李四", "lisi@example.com", "viewer"),
            (12, "wangwu", "王五", "wangwu@example.com", "analyst"),
            (13, "zhaoliu", "赵六", "zhaoliu@example.com", "viewer"),
        ]

        # admin123 的 bcrypt hash
        pw_hash = "$2b$12$LJ3m4ys3Lz0YBNOURq0Y3OjCfKJmKPOJYqDTPVCKzLOBhZMHfWO6e"

        with conn.cursor() as cur:
            for uid, username, real_name, email, role in users_to_create:
                if username in existing:
                    print(f"  ℹ️  用户 {username} 已存在 (id={existing[username]})")
                    continue

                cur.execute("""
                    INSERT INTO adh_users (id, username, password_hash, email, user_role, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 'active', NOW(), NOW())
                """, (uid, username, pw_hash, email, role))
                print(f"  ✅ 创建用户 {username} (id={uid}, role={role})")

        conn.commit()
        print("  ✅ 演示用户创建完成")
    finally:
        conn.close()


# ============================================================================
# Step 2: 在远程 MySQL 创建工作空间
# ============================================================================

def create_workspaces():
    """Create workspaces in remote MySQL."""
    print("\n🏢 Step 2: Creating workspaces in remote MySQL...")

    conn = get_connection(REMOTE_DB_CONFIG)
    try:
        workspaces = [
            (100, "华东数据", "华东区域数据分析工作空间", "🌊", "#52c41a"),
            (200, "华南数据", "华南区域数据分析工作空间", "🌴", "#fa8c16"),
            (300, "全国数据", "全国数据总览工作空间", "🗺️", "#722ed1"),
        ]

        with conn.cursor() as cur:
            # 检查现有工作空间
            cur.execute("SELECT id, name FROM adh_workspaces")
            existing = {r["id"]: r["name"] for r in cur.fetchall()}
            print(f"  ℹ️  现有工作空间: {list(existing.values())}")

            for ws_id, name, desc, icon, color in workspaces:
                if ws_id in existing:
                    print(f"  ℹ️  工作空间 {name} 已存在")
                    continue

                cur.execute("""
                    INSERT INTO adh_workspaces (id, name, description, icon, color, owner_id, is_default, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 1, 0, NOW(), NOW())
                """, (ws_id, name, desc, icon, color))
                print(f"  ✅ 创建工作空间 {name} (id={ws_id})")

        conn.commit()
        print("  ✅ 工作空间创建完成")
    finally:
        conn.close()


# ============================================================================
# Step 3: 创建用户-工作空间关联
# ============================================================================

def create_workspace_users():
    """Create workspace-user associations."""
    print("\n👥 Step 3: Creating workspace-user associations...")

    conn = get_connection(REMOTE_DB_CONFIG)
    try:
        associations = [
            # (workspace_id, user_id, role)
            (1, 1, "owner"),       # admin → 默认工作空间
            (100, 1, "owner"),     # admin → 华东数据
            (200, 1, "owner"),     # admin → 华南数据
            (300, 1, "owner"),     # admin → 全国数据
            (100, 10, "admin"),    # zhangsan → 华东数据
            (300, 10, "member"),   # zhangsan → 全国数据
            (200, 11, "member"),   # lisi → 华南数据
            (300, 12, "admin"),    # wangwu → 全国数据
            (100, 12, "member"),   # wangwu → 华东数据
            (200, 12, "member"),   # wangwu → 华南数据
            (100, 13, "member"),   # zhaoliu → 华东数据
        ]

        with conn.cursor() as cur:
            for ws_id, user_id, role in associations:
                try:
                    cur.execute("""
                        INSERT IGNORE INTO adh_workspace_users (workspace_id, user_id, role, is_default, joined_at)
                        VALUES (%s, %s, %s, 0, NOW())
                    """, (ws_id, user_id, role))
                except Exception as e:
                    pass  # 忽略重复键错误

        conn.commit()
        print("  ✅ 用户-工作空间关联创建完成")
    finally:
        conn.close()


# ============================================================================
# Step 4: 创建角色和权限
# ============================================================================

def create_roles_and_permissions():
    """Create roles and permissions."""
    print("\n🔐 Step 4: Creating roles and permissions...")

    conn = get_connection(REMOTE_DB_CONFIG)
    try:
        with conn.cursor() as cur:
            # 创建自定义角色
            roles = [
                (100, "region_analyst", "区域分析师", "只能查看本区域数据"),
                (200, "data_viewer", "数据查看者", "只能查看部分表，敏感列脱敏"),
                (300, "full_analyst", "全量分析师", "可查看所有区域数据"),
            ]

            for role_id, name, display_name, desc in roles:
                try:
                    cur.execute("""
                        INSERT IGNORE INTO adh_roles (id, name, display_name, description, is_system)
                        VALUES (%s, %s, %s, %s, 0)
                    """, (role_id, name, display_name, desc))
                except Exception:
                    pass

            print("  ✅ 角色创建完成")

            # 分配用户角色
            user_roles = [
                (1, 1, 0),      # admin → admin 角色 (全局)
                (10, 100, 100),  # zhangsan → 区域分析师 (华东)
                (10, 300, 300),  # zhangsan → 全量分析师 (全国)
                (11, 200, 200),  # lisi → 数据查看者 (华南)
                (12, 300, 300),  # wangwu → 全量分析师 (全国)
                (12, 100, 100),  # wangwu → 区域分析师 (华东)
                (13, 200, 100),  # zhaoliu → 数据查看者 (华东)
            ]

            for user_id, role_id, ws_id in user_roles:
                try:
                    cur.execute("""
                        INSERT IGNORE INTO adh_user_roles (id, user_id, role_id, workspace_id, created_at)
                        VALUES (%s, %s, %s, %s, NOW())
                    """, (gen_id(), user_id, role_id, ws_id))
                except Exception:
                    pass

            print("  ✅ 用户角色分配完成")

            # 创建角色属性（用于 RLS 动态过滤）
            role_attrs = [
                (100, 100, "region", "华东"),  # 区域分析师 → 华东
                (200, 200, "region", "华南"),  # 数据查看者 → 华南
                (300, 300, "region", ""),      # 全量分析师 → 全部
            ]

            for role_id, ws_id, attr_key, attr_val in role_attrs:
                try:
                    cur.execute("""
                        INSERT IGNORE INTO adh_role_attributes (id, role_id, workspace_id, attr_key, attr_value, created_at)
                        VALUES (%s, %s, %s, %s, %s, NOW())
                    """, (gen_id(), role_id, ws_id, attr_key, attr_val))
                except Exception:
                    pass

            print("  ✅ 角色属性创建完成")

        conn.commit()
    finally:
        conn.close()


# ============================================================================
# Step 5: 在本地 MySQL 创建演示业务数据
# ============================================================================

def create_local_demo_data():
    """Create demo business data in local MySQL."""
    print("\n📊 Step 5: Creating demo business data in local MySQL...")

    # 创建数据库
    conn = pymysql.connect(
        host="localhost", port=3306, user="root", password="",
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE DATABASE IF NOT EXISTS demo_business")
        conn.commit()
    finally:
        conn.close()

    # 连接到 demo_business 数据库
    conn = get_connection(LOCAL_DB_CONFIG)
    try:
        with conn.cursor() as cur:
            # 创建表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS demo_users (
                    id BIGINT PRIMARY KEY,
                    username VARCHAR(64) NOT NULL,
                    real_name VARCHAR(64) NOT NULL,
                    email VARCHAR(128) DEFAULT '',
                    phone VARCHAR(20) DEFAULT '',
                    gender VARCHAR(10) DEFAULT '',
                    age INT DEFAULT 0,
                    department VARCHAR(64) DEFAULT '',
                    region VARCHAR(32) DEFAULT '',
                    city VARCHAR(32) DEFAULT '',
                    salary DECIMAL(12,2) DEFAULT 0,
                    hire_date DATE DEFAULT NULL,
                    status VARCHAR(20) DEFAULT 'active',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS demo_orders (
                    id BIGINT PRIMARY KEY,
                    order_no VARCHAR(32) NOT NULL,
                    user_id BIGINT NOT NULL,
                    username VARCHAR(64) DEFAULT '',
                    product_id BIGINT NOT NULL,
                    product_name VARCHAR(128) DEFAULT '',
                    category VARCHAR(64) DEFAULT '',
                    quantity INT DEFAULT 1,
                    unit_price DECIMAL(10,2) DEFAULT 0,
                    amount DECIMAL(12,2) DEFAULT 0,
                    payment_method VARCHAR(32) DEFAULT '',
                    region VARCHAR(32) DEFAULT '',
                    city VARCHAR(32) DEFAULT '',
                    status VARCHAR(20) DEFAULT 'completed',
                    order_date DATE DEFAULT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS demo_products (
                    id BIGINT PRIMARY KEY,
                    name VARCHAR(128) NOT NULL,
                    category VARCHAR(64) DEFAULT '',
                    brand VARCHAR(64) DEFAULT '',
                    price DECIMAL(10,2) DEFAULT 0,
                    cost DECIMAL(10,2) DEFAULT 0,
                    stock INT DEFAULT 0,
                    sales INT DEFAULT 0,
                    rating DECIMAL(3,1) DEFAULT 0,
                    status VARCHAR(20) DEFAULT 'active',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            print("  ✅ 表结构创建完成")

        conn.commit()

        # 生成数据
        with conn.cursor() as cur:
            # 检查是否已有数据
            cur.execute("SELECT COUNT(*) as cnt FROM demo_users")
            if cur.fetchone()["cnt"] > 0:
                print("  ℹ️  数据已存在，跳过插入")
                return

            # 插入用户
            users = []
            for i in range(50):
                region = random.choice(REGIONS)
                city = random.choice(CITIES[region])
                name = DEMO_NAMES[i % len(DEMO_NAMES)]
                user_id = gen_id()
                username = f"user_{i+1:03d}"
                users.append((
                    user_id, username, name, f"{username}@example.com",
                    f"1{random.choice(['3','5','7','8','9'])}{random.randint(100000000, 999999999)}",
                    random.choice(["男", "女"]), random.randint(22, 55),
                    random.choice(DEPARTMENTS), region, city,
                    round(random.uniform(5000, 30000), 2),
                    (datetime.now() - timedelta(days=random.randint(30, 3650))).strftime("%Y-%m-%d"),
                    "active",
                ))

            cur.executemany("""
                INSERT INTO demo_users (id, username, real_name, email, phone, gender, age,
                    department, region, city, salary, hire_date, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, users)
            print(f"  ✅ 插入 {len(users)} 个用户")

            # 插入产品
            products = []
            brands = {
                "电子产品": ["华为", "小米", "苹果"],
                "服装": ["优衣库", "ZARA", "耐克"],
                "食品": ["三只松鼠", "良品铺子"],
                "家居": ["宜家", "林氏木业"],
                "图书": ["人民文学", "机械工业"],
            }
            for i in range(30):
                category = random.choice(PRODUCT_CATEGORIES)
                product_id = gen_id()
                products.append((
                    product_id, f"{random.choice(brands[category])}产品{i+1}",
                    category, random.choice(brands[category]),
                    round(random.uniform(10, 5000), 2),
                    round(random.uniform(5, 2000), 2),
                    random.randint(10, 1000), random.randint(0, 500),
                    round(random.uniform(3.0, 5.0), 1), "active",
                ))

            cur.executemany("""
                INSERT INTO demo_products (id, name, category, brand, price, cost, stock, sales, rating, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, products)
            print(f"  ✅ 插入 {len(products)} 个产品")

            # 插入订单
            orders = []
            for i in range(200):
                user = random.choice(users)
                product = random.choice(products)
                order_id = gen_id()
                quantity = random.randint(1, 5)
                amount = round(product[4] * quantity, 2)  # price * quantity

                orders.append((
                    order_id, f"ORD{datetime.now().strftime('%Y%m%d')}{i+1:06d}",
                    user[0], user[1],  # user_id, username
                    product[0], product[1], product[2],  # product_id, name, category
                    quantity, product[4], amount,
                    random.choice(PAYMENT_METHODS),
                    user[8], user[9],  # region, city
                    random.choice(["completed", "completed", "completed", "pending"]),
                    (datetime.now() - timedelta(days=random.randint(0, 365))).strftime("%Y-%m-%d"),
                ))

            cur.executemany("""
                INSERT INTO demo_orders (id, order_no, user_id, username, product_id, product_name,
                    category, quantity, unit_price, amount, payment_method, region, city, status, order_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, orders)
            print(f"  ✅ 插入 {len(orders)} 个订单")

        conn.commit()
        print("  ✅ 本地演示数据创建完成")

        # 统计
        with conn.cursor() as cur:
            cur.execute("SELECT region, COUNT(*) as cnt FROM demo_users GROUP BY region")
            regions = cur.fetchall()
            print("\n  📍 用户区域分布:")
            for r in regions:
                print(f"     {r['region']}: {r['cnt']} 人")
    finally:
        conn.close()


# ============================================================================
# Step 6: 在远程 MySQL 注册本地数据源
# ============================================================================

def register_local_datasource():
    """Register local MySQL as datasource in remote MySQL."""
    print("\n📋 Step 6: Registering local MySQL as datasource...")

    conn = get_connection(REMOTE_DB_CONFIG)
    try:
        with conn.cursor() as cur:
            # 检查是否已存在
            cur.execute("SELECT id FROM adh_datasources WHERE name = 'demo_business'")
            existing = cur.fetchone()

            if existing:
                ds_id = existing["id"]
                print(f"  ℹ️  数据源已存在 (id={ds_id})")
            else:
                ds_id = gen_id()
                cur.execute("""
                    INSERT INTO adh_datasources (id, name, db_type, host, port, username, password, database_name, is_default, owner_id, created_at, updated_at)
                    VALUES (%s, 'demo_business', 'mysql', 'localhost', 3306, 'root', '', 'demo_business', 0, 1, NOW(), NOW())
                """, (ds_id,))
                print(f"  ✅ 注册数据源 (id={ds_id})")

            # 注册表元数据
            tables = [
                ("demo_users", "用户表", [
                    ("id", "BIGINT", "用户ID"), ("username", "VARCHAR", "用户名"),
                    ("real_name", "VARCHAR", "真实姓名"), ("email", "VARCHAR", "邮箱"),
                    ("phone", "VARCHAR", "手机号"), ("gender", "VARCHAR", "性别"),
                    ("age", "INT", "年龄"), ("department", "VARCHAR", "部门"),
                    ("region", "VARCHAR", "区域"), ("city", "VARCHAR", "城市"),
                    ("salary", "DECIMAL", "薪资"), ("hire_date", "DATE", "入职日期"),
                    ("status", "VARCHAR", "状态"),
                ]),
                ("demo_orders", "订单表", [
                    ("id", "BIGINT", "订单ID"), ("order_no", "VARCHAR", "订单号"),
                    ("user_id", "BIGINT", "用户ID"), ("username", "VARCHAR", "用户名"),
                    ("product_id", "BIGINT", "产品ID"), ("product_name", "VARCHAR", "产品名称"),
                    ("category", "VARCHAR", "产品分类"), ("quantity", "INT", "数量"),
                    ("unit_price", "DECIMAL", "单价"), ("amount", "DECIMAL", "订单金额"),
                    ("payment_method", "VARCHAR", "支付方式"), ("region", "VARCHAR", "区域"),
                    ("city", "VARCHAR", "城市"), ("status", "VARCHAR", "状态"),
                    ("order_date", "DATE", "订单日期"),
                ]),
                ("demo_products", "产品表", [
                    ("id", "BIGINT", "产品ID"), ("name", "VARCHAR", "产品名称"),
                    ("category", "VARCHAR", "分类"), ("brand", "VARCHAR", "品牌"),
                    ("price", "DECIMAL", "价格"), ("cost", "DECIMAL", "成本"),
                    ("stock", "INT", "库存"), ("sales", "INT", "销量"),
                    ("rating", "DECIMAL", "评分"), ("status", "VARCHAR", "状态"),
                ]),
            ]

            for table_name, table_desc, columns in tables:
                # 检查表是否已注册
                cur.execute("SELECT id FROM adh_table_info WHERE table_name = %s AND datasource_id = %s",
                           (table_name, ds_id))
                if cur.fetchone():
                    print(f"  ℹ️  表 {table_name} 已注册")
                    continue

                # 注册表
                table_id = gen_id()
                cur.execute("""
                    INSERT INTO adh_table_info (id, table_name, table_comment, datasource_id, is_active, sync_time)
                    VALUES (%s, %s, %s, %s, 1, NOW())
                """, (table_id, table_name, table_desc, ds_id))

                # 注册列
                for col_name, col_type, col_desc in columns:
                    col_id = gen_id()
                    cur.execute("""
                        INSERT INTO adh_column_metadata (id, table_name, column_name, data_type, column_comment, datasource_id, is_active, sync_time)
                        VALUES (%s, %s, %s, %s, %s, %s, 1, NOW())
                    """, (col_id, table_name, col_name, col_type, col_desc, ds_id))

                print(f"  ✅ 注册表 {table_name} ({len(columns)} 列)")

            # 关联数据源到工作空间
            for ws_id in [1, 100, 200, 300]:
                cur.execute("""
                    INSERT IGNORE INTO adh_workspace_datasources (workspace_id, datasource_id, is_primary)
                    VALUES (%s, %s, 1)
                """, (ws_id, ds_id))

        conn.commit()
        print("  ✅ 数据源注册完成")
        return ds_id
    finally:
        conn.close()


# ============================================================================
# Step 7: 创建 RLS 策略
# ============================================================================

def create_rls_policies(ds_id):
    """Create RLS policies in remote MySQL."""
    print("\n🔒 Step 7: Creating RLS policies...")

    conn = get_connection(REMOTE_DB_CONFIG)
    try:
        with conn.cursor() as cur:
            # RLS 策略
            policies = [
                (7001, "华东订单过滤", "华东数据只看华东订单", 100, "demo_orders", "region = :user_region", "region"),
                (7002, "华东用户过滤", "华东数据只看华东用户", 100, "demo_users", "region = :user_region", "region"),
                (7003, "华南订单过滤", "华南数据只看华南订单", 200, "demo_orders", "region = :user_region", "region"),
                (7004, "华南用户过滤", "华南数据只看华南用户", 200, "demo_users", "region = :user_region", "region"),
            ]

            for pid, name, desc, ws_id, table, filter_expr, user_attr in policies:
                try:
                    cur.execute("""
                        INSERT IGNORE INTO adh_rls_policies
                        (id, name, description, workspace_id, datasource_id, table_name,
                         policy_type, filter_type, filter_expr, user_attribute, is_active, created_by)
                        VALUES (%s, %s, %s, %s, %s, %s, 'both', 'user_attribute', %s, %s, 1, 1)
                    """, (pid, name, desc, ws_id, ds_id, table, filter_expr, user_attr))
                except Exception:
                    pass

            print("  ✅ RLS 策略创建完成")

            # 列级策略
            column_policies = [
                (8001, 7002, "salary", "hidden", "", "华东不看薪资"),
                (8002, 7002, "phone", "masked", "partial", "华东电话脱敏"),
                (8003, 7004, "salary", "hidden", "", "华南不看薪资"),
            ]

            for cid, pid, col, access_type, mask, desc in column_policies:
                try:
                    cur.execute("""
                        INSERT IGNORE INTO adh_rls_column_policies
                        (id, policy_id, column_name, access_type, mask_pattern, description)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (cid, pid, col, access_type, mask, desc))
                except Exception:
                    pass

            print("  ✅ 列级策略创建完成")

            # 用户属性
            user_attrs = [
                (10, 100, "region", "华东"),  # zhangsan → 华东
                (11, 200, "region", "华南"),  # lisi → 华南
                (12, 100, "region", "华东"),  # wangwu → 华东
                (13, 100, "region", "华东"),  # zhaoliu → 华东
            ]

            for uid, ws_id, attr_key, attr_val in user_attrs:
                try:
                    cur.execute("""
                        INSERT IGNORE INTO adh_rls_user_attributes
                        (id, user_id, workspace_id, attr_key, attr_value)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (gen_id(), uid, ws_id, attr_key, attr_val))
                except Exception:
                    pass

            print("  ✅ 用户属性创建完成")

        conn.commit()
    finally:
        conn.close()


# ============================================================================
# 验证
# ============================================================================

def verify():
    """Verify all data."""
    print("\n" + "=" * 60)
    print("✅ 演示数据初始化完成！")
    print("=" * 60)

    # 远程 MySQL
    conn = get_connection(REMOTE_DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM adh_users")
            users = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) as cnt FROM adh_workspaces")
            workspaces = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) as cnt FROM adh_user_roles")
            user_roles = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) as cnt FROM adh_rls_policies")
            rls = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) as cnt FROM adh_datasources WHERE name = 'demo_business'")
            ds = cur.fetchone()["cnt"]

            print(f"\n📊 远程 MySQL (元数据):")
            print(f"   用户: {users}, 工作空间: {workspaces}, 用户角色: {user_roles}")
            print(f"   RLS策略: {rls}, 数据源: {ds}")
    finally:
        conn.close()

    # 本地 MySQL
    conn = get_connection(LOCAL_DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM demo_users")
            users = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) as cnt FROM demo_orders")
            orders = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) as cnt FROM demo_products")
            products = cur.fetchone()["cnt"]

            print(f"\n📊 本地 MySQL (业务数据):")
            print(f"   用户: {users}, 订单: {orders}, 产品: {products}")
    finally:
        conn.close()

    print("\n📝 演示账号 (密码: admin123):")
    print("   - admin     : 管理员，全部权限")
    print("   - zhangsan  : 华东分析师")
    print("   - lisi      : 华南查看者")
    print("   - wangwu    : 全国分析师")
    print("   - zhaoliu   : 华东查看者")


if __name__ == "__main__":
    create_demo_users()
    create_workspaces()
    create_workspace_users()
    create_roles_and_permissions()
    create_local_demo_data()
    ds_id = register_local_datasource()
    create_rls_policies(ds_id)
    verify()
