"""Generate Android launcher icons and splash images for TerraINS360 Logger"""
from PIL import Image, ImageDraw, ImageFont
import os, math

# Branding colors
BG_COLOR = (15, 23, 42)      # #0f172a
ACCENT = (14, 165, 233)      # #0ea5e9
ACCENT2 = (6, 182, 212)      # #06b6d4
WHITE = (241, 245, 249)

def draw_logo(img, cx, cy, radius):
    """Draw the TerraINS360 logo - stylized GNSS satellite icon"""
    draw = ImageDraw.Draw(img)
    
    # Outer circle (orbit ring)
    r_outer = int(radius * 0.9)
    draw.ellipse([cx-r_outer, cy-r_outer, cx+r_outer, cy+r_outer], 
                 outline=ACCENT, width=max(2, int(radius*0.06)))
    
    # Inner filled circle (earth/signal dot)
    r_inner = int(radius * 0.32)
    draw.ellipse([cx-r_inner, cy-r_inner, cx+r_inner, cy+r_inner], fill=ACCENT)
    
    # Cross/reticle lines
    lw = max(2, int(radius * 0.04))
    r_cross = int(radius * 0.55)
    draw.line([cx, cy-r_cross, cx, cy+r_cross], fill=ACCENT2, width=lw)
    draw.line([cx-r_cross, cy, cx+r_cross, cy], fill=ACCENT2, width=lw)
    
    # Small satellite dots at orbit positions
    for angle_deg in [30, 150, 270]:
        angle = math.radians(angle_deg)
        sx = cx + int(r_outer * 0.85 * math.cos(angle))
        sy = cy + int(r_outer * 0.85 * math.sin(angle))
        r_sat = max(2, int(radius * 0.08))
        draw.ellipse([sx-r_sat, sy-r_sat, sx+r_sat, sy+r_sat], fill=ACCENT2)
    
    # Signal arcs from center
    for i, frac in enumerate([0.45, 0.6, 0.75]):
        arc_r = int(radius * frac)
        arc_w = max(1, int(radius * 0.03))
        draw.arc([cx-arc_r, cy-arc_r, cx+arc_r, cy+arc_r], 
                 -60, -30, fill=(*ACCENT, 180-i*40), width=arc_w)

def create_icon(size, output_path, is_round=False, padding_frac=0.15):
    """Create a launcher icon at given size"""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    padding = int(size * padding_frac)
    
    if is_round:
        # Round icon with circular background
        r = size // 2 - 2
        draw.ellipse([size//2-r, size//2-r, size//2+r, size//2+r], fill=BG_COLOR)
    else:
        # Square icon with rounded corners
        draw.rounded_rectangle([2, 2, size-2, size-2], radius=size//6, fill=BG_COLOR)
    
    logo_radius = (size - padding*2) // 2
    draw_logo(img, size//2, size//2, logo_radius)
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, 'PNG')
    print(f"  Created: {output_path} ({size}x{size})")

def create_splash(width, height, output_path):
    """Create splash screen"""
    img = Image.new('RGB', (width, height), BG_COLOR)
    draw = ImageDraw.Draw(img)
    
    logo_radius = min(width, height) // 6
    cx, cy = width // 2, height // 2 - 40
    draw_logo(img, cx, cy, logo_radius)
    
    # App name text below logo
    try:
        font = ImageFont.truetype("arial.ttf", max(20, min(width, height)//18))
        font_small = ImageFont.truetype("arial.ttf", max(12, min(width, height)//30))
    except:
        font = ImageFont.load_default()
        font_small = font
    
    text_y = cy + logo_radius + 30
    draw.text((cx, text_y), "TerraINS360", fill=WHITE, font=font, anchor="mt")
    draw.text((cx, text_y + 35), "GNSS Survey Data Logger", fill=ACCENT, font=font_small, anchor="mt")
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, 'PNG')
    print(f"  Created: {output_path} ({width}x{height})")

# Base paths
android_res = r"C:\Users\HP\360Imagery_collection\TerraFusion\android\app\src\main\res"
static_dir = r"C:\Users\HP\360Imagery_collection\TerraFusion\static"
www_dir = r"C:\Users\HP\360Imagery_collection\TerraFusion\www"

# Android launcher icons (mipmap)
icon_sizes = {
    'mipmap-mdpi': 48,
    'mipmap-hdpi': 72,
    'mipmap-xhdpi': 96,
    'mipmap-xxhdpi': 144,
    'mipmap-xxxhdpi': 192,
}

print("=== Generating Android Launcher Icons ===")
for folder, size in icon_sizes.items():
    create_icon(size, os.path.join(android_res, folder, 'ic_launcher.png'), is_round=False)
    create_icon(size, os.path.join(android_res, folder, 'ic_launcher_round.png'), is_round=True)
    # Foreground layer for adaptive icons
    create_icon(size, os.path.join(android_res, folder, 'ic_launcher_foreground.png'), is_round=False, padding_frac=0.25)

print("\n=== Generating Splash Screens ===")
create_splash(480, 800, os.path.join(android_res, 'drawable', 'splash.png'))
create_splash(720, 1280, os.path.join(android_res, 'drawable-hdpi', 'splash.png'))
create_splash(1080, 1920, os.path.join(android_res, 'drawable-xhdpi', 'splash.png'))

print("\n=== Generating PWA Icons ===")
create_icon(192, os.path.join(static_dir, 'icon-192.png'), is_round=False)
create_icon(512, os.path.join(static_dir, 'icon-512.png'), is_round=False)
create_icon(192, os.path.join(www_dir, 'icon-192.png'), is_round=False)
create_icon(512, os.path.join(www_dir, 'icon-512.png'), is_round=False)

print("\n✓ All icons generated successfully!")
