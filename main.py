"""
Lottery Image Generator — FastAPI Edition
ปรับปรุงจาก Flask เดิม:
  - FastAPI + Jinja2 (async, เร็วกว่า Flask ~2-3x)
  - In-memory image & ZIP (ไม่เซฟลง disk เลย → เหมาะ Render/Railway/Fly.io)
  - Image/font cache ที่ startup (โหลดครั้งเดียว)
  - JWT-based session แทน Flask-Login
"""

import io
import random
import zipfile
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from PIL import Image, ImageDraw, ImageFont
from zoneinfo import ZoneInfo

# ─── App setup ───────────────────────────────────────────────────────────────

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ─── Auth config (เปลี่ยน SECRET_KEY ก่อน deploy!) ───────────────────────────

SECRET_KEY = "change-me-before-deploy-use-openssl-rand-hex-32"
ALGORITHM  = "HS256"
TOKEN_EXPIRE_HOURS = 8

USERS = {"admin": "1234"}  # TODO: ใช้ DB + bcrypt จริง ๆ ใน production


def create_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode({"sub": username, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str | None = Cookie(default=None, alias="access_token")) -> str:
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
    return Image.open("static/Baan Demo.png").convert("RGBA")


@lru_cache(maxsize=8)
def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """Cache แต่ละขนาด font แยกกัน"""
    return ImageFont.truetype("static/SURATANADEMO-ExtraBold.ttf", size)


# ─── Image generation (ไม่แตะ disk เลย) ─────────────────────────────────────

def _get_auto_font(draw: ImageDraw.ImageDraw, text: str, max_width: int,
                   start: int = 50, min_size: int = 20) -> ImageFont.FreeTypeFont:
    for size in range(start, min_size - 1, -1):
        font = _load_font(size)
        w = draw.textbbox((0, 0), text, font=font)[2]
        if w <= max_width:
            return font
    return _load_font(min_size)


def _bold_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
               font: ImageFont.FreeTypeFont, fill: str = "#ffca08", boldness: int = 1) -> None:
    x, y = xy
    for dx in range(-boldness, boldness + 1):
        for dy in range(-boldness, boldness + 1):
            draw.text((x + dx, y + dy), text, font=font, fill=fill)


def create_image_bytes(lottery_type: str) -> bytes:
    """
    สร้างรูปภาพในหน่วยความจำและคืนค่าเป็น bytes (PNG)
    ไม่มีการเขียนไฟล์ลง disk เลย
    """
    # deepcopy เพื่อไม่ให้แก้ไข cached image โดยตรง
    image = deepcopy(_load_bg()).convert("RGB")
    draw  = ImageDraw.Draw(image)

    # วันที่ปัจจุบัน
    date_text = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%d.%m.%y")
    draw.text((190, 50), date_text, font=_load_font(30), fill="#ffca08")

    # ชื่อประเภทหวย (auto-fit)
    font_auto = _get_auto_font(draw, lottery_type, image.width - 100)
    bbox = draw.textbbox((0, 0), lottery_type, font=font_auto)
    x_pos = (image.width - (bbox[2] - bbox[0])) // 2
    _bold_text(draw, (x_pos, 110), lottery_type, font_auto)

    # ─── สุ่มเลข ───────────────────────────────────────────────────────────
    num1, num2 = random.sample(range(10), 2)
    disallowed = {f"{num1}{num2}", f"{num2}{num1}"}

    def pick(prefix: int, exclude: list[str]) -> list[str]:
        pool = [f"{prefix}{i}" for i in range(10) if f"{prefix}{i}" not in disallowed]
        chosen: list[str] = []
        for _ in range(3):
            available = [x for x in pool if x not in exclude + chosen]
            chosen.append(random.choice(available))
        return chosen

    tens  = pick(num1, [])
    units = pick(num2, [])

    other  = [i for i in range(10) if i not in (num1, num2)]
    extras = random.sample(other, 4)
    six    = [num1, num2] + extras
    random.shuffle(six)
    random_6 = "".join(map(str, six))

    # ─── วาดผลลัพธ์ ────────────────────────────────────────────────────────
    f_large  = _load_font(75)
    f_medium = _load_font(60)
    f_small  = _load_font(50)

    _bold_text(draw, (160, 190), f"{num1} - {num2}", f_large)
    for i, val in enumerate(tens):
        _bold_text(draw, (120 + i * 90, 320), val, f_medium)
    for i, val in enumerate(units):
        _bold_text(draw, (120 + i * 90, 430), val, f_medium)
    _bold_text(draw, (55, 520), f"วิน.{random_6}", f_small)

    # ─── คืนค่าเป็น bytes (ไม่เซฟไฟล์) ────────────────────────────────────
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=85, optimize=True)
    buf.seek(0)
    return buf.read()


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(
    username: str = Form(...),
    password: str = Form(...),
):
    if USERS.get(username) != password:
        raise HTTPException(status_code=400, detail="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key="access_token",
        value=create_token(username),
        httponly=True,
        samesite="lax",
        max_age=TOKEN_EXPIRE_HOURS * 3600,
    )
    return response


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
):
    if not lottery_type:
        raise HTTPException(status_code=400, detail="กรุณาเลือกประเภทหวยอย่างน้อย 1 รายการ")

    # ─── ไฟล์เดียว: ส่งตรง ─────────────────────────────────────────────────
    if len(lottery_type) == 1:
        img_bytes = create_image_bytes(lottery_type[0])
        return StreamingResponse(
            io.BytesIO(img_bytes),
            media_type="image/jpeg",
            headers={"Content-Disposition": 'attachment; filename="lottery_result.jpg"'},
        )

    # ─── หลายไฟล์: ZIP ใน RAM ──────────────────────────────────────────────
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for lt in lottery_type:
            zf.writestr(f"{lt}.jpg", create_image_bytes(lt))
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
