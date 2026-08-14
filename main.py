import io
import random
import zipfile
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Annotated
from urllib.parse import quote

from fastapi import Cookie, Depends, FastAPI, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from PIL import Image, ImageDraw, ImageFont
from zoneinfo import ZoneInfo
from typing import Annotated, Optional

# ─── App setup ───────────────────────────────────────────────────────────────

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ─── Auth config (เปลี่ยน SECRET_KEY ก่อน deploy!) ───────────────────────────

SECRET_KEY = "change-me-before-deploy-use-openssl-rand-hex-32"
ALGORITHM  = "HS256"
TOKEN_EXPIRE_HOURS = 8

USERS = {"Sansiri": "1234"}  # TODO: ใช้ DB + bcrypt จริง ๆ ใน production


def create_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode({"sub": username, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


# แก้บรรทัดนี้:
def get_current_user(token: Optional[str] = Cookie(default=None, alias="access_token")) -> str:
    if not token:
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": "/login"})
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload["sub"]
    except JWTError:
        raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": "/login"})


CurrentUser = Annotated[str, Depends(get_current_user)]


# ─── Image/font cache (โหลดครั้งเดียวตอน startup) ───────────────────────────

@lru_cache(maxsize=1)
def _load_bg() -> Image.Image:
    """โหลดภาพพื้นหลังครั้งเดียว แล้ว cache ไว้ใน RAM"""
    return Image.open("static/Baan.png").convert("RGBA")


@lru_cache(maxsize=16) # เพิ่มขนาด cache เผื่อโหลดหลายฟอนต์
def _load_font(size: int, font_path: str = "static/Opun Mai Bold.ttf") -> ImageFont.FreeTypeFont:
    """Cache แต่ละขนาดและไฟล์ฟอนต์แยกกัน (ค่าเริ่มต้นคือ COOOPBL สำหรับตัวเลข)"""
    return ImageFont.truetype(font_path, size)


def _get_auto_font(draw: ImageDraw.ImageDraw, text: str, max_width: int,
                   start: int = 55, min_size: int = 20, 
                   font_path: str = "static/Opun Mai Bold.ttf") -> ImageFont.FreeTypeFont:
    for size in range(start, min_size - 1, -1):
        font = _load_font(size, font_path)
        w = draw.textbbox((0, 0), text, font=font)[2]
        if w <= max_width:
            return font
    return _load_font(min_size, font_path)

def _bold_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
               font: ImageFont.FreeTypeFont, fill: str = "#ffffff", boldness: int = 1) -> None:
    x, y = xy
    for dx in range(-boldness, boldness + 1):
        for dy in range(-boldness, boldness + 1):
            draw.text((x + dx, y + dy), text, font=font, fill=fill)


def create_image_bytes(
    lottery_type: str, 
    main1: Optional[str] = None, 
    main2: Optional[str] = None,
    pair1: Optional[str] = None,
    pair2: Optional[str] = None,
    pair3: Optional[str] = None,
    pair4: Optional[str] = None,
    pair5: Optional[str] = None,
    pair6: Optional[str] = None,
    win_num: Optional[str] = None
) -> bytes:
    # deepcopy เพื่อไม่ให้แก้ไข cached image โดยตรง
    image = deepcopy(_load_bg()).convert("RGB")
    draw  = ImageDraw.Draw(image)

    # วันที่ปัจจุบัน
    now = datetime.now(ZoneInfo("Asia/Bangkok"))
    thai_year = now.year + 543
    date_text = f"{now.strftime('%d %m')} {str(thai_year)[-2:]}"
    draw.text((280, 552), date_text, font=_load_font(25), fill="#ffffff",stroke_width=2, stroke_fill="#000000")

    # ชื่อประเภทหวย (auto-fit)
    text_font_path = "static/Mitr-Regular.ttf"
    font_auto = _get_auto_font(draw, lottery_type, image.width - 100 ,font_path = text_font_path)
    text_width = draw.textlength(lottery_type, font=font_auto)
    x_pos = (image.width - text_width) // 2
    draw.text((x_pos, 105), lottery_type, font=font_auto, fill="#ffffff",stroke_width=3, stroke_fill="#000000")

    # ─── สุ่มเลขตามเงื่อนไขใหม่ ──────────────────────────────────────────────
    # 1. จัดการ Main 1 (รูด/เน้น) และ Main 2 (รอง)
    m1 = int(main1) if main1 and main1.isdigit() else None
    m2 = int(main2) if main2 and main2.isdigit() else None

    if m1 is not None and m2 is not None:
        num1, num2 = m1, m2
        if num1 == num2:
            available = [i for i in range(10) if i != num1]
            num2 = random.choice(available)
    elif m1 is not None:
        num1 = m1
        available = [i for i in range(10) if i != num1]
        num2 = random.choice(available)
    elif m2 is not None:
        num2 = m2
        available = [i for i in range(10) if i != num2]
        num1 = random.choice(available)
    else:
        num1, num2 = random.sample(range(10), 2)

    # 2. จัดการ เลขคู่ 4 ชุด (pairs_list)
    available_digits = [d for d in range(10) if d not in (num1, num2)]
    
    def get_or_random_pair(user_input, default_format):
        if user_input and len(user_input) == 2 and user_input.isdigit():
            return user_input
        return default_format

    r1, r2, r3, r4, r5, r6 = random.sample(available_digits, 6)
    pairs_list1 = [
        get_or_random_pair(pair1, f"{num1}{r1}"),
        get_or_random_pair(pair2, f"{num1}{r2}"),
        get_or_random_pair(pair3, f"{num1}{r3}")
    ]
    pairs_list2 = [
            get_or_random_pair(pair4, f"{num2}{r4}"),
            get_or_random_pair(pair5, f"{num2}{r5}"),
            get_or_random_pair(pair6, f"{num2}{r6}")
        ]

    # 3. จัดการ เลขวิน 6 ตัว (random_6)
    if win_num and len(win_num) == 6 and win_num.isdigit():
        random_6 = win_num
    else:
        other = [i for i in range(10) if i not in (num1, num2)]
        extras = random.sample(other, 4)
        six = [num1, num2] + extras
        random.shuffle(six)
        random_6 = "".join(map(str, six))

    # ─── วาดผลลัพธ์ลงบนภาพ ────────────────────────────────────────────────
    f_large  = _load_font(70)
    f_medium = _load_font(50)
    f_small  = _load_font(45)

    #_bold_text((160, 190), f"{num1} - {num2}", f_large)#
    draw.text((199, 200), f"{num1}          {num2}", font = f_large, fill="#ffffff",stroke_width=4, stroke_fill="#000000") 

    for i, pair in enumerate(pairs_list1):
        draw.text((150 + i * 170, 305), pair, font = f_medium, fill="#ffffff",stroke_width=4, stroke_fill="#000000")
    for i, pair in enumerate(pairs_list2):
        draw.text((150 + i * 170, 385), pair, font = f_medium, fill="#ffffff",stroke_width=4, stroke_fill="#000000")
    draw.text((315, 468), f"{random_6}", font = f_small, fill="#ffffff",stroke_width=3, stroke_fill="#000000")

    
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=85, optimize=True)
    buf.seek(0)
    return buf.read()


# ─── Routes ──────────────────────────────────────────────────────────────────

# 1. ตัวที่ทำให้เกิด Error 405 คือตัวนี้หายไป (สำหรับโหลดหน้าเว็บเข้าสู่ระบบ)
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

# 2. สำหรับกดปุ่มเข้าสู่ระบบ (เช็ครหัสผ่าน)
@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if USERS.get(username) != password:
        return templates.TemplateResponse(
            "login.html", 
            {"request": request, "error": "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"},
            status_code=400
        )
    
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key="access_token",
        value=create_token(username),
        httponly=True,
        samesite="lax",
        max_age=TOKEN_EXPIRE_HOURS * 3600,
    )
    return response

# 3. สำหรับออกจากระบบ
@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("access_token")
    return response


@app.get("/", response_class=HTMLResponse)
async def lottery_page(request: Request, user: CurrentUser):
    return templates.TemplateResponse("index.html", {"request": request, "user": user})


@app.post("/")
async def lottery_generate(
    user: CurrentUser,
    lottery_type: list[str] = Form(...),
    main1: Optional[str] = Form(None), 
    main2: Optional[str] = Form(None),
    pair1: Optional[str] = Form(None), 
    pair2: Optional[str] = Form(None), 
    pair3: Optional[str] = Form(None), 
    pair4: Optional[str] = Form(None),
    pair5: Optional[str] = Form(None),
    pair6: Optional[str] = Form(None), 
    win_num: Optional[str] = Form(None), 
):
    if not lottery_type:
        raise HTTPException(status_code=400, detail="กรุณาเลือกประเภทหวยอย่างน้อย 1 รายการ")

    # --- เริ่มต้นส่วนตรวจสอบเงื่อนไขตัวเลข (Backend Validation) ---
    pairs = [pair1, pair2, pair3, pair4, pair5, pair6]
    for i, p in enumerate(pairs, 1):
        if p and len(p) == 2:
            if not main1 and not main2:
                raise HTTPException(status_code=400, detail=f"กรุณาระบุ วิ่ง หรือ รูด ก่อนกำหนดเลขคู่ชุดที่ {i}")
            
            valid = False
            if main1 and main1 in p: valid = True
            if main2 and main2 in p: valid = True
            
            if not valid:
                raise HTTPException(status_code=400, detail=f"เลขเจาะชุดที่ {i} ({p}) ต้องมีเลข วิ่ง หรือ รูด อย่างน้อย 1 ตัว")
    
    if win_num and len(win_num) == 6:
        if not main1 or not main2:
            raise HTTPException(status_code=400, detail="กรุณาระบุทั้ง วิ่ง และ รูด ให้ครบก่อนกำหนดเลขวิน")
        if main1 not in win_num or main2 not in win_num:
            raise HTTPException(status_code=400, detail=f"เลขวิน ({win_num}) ต้องมีทั้งเลข วิ่ง ({main1}) และ รูด ({main2}) รวมอยู่ด้วย")
    # --- สิ้นสุดส่วนตรวจสอบเงื่อนไขตัวเลข ---

    # --- 1. เตรียมข้อมูลและเรียงลำดับตามเวลาก่อน ---
    parsed_items = []
    for lt_data in lottery_type:
        time_str, name_str = lt_data.split("|", 1) if "|" in lt_data else ("", lt_data)
        parsed_items.append({
            "time": time_str, 
            "name": name_str
        })
    
    parsed_items.sort(key=lambda x: x["time"] if x["time"] else "99:99")

    # ─── ไฟล์เดียว: ส่งตรง ─────────────────────────────────────────────────
    if len(parsed_items) == 1:
        item = parsed_items[0]
        time_str = item["time"]
        name_str = item["name"]
        
        filename = f"{time_str.replace(':', '.')}_{name_str}.jpg" if time_str else f"{name_str}.jpg"
        encoded_filename = quote(filename)
        
        # ส่งค่าทั้งหมดเข้าไปในฟังก์ชัน
        img_bytes = create_image_bytes(name_str, main1, main2, pair1, pair2, pair3, pair4, pair5, pair6, win_num)
        return StreamingResponse(
            io.BytesIO(img_bytes),
            media_type="image/jpeg",
            headers={"Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}"},
        )

    # ─── หลายไฟล์: ZIP ใน RAM ──────────────────────────────────────────────
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for index, item in enumerate(parsed_items, start=1):
            time_str = item["time"]
            name_str = item["name"]
            
            prefix = f"{index:02d}_" 
            time_part = f"{time_str.replace(':', '.')}_" if time_str else ""
            
            filename = f"{prefix}{time_part}{name_str}.jpg"
            
            # ส่งค่าทั้งหมดเข้าไปในฟังก์ชัน
            zf.writestr(filename, create_image_bytes(name_str, main1, main2, pair1, pair2, pair3, pair4, pair5, pair6, win_num))
    zip_buf.seek(0)

    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="lottery_results.zip"'},
    )


# ─── Entrypoint ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=False)
