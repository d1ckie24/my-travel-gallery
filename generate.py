import os
import json
import mimetypes
from PIL import Image, ImageOps
import exifread
import boto3
from botocore.config import Config



# Cloudflare R2 配置 (请替换为你自己的真实信息)

R2_ENDPOINT_URL = "https://d3d5c673304dd19da39b031e0d17acfb.r2.cloudflarestorage.com" # 填写 Endpoint，不要末尾斜杠
R2_ACCESS_KEY_ID = "0c15b967194cf477c5b6d46b380ae92d"
R2_SECRET_ACCESS_KEY = "265aab6e1ef57b2103985c0c2254cee84a822926620087d5607ba165b2e6ec48"
R2_BUCKET_NAME = "my-travel-photos"  # 你的 Bucket 名称
R2_PUBLIC_DOMAIN = "https://pub-af9c14e228f5496cba389e5f458c6b09.r2.dev" # 你的 R2 公开访问域名 (不带末尾斜杠)


PHOTOS_DIR = "photos"               
OUTPUT_JSON = "photos.json"
OUTPUT_FILE = OUTPUT_JSON  # 兼容不同配置变量名
SUPPORTED_EXTS = ('.jpg', '.jpeg', '.png', '.webp')


# 初始化 S3/R2 客户端
s3_client = boto3.client(
    "s3",
    endpoint_url=f"https://d3d5c673304dd19da39b031e0d17acfb.r2.cloudflarestorage.com",
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    config=Config(signature_version="s3v4"),
    region_name="auto"
)

def get_exif_date(file_path):
    """从照片 EXIF 中提取拍摄日期"""
    try:
        with open(file_path, 'rb') as f:
            tags = exifread.process_file(f, stop_tag='EXIF DateTimeOriginal', details=False)
            if 'EXIF DateTimeOriginal' in tags:
                date_str = str(tags['EXIF DateTimeOriginal'])
                return date_str.split(' ')[0].replace(':', '-')
    except Exception:
        pass
    return None

def process_and_upload():
    if not os.path.exists(PHOTOS_DIR):
        os.makedirs(PHOTOS_DIR)
        print(f"📁 已创建 '{PHOTOS_DIR}' 文件夹，请放入照片文件夹后重新运行。")
        return

    # 1. 递归扫描本地 photos/ 下的所有照片（含子文件夹）
    local_photo_map = {} # rel_path -> full_path
    for root, _, files in os.walk(PHOTOS_DIR):
        for file in files:
            if file.lower().endswith(SUPPORTED_EXTS) and not file.startswith('.'):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, PHOTOS_DIR).replace("\\", "/")
                local_photo_map[rel_path] = full_path

    print(f"🔍 本地扫描到 {len(local_photo_map)} 张照片。")

    # 2. 检查与清理 R2 上的废弃/不规范文件
    print("🔍 正在检查与清理 R2 上的废弃与错乱文件...")
    try:
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=R2_BUCKET_NAME):
            if 'Contents' in page:
                for obj in page['Contents']:
                    key = obj['Key']
                    
                    # 判断 R2 key 结构
                    is_valid = False
                    for prefix in ['raw/', 'images/', 'thumbs/']:
                        if key.startswith(prefix):
                            # 提取相对路径
                            sub_path = key[len(prefix):]
                            # 还原原始相对路径（去除 _thumb.jpg / _large.jpg 后缀）
                            if prefix == 'thumbs/':
                                raw_rel = sub_path.rsplit('_thumb.', 1)[0]
                            elif prefix == 'images/':
                                raw_rel = sub_path.rsplit('_large.', 1)[0]
                            else:
                                raw_rel = sub_path
                            
                            # 检查本地是否存在匹配前缀的源文件
                            for local_rel in local_photo_map:
                                if local_rel.rsplit('.', 1)[0] == raw_rel or local_rel == raw_rel:
                                    is_valid = True
                                    break
                            break

                    # 若不规范（如直接建在根目录的文件夹）或本地已删除，则从 R2 删除
                    if not is_valid:
                        print(f"🗑️ 正在清理 R2 废弃/无效文件: {key}")
                        s3_client.delete_object(Bucket=R2_BUCKET_NAME, Key=key)
    except Exception as e:
        print(f"⚠️ 清理提示: {e}")

    print("🚀 开始处理并同步照片...")
    photos_data = []

    for rel_path, full_path in local_photo_map.items():
        base_rel_no_ext, _ = os.path.splitext(rel_path)
        filename = os.path.basename(full_path)
        name_without_ext, _ = os.path.splitext(filename)

        # 解析行程名称 (如果放在子文件夹中，子文件夹名即为行程名)
        path_parts = rel_path.split("/")
        trip_name = path_parts[0] if len(path_parts) > 1 else "随手抓拍"

        date_str = get_exif_date(full_path)

        with Image.open(full_path) as img:
            img = ImageOps.exif_transpose(img) # 自动纠正旋转
            
            # --- 1. 缩略图 (网格极速加载) ---
            img_thumb = img.copy()
            img_thumb.thumbnail((500, 500), Image.Resampling.LANCZOS)
            thumb_filename = f"{name_without_ext}_thumb.jpg"
            thumb_path = full_path + "_thumb_temp.jpg"
            img_thumb.convert("RGB").save(thumb_path, "JPEG", quality=75, optimize=True)

            # --- 2. 高清大图 (弹窗预览) ---
            img_large = img.copy()
            img_large.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            large_w, large_h = img_large.size
            large_filename = f"{name_without_ext}_large.jpg"
            large_path = full_path + "_large_temp.jpg"
            img_large.convert("RGB").save(large_path, "JPEG", quality=85, optimize=True)

        # R2 存储路径设计（保持子文件夹层级）
        rel_dir = os.path.dirname(rel_path)
        r2_raw_key = f"raw/{rel_path}"
        r2_large_key = f"images/{os.path.join(rel_dir, large_filename).replace('\\', '/')}" if rel_dir else f"images/{large_filename}"
        r2_thumb_key = f"thumbs/{os.path.join(rel_dir, thumb_filename).replace('\\', '/')}" if rel_dir else f"thumbs/{thumb_filename}"

        print(f"📦 正在同步: {rel_path}")

        content_type, _ = mimetypes.guess_type(full_path)
        s3_client.upload_file(full_path, R2_BUCKET_NAME, r2_raw_key, ExtraArgs={"ContentType": content_type or "image/jpeg"})
        s3_client.upload_file(large_path, R2_BUCKET_NAME, r2_large_key, ExtraArgs={"ContentType": "image/jpeg"})
        s3_client.upload_file(thumb_path, R2_BUCKET_NAME, r2_thumb_key, ExtraArgs={"ContentType": "image/jpeg"})

        # 清理本地生成的临时文件
        if os.path.exists(thumb_path): os.remove(thumb_path)
        if os.path.exists(large_path): os.remove(large_path)

        photos_data.append({
            "id": name_without_ext,
            "filename": filename,
            "trip": trip_name,
            "date": date_str or "未知日期",
            "url_thumb": f"{R2_PUBLIC_DOMAIN}/{r2_thumb_key}",
            "url_large": f"{R2_PUBLIC_DOMAIN}/{r2_large_key}",
            "url_raw": f"{R2_PUBLIC_DOMAIN}/{r2_raw_key}",
            "width": large_w,
            "height": large_h
        })

    # 按日期倒序排列
    photos_data.sort(key=lambda x: x["date"], reverse=True)

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(photos_data, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 全部完成！共处理 {len(photos_data)} 张照片。数据已更新至 {OUTPUT_JSON}")

if __name__ == "__main__":
    process_and_upload()