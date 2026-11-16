# PetStock Hub

เว็บแอปภาษาไทยสำหรับจัดการคลังกลาง 1 แห่งและสาขา 3 แห่งของร้านอาหาร/อุปกรณ์สัตว์เลี้ยง พร้อมข้อมูลตัวอย่างและสิทธิ์ตรวจสอบที่ฝั่งเซิร์ฟเวอร์

## สิ่งที่พัฒนา

- สินค้า SKU เดียวต่อสูตร/ขนาด, หมวดหมู่, บาร์โค้ด, ราคา และจุดเตือนสต็อก
- ล็อตและวันหมดอายุสำหรับอาหาร/อาหารเสริม พร้อมเลือกล็อตแบบ FEFO และไม่จ่ายล็อตหมดอายุ
- สต็อก `available` แยกคลัง/สาขา และ `in_transfer` แยกออกจากกัน
- พนักงานสาขาสร้างคำขอได้ แต่ไม่มีสิทธิ์ดูหรือดำเนินการข้อมูลสาขาอื่น
- ผู้ดูแลอนุมัติ/ปฏิเสธทั้งใบ: ตรวจทุกบรรทัดก่อนเริ่มตัด, ตัดแบบ all-or-nothing, ป้องกันกดอนุมัติซ้ำ
- เมื่ออนุมัติ: สต็อกคลังลดและกลายเป็นระหว่างโอน; เมื่อสาขายืนยัน: ย้ายเป็นสต็อกพร้อมขายของสาขา
- ประวัติ movement audit trail, dashboard, แจ้งเตือนสต็อกต่ำ/ใกล้หมดอายุ และดาวน์โหลด CSV
- จัดการสาขาและบัญชีผู้ใช้จากหน้าผู้ดูแล พร้อม hash รหัสผ่าน, CSRF token และ session cookie แบบ HttpOnly/SameSite

## เทคโนโลยี

ใช้ **Flask + SQLite + Jinja/Vanilla JavaScript** เพื่อให้เริ่มใช้งานบนเครื่องได้ง่ายและยังบังคับสิทธิ์/ธุรกรรมในฝั่งเซิร์ฟเวอร์ได้จริง. เมื่อขยายเป็นระบบหลายผู้ใช้ข้ามหลายเครื่อง แนะนำ PostgreSQL แทน SQLite โดยคงโครงสร้างตารางและ API เดิมได้

## ความต้องการและติดตั้ง (Windows / PowerShell)

ต้องมี Python 3.10 ขึ้นไป แล้วเปิด PowerShell ที่โฟลเดอร์นี้:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

เปิด `http://127.0.0.1:5000` ในเบราว์เซอร์ ครั้งแรกจะสร้าง `petstock.db` และใส่ข้อมูลตัวอย่างโดยอัตโนมัติ

บัญชีตัวอย่าง:

| บทบาท | ผู้ใช้ / รหัสผ่าน |
| --- | --- |
| ผู้ดูแลคลัง | `admin` / `admin123` |
| สาขาสยาม | `siam` / `branch123` |
| สาขาลาดพร้าว | `ladprao` / `branch123` |
| สาขาบางนา | `bangna` / `branch123` |

> เปลี่ยน `SECRET_KEY` และรหัสผ่านตัวอย่างก่อนนำขึ้นใช้งานจริง; เวอร์ชันนี้ตั้งใจให้เป็น starter ที่ทำงานในเครื่องได้ทันที

หากเข้าสู่ระบบไม่ได้หลังอัปเดตโค้ด ให้หยุดเซิร์ฟเวอร์ด้วย `Ctrl+C` แล้วรัน `python app.py` ใหม่หนึ่งครั้ง ระบบจะปรับบัญชีตัวอย่างของฐานข้อมูลเดิมให้เป็นรูปแบบ hash อัตโนมัติ

## ทดสอบ

```powershell
python -m unittest discover -s tests -v
```

ชุดทดสอบยืนยันกรณีตามโจทย์: คลังกลางอาหารสุนัข 100 ถุง → สาขาสยามขอ 20 → อนุมัติ (80 + ระหว่างโอน 20) → รับสินค้า (สาขาจาก 5 เป็น 25), กดซ้ำไม่เปลี่ยนยอด, และสต็อกไม่พอไม่ตัดสินค้า

## ขั้นตอนการใช้งาน

1. ผู้ดูแลล็อกอินและเพิ่มสินค้า หรือรับสินค้าเข้า **คลังกลาง** พร้อมล็อต/วันหมดอายุ
2. พนักงานสาขาล็อกอิน กด **สร้างคำขอ** เลือกได้หลายสินค้า แล้วส่งคำขอ
3. ผู้ดูแลเปิดหน้า **คำขอเบิก/โอน** และกดอนุมัติหรือปฏิเสธ (ต้องระบุเหตุผล)
4. สาขากด **ยืนยันรับ** เมื่อได้รับสินค้าครบ ระบบจึงเพิ่มยอดพร้อมขายให้สาขา
5. ดาวน์โหลดรายงาน CSV จาก Dashboard หรือหน้า **ประวัติและรายงาน**

## แบบจำลองข้อมูล

```text
locations 1---* users
categories 1---* products
products  1---* stock_batches ---1 locations
requisitions 1---* requisition_items ---1 products
requisitions 1---* movements ---1 products
```

ตารางหลักอยู่ใน [schema.sql](schema.sql): `stock_batches` เก็บยอดตามล็อต/สถานที่/สถานะ, `requisitions` และ `requisition_items` เก็บเอกสาร, ส่วน `movements` เป็นประวัติตรวจสอบย้อนกลับ

## กติกาธุรกรรม

การอนุมัติใช้ `BEGIN IMMEDIATE` ของ SQLite และตรวจ status `pending` ภายใน transaction เดียว จึงกันผู้ดูแลสองคนอนุมัติคำขอเดียวกันพร้อมกันได้ในฐานข้อมูลไฟล์เดียว ทั้งการตรวจยอดทุก SKU, เลือกล็อต FEFO, ลดคลัง, สร้างล็อตระหว่างโอน และเปลี่ยนสถานะคำขอจะสำเร็จพร้อมกันหรือ rollback ทั้งหมด

สำหรับการใช้งานหลายเซิร์ฟเวอร์/หลายคลังพร้อมกัน ควรย้าย schema เดิมไป PostgreSQL แล้วใช้ row lock (`SELECT ... FOR UPDATE`) และเพิ่ม migration tool, HTTPS และการสำรองฐานข้อมูลตามแผนก่อน production

## Deploy ด้วย Render

โปรเจกต์มี `Dockerfile`, `render.yaml` และ health check ที่ `/healthz` พร้อม deploy แล้ว โดยเลือกใช้ **Render Persistent Disk** เก็บ SQLite ที่ `/data/petstock.db` เพื่อไม่ให้ข้อมูลหายเมื่อ deploy ใหม่ และจำกัด Gunicorn ไว้ `1` worker เพราะ SQLite รองรับผู้เขียนข้อมูลได้ทีละราย

> Render Persistent Disk ใช้กับ Web Service แบบมีค่าใช้จ่าย หากเลือกแผนที่ไม่มี disk ข้อมูล SQLite จะหายได้ทุกครั้งที่ redeploy; ในกรณีนั้นควรย้ายฐานข้อมูลไป PostgreSQL ก่อนเปิดใช้งานจริง

1. สร้าง repository ใหม่บน GitHub แล้ว push โค้ดขึ้น `main` (ไฟล์ฐานข้อมูลและ `.env` จะไม่ถูก push)
2. ที่ Render เลือก **New → Blueprint** แล้วเชื่อม GitHub repository นี้
3. Render จะอ่าน `render.yaml`, สร้าง `SECRET_KEY` อัตโนมัติ และใช้ disk `/data`
4. กด Apply แล้วรอให้ health check `/healthz` ผ่าน จากนั้นเปิด URL ที่ Render ให้มา
5. หลังเข้าใช้งานครั้งแรก ให้ล็อกอิน `admin / admin123` แล้วสร้างบัญชีผู้ดูแลจริง/เปลี่ยนรหัสผ่านทันที

### ทดสอบ Docker ในเครื่อง (ไม่บังคับ)

```powershell
docker build -t petstock-hub .
docker run --rm -p 8000:8000 -e SECRET_KEY="replace-with-a-long-random-value" -v "${PWD}/data:/data" petstock-hub
```

แล้วเปิด `http://127.0.0.1:8000` การใช้งานแบบ Docker ต้องกำหนด `SECRET_KEY`; หากลืมกำหนด ระบบจะไม่เริ่มทำงานโดยตั้งใจ

## Deploy ด้วย Vercel + PostgreSQL

สำหรับ Vercel ให้ใช้ PostgreSQL แทน SQLite โปรเจกต์ใช้ `app.py` เป็น entrypoint โดยตรง พร้อม [vercel.json](vercel.json) และ [schema_postgres.sql](schema_postgres.sql)

1. ใน Vercel Marketplace ติดตั้ง PostgreSQL provider เช่น Neon หรือ Supabase แล้วเชื่อมกับ project
2. ตั้ง Environment Variables ใน Vercel: `DATABASE_URL` (connection string ของ provider), `SECRET_KEY` (ค่าสุ่มยาว), และ `REQUIRE_SECRET_KEY=1`
3. Import GitHub repository นี้ใน Vercel แล้วกด Deploy — ไม่ต้องใช้ Dockerfile หรือ Render config
4. การ deploy ครั้งแรกจะสร้างตารางและข้อมูลตัวอย่างใน PostgreSQL โดยอัตโนมัติ; เปลี่ยนรหัสผ่าน `admin123` ทันทีหลังเข้าใช้

Vercel ใช้ Flask `app` จาก `app.py` โดยตรงและใช้ `DATABASE_URL` (หรือ `POSTGRES_URL`) เพื่อเชื่อม PostgreSQL. อย่าตั้ง `PETSTOCK_DB` บน Vercel เพราะใช้เฉพาะโหมด SQLite/Docker

### แก้ sqlite3.OperationalError: unable to open database file

หมายถึง deployment ไม่ได้เลือก PostgreSQL ให้เชื่อม Neon กับโปรเจกต์ แล้วตั้ง `DATABASE_URL` ใน Settings → Environment Variables เป็น connection string ที่ขึ้นต้น `postgresql://` หรือ `postgres://` (ใช้ pooled connection พร้อม SSL ตามค่าจาก Neon) เลือก environment ให้ตรงกับ deployment เช่น Preview หรือ Production แล้ว redeploy ใหม่ ค่าที่ตั้งใหม่ไม่เปลี่ยน deployment เก่าที่เปิดอยู่ อย่าส่ง connection string หรือรหัสผ่านลงใน chat หรือ Git

การเริ่มระบบใช้ transaction และ PostgreSQL advisory lock เพื่อประสานหลาย Function และไม่เพิ่มข้อมูลตัวอย่างซ้ำเมื่อเริ่มใหม่ ชุดทดสอบ `.github/workflows/tests.yml` ตรวจทั้ง SQLite และ PostgreSQL บนฐานข้อมูลทดสอบแยก ในเครื่องที่ไม่ตั้ง `TEST_POSTGRES_URL` กรณี PostgreSQL จะถูก skip ไม่ถือว่าทดสอบผ่าน
