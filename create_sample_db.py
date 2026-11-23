"""
Creates sample.db — a small e-commerce SQLite database with enough
relational structure (4 tables, FKs, 1-to-many + many-to-many via
order_items) to exercise real joins.

Tables:
  customers(customer_id, name, email, country, signup_date)
  products(product_id, name, category, price, stock)
  orders(order_id, customer_id, order_date, status)
  order_items(order_item_id, order_id, product_id, quantity, unit_price)
"""
import sqlite3
import random
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "sample.db"

SCHEMA = """
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    country     TEXT NOT NULL,
    signup_date TEXT NOT NULL
);

CREATE TABLE products (
    product_id INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    category   TEXT NOT NULL,
    price      REAL NOT NULL,
    stock      INTEGER NOT NULL
);

CREATE TABLE orders (
    order_id    INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    order_date  TEXT NOT NULL,
    status      TEXT NOT NULL CHECK(status IN ('pending','shipped','delivered','cancelled')),
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL,
    product_id    INTEGER NOT NULL,
    quantity      INTEGER NOT NULL,
    unit_price    REAL NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);
"""

FIRST_NAMES = ["Asha","Ravi","Meera","John","Priya","Wei","Sofia","Liam","Fatima","Noah",
               "Ananya","Carlos","Emma","Kenji","Zara","Ivan","Grace","Omar","Elena","Tom"]
LAST_NAMES = ["Sharma","Patel","Smith","Chen","Kumar","Garcia","Muller","Silva","Khan","Rossi"]
COUNTRIES = ["India","USA","UK","Germany","Brazil","Japan","Canada","France","UAE","Australia"]

CATEGORIES = {
    "Electronics": ["Wireless Mouse","USB-C Hub","Mechanical Keyboard","4K Monitor","Bluetooth Speaker","Webcam"],
    "Books": ["Learn SQL Fast","Python for Data","The Midnight Library","Atomic Habits","Deep Work"],
    "Home": ["Ceramic Mug Set","LED Desk Lamp","Throw Blanket","Air Purifier","Scented Candle"],
    "Sports": ["Yoga Mat","Resistance Bands","Water Bottle","Running Shoes","Dumbbell Set"],
    "Clothing": ["Cotton T-Shirt","Denim Jacket","Wool Sweater","Running Shorts","Rain Jacket"],
}

STATUSES = ["pending", "shipped", "delivered", "delivered", "delivered", "cancelled"]  # weighted


def random_date(start_year=2023):
    start = date(start_year, 1, 1)
    days = random.randint(0, 700)
    return (start + timedelta(days=days)).isoformat()


def build():
    random.seed(42)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript(SCHEMA)

    # customers
    customers = []
    for i in range(1, 41):
        fn, ln = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
        name = f"{fn} {ln}"
        email = f"{fn.lower()}.{ln.lower()}{i}@example.com"
        customers.append((i, name, email, random.choice(COUNTRIES), random_date(2022)))
    cur.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", customers)

    # products
    products = []
    pid = 1
    for cat, items in CATEGORIES.items():
        for item in items:
            price = round(random.uniform(5, 250), 2)
            stock = random.randint(0, 200)
            products.append((pid, item, cat, price, stock))
            pid += 1
    cur.executemany("INSERT INTO products VALUES (?,?,?,?,?)", products)

    # orders + order_items
    orders = []
    items = []
    oid = 1
    item_id = 1
    for cust in customers:
        num_orders = random.randint(0, 6)
        for _ in range(num_orders):
            odate = random_date(2023)
            status = random.choice(STATUSES)
            orders.append((oid, cust[0], odate, status))
            num_items = random.randint(1, 4)
            chosen = random.sample(products, num_items)
            for prod in chosen:
                qty = random.randint(1, 5)
                items.append((item_id, oid, prod[0], qty, prod[3]))
                item_id += 1
            oid += 1
    cur.executemany("INSERT INTO orders VALUES (?,?,?,?)", orders)
    cur.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", items)

    conn.commit()
    conn.close()
    print(f"Built {DB_PATH}: {len(customers)} customers, {len(products)} products, "
          f"{len(orders)} orders, {len(items)} order_items.")


if __name__ == "__main__":
    build()
