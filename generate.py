import os
import json
import boto3
from urllib.parse import quote
from PIL import Image, ExifTags
from datetime import datetime



# Cloudflare R2 配置 (请替换为你自己的真实信息)

R2_ENDPOINT_URL = "https://d3d5c673304dd19da39b031e0d17acfb.r2.cloudflarestorage.com" # 填写 Endpoint，不要末尾斜杠
R2_ACCESS_KEY_ID = "0c15b967194cf477c5b6d46b380ae92d"
R2_SECRET_ACCESS_KEY = "265aab6e1ef57b2103985c0c2254cee84a822926620087d5607ba165b2e6ec48"
R2_BUCKET_NAME = "my-travel-photos"  # 你的 Bucket 名称
R2_PUBLIC_DOMAIN = "https://pub-af9c14e228f5496cba389e5f458c6b09.r2.dev" # 你的 R2 公开访问域名 (不带末尾斜杠)

# 2. 图片处理配置
PHOTOS_DIR = "photos"
OUTPUT_FILE = "photos.json"
ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".JPG", ".JPEG", ".PNG"}

# 高清大图配置（用于点击放大查看）
LARGE_MAX_DIM = 2048  
LARGE_QUALITY = 82    

# 首页缩略图配置（用于首页极速网格展示，约 20-50KB/张）
THUMB_MAX_DIM = 500   
THUMB_QUALITY = 75    
# ==================================================

def is_r2_configured():
    """检查用户是否配置了有效的 R2 密钥信息"""
    return not (
        "你的" in R2_ENDPOINT_URL or
        "你的" in R2_ACCESS_KEY_ID or
        "你的" in R2_SECRET_ACCESS_KEY or
        "xxxxxxxx" in R2_PUBLIC_DOMAIN or
        "<" in R2_ENDPOINT_URL
    )

def get_exif_data(img_path):
    """提取照片 EXIF 拍摄日期和尺寸，失败退回文件修改时间"""
    date_str = None
    width, height = 1920, 1080
    try:
        with Image.open(img_path) as img:
            width, height = img.size
            exif = img._getexif()
            if exif:
                for tag_id, value in exif.items():
                    tag = ExifTags.TAGS.get(tag_id, tag_id)
                    if tag in ['DateTimeOriginal', 'DateTime']:
                        date_part = str(value).split(" ")[0].replace(":", "-")
                        if len(date_part) == 10:
                            date_str = date_part
                            break
    except Exception:
        pass
    
    if not date_str:
        mtime = os.path.getmtime(img_path)
        date_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
        
    return date_str, width, height

def fix_orientation(img):
    """修正图片 EXIF 旋转方向"""
    try:
        for orientation in ExifTags.TAGS.keys():
            if ExifTags.TAGS[orientation] == 'Orientation':
                break
        exif = dict(img._getexif().items())
        if exif[orientation] == 3: return img.rotate(180, expand=True)
        elif exif[orientation] == 6: return img.rotate(270, expand=True)
        elif exif[orientation] == 8: return img.rotate(90, expand=True)
    except Exception:
        pass
    return img

def run_local_mode():
    """本地离线模式：直接读取本地照片文件夹生成 photos.json"""
    print("💡 提示: 未检测到有效的 R2 密钥信息，启动【本地测试模式】。")
    photos = []
    
    for root, _, files in os.walk(PHOTOS_DIR):
        for file in sorted(files):
            ext = os.path.splitext(file)[1]
            if ext in ALLOWED_EXTS and not file.endswith("_temp.webp"):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, PHOTOS_DIR).replace("\\", "/")
                
                date_str, width, height = get_exif_data(full_path)
                path_parts = rel_path.split("/")
                trip_name = path_parts[0] if len(path_parts) > 1 else "随手抓拍"
                year = date_str.split("-")[0] if "-" in date_str else "未知"
                title = os.path.splitext(os.path.basename(full_path))[0].replace("-", " ").replace("_", " ")

                local_url = f"{PHOTOS_DIR}/{rel_path}"
                
                photos.append({
                    "src": local_url,
                    "thumb": local_url,
                    "width": width,
                    "height": height,
                    "title": title,
                    "date": date_str,
                    "year": year,
                    "trip": trip_name
                })

    photos.sort(key=lambda x: x['date'], reverse=True)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(photos, f, ensure_ascii=False, indent=2)

    print(f"🎉 [本地模式] 完成！共生成 {len(photos)} 张照片索引至 {OUTPUT_FILE}。")

def run_r2_mode():
    """云端增量同步模式：仅上传 R2 缺失的图片，并进行 URL 安全转码"""
    s3_client = boto3.client(
        's3',
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name='auto'
    )

    existing_keys = set()
    print("🔍 正在拉取 Cloudflare R2 云端已有文件列表...")
    try:
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=R2_BUCKET_NAME):
            if 'Contents' in page:
                for obj in page['Contents']:
                    existing_keys.add(obj['Key'])
        print(f"✅ R2 检查完成，云端共有 {len(existing_keys)} 个文件。")
    except Exception as e:
        print(f"⚠️ 获取 R2 已有文件列表失败: {e}")

    photos = []

    for root, _, files in os.walk(PHOTOS_DIR):
        for file in sorted(files):
            ext = os.path.splitext(file)[1]
            if ext in ALLOWED_EXTS and not file.endswith("_temp.webp"):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, PHOTOS_DIR).replace("\\", "/")
                base_rel = os.path.splitext(rel_path)[0]

                large_r2_key = f"images/{base_rel}.webp"
                thumb_r2_key = f"thumbs/{base_rel}.webp"

                date_str, width, height = get_exif_data(full_path)

                # 核心修复点：将路径中的空格、中文等转码为标准的 HTTP URL (%20 等)
                large_url = f"{R2_PUBLIC_DOMAIN}/{quote(large_r2_key, safe='/')}"
                thumb_url = f"{R2_PUBLIC_DOMAIN}/{quote(thumb_r2_key, safe='/')}"

                need_large = large_r2_key not in existing_keys
                need_thumb = thumb_r2_key not in existing_keys

                if need_large or need_thumb:
                    try:
                        with Image.open(full_path) as orig_img:
                            img = fix_orientation(orig_img)
                            if img.mode in ("RGBA", "P"):
                                img = img.convert("RGB")

                            if need_large:
                                large_img = img.copy()
                                if large_img.width > LARGE_MAX_DIM or large_img.height > LARGE_MAX_DIM:
                                    large_img.thumbnail((LARGE_MAX_DIM, LARGE_MAX_DIM), Image.Resampling.LANCZOS)
                                temp_large = full_path + "_large_temp.webp"
                                large_img.save(temp_large, "WEBP", quality=LARGE_QUALITY, optimize=True)
                                print(f"🖼️ 上传大图: {large_r2_key}")
                                s3_client.upload_file(temp_large, R2_BUCKET_NAME, large_r2_key, ExtraArgs={'ContentType': 'image/webp'})
                                if os.path.exists(temp_large): os.remove(temp_large)

                            if need_thumb:
                                thumb_img = img.copy()
                                if thumb_img.width > THUMB_MAX_DIM or thumb_img.height > THUMB_MAX_DIM:
                                    thumb_img.thumbnail((THUMB_MAX_DIM, THUMB_MAX_DIM), Image.Resampling.LANCZOS)
                                temp_thumb = full_path + "_thumb_temp.webp"
                                thumb_img.save(temp_thumb, "WEBP", quality=THUMB_QUALITY, optimize=True)
                                print(f"⚡ 上传缩略图: {thumb_r2_key}")
                                s3_client.upload_file(temp_thumb, R2_BUCKET_NAME, thumb_r2_key, ExtraArgs={'ContentType': 'image/webp'})
                                if os.path.exists(temp_thumb): os.remove(temp_thumb)

                    except Exception as e:
                        print(f"❌ 上传失败 {full_path}: {e}")
                else:
                    print(f"⏩ 已存在，自动跳过: {rel_path}")

                path_parts = rel_path.split("/")
                trip_name = path_parts[0] if len(path_parts) > 1 else "随手抓拍"
                year = date_str.split("-")[0] if "-" in date_str else "未知"
                title = os.path.splitext(os.path.basename(full_path))[0].replace("-", " ").replace("_", " ")

                photos.append({
                    "src": large_url,
                    "thumb": thumb_url,
                    "width": width,
                    "height": height,
                    "title": title,
                    "date": date_str,
                    "year": year,
                    "trip": trip_name
                })

    photos.sort(key=lambda x: x['date'], reverse=True)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(photos, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 [云端模式] 增量同步完成！相册库共包含 {len(photos)} 张照片，已更新 {OUTPUT_FILE}。")

def build_gallery():
    if not os.path.exists(PHOTOS_DIR):
        os.makedirs(PHOTOS_DIR)
        print(f"📁 已创建 {PHOTOS_DIR} 目录，请放入照片文件夹后再重新运行此脚本。")
        return

    if is_r2_configured():
        run_r2_mode()
    else:
        run_local_mode()

if __name__ == "__main__":
    build_gallery()