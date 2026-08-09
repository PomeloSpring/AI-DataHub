#!/usr/bin/env python3
"""初始化演示数据库和演示数据

创建业务演示表并插入带区域分布的测试数据，用于验证行级/列级/工作空间权限。

使用方式:
    python scripts/init_demo_data.py

演示场景:
    - 4个区域: 华东、华南、华北、西南
    - 5个用户角色: admin、analyst、viewer、region_analyst、data_viewer
    - 3个工作空间: 华东数据、华南数据、全国数据
    - RLS策略: 按工作空间过滤区域数据
    - 列级权限: salary隐藏、phone/email脱敏
"""

import sys
import os
import random
import time
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pymysql
from services.shared.common.config import (
    DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, DORIS_DATABASE,
    METADATA_DB_HOST, METADATA_DB_PORT, METADATA_DB_USER,
    METADATA_DB_PASSWORD, METADATA_DB_DATABASE,
)


# ============================================================================
# Configuration
# ============================================================================

DEMO_DB_NAME = METADATA_DB_DATABASE  # 使用同一个MySQL数据库存放演示表

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

# Usernames for demo
DEMO_NAMES = [
    "张伟", "李娜", "王芳", "刘洋", "陈明", "杨静", "赵磊", "黄丽",
    "周强", "吴敏", "徐超", "孙艳", "马军", "朱琳", "胡斌", "郭婷",
    "何勇", "高雪", "林涛", "罗慧", "梁杰", "宋颖", "唐浩", "韩璐",
    "冯刚", "董梅", "程鹏", "曹倩", "袁明", "邓丽", "许超", "傅敏",
    "沈洋", "曾艳", "彭磊", "吕琳", "苏军", "卢婷", "蒋勇", "蔡雪",
    "贾涛", "丁慧", "魏杰", "薛颖", "叶浩", "阎璐", "余刚", "潘梅",
    "杜鹏", "戴倩",
]


def get_metadata_connection():
    """Get connection to metadata database (MySQL)."""
    return pymysql.connect(
        host=METADATA_DB_HOST,
        port=METADATA_DB_PORT,
        user=METADATA_DB_USER,
        password=METADATA_DB_PASSWORD,
        database=METADATA_DB_DATABASE,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def get_doris_connection(database=None):
    """Get connection to Doris."""
    return pymysql.connect(
        host=DORIS_HOST,
        port=DORIS_PORT,
        user=DORIS_USER,
        password=DORIS_PASSWORD,
        database=database or DORIS_DATABASE,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


import uuid

def gen_id():
    """Generate a unique ID using UUID."""
    return int(uuid.uuid4().int % (10**15))


# ============================================================================
# Step 1: Create Demo Database and Tables in Doris
# ============================================================================

def create_demo_database():
    """Create demo business tables in MySQL."""
    print("\n📦 Step 1: Creating demo tables...")

    conn = get_metadata_connection()
    try:
        with conn.cursor() as cur:
            # ── Users table ──
            cur.execute("""
                CREATE TABLE IF NOT EXISTS `demo_users` (
                    `id` BIGINT NOT NULL PRIMARY KEY,
                    `username` VARCHAR(64) NOT NULL COMMENT '用户名',
                    `real_name` VARCHAR(64) NOT NULL COMMENT '真实姓名',
                    `email` VARCHAR(128) DEFAULT '' COMMENT '邮箱',
                    `phone` VARCHAR(20) DEFAULT '' COMMENT '手机号',
                    `gender` VARCHAR(10) DEFAULT '' COMMENT '性别',
                    `age` INT DEFAULT 0 COMMENT '年龄',
                    `department` VARCHAR(64) DEFAULT '' COMMENT '部门',
                    `region` VARCHAR(32) DEFAULT '' COMMENT '区域',
                    `city` VARCHAR(32) DEFAULT '' COMMENT '城市',
                    `salary` DECIMAL(12,2) DEFAULT 0 COMMENT '薪资',
                    `hire_date` DATE DEFAULT NULL COMMENT '入职日期',
                    `status` VARCHAR(20) DEFAULT 'active' COMMENT '状态',
                    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
                    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='演示用户表'
            """)
            print("  ✅ Table 'demo_users' created")

            # ── Orders table ──
            cur.execute("""
                CREATE TABLE IF NOT EXISTS `demo_orders` (
                    `id` BIGINT NOT NULL PRIMARY KEY,
                    `order_no` VARCHAR(32) NOT NULL COMMENT '订单号',
                    `user_id` BIGINT NOT NULL COMMENT '用户ID',
                    `username` VARCHAR(64) DEFAULT '' COMMENT '用户名',
                    `product_id` BIGINT NOT NULL COMMENT '产品ID',
                    `product_name` VARCHAR(128) DEFAULT '' COMMENT '产品名称',
                    `category` VARCHAR(64) DEFAULT '' COMMENT '产品分类',
                    `quantity` INT DEFAULT 1 COMMENT '数量',
                    `unit_price` DECIMAL(10,2) DEFAULT 0 COMMENT '单价',
                    `amount` DECIMAL(12,2) DEFAULT 0 COMMENT '订单金额',
                    `payment_method` VARCHAR(32) DEFAULT '' COMMENT '支付方式',
                    `region` VARCHAR(32) DEFAULT '' COMMENT '区域',
                    `city` VARCHAR(32) DEFAULT '' COMMENT '城市',
                    `status` VARCHAR(20) DEFAULT 'completed' COMMENT '状态',
                    `order_date` DATE DEFAULT NULL COMMENT '订单日期',
                    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='演示订单表'
            """)
            print("  ✅ Table 'demo_orders' created")

            # ── Products table ──
            cur.execute("""
                CREATE TABLE IF NOT EXISTS `demo_products` (
                    `id` BIGINT NOT NULL PRIMARY KEY,
                    `name` VARCHAR(128) NOT NULL COMMENT '产品名称',
                    `category` VARCHAR(64) DEFAULT '' COMMENT '分类',
                    `brand` VARCHAR(64) DEFAULT '' COMMENT '品牌',
                    `price` DECIMAL(10,2) DEFAULT 0 COMMENT '价格',
                    `cost` DECIMAL(10,2) DEFAULT 0 COMMENT '成本',
                    `stock` INT DEFAULT 0 COMMENT '库存',
                    `sales` INT DEFAULT 0 COMMENT '销量',
                    `rating` DECIMAL(3,1) DEFAULT 0 COMMENT '评分',
                    `status` VARCHAR(20) DEFAULT 'active' COMMENT '状态',
                    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='演示产品表'
            """)
            print("  ✅ Table 'demo_products' created")

            # ── Payments table ──
            cur.execute("""
                CREATE TABLE IF NOT EXISTS `demo_payments` (
                    `id` BIGINT NOT NULL PRIMARY KEY,
                    `order_id` BIGINT NOT NULL COMMENT '订单ID',
                    `order_no` VARCHAR(32) DEFAULT '' COMMENT '订单号',
                    `user_id` BIGINT NOT NULL COMMENT '用户ID',
                    `amount` DECIMAL(12,2) DEFAULT 0 COMMENT '支付金额',
                    `payment_method` VARCHAR(32) DEFAULT '' COMMENT '支付方式',
                    `status` VARCHAR(20) DEFAULT 'success' COMMENT '状态',
                    `paid_at` DATETIME DEFAULT NULL COMMENT '支付时间',
                    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='演示支付表'
            """)
            print("  ✅ Table 'demo_payments' created")

        conn.commit()
        print("  ✅ All demo tables created successfully")
    finally:
        conn.close()


# ============================================================================
# Step 2: Generate and Insert Demo Data
# ============================================================================

def generate_users(conn, count=50):
    """Generate demo users with region distribution."""
    print(f"\n👤 Step 2a: Generating {count} demo users...")

    users = []
    for i in range(count):
        region = random.choice(REGIONS)
        city = random.choice(CITIES[region])
        name = DEMO_NAMES[i % len(DEMO_NAMES)]
        gender = random.choice(["男", "女"])
        age = random.randint(22, 55)
        dept = random.choice(DEPARTMENTS)
        salary = round(random.uniform(5000, 30000), 2)
        hire_date = (datetime.now() - timedelta(days=random.randint(30, 3650))).strftime("%Y-%m-%d")

        user_id = gen_id()
        username = f"user_{i+1:03d}"
        email = f"{username}@example.com"
        phone = f"1{random.choice(['3','5','7','8','9'])}{random.randint(100000000, 999999999)}"

        users.append((
            user_id, username, name, email, phone, gender, age,
            dept, region, city, salary, hire_date, "active",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO `demo_users`
            (id, username, real_name, email, phone, gender, age,
             department, region, city, salary, hire_date, status,
             created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, users)

    conn.commit()
    print(f"  ✅ Inserted {count} users")
    return users


def generate_products(conn, count=30):
    """Generate demo products."""
    print(f"\n📦 Step 2b: Generating {count} demo products...")

    products = []
    brands = {
        "电子产品": ["华为", "小米", "苹果", "三星", "OPPO"],
        "服装": ["优衣库", "ZARA", "H&M", "耐克", "阿迪达斯"],
        "食品": ["三只松鼠", "良品铺子", "百草味", "蒙牛", "伊利"],
        "家居": ["宜家", "林氏木业", "全友", "顾家", "芝华仕"],
        "图书": ["人民文学", "机械工业", "清华大学", "电子工业", "人民邮电"],
    }

    for i in range(count):
        category = random.choice(PRODUCT_CATEGORIES)
        brand = random.choice(brands[category])
        price = round(random.uniform(10, 5000), 2)
        cost = round(price * random.uniform(0.3, 0.7), 2)
        stock = random.randint(10, 1000)
        sales = random.randint(0, 500)
        rating = round(random.uniform(3.0, 5.0), 1)

        product_id = gen_id()
        product_name = f"{brand}{category}产品{i+1}"

        products.append((
            product_id, product_name, category, brand, price, cost,
            stock, sales, rating, "active",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO `demo_products`
            (id, name, category, brand, price, cost, stock, sales, rating,
             status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, products)

    conn.commit()
    print(f"  ✅ Inserted {count} products")
    return products


def generate_orders(conn, users, products, count=200):
    """Generate demo orders with region distribution."""
    print(f"\n🛒 Step 2c: Generating {count} demo orders...")

    orders = []
    for i in range(count):
        user = random.choice(users)
        product = random.choice(products)

        user_id = user[0]
        username = user[1]
        region = user[8]  # region
        city = user[9]    # city

        product_id = product[0]
        product_name = product[1]
        category = product[2]
        unit_price = product[4]  # price

        quantity = random.randint(1, 5)
        amount = round(unit_price * quantity, 2)
        payment_method = random.choice(PAYMENT_METHODS)
        order_date = (datetime.now() - timedelta(days=random.randint(0, 365))).strftime("%Y-%m-%d")
        status = random.choice(["completed", "completed", "completed", "pending", "cancelled"])

        order_id = gen_id()
        order_no = f"ORD{datetime.now().strftime('%Y%m%d')}{i+1:06d}"

        orders.append((
            order_id, order_no, user_id, username, product_id, product_name,
            category, quantity, unit_price, amount, payment_method,
            region, city, status, order_date,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO `demo_orders`
            (id, order_no, user_id, username, product_id, product_name,
             category, quantity, unit_price, amount, payment_method,
             region, city, status, order_date, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, orders)

    conn.commit()
    print(f"  ✅ Inserted {count} orders")
    return orders


def generate_payments(conn, orders):
    """Generate payments for completed orders."""
    print(f"\n💰 Step 2d: Generating payments for completed orders...")

    payments = []
    for order in orders:
        if order[13] != "completed":  # status
            continue

        order_id = order[0]
        order_no = order[1]
        user_id = order[2]
        amount = order[9]  # amount
        payment_method = order[10]  # payment_method
        paid_at = datetime.now() - timedelta(days=random.randint(0, 365))

        payment_id = gen_id()
        payments.append((
            payment_id, order_id, order_no, user_id, amount,
            payment_method, "success", paid_at.strftime("%Y-%m-%d %H:%M:%S"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

    # Batch insert
    batch_size = 100
    with conn.cursor() as cur:
        for i in range(0, len(payments), batch_size):
            batch = payments[i:i+batch_size]
            cur.executemany("""
                INSERT INTO `demo_payments`
                (id, order_id, order_no, user_id, amount,
                 payment_method, status, paid_at, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, batch)

    conn.commit()
    print(f"  ✅ Inserted {len(payments)} payments")
    return payments


# ============================================================================
# Step 3: Register Demo Tables in Metadata
# ============================================================================

def register_demo_datasource(conn):
    """Register demo datasource in adh_datasources."""
    print("\n📋 Step 3a: Registering demo datasource...")

    with conn.cursor() as cur:
        # Check if demo datasource already exists
        cur.execute("SELECT id FROM adh_datasources WHERE name = 'demo_business'")
        existing = cur.fetchone()

        if existing:
            ds_id = existing["id"]
            print(f"  ℹ️  Demo datasource already exists (id={ds_id})")
            return ds_id

        # Create new datasource
        ds_id = gen_id()
        cur.execute("""
            INSERT INTO adh_datasources
            (id, name, db_type, host, port, username, password, database_name, is_default, owner_id, created_at, updated_at)
            VALUES (%s, 'demo_business', 'doris', %s, %s, %s, %s, %s, 0, 1, NOW(), NOW())
        """, (ds_id, DORIS_HOST, DORIS_PORT, DORIS_USER, DORIS_PASSWORD, DEMO_DB_NAME))

    conn.commit()
    print(f"  ✅ Demo datasource registered (id={ds_id})")
    return ds_id


def register_demo_tables(conn, ds_id):
    """Register demo tables in adh_table_info and adh_column_metadata."""
    print("\n📋 Step 3b: Registering demo tables in metadata...")

    tables = {
        "users": {
            "description": "用户表 - 包含员工基本信息、薪资、区域归属",
            "columns": [
                ("id", "BIGINT", "用户ID"),
                ("username", "VARCHAR", "用户名"),
                ("real_name", "VARCHAR", "真实姓名"),
                ("email", "VARCHAR", "邮箱"),
                ("phone", "VARCHAR", "手机号"),
                ("gender", "VARCHAR", "性别"),
                ("age", "INT", "年龄"),
                ("department", "VARCHAR", "部门"),
                ("region", "VARCHAR", "区域"),
                ("city", "VARCHAR", "城市"),
                ("salary", "DECIMAL", "薪资"),
                ("hire_date", "DATE", "入职日期"),
                ("status", "VARCHAR", "状态"),
            ],
        },
        "orders": {
            "description": "订单表 - 包含订单信息、产品、金额、区域",
            "columns": [
                ("id", "BIGINT", "订单ID"),
                ("order_no", "VARCHAR", "订单号"),
                ("user_id", "BIGINT", "用户ID"),
                ("username", "VARCHAR", "用户名"),
                ("product_id", "BIGINT", "产品ID"),
                ("product_name", "VARCHAR", "产品名称"),
                ("category", "VARCHAR", "产品分类"),
                ("quantity", "INT", "数量"),
                ("unit_price", "DECIMAL", "单价"),
                ("amount", "DECIMAL", "订单金额"),
                ("payment_method", "VARCHAR", "支付方式"),
                ("region", "VARCHAR", "区域"),
                ("city", "VARCHAR", "城市"),
                ("status", "VARCHAR", "状态"),
                ("order_date", "DATE", "订单日期"),
            ],
        },
        "products": {
            "description": "产品表 - 包含产品信息、价格、库存",
            "columns": [
                ("id", "BIGINT", "产品ID"),
                ("name", "VARCHAR", "产品名称"),
                ("category", "VARCHAR", "分类"),
                ("brand", "VARCHAR", "品牌"),
                ("price", "DECIMAL", "价格"),
                ("cost", "DECIMAL", "成本"),
                ("stock", "INT", "库存"),
                ("sales", "INT", "销量"),
                ("rating", "DECIMAL", "评分"),
                ("status", "VARCHAR", "状态"),
            ],
        },
        "payments": {
            "description": "支付表 - 包含支付记录",
            "columns": [
                ("id", "BIGINT", "支付ID"),
                ("order_id", "BIGINT", "订单ID"),
                ("order_no", "VARCHAR", "订单号"),
                ("user_id", "BIGINT", "用户ID"),
                ("amount", "DECIMAL", "支付金额"),
                ("payment_method", "VARCHAR", "支付方式"),
                ("status", "VARCHAR", "状态"),
                ("paid_at", "DATETIME", "支付时间"),
            ],
        },
    }

    with conn.cursor() as cur:
        for table_name, table_info in tables.items():
            # Check if table already registered
            cur.execute(
                "SELECT id FROM adh_table_info WHERE table_name = %s AND datasource_id = %s",
                (table_name, ds_id)
            )
            if cur.fetchone():
                print(f"  ℹ️  Table '{table_name}' already registered")
                continue

            # Register table
            table_id = gen_id()
            cur.execute("""
                INSERT INTO adh_table_info
                (id, table_name, table_comment, datasource_id, is_active, sync_time)
                VALUES (%s, %s, %s, %s, 1, NOW())
            """, (table_id, table_name, table_info["description"], ds_id))

            # Register columns
            for col_name, col_type, col_desc in table_info["columns"]:
                col_id = gen_id()
                cur.execute("""
                    INSERT INTO adh_column_metadata
                    (id, table_name, column_name, data_type, column_comment, datasource_id, is_active, sync_time)
                    VALUES (%s, %s, %s, %s, %s, %s, 1, NOW())
                """, (col_id, table_name, col_name, col_type, col_desc, ds_id))

            print(f"  ✅ Table '{table_name}' registered with {len(table_info['columns'])} columns")

    conn.commit()


# ============================================================================
# Step 4: Register Demo Tables in Workspace
# ============================================================================

def register_tables_in_workspace(conn, ds_id):
    """Register demo tables in workspace datasource associations."""
    print("\n📋 Step 4: Registering tables in workspaces...")

    with conn.cursor() as cur:
        # Associate datasource with workspaces
        for ws_id in [1, 100, 200, 300]:
            cur.execute("""
                INSERT IGNORE INTO adh_workspace_datasources (workspace_id, datasource_id, is_primary)
                VALUES (%s, %s, 1)
            """, (ws_id, ds_id))

    conn.commit()
    print("  ✅ Datasource associated with all workspaces")


# ============================================================================
# Step 5: Apply Permission Migration
# ============================================================================

def apply_permission_migration(conn):
    """Apply permission demo migration SQL."""
    print("\n🔐 Step 5: Applying permission demo migration...")

    migration_file = os.path.join(
        os.path.dirname(__file__), "..", "docker", "mysql", "permission_demo_migration.sql"
    )

    if not os.path.exists(migration_file):
        print(f"  ⚠️  Migration file not found: {migration_file}")
        return

    with open(migration_file, "r") as f:
        sql_content = f.read()

    # Split by semicolons and execute each statement
    statements = [s.strip() for s in sql_content.split(";") if s.strip() and not s.strip().startswith("--")]

    with conn.cursor() as cur:
        for i, stmt in enumerate(statements):
            if not stmt or stmt.startswith("--"):
                continue
            try:
                cur.execute(stmt)
            except Exception as e:
                # Ignore duplicate key errors (idempotent)
                if "Duplicate" not in str(e) and "already exists" not in str(e):
                    print(f"  ⚠️  Statement {i+1} failed: {str(e)[:100]}")

    conn.commit()
    print("  ✅ Permission migration applied")


# ============================================================================
# Step 6: Verify Data
# ============================================================================

def verify_data():
    """Verify demo data was inserted correctly."""
    print("\n✅ Step 6: Verifying demo data...")

    conn = get_metadata_connection()
    try:
        with conn.cursor() as cur:
            tables = ["demo_users", "demo_orders", "demo_products", "demo_payments"]
            for table in tables:
                cur.execute(f"SELECT COUNT(*) as cnt FROM `{table}`")
                count = cur.fetchone()["cnt"]
                print(f"  📊 {table}: {count} rows")

            # Check region distribution
            cur.execute("SELECT region, COUNT(*) as cnt FROM demo_users GROUP BY region ORDER BY cnt DESC")
            regions = cur.fetchall()
            print("\n  📍 用户区域分布:")
            for r in regions:
                print(f"     {r['region']}: {r['cnt']} 人")

            cur.execute("SELECT region, COUNT(*) as cnt, SUM(amount) as total FROM demo_orders GROUP BY region ORDER BY total DESC")
            regions = cur.fetchall()
            print("\n  📍 订单区域分布:")
            for r in regions:
                print(f"     {r['region']}: {r['cnt']} 单, 金额 ¥{r['total']:,.2f}")

            # Check permission tables
            cur.execute("SELECT COUNT(*) as cnt FROM adh_datasources WHERE name = 'demo_business'")
            ds_count = cur.fetchone()["cnt"]
            print(f"\n  📋 数据源注册: {ds_count} 个")

            cur.execute("SELECT COUNT(*) as cnt FROM adh_roles")
            role_count = cur.fetchone()["cnt"]
            print(f"  📋 角色数量: {role_count} 个")

            cur.execute("SELECT COUNT(*) as cnt FROM adh_user_roles")
            ur_count = cur.fetchone()["cnt"]
            print(f"  📋 用户角色分配: {ur_count} 条")

            cur.execute("SELECT COUNT(*) as cnt FROM adh_rls_policies")
            rls_count = cur.fetchone()["cnt"]
            print(f"  📋 RLS策略: {rls_count} 条")
    finally:
        conn.close()


# ============================================================================
# Main
# ============================================================================

def main():
    print("=" * 60)
    print("🚀 AI-DataHub 演示数据初始化")
    print("=" * 60)

    # Step 1: Create demo database and tables
    create_demo_database()

    # Step 2: Generate demo data
    conn = get_metadata_connection()
    try:
        users = generate_users(conn, count=50)
        products = generate_products(conn, count=30)
        orders = generate_orders(conn, users, products, count=200)
        generate_payments(conn, orders)
    finally:
        conn.close()

    # Step 3: Register in metadata
    meta_conn = get_metadata_connection()
    try:
        ds_id = register_demo_datasource(meta_conn)
        register_demo_tables(meta_conn, ds_id)

        # Step 4: Register in workspace
        register_tables_in_workspace(meta_conn, ds_id)

        # Step 5: Apply permission migration
        apply_permission_migration(meta_conn)
    finally:
        meta_conn.close()

    # Step 6: Verify
    verify_data()

    print("\n" + "=" * 60)
    print("✅ 演示数据初始化完成！")
    print("=" * 60)
    print("\n📝 演示账号 (密码都是 admin123):")
    print("   - admin     : 管理员，全部权限")
    print("   - zhangsan  : 华东分析师，只能看华东数据")
    print("   - lisi      : 华南查看者，只能看华南orders，salary隐藏")
    print("   - wangwu    : 全国分析师，可看所有数据")
    print("   - zhaoliu   : 华东查看者，只能看华东orders，amount隐藏")
    print("\n🏢 工作空间:")
    print("   - 华东数据(100): 只看华东区域数据")
    print("   - 华南数据(200): 只看华南区域数据")
    print("   - 全国数据(300): 看所有区域数据")
    print()


if __name__ == "__main__":
    main()
