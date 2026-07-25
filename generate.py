import os
import json
import mimetypes
from PIL import Image, ExifTags, ImageOps
from datetime import datetime
import boto3

# ==================== 🛠️ 1. 全局配置区域 (请修改) ====================
# 环境变量与配置（必须放在所有函数之前，防止 NameError）
PHOTOS_DIR = "photos"               # 本地照片存储目录
OUTPUT_FILE = "photos.json"         # 生成的 JSON 索引文件名

# Cloudflare R2 配置 (请替换为你自己的真实信息)

R2_ENDPOINT_URL = "https://d3d5c673304dd19da39b031e0d17acfb.r2.cloudflarestorage.com" # 填写 Endpoint，不要末尾斜杠
R2_ACCESS_KEY_ID = "0c15b967194cf477c5b6d46b380ae92d"
R2_SECRET_ACCESS_KEY = "265aab6e1ef57b2103985c0c2254cee84a822926620087d5607ba165b2e6ec48"
R2_BUCKET_NAME = "my-travel-photos"  # 你的 Bucket 名称
R2_PUBLIC_DOMAIN = "https://pub-af9c14e228f5496cba389e5f458c6b09.r2.dev" # 你的 R2 公开访问域名 (不带末尾斜杠)

# 尺寸与质量配置
ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".JPG", ".JPEG", ".PNG", ".WEBP"}
LARGE_MAX_DIM = 2048  # 高清大图最大边长
LARGE_QUALITY = 82    # 高清大图压缩质量
THUMB_MAX_DIM = 500   # 首页缩略图最大边长
THUMB_QUALITY = 75    # 首页缩略图压缩质量
# ======================================================================

# 初始化 S3/R2 客户端
s3_client = boto3.client(
    's3',
    endpoint_url=R2_ENDPOINT_URL,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    region_name='auto'
)

def get_exif_date(img_path):
    """提取照片 EXIF 拍摄日期，失败退回文件修改时间"""
    try:
        with Image.open(img_path) as img:
            exif = img._getexif()
            if exif:
                for tag_id, value in exif.items():
                    tag = ExifTags.TAGS.get(tag_id, tag_id)
                    if tag in ['DateTimeOriginal', 'DateTime']:
                        date_part = str(value).split(" ")[0].replace(":", "-")
                        if len(date_part) == 10:
                            return date_part
    except Exception:
        pass
    
    mtime = os.path.getmtime(img_path)
    return datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')

def cleanup_r2_orphaned_files(current_local_rel_paths):
    """清理 R2 上本地已经删除了的废弃文件"""
    print("\n🔍 正在对比检查 R2 桶中的废弃文件...")
    try:
        paginator = s3_client.get_paginator('list_objects_v2')
        deleted_count = 0
        
        for page in paginator.paginate(Bucket=R2_BUCKET_NAME):
            if 'Contents' in page:
                for obj in page['Contents']:
                    key = obj['Key']  # 例如: "thumbs/2024/1.webp", "images/2024/1.webp", "raw/2024/1.jpg"
                    
                    # 计算相对应的本地相对路径
                    if key.startswith("thumbs/") or key.startswith("images/"):
                        rel_base = "/".join(key.split("/")[1:]) # 移除前缀
                        rel_base_no_ext = os.path.splitext(rel_base)[0]
                    elif key.startswith("raw/"):
                        rel_base_no_ext = os.path.splitext("/".join(key.split("/")[1:]))[0]
                    else:
                        continue

                    # 检查本地 photos 目录下是否存在同名的原始照片
                    matched = any(os.path.splitext(p)[0] == rel_base_no_ext for p in current_local_rel_paths)
                    
                    if not matched:
                        print(f"🗑️ 自动清理 R2 废弃文件: {key}")
                        s3_client.delete_object(Bucket=R2_BUCKET_NAME, Key=key)
                        deleted_count += 1
                        
        if deleted_count > 0:
            print(f"✨ 已自动清理 {deleted_count} 个同步废弃文件。")
        else:
            print("✅ R2 存储桶状态良好，无需清理。")
    except Exception as e:
        print(f"⚠️ 清理检查跳过: {e}")

def process_and_upload(img_path):
    """同时处理原图、高清大图与极速缩略图，并上传到 Cloudflare R2"""
    try:
        with Image.open(img_path) as orig_img:
            # 自动修正方向
            img = ImageOps.exif_transpose(orig_img)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            # 计算在 R2 上的相对路径结构
            rel_path = os.path.relpath(img_path, PHOTOS_DIR).replace("\\", "/")
            base_rel = os.path.splitext(rel_path)[0]
            ext = os.path.splitext(rel_path)[1]

            # ---------------- 1. 上传超清无损原图 ----------------
            raw_r2_key = f"raw/{rel_path}"
            content_type, _ = mimetypes.guess_type(img_path)
            print(f"📦 上传超清原图: {raw_r2_key} ...")
            s3_client.upload_file(
                img_path,
                R2_BUCKET_NAME,
                raw_r2_key,
                ExtraArgs={'ContentType': content_type or 'image/jpeg'}
            )

            # ---------------- 2. 处理并上传高清大图 ----------------
            large_img = img.copy()
            w, h = large_img.size
            if w > LARGE_MAX_DIM or h > LARGE_MAX_DIM:
                large_img.thumbnail((LARGE_MAX_DIM, LARGE_MAX_DIM), Image.Resampling.LANCZOS)
                w, h = large_img.size

            temp_large_path = img_path + "_large_temp.webp"
            large_img.save(temp_large_path, "WEBP", quality=LARGE_QUALITY, optimize=True)

            large_r2_key = f"images/{base_rel}.webp"
            print(f"🖼️ 上传高清大图: {large_r2_key} ...")
            s3_client.upload_file(
                temp_large_path,
                R2_BUCKET_NAME,
                large_r2_key,
                ExtraArgs={'ContentType': 'image/webp'}
            )
            os.remove(temp_large_path)

            # ---------------- 3. 处理并上传首页微缩图 ----------------
            thumb_img = img.copy()
            if thumb_img.width > THUMB_MAX_DIM or thumb_img.height > THUMB_MAX_DIM:
                thumb_img.thumbnail((THUMB_MAX_DIM, THUMB_MAX_DIM), Image.Resampling.LANCZOS)

            temp_thumb_path = img_path + "_thumb_temp.webp"
            thumb_img.save(temp_thumb_path, "WEBP", quality=THUMB_QUALITY, optimize=True)

            thumb_r2_key = f"thumbs/{base_rel}.webp"
            print(f"⚡ 上传极速缩略图: {thumb_r2_key} ...")
            s3_client.upload_file(
                temp_thumb_path,
                R2_BUCKET_NAME,
                thumb_r2_key,
                ExtraArgs={'ContentType': 'image/webp'}
            )
            os.remove(temp_thumb_path)

            raw_url = f"{R2_PUBLIC_DOMAIN}/{raw_r2_key}"
            large_url = f"{R2_PUBLIC_DOMAIN}/{large_r2_key}"
            thumb_url = f"{R2_PUBLIC_DOMAIN}/{thumb_r2_key}"

            return raw_url, large_url, thumb_url, w, h

    except Exception as e:
        print(f"❌ 处理/上传 {img_path} 失败: {e}")
        return None, None, None, 0, 0

def build_gallery():
    if not os.path.exists(PHOTOS_DIR):
        os.makedirs(PHOTOS_DIR)
        print(f"📁 已创建 {PHOTOS_DIR} 目录，请放入照片后重新运行。")
        return

    photos = []
    local_rel_paths = []

    # 1. 搜集所有本地照片路径
    for root, _, files in os.walk(PHOTOS_DIR):
        for file in sorted(files):
            ext = os.path.splitext(file)[1]
            if ext in ALLOWED_EXTS and not file.endswith("_temp.webp"):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, PHOTOS_DIR).replace("\\", "/")
                local_rel_paths.append(rel_path)

    # 2. 执行自动同步清理
    cleanup_r2_orphaned_files(local_rel_paths)

    # 3. 处理图片与生成 JSON
    print("\n🚀 开始处理与同步照片...")
    for rel_path in local_rel_paths:
        full_path = os.path.join(PHOTOS_DIR, rel_path)
        date_str = get_exif_date(full_path)
        
        raw_url, large_url, thumb_url, width, height = process_and_upload(full_path)
        if not large_url:
            continue

        path_parts = rel_path.split("/")
        trip_name = path_parts[0] if len(path_parts) > 1 else "随手抓拍"
        year = date_str.split("-")[0] if "-" in date_str else "未知"
        title = os.path.splitext(os.path.basename(full_path))[0].replace("-", " ").replace("_", " ")

        photos.append({
            "src": large_url,     # 放大查看的高清 WebP 图
            "thumb": thumb_url,   # 首页网格用的轻量缩略图
            "raw": raw_url,       # 原始高清图片 (下载原图使用)
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

    print(f"\n🎉 双图处理与同步完成！共索引 {len(photos)} 张照片，已更新 {OUTPUT_FILE}。")

if __name__ == "__main__":
    build_gallery()