# SmartVibe v2.3 — ระบบเฝ้าระวังโครงสร้างอาคารจากการสั่นสะเทือน

ระบบตรวจวัดการสั่นสะเทือนอาคารจำลอง 3 ชั้น ด้วย ESP32-S3 + MPU6050 จำนวน 3 ตัว
ส่งข้อมูลขึ้น Firebase Realtime Database แล้ววิเคราะห์ผ่านแดชบอร์ด Streamlit แบบเรียลไทม์

## หลักการทำงาน

| โหมด | ใช้เมื่อ | ตัวชี้วัด |
|---|---|---|
| ติดตาม fn | กระตุ้นด้วย White Noise / Sweep | Health = (fn/fn₀)² × 100 = % ค่าความแข็ง k ที่เหลือ |
| ไซน์คงที่ | กระตุ้นด้วยไซน์ความถี่เดียว | Transmissibility ระหว่างชั้น เทียบ baseline |

ระบบเลือกโหมดอัตโนมัติได้ โดยดูความคมของพีคใน spectrum

## โครงสร้างโปรเจค

​```
smartvibe/
├── streamlit_app.py              ← แดชบอร์ดหลัก
├── requirements.txt              ← ไลบรารีที่ต้องติดตั้ง
├── .gitignore                    ← กันไฟล์ลับหลุดขึ้น GitHub
├── .streamlit/
│   ├── config.toml               ← ธีมหน้าเว็บ
│   ├── secrets.toml              🔒 token จริง (ไม่ขึ้น GitHub)
│   └── secrets.toml.example      ← ไฟล์ตัวอย่าง
└── arduino/SmartVibe_ESP32S3/
    ├── SmartVibe_ESP32S3.ino     ← โค้ดบอร์ด
    ├── arduino_secrets.h         🔒 WiFi + token จริง (ไม่ขึ้น GitHub)
    └── arduino_secrets_example.h ← ไฟล์ตัวอย่าง
​```

## รันในเครื่องตัวเอง

​```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
​```

## นำขึ้น GitHub

​```bash
cd smartvibe
git init
git add .
git commit -m "SmartVibe v2.3"
git branch -M main
git remote add origin https://github.com/USERNAME/smartvibe.git
git push -u origin main
​```

ไฟล์ `secrets.toml` และ `arduino_secrets.h` จะถูกกันไว้โดย `.gitignore` อัตโนมัติ
ตรวจสอบก่อน push ได้ด้วย `git status` — ต้องไม่เห็นสองไฟล์นี้ในรายการ

## Deploy บน Streamlit Community Cloud (ฟรี)

1. เข้า https://share.streamlit.io → ล็อกอินด้วยบัญชี GitHub
2. กด **New app** → เลือก repo `smartvibe`, branch `main`
3. ช่อง Main file path ใส่ `streamlit_app.py`
4. กด **Advanced settings** → ช่อง **Secrets** วางข้อความนี้:
   ​```toml
   FB_TOKEN = "ใส่_DATABASE_SECRET_ตรงนี้"
   ​```
5. กด **Deploy** รอประมาณ 2–3 นาที

## อัปโหลดโค้ดลงบอร์ด

**ไลบรารีที่ต้องติดตั้งใน Arduino IDE**
- Firebase Arduino Client Library for ESP8266 and ESP32 (mobizt)
- MPU6050_tockn

**การตั้งค่าบอร์ด**
- Board: ESP32S3 Dev Module
- PSRAM: OPI PSRAM
- Flash Size: 16MB
- USB CDC On Boot: Enabled

**การต่อสาย**

| อุปกรณ์ | ขา ESP32-S3 |
|---|---|
| TCA9548A SDA | GPIO 8 |
| TCA9548A SCL | GPIO 9 |
| MPU6050 ชั้น 1 | ช่อง CH0 ของ TCA |
| MPU6050 ชั้น 2 | ช่อง CH1 ของ TCA |
| MPU6050 ชั้น 3 | ช่อง CH2 ของ TCA |

## ขั้นตอนใช้งานจริง

1. เปิดบอร์ด รอจน Serial Monitor ขึ้น `✅ ระบบพร้อมทำงาน`
2. เปิดแดชบอร์ด รอจนข้อมูลไหลเข้า (ไม่ขึ้น "กำลังรอข้อมูล" แล้ว)
3. เปิดลำโพงกระตุ้นแบบ White Noise ให้ค่า RMS ทั้ง 3 ชั้นเกินเกณฑ์
4. **ขณะโครงสร้างยังสมบูรณ์** กดปุ่ม 🔒 ล็อก Baseline
5. เริ่มทดลองสร้างความเสียหาย แล้วสังเกตค่า Health % และไฟสถานะ

## กฎความปลอดภัยของ Firebase ที่แนะนำ

​```json
{
  "rules": {
    ".read": "auth != null",
    ".write": "auth != null"
  }
}
​```
