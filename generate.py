import os
import json
from PIL import Image, ExifTags
from datetime import datetime

PHOTOS_DIR = "photos"
OUTPUT_FILE = "photos.json"
ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".JPG", ".JPEG", ".PNG"}

def get_exif_date(img_path):
    """尝试提取照片 EXIF 拍摄日期，失败则退回文件修改时间"""
    try:
        with Image.open(img_path) as img:
            exif = img._getexif()
            if exif:
                for tag_id, value in exif.items():
                    tag = ExifTags.TAGS.get(tag_id, tag_id)
                    if tag in ['DateTimeOriginal', 'DateTime']:
                        # 解析 "YYYY:MM:DD HH:MM:SS"
                        date_part = str(value).split(" ")[0].replace(":", "-")
                        if len(date_part) == 10:
                            return date_part
    except Exception as e:
        pass
    
    # 备用方案：获取文件最后修改时间
    mtime = os.path.getmtime(img_path)
    return datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')

def build_gallery():
    photos = []
    if not os.path.exists(PHOTOS_DIR):
        os.makedirs(PHOTOS_DIR)
        print(f"📁 已自动创建 {PHOTOS_DIR} 文件夹，请将照片或旅行文件夹放进去。")
        return

    # 遍历 photos 文件夹
    for root, _, files in os.walk(PHOTOS_DIR):
        for file in sorted(files):
            ext = os.path.splitext(file)[1]
            if ext in ALLOWED_EXTS:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, ".").replace("\\", "/")
                
                # 判断是否有父级文件夹作为旅行主题 (例如: photos/2024-05 京都/01.jpg)
                rel_to_photos = os.path.relpath(full_path, PHOTOS_DIR).replace("\\", "/")
                path_parts = rel_to_photos.split("/")
                
                trip_name = "随手抓拍"
                if len(path_parts) > 1:
                    trip_name = path_parts[0] # 使用子文件夹名字作为旅行行程名

                try:
                    with Image.open(full_path) as img:
                        width, height = img.size
                        
                    date_str = get_exif_date(full_path)
                    year = date_str.split("-")[0] if "-" in date_str else "未知"
                    title = os.path.splitext(file)[0].replace("-", " ").replace("_", " ")
                    
                    photos.append({
                        "src": rel_path,
                        "width": width,
                        "height": height,
                        "title": title,
                        "date": date_str,
                        "year": year,
                        "trip": trip_name
                    })
                    print(f"✨ 已解析: [{date_str}] [{trip_name}] {file}")
                except Exception as e:
                    print(f"⚠️ 跳过异常图片 {full_path}: {e}")

    # 按拍摄日期倒序排列（最新拍摄的照片排前面）
    photos.sort(key=lambda x: x['date'], reverse=True)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(photos, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 成功生成 {OUTPUT_FILE}！共索引 {len(photos)} 张旅行记忆。")

if __name__ == "__main__":
    build_gallery()