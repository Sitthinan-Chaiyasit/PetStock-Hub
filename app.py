"""PetStock Hub: local-first inventory application for a central warehouse and branches."""
import csv
import io
import os
import sqlite3
import secrets
import re
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from functools import wraps

from flask import Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # Local SQLite-only development remains possible.
    psycopg = None
    dict_row = None

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DEFAULT_SECRET_KEY = "change-this-before-production"
DATABASE_URL = (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or "").strip()
USING_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))
DB_PATH = os.environ.get("PETSTOCK_DB", os.path.join(BASE_DIR, "petstock.db"))

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", DEFAULT_SECRET_KEY),
    DATABASE=DB_PATH,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
_initialization_lock = threading.Lock()
_database_initialized = False

class PostgresConnection:
    """Small compatibility layer so the existing application can use PostgreSQL or SQLite."""
    def __init__(self, raw): self.raw = raw
    @staticmethod
    def sql(query): return re.sub(r"\?", "%s", query)
    def execute(self, query, params=()):
        if query.strip().upper() == "BEGIN IMMEDIATE": query = "BEGIN"
        return self.raw.execute(self.sql(query), params)
    def executemany(self, query, params):
        with self.raw.cursor() as cursor:
            cursor.executemany(self.sql(query), params)
    def executescript(self, script):
        for statement in script.split(";"):
            if statement.strip(): self.execute(statement)
    def commit(self): self.raw.commit()
    def rollback(self): self.raw.rollback()
    def close(self): self.raw.close()

def open_connection():
    if USING_POSTGRES:
        if psycopg is None: raise RuntimeError("PostgreSQL requires psycopg; install requirements.txt")
        return PostgresConnection(psycopg.connect(DATABASE_URL, autocommit=True, row_factory=dict_row, connect_timeout=10))
    conn = sqlite3.connect(app.config["DATABASE"], isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn

def database_errors(): return (sqlite3.Error,) if not USING_POSTGRES else (psycopg.Error,)
def integrity_errors(): return (sqlite3.IntegrityError,) if not USING_POSTGRES else (psycopg.IntegrityError,)

def csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]

@app.context_processor
def csrf_context():
    return {"csrf_token": csrf_token}

@app.before_request
def validate_csrf():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        if not supplied or not secrets.compare_digest(supplied, csrf_token()):
            abort(400, "CSRF token ไม่ถูกต้อง กรุณารีเฟรชหน้าแล้วลองใหม่")

@app.errorhandler(400)
def bad_request(error):
    if request.path.startswith("/api/"):
        return jsonify(error=error.description), 400
    return error

def db():
    if "db" not in g:
        g.db = open_connection()
    return g.db

@app.teardown_appcontext
def close_db(_error):
    conn = g.pop("db", None)
    if conn: conn.close()

@contextmanager
def transaction():
    conn = db()
    conn.execute("BEGIN IMMEDIATE")  # serializes approvals and prevents overselling stock
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()

def row(sql, params=()): return db().execute(sql, params).fetchone()
def rows(sql, params=()): return db().execute(sql, params).fetchall()

def current_user():
    uid = session.get("user_id")
    return row("SELECT * FROM users WHERE id=?", (uid,)) if uid else None

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_user(): return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped

def roles(*allowed):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user or user["role"] not in allowed:
                return jsonify(error="ไม่มีสิทธิ์ดำเนินการ"), 403
            return fn(*args, **kwargs)
        return wrapped
    return decorator

def document_no(): return "REQ-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
def today(): return date.today().isoformat()
def json_data(): return request.get_json(silent=True) or request.form.to_dict()

@app.get("/healthz")
def healthz():
    """Unauthenticated liveness/readiness endpoint for the deployment platform."""
    try:
        db().execute("SELECT 1").fetchone()
    except database_errors():
        return jsonify(status="unhealthy"), 503
    return jsonify(status="ok"), 200

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = row("SELECT * FROM users WHERE username=?", (request.form.get("username"),))
        if user and check_password_hash(user["password"], request.form.get("password", "")):
            session.clear(); session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))
        flash("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง", "error")
    return render_template("login.html")

@app.post("/logout")
def logout(): session.clear(); return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    user = current_user()
    warehouse = row("SELECT id FROM locations WHERE kind='warehouse'")["id"]
    pending = row("SELECT COUNT(*) n FROM requisitions WHERE status='pending'")["n"]
    transferring = row("SELECT COALESCE(SUM(quantity),0) n FROM stock_batches WHERE state='in_transfer'")["n"]
    low = rows("""SELECT p.name, p.sku, l.name location, COALESCE(SUM(b.quantity),0) qty, p.reorder_point
                  FROM products p CROSS JOIN locations l LEFT JOIN stock_batches b ON b.product_id=p.id AND b.location_id=l.id AND b.state='available'
                  GROUP BY p.id,l.id HAVING COALESCE(SUM(b.quantity),0) <= p.reorder_point ORDER BY qty LIMIT 8""")
    expiring = rows("""SELECT p.name,b.lot_no,b.expires_on,b.quantity,l.name location FROM stock_batches b
                      JOIN products p ON p.id=b.product_id JOIN locations l ON l.id=b.location_id
                      WHERE b.state='available' AND b.expires_on IS NOT NULL AND b.expires_on >= ? AND b.expires_on <= ? ORDER BY b.expires_on LIMIT 8""", (today(), (date.today() + timedelta(days=60)).isoformat()))
    scope = "ทุกสาขา" if user["role"] == "admin" else row("SELECT name FROM locations WHERE id=?", (user["location_id"],))["name"]
    return render_template("dashboard.html", user=user, scope=scope, pending=pending, transferring=transferring, low=low, expiring=expiring)

@app.route("/products", methods=["GET", "POST"])
@login_required
def products():
    user = current_user()
    if request.method == "POST":
        if user["role"] != "admin": return jsonify(error="เฉพาะผู้ดูแลคลัง"), 403
        f=request.form
        try:
            db().execute("INSERT INTO products(sku,barcode,name,category_id,brand,pack_size,unit,cost,price,reorder_point,is_lot_tracked) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (f["sku"],f.get("barcode"),f["name"],f["category_id"],f.get("brand"),f.get("pack_size"),f["unit"],f["cost"],f["price"],f["reorder_point"],1 if f.get("lot") else 0))
            flash("เพิ่มสินค้าแล้ว", "success")
        except integrity_errors(): flash("SKU หรือบาร์โค้ดซ้ำ", "error")
        return redirect(url_for("products"))
    q=request.args.get("q", "")
    products=rows("""SELECT p.*,c.name category, COALESCE(SUM(b.quantity),0) total FROM products p JOIN categories c ON c.id=p.category_id
                   LEFT JOIN stock_batches b ON b.product_id=p.id AND b.state='available' WHERE p.name LIKE ? OR p.sku LIKE ? OR COALESCE(p.barcode,'') LIKE ? GROUP BY p.id,c.name ORDER BY p.name""", (f"%{q}%",)*3)
    return render_template("products.html", user=user, products=products, categories=rows("SELECT * FROM categories"), q=q)

@app.route("/stock")
@login_required
def stock():
    user=current_user(); location_id=request.args.get("location", type=int)
    query=request.args.get("q", "").strip()
    state=request.args.get("state", "")
    detail=request.args.get("view") == "lots"
    locations=rows("SELECT * FROM locations ORDER BY kind,name")
    if user["role"] == "branch": location_id=user["location_id"]
    filters=["b.quantity > 0"]
    params=[]
    if location_id: filters.append("b.location_id=?"); params.append(location_id)
    if state in {"available", "in_transfer"}: filters.append("b.state=?"); params.append(state)
    if query:
        filters.append("(p.name LIKE ? OR p.sku LIKE ? OR COALESCE(p.barcode,'') LIKE ?)")
        params.extend([f"%{query}%"] * 3)
    where=" WHERE " + " AND ".join(filters)
    summary_sql="""SELECT p.id,p.sku,p.name,p.unit,p.reorder_point,l.name location,b.state,
        SUM(b.quantity) quantity,COUNT(b.id) lot_count,MIN(b.expires_on) nearest_expiry
        FROM stock_batches b JOIN products p ON p.id=b.product_id JOIN locations l ON l.id=b.location_id""" + where + " GROUP BY p.id,l.id,b.state ORDER BY p.name,l.name,b.state"
    summary=rows(summary_sql, params)
    batches=[]
    if detail:
        batch_sql="SELECT b.*,p.sku,p.name,p.unit,p.reorder_point,l.name location FROM stock_batches b JOIN products p ON p.id=b.product_id JOIN locations l ON l.id=b.location_id" + where
        batch_sql += " ORDER BY p.name, CASE WHEN b.expires_on IS NULL THEN 1 ELSE 0 END, b.expires_on"
        batches=rows(batch_sql, params)
    metrics={"available": sum(x["quantity"] for x in summary if x["state"] == "available"), "transfer": sum(x["quantity"] for x in summary if x["state"] == "in_transfer"), "low": sum(1 for x in summary if x["state"] == "available" and x["quantity"] <= x["reorder_point"])}
    return render_template("stock.html", user=user, batches=batches, summary=summary, metrics=metrics, locations=locations, selected=location_id, q=query, state=state, detail=detail, today=today(), alert_until=date.fromordinal(date.today().toordinal()+60).isoformat(), products=rows("SELECT * FROM products ORDER BY name"))

@app.route("/admin", methods=["GET", "POST"])
@login_required
@roles("admin")
def admin_settings():
    if request.method == "POST":
        f=request.form
        try:
            if f["action"] == "location":
                db().execute("INSERT INTO locations(code,name,kind) VALUES(?,?,'branch')", (f["code"].upper(), f["name"]))
                flash("เพิ่มสาขาแล้ว", "success")
            elif f["action"] == "user":
                if f["role"] not in {"admin", "branch"} or len(f["password"]) < 8:
                    flash("บทบาทไม่ถูกต้อง หรือรหัสผ่านต้องมีอย่างน้อย 8 ตัวอักษร", "error")
                    return redirect(url_for("admin_settings"))
                db().execute("INSERT INTO users(username,password,display_name,role,location_id) VALUES(?,?,?,?,?)", (f["username"], generate_password_hash(f["password"]), f["display_name"], f["role"], f.get("location_id") or None))
                flash("เพิ่มผู้ใช้แล้ว", "success")
        except integrity_errors():
            flash("รหัสสาขาหรือชื่อผู้ใช้ซ้ำ", "error")
        return redirect(url_for("admin_settings"))
    return render_template("admin.html", user=current_user(), locations=rows("SELECT * FROM locations ORDER BY kind,name"), users=rows("SELECT u.*,l.name location FROM users u LEFT JOIN locations l ON l.id=u.location_id ORDER BY u.role,u.display_name"))

@app.route("/receipts", methods=["GET", "POST"])
@login_required
@roles("admin")
def receipts():
    if request.method == "POST":
        f=request.form; qty=int(f["quantity"])
        if qty <= 0: flash("จำนวนต้องมากกว่า 0", "error")
        else:
            with transaction() as conn:
                conn.execute("INSERT INTO stock_batches(product_id,location_id,quantity,lot_no,expires_on,state) VALUES(?,?,?,?,?,'available')", (f["product_id"],f["location_id"],qty,f.get("lot_no"),f.get("expires_on") or None))
                conn.execute("INSERT INTO movements(product_id,to_location_id,quantity,movement_type,lot_no,note,performed_by) VALUES(?,?,?,'receipt',?,?,?)", (f["product_id"],f["location_id"],qty,f.get("lot_no"),f.get("note"),current_user()["id"]))
            flash("รับสินค้าเข้าคลังเรียบร้อย", "success")
        return redirect(url_for("receipts"))
    return render_template("receipts.html", user=current_user(), products=rows("SELECT * FROM products ORDER BY name"), locations=rows("SELECT * FROM locations WHERE kind='warehouse'"))

@app.route("/requisitions")
@login_required
def requisitions():
    user=current_user(); sql="""SELECT r.*,l.name branch,u.display_name requester FROM requisitions r JOIN locations l ON l.id=r.branch_id JOIN users u ON u.id=r.requester_id"""; params=[]
    if user["role"] == "branch": sql += " WHERE r.branch_id=?"; params=[user["location_id"]]
    sql += " ORDER BY r.id DESC"
    return render_template("requisitions.html", user=user, requisitions=rows(sql,params), products=rows("SELECT * FROM products ORDER BY name"), branches=rows("SELECT * FROM locations WHERE kind='branch'"))

@app.post("/api/requisitions")
@login_required
@roles("branch")
def create_requisition():
    payload=request.get_json(force=True); items=payload.get("items",[])
    clean=[]
    for item in items:
        try:
            product_id, qty=int(item["product_id"]),int(item["quantity"])
            if qty > 0: clean.append((product_id,qty))
        except (KeyError,TypeError,ValueError): pass
    if not clean: return jsonify(error="โปรดเพิ่มสินค้าอย่างน้อย 1 รายการ"),400
    with transaction() as conn:
        doc_no=document_no()
        conn.execute("INSERT INTO requisitions(document_no,branch_id,requester_id,status,note) VALUES(?,?,?,'pending',?)",(doc_no,current_user()["location_id"],current_user()["id"],payload.get("note")))
        req_id=conn.execute("SELECT id FROM requisitions WHERE document_no=?", (doc_no,)).fetchone()["id"]
        conn.executemany("INSERT INTO requisition_items(requisition_id,product_id,quantity) VALUES(?,?,?)",[(req_id,*x) for x in clean])
    return jsonify(message="ส่งคำขอรออนุมัติแล้ว", id=req_id),201

def requisition_or_404(req_id):
    req=row("SELECT * FROM requisitions WHERE id=?",(req_id,))
    if not req: return None, (jsonify(error="ไม่พบคำขอ"),404)
    return req, None

@app.get("/api/requisitions/<int:req_id>")
@login_required
def requisition_detail(req_id):
    req,error=requisition_or_404(req_id)
    if error:return error
    user=current_user()
    if user["role"] == "branch" and req["branch_id"] != user["location_id"]: return jsonify(error="ไม่มีสิทธิ์"),403
    return jsonify(requisition=dict(req),items=[dict(x) for x in rows("SELECT i.*,p.name,p.sku,p.unit FROM requisition_items i JOIN products p ON p.id=i.product_id WHERE i.requisition_id=?",(req_id,))])

@app.post("/api/requisitions/<int:req_id>/approve")
@login_required
@roles("admin")
def approve(req_id):
    # Entire operation is deliberately one IMMEDIATE transaction: no partial issue, no double approval.
    try:
        with transaction() as conn:
            req=conn.execute("SELECT * FROM requisitions WHERE id=?",(req_id,)).fetchone()
            if not req: return jsonify(error="ไม่พบคำขอ"),404
            if req["status"] != "pending": return jsonify(error="คำขอนี้ดำเนินการแล้ว"),409
            items=conn.execute("SELECT * FROM requisition_items WHERE requisition_id=?",(req_id,)).fetchall()
            warehouse=conn.execute("SELECT id FROM locations WHERE kind='warehouse'").fetchone()["id"]
            allocations=[]
            for item in items:
                batch_query="""SELECT * FROM stock_batches WHERE product_id=? AND location_id=? AND state='available' AND quantity>0
                    AND (expires_on IS NULL OR expires_on >= ?) ORDER BY CASE WHEN expires_on IS NULL THEN 1 ELSE 0 END, expires_on, id"""
                if USING_POSTGRES: batch_query += " FOR UPDATE"
                batches=conn.execute(batch_query,(item["product_id"],warehouse,today())).fetchall()
                available=sum(x["quantity"] for x in batches)
                if available < item["quantity"]:
                    return jsonify(error=f"สต็อกไม่พอสำหรับสินค้า ID {item['product_id']} (มี {available}, ขอ {item['quantity']})"),409
                needed=item["quantity"]
                for batch in batches:
                    take=min(needed,batch["quantity"]); allocations.append((batch,take)); needed-=take
                    if needed==0: break
            for batch,qty in allocations:
                conn.execute("UPDATE stock_batches SET quantity=quantity-? WHERE id=? AND quantity>=?",(qty,batch["id"],qty))
                conn.execute("INSERT INTO stock_batches(product_id,location_id,quantity,lot_no,expires_on,state,requisition_id) VALUES(?,?,?,?,?,'in_transfer',?)",(batch["product_id"],warehouse,qty,batch["lot_no"],batch["expires_on"],req_id))
                conn.execute("INSERT INTO movements(product_id,from_location_id,to_location_id,quantity,movement_type,requisition_id,lot_no,performed_by) VALUES(?,?,?,?, 'transfer_out',?,?,?)",(batch["product_id"],warehouse,req["branch_id"],qty,req_id,batch["lot_no"],current_user()["id"]))
            conn.execute("UPDATE requisitions SET status='approved',approved_by=?,approved_at=CURRENT_TIMESTAMP WHERE id=?",(current_user()["id"],req_id))
        return jsonify(message="อนุมัติและย้ายสินค้าเข้าสถานะระหว่างโอนแล้ว")
    except database_errors() as exc: return jsonify(error=str(exc)),500

@app.post("/api/requisitions/<int:req_id>/reject")
@login_required
@roles("admin")
def reject(req_id):
    reason=json_data().get("reason","").strip()
    if not reason:return jsonify(error="ต้องระบุเหตุผลที่ปฏิเสธ"),400
    with transaction() as conn:
        req=conn.execute("SELECT status FROM requisitions WHERE id=?",(req_id,)).fetchone()
        if not req:return jsonify(error="ไม่พบคำขอ"),404
        if req["status"] != 'pending':return jsonify(error="คำขอนี้ดำเนินการแล้ว"),409
        conn.execute("UPDATE requisitions SET status='rejected',rejection_reason=?,approved_by=?,approved_at=CURRENT_TIMESTAMP WHERE id=?",(reason,current_user()["id"],req_id))
    return jsonify(message="ปฏิเสธคำขอแล้ว โดยไม่มีการเปลี่ยนสต็อก")

@app.post("/api/requisitions/<int:req_id>/cancel")
@login_required
@roles("branch")
def cancel_requisition(req_id):
    with transaction() as conn:
        req = conn.execute("SELECT * FROM requisitions WHERE id=?", (req_id,)).fetchone()
        if not req: return jsonify(error="ไม่พบคำขอ"), 404
        if req["branch_id"] != current_user()["location_id"]: return jsonify(error="ยกเลิกได้เฉพาะคำขอของสาขาตน"), 403
        if req["status"] != "pending": return jsonify(error="ยกเลิกได้เฉพาะคำขอที่รออนุมัติ"), 409
        conn.execute("UPDATE requisitions SET status='cancelled' WHERE id=?", (req_id,))
    return jsonify(message="ยกเลิกคำขอแล้ว โดยไม่มีการเปลี่ยนสต็อก")

@app.post("/api/requisitions/<int:req_id>/receive")
@login_required
@roles("branch")
def receive(req_id):
    with transaction() as conn:
        req=conn.execute("SELECT * FROM requisitions WHERE id=?",(req_id,)).fetchone()
        if not req:return jsonify(error="ไม่พบคำขอ"),404
        if req["branch_id"] != current_user()["location_id"]:return jsonify(error="รับได้เฉพาะสาขาของตน"),403
        if req["status"] != 'approved':return jsonify(error="คำขอยังไม่พร้อมรับ หรือรับแล้ว"),409
        batches=conn.execute("SELECT * FROM stock_batches WHERE requisition_id=? AND state='in_transfer'",(req_id,)).fetchall()
        if not batches:return jsonify(error="ไม่พบสินค้าระหว่างโอน"),409
        for batch in batches:
            conn.execute("UPDATE stock_batches SET location_id=?, state='available' WHERE id=?",(req["branch_id"],batch["id"]))
            conn.execute("INSERT INTO movements(product_id,from_location_id,to_location_id,quantity,movement_type,requisition_id,lot_no,performed_by) VALUES(?,?,?,?, 'transfer_receive',?,?,?)",(batch["product_id"],batch["location_id"],req["branch_id"],batch["quantity"],req_id,batch["lot_no"],current_user()["id"]))
        conn.execute("UPDATE requisitions SET status='received',received_by=?,received_at=CURRENT_TIMESTAMP WHERE id=?",(current_user()["id"],req_id))
    return jsonify(message="ยืนยันรับสินค้าแล้ว")

@app.post("/api/sales")
@login_required
@roles("branch")
def sale():
    data=request.get_json(force=True)
    try: product_id,qty=int(data["product_id"]),int(data["quantity"])
    except (KeyError,TypeError,ValueError):return jsonify(error="ข้อมูลไม่ถูกต้อง"),400
    if qty<=0:return jsonify(error="จำนวนต้องมากกว่า 0"),400
    with transaction() as conn:
        batch_query="SELECT * FROM stock_batches WHERE product_id=? AND location_id=? AND state='available' AND quantity>0 AND (expires_on IS NULL OR expires_on>=?) ORDER BY CASE WHEN expires_on IS NULL THEN 1 ELSE 0 END,expires_on,id"
        if USING_POSTGRES: batch_query += " FOR UPDATE"
        batches=conn.execute(batch_query,(product_id,current_user()["location_id"],today())).fetchall()
        if sum(x["quantity"] for x in batches)<qty:return jsonify(error="สต็อกสาขาไม่เพียงพอ"),409
        needed=qty
        for b in batches:
            take=min(needed,b["quantity"]); conn.execute("UPDATE stock_batches SET quantity=quantity-? WHERE id=?",(take,b["id"]))
            conn.execute("INSERT INTO movements(product_id,from_location_id,quantity,movement_type,lot_no,note,performed_by) VALUES(?,?,?,'sale',?,?,?)",(product_id,current_user()["location_id"],take,b["lot_no"],data.get("note"),current_user()["id"]))
            needed-=take
            if not needed:break
    return jsonify(message="บันทึกตัดขายแล้ว")

@app.route("/reports")
@login_required
def reports():
    user=current_user(); sql="""SELECT m.*,p.name product,p.sku,COALESCE(f.name,'-') origin,COALESCE(t.name,'-') destination FROM movements m JOIN products p ON p.id=m.product_id LEFT JOIN locations f ON f.id=m.from_location_id LEFT JOIN locations t ON t.id=m.to_location_id"""; params=[]
    if user["role"] == "branch":sql += " WHERE m.from_location_id=? OR m.to_location_id=?";params=[user["location_id"],user["location_id"]]
    sql += " ORDER BY m.id DESC LIMIT 150"
    return render_template("reports.html",user=user,movements=rows(sql,params))

@app.route("/reports/stock.csv")
@login_required
def stock_csv():
    out=io.StringIO(); writer=csv.writer(out); writer.writerow(["SKU","สินค้า","สถานที่","ล็อต","หมดอายุ","สถานะ","จำนวน","หน่วย"])
    fields = ('sku', 'product_name', 'location_name', 'lot_no', 'expires_on', 'state', 'quantity', 'unit')
    for b in rows("SELECT p.sku,p.name product_name,l.name location_name,b.lot_no,b.expires_on,b.state,b.quantity,p.unit FROM stock_batches b JOIN products p ON p.id=b.product_id JOIN locations l ON l.id=b.location_id ORDER BY p.name"):
        writer.writerow([b[key] for key in fields])
    return Response("\ufeff"+out.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=petstock-stock.csv"})

def init_database():
    global _database_initialized
    if (os.environ.get("REQUIRE_SECRET_KEY") == "1" or os.environ.get("VERCEL")) and (not app.config["SECRET_KEY"] or app.config["SECRET_KEY"] == DEFAULT_SECRET_KEY):
        raise RuntimeError("SECRET_KEY must be set before production startup")
    if os.environ.get("VERCEL") and not USING_POSTGRES:
        raise RuntimeError("Vercel requires a PostgreSQL DATABASE_URL in this deployment environment")
    if not USING_POSTGRES:
        database_dir = os.path.dirname(os.path.abspath(app.config["DATABASE"]))
        os.makedirs(database_dir, exist_ok=True)
    conn=open_connection()
    try:
        if not USING_POSTGRES: conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN" if USING_POSTGRES else "BEGIN IMMEDIATE")
        if USING_POSTGRES:
            # Transaction-scoped lock also coordinates separate Vercel instances.
            conn.execute("SELECT pg_advisory_xact_lock(73910482)")
        _initialize_schema_and_seed(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    _database_initialized = True

def _initialize_schema_and_seed(conn):
    schema_name = "schema_postgres.sql" if USING_POSTGRES else "schema.sql"
    with open(os.path.join(BASE_DIR, schema_name), encoding="utf-8") as schema_file:
        # sqlite3.executescript implicitly commits; execute separately to keep startup atomic.
        for statement in schema_file.read().split(';'):
            if statement.strip(): conn.execute(statement)
    if not conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]:
        seed_sql="""
        INSERT INTO locations(id,code,name,kind) VALUES(1,'WH','คลังกลาง','warehouse'),(2,'BKK01','สาขาสยาม','branch'),(3,'BKK02','สาขาลาดพร้าว','branch'),(4,'BKK03','สาขาบางนา','branch');
        INSERT INTO categories(id,name) VALUES(1,'อาหารสุนัข'),(2,'อาหารแมว'),(3,'อาหารเสริมสัตว์'),(4,'อุปกรณ์สัตว์เลี้ยง');
        INSERT INTO products(id,sku,barcode,name,category_id,brand,pack_size,unit,cost,price,reorder_point,is_lot_tracked) VALUES
        (1,'DOG-AD-1KG','885000000001','อาหารสุนัข Adult สูตรไก่ 1 กก.',1,'PawLife','1 กก.','ถุง',120,169,15,1),(2,'DOG-AD-10KG','885000000002','อาหารสุนัข Adult สูตรไก่ 10 กก.',1,'PawLife','10 กก.','ถุง',850,1090,8,1),(3,'CAT-IN-1KG','885000000003','อาหารแมว Indoor 1 กก.',2,'MeowMore','1 กก.','ถุง',145,199,12,1),(4,'SUP-OMEGA-60','885000000004','โอเมก้า 3 สำหรับสัตว์ 60 เม็ด',3,'VitaPet','60 เม็ด','ขวด',220,320,10,1),(5,'ACC-BOWL-M','885000000005','ชามอาหารสเตนเลส M',4,'PetHome','M','ชิ้น',65,109,10,0);
        INSERT INTO stock_batches(product_id,location_id,quantity,lot_no,expires_on,state) VALUES
        (1,1,100,'DOG2409A','2027-03-31','available'),(2,1,35,'DOG2410B','2027-06-30','available'),(3,1,60,'CAT2408A','2027-01-15','available'),(4,1,25,'OMG2407A','2027-02-28','available'),(5,1,40,NULL,NULL,'available'),
        (1,2,5,'DOG2408S','2027-01-31','available'),(3,2,8,'CAT2407S','2026-12-20','available'),(5,2,15,NULL,NULL,'available'),(2,3,4,'DOG2409L','2027-03-31','available'),(4,4,3,'OMG2407B','2027-02-28','available');
        """
        for statement in seed_sql.split(';'):
            if not statement.strip(): continue
            if 'INSERT INTO stock_batches' in statement:
                # Preserve stock left behind by an older, partially completed startup.
                if conn.execute('SELECT COUNT(*) AS total FROM stock_batches').fetchone()['total']:
                    continue
                conn.execute(statement)
            else:
                conn.execute(statement + ' ON CONFLICT DO NOTHING')
        conn.executemany("INSERT INTO users(id,username,password,display_name,role,location_id) VALUES(?,?,?,?,?,?)", [
            (1, "admin", generate_password_hash("admin123"), "ผู้ดูแลคลัง", "admin", 1),
            (2, "siam", generate_password_hash("branch123"), "พนักงานสาขาสยาม", "branch", 2),
            (3, "ladprao", generate_password_hash("branch123"), "พนักงานสาขาลาดพร้าว", "branch", 3),
            (4, "bangna", generate_password_hash("branch123"), "พนักงานสาขาบางนา", "branch", 4),
        ])
        if USING_POSTGRES:
            for table in ("locations", "categories", "users", "products", "stock_batches"):
                conn.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT MAX(id) FROM {table}))")
    # One-time compatibility upgrade for a database created by the first local starter.
    # It upgrades only an exact known plain-text demo password; user-changed passwords remain untouched.
    for username, legacy_password in (("admin", "admin123"), ("siam", "branch123"), ("ladprao", "branch123"), ("bangna", "branch123")):
        record = conn.execute("SELECT password FROM users WHERE username=?", (username,)).fetchone()
        if record and record["password"] == legacy_password:
            conn.execute("UPDATE users SET password=? WHERE username=?", (generate_password_hash(legacy_password), username))

@app.before_request
def initialize_database_for_serverless():
    """Vercel imports app.py directly, so initialize the schema on the first request."""
    global _database_initialized
    if not _database_initialized:
        with _initialization_lock:
            if not _database_initialized:
                init_database()

if __name__ == "__main__":
    init_database()
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", host="127.0.0.1", port=5000)
