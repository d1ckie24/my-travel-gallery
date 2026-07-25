import os
import json
import boto3
from PIL import Image, ExifTags
from datetime import datetime

# ==================== 配置区域 ====================
# 1. Cloudflare R2 配置 (请替换为你自己的信息)
R2_ENDPOINT_URL = "https://d3d5c673304dd19da39b031e0d17acfb.r2.cloudflarestorage.com" # 填写 Endpoint，不要末尾斜杠
R2_ACCESS_KEY_ID = "0c15b967194cf477c5b6d46b380ae92d"
R2_SECRET_ACCESS_KEY = "265aab6e1ef57b2103985c0c2254cee84a822926620087d5607ba165b2e6ec48"
R2_BUCKET_NAME = "my-travel-photos"  # 你的 Bucket 名称
R2_PUBLIC_DOMAIN = "https://pub-af9c14e228f5496cba389e5f458c6b09.r2.dev" # 你的 R2 公开访问域名 (不带末尾斜杠)

# 2. 图片处理配置
PHOTOS_DIR = "photos"
OUTPUT_FILE = "photos.json"
ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".JPG", ".JPEG", ".PNG"}
MAX_DIMENSION = 2048  # 网页展示的最大宽高（等比例缩放）
QUALITY = 82          # 图片 WebP 压缩质量 (80-85 画质高且体积小)
# ==================================================

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

def process_and_upload(img_path):
    """压缩大图为 WebP 并上传至 Cloudflare R2"""
    try:
        with Image.open(img_path) as img:
            # 修正方向
            try:
                for orientation in ExifTags.TAGS.keys():
                    if ExifTags.TAGS[orientation] == 'Orientation':
                        break
                exif = dict(img._getexif().items())
                if exif[orientation] == 3: img = img.rotate(180, expand=True)
                elif exif[orientation] == 6: img = img.rotate(270, expand=True)
                elif exif[orientation] == 8: img = img.rotate(90, expand=True)
            except Exception:
                pass

            # 尺寸缩放
            w, h = img.size
            if w > MAX_DIMENSION or h > MAX_DIMENSION:
                img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
                w, h = img.size

            # 转换为 WebP
            base_path, _ = os.path.splitext(img_path)
            temp_webp_path = base_path + "_optimized.webp"
            
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(temp_webp_path, "WEBP", quality=QUALITY, optimize=True)

            # 计算在 R2 上的存储路径 (Key)
            rel_path = os.path.relpath(img_path, PHOTOS_DIR).replace("\\", "/")
            r2_key = os.path.splitext(rel_path)[0] + ".webp"

            # 上传到 R2
            print(f"☁️ 正在上传到 R2: {r2_key} ...")
            s3_client.upload_file(
                temp_webp_path,
                R2_BUCKET_NAME,
                r2_key,
                ExtraArgs={'ContentType': 'image/webp'}
            )

            # 删除临时压缩文件
            if os.path.exists(temp_webp_path):
                os.remove(temp_webp_path)

            r2_url = f"{R2_PUBLIC_DOMAIN}/{r2_key}"
            return r2_url, w, h

    except Exception as e:
        print(f"❌ 处理/上传 {img_path} 失败: {e}")
        return None, 0, 0

def build_gallery():
    photos = []
    if not os.path.exists(PHOTOS_DIR):
        os.makedirs(PHOTOS_DIR)
        print(f"📁 已创建 {PHOTOS_DIR} 目录，请放入照片文件夹后重新运行。")
        return

    for root, _, files in os.walk(PHOTOS_DIR):
        for file in sorted(files):
            ext = os.path.splitext(file)[1]
            if ext in ALLOWED_EXTS and not file.endswith("_optimized.webp"):
                full_path = os.path.join(root, file)
                
                # 1. 获取 EXIF 日期
                date_str = get_exif_date(full_path)
                
                # 2. 压缩并上传至 Cloudflare R2
                r2_url, width, height = process_and_upload(full_path)
                if not r2_url:
                    continue

                # 3. 解析行程分类
                rel_to_photos = os.path.relpath(full_path, PHOTOS_DIR).replace("\\", "/")
                path_parts = rel_to_photos.split("/")
                trip_name = path_parts[0] if len(path_parts) > 1 else "随手抓拍"

                year = date_str.split("-")[0] if "-" in date_str else "未知"
                title = os.path.splitext(os.path.basename(full_path))[0].replace("-", " ").replace("_", " ")

                photos.append({
                    "src": r2_url,
                    "width": width,
                    "height": height,
                    "title": title,
                    "date": date_str,
                    "year": year,
                    "trip": trip_name
                })

    # 按拍摄时间倒序
    photos.sort(key=lambda x: x['date'], reverse=True)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(photos, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 处理完毕！共上传 {len(photos)} 张照片至 R2，生成 {OUTPUT_FILE}。")

if __name__ == "__main__":
    build_gallery()