#!/usr/bin/env python3
"""
KAGE // 影の道 — Official Brand Icon & PWA Asset Generator
Generates pixel-perfect icons for mobile home screens, PWA installs, and browser favicons.
"""

import math
import os
import struct
import zlib
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "web" / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

W, H = 512, 512

def make_png(width, height, rgba_data):
    """Encode raw RGBA buffer into a valid standard PNG."""
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)

    header = b'\x89PNG\r\n\x1a\n'
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
    raw_lines = bytearray()
    for y in range(height):
        raw_lines.append(0)
        start = y * width * 4
        raw_lines.extend(rgba_data[start : start + width * 4])
    idat = chunk(b'IDAT', zlib.compress(bytes(raw_lines), 9))
    iend = chunk(b'IEND', b'')
    return header + ihdr + idat + iend

def make_ico(png_32, png_16):
    """Pack 32x32 and 16x16 PNGs into standard multi-resolution .ico format."""
    header = struct.pack('<HHH', 0, 1, 2)
    offset = 6 + 16 * 2
    dir32 = struct.pack('<BBBBHHII', 32, 32, 0, 0, 1, 32, len(png_32), offset)
    offset += len(png_32)
    dir16 = struct.pack('<BBBBHHII', 16, 16, 0, 0, 1, 32, len(png_16), offset)
    return header + dir32 + dir16 + png_32 + png_16

def downsample(src_buf, src_w, src_h, dst_w, dst_h):
    """Area-averaging box filter for anti-aliased downsampling."""
    dst_buf = bytearray(dst_w * dst_h * 4)
    x_ratio = src_w / dst_w
    y_ratio = src_h / dst_h
    for dy in range(dst_h):
        sy_start = int(dy * y_ratio)
        sy_end = max(sy_start + 1, int((dy + 1) * y_ratio))
        for dx in range(dst_w):
            sx_start = int(dx * x_ratio)
            sx_end = max(sx_start + 1, int((dx + 1) * x_ratio))
            r_sum = g_sum = b_sum = a_sum = count = 0
            for sy in range(sy_start, sy_end):
                row_offset = sy * src_w * 4
                for sx in range(sx_start, sx_end):
                    idx = row_offset + sx * 4
                    r_sum += src_buf[idx]
                    g_sum += src_buf[idx + 1]
                    b_sum += src_buf[idx + 2]
                    a_sum += src_buf[idx + 3]
                    count += 1
            dst_idx = (dy * dst_w + dx) * 4
            dst_buf[dst_idx] = r_sum // count
            dst_buf[dst_idx + 1] = g_sum // count
            dst_buf[dst_idx + 2] = b_sum // count
            dst_buf[dst_idx + 3] = a_sum // count
    return dst_buf

def dist_segment(px, py, x1, y1, x2, y2):
    """Distance from point to line segment."""
    dx, dy = x2 - x1, y2 - y1
    l2 = dx*dx + dy*dy
    if l2 == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1)*dx + (py - y1)*dy) / l2))
    return math.hypot(px - (x1 + t*dx), py - (y1 + t*dy))

def render_master_icon():
    """Render 512x512 high-definition KAGE Torii emblem."""
    pixels = bytearray(W * H * 4)

    def blend_pixel(x, y, r, g, b, a):
        if 0 <= x < W and 0 <= y < H and a > 0:
            idx = (y * W + x) * 4
            bg_a = pixels[idx + 3] / 255.0
            fg_a = (a / 255.0)
            out_a = fg_a + bg_a * (1.0 - fg_a)
            if out_a > 0:
                pixels[idx] = min(255, int((r * fg_a + pixels[idx] * bg_a * (1.0 - fg_a)) / out_a))
                pixels[idx + 1] = min(255, int((g * fg_a + pixels[idx + 1] * bg_a * (1.0 - fg_a)) / out_a))
                pixels[idx + 2] = min(255, int((b * fg_a + pixels[idx + 2] * bg_a * (1.0 - fg_a)) / out_a))
                pixels[idx + 3] = min(255, int(out_a * 255))

    corner_r = 112.0
    cx, cy = 256.0, 256.0

    # 1. Background Fill with Rounded Squircle Mask
    for y in range(H):
        for x in range(W):
            # Compute distance to rounded rect boundary
            dx = max(0.0, abs(x - 255.5) - (256.0 - corner_r))
            dy = max(0.0, abs(y - 255.5) - (256.0 - corner_r))
            dist = math.hypot(dx, dy)
            if dist <= corner_r:
                # Radial gradient from center
                r_dist = math.hypot(x - cx, y - cy) / 256.0
                bg_r = int(14 * (1.0 - r_dist*0.5))
                bg_g = int(18 * (1.0 - r_dist*0.5))
                bg_b = int(24 * (1.0 - r_dist*0.5))
                
                # Smooth edge anti-aliasing
                alpha = 255
                if dist > corner_r - 1.5:
                    alpha = int(255 * (corner_r - dist) / 1.5)
                
                idx = (y * W + x) * 4
                pixels[idx] = bg_r
                pixels[idx + 1] = bg_g
                pixels[idx + 2] = bg_b
                pixels[idx + 3] = max(0, min(255, alpha))

    # 2. Outer Vermilion Accent Ring
    for y in range(H):
        for x in range(W):
            dx = max(0.0, abs(x - 255.5) - (256.0 - corner_r))
            dy = max(0.0, abs(y - 255.5) - (256.0 - corner_r))
            dist = math.hypot(dx, dy)
            border_w = 4.0
            if corner_r - border_w - 1.0 <= dist <= corner_r:
                edge_a = 1.0
                if dist > corner_r - 1.0:
                    edge_a = corner_r - dist
                elif dist < corner_r - border_w:
                    edge_a = dist - (corner_r - border_w - 1.0)
                a = int(110 * max(0.0, min(1.0, edge_a)))
                blend_pixel(x, y, 224, 35, 28, a)

    # 3. Glowing Kyoto Crimson Sun
    sun_cx, sun_cy = 256.0, 248.0
    sun_r = 118.0

    # Sun Outer Ambient Glow
    for y in range(int(sun_cy - sun_r - 45), int(sun_cy + sun_r + 45)):
        for x in range(int(sun_cx - sun_r - 45), int(sun_cx + sun_r + 45)):
            d = math.hypot(x - sun_cx, y - sun_cy)
            if d > sun_r and d < sun_r + 40:
                glow_factor = (1.0 - (d - sun_r) / 40.0) ** 2
                a = int(75 * glow_factor)
                blend_pixel(x, y, 224, 35, 28, a)

    # Sun Core with Inner Radiant Gradient
    for y in range(int(sun_cy - sun_r - 2), int(sun_cy + sun_r + 2)):
        for x in range(int(sun_cx - sun_r - 2), int(sun_cx + sun_r + 2)):
            d = math.hypot(x - sun_cx, y - sun_cy)
            if d <= sun_r + 1.0:
                norm_d = d / sun_r
                # Gradient: #ff4a36 (center) -> #e0231c (mid) -> #b31610 (edge)
                sr = int(255 - 65 * norm_d)
                sg = int(74 - 54 * norm_d)
                sb = int(54 - 38 * norm_d)
                a = 255
                if d > sun_r - 1.0:
                    a = int(255 * (sun_r + 1.0 - d) / 2.0)
                blend_pixel(x, y, sr, sg, sb, a)

    # 4. Sacred Torii Gate in Bone White & Platinum Silver
    # Helper for anti-aliased thick lines
    def draw_thick_line(x1, y1, x2, y2, thickness, r, g, b, alpha=255):
        half_th = thickness / 2.0
        min_x = max(0, int(min(x1, x2) - half_th - 2))
        max_x = min(W, int(max(x1, x2) + half_th + 2))
        min_y = max(0, int(min(y1, y2) - half_th - 2))
        max_y = min(H, int(max(y1, y2) + half_th + 2))
        for py in range(min_y, max_y):
            for px in range(min_x, max_x):
                d = dist_segment(px, py, x1, y1, x2, y2)
                if d <= half_th:
                    blend_pixel(px, py, r, g, b, alpha)
                elif d <= half_th + 1.2:
                    edge_a = int(alpha * (half_th + 1.2 - d) / 1.2)
                    blend_pixel(px, py, r, g, b, edge_a)

    # Upper Kasagi Curved Beam
    # Drawn via connected smooth segments
    kasagi_pts = []
    num_steps = 60
    for i in range(num_steps + 1):
        t = i / float(num_steps)
        kx = 72.0 + t * (440.0 - 72.0)
        norm_x = (kx - 256.0) / 184.0
        # Upward curved parabola
        ky = 148.0 - 18.0 * (1.0 - norm_x * norm_x)
        # Upturned wings at tips
        if abs(norm_x) > 0.82:
            tip_lift = ((abs(norm_x) - 0.82) / 0.18) ** 2 * 14.0
            ky -= tip_lift
        kasagi_pts.append((kx, ky))

    for i in range(len(kasagi_pts) - 1):
        x1, y1 = kasagi_pts[i]
        x2, y2 = kasagi_pts[i + 1]
        draw_thick_line(x1, y1, x2, y2, 22.0, 255, 255, 255, 255)
        # Subtle top highlight
        draw_thick_line(x1, y1 - 3, x2, y2 - 3, 4.0, 223, 231, 224, 180)

    # Secondary Horizontal Beam (Nuki)
    draw_thick_line(108.0, 206.0, 404.0, 206.0, 16.0, 255, 255, 255, 255)

    # Left & Right Hashira (Pillars)
    # Slanted slightly inward towards top
    draw_thick_line(164.0, 138.0, 150.0, 396.0, 20.0, 255, 255, 255, 255)
    draw_thick_line(348.0, 138.0, 362.0, 396.0, 20.0, 255, 255, 255, 255)

    # Central Gakuzuka Tablet / Keystone Strut
    for py in range(142, 206):
        for px in range(246, 267):
            blend_pixel(px, py, 255, 255, 255, 255)

    # Pillar Foundation Bases (Kamebara)
    draw_thick_line(134.0, 398.0, 168.0, 398.0, 14.0, 223, 231, 224, 255)
    draw_thick_line(344.0, 398.0, 378.0, 398.0, 14.0, 223, 231, 224, 255)

    # 5. Crisp Geometric "K A G E" Typography
    # Render crisp custom letterforms at bottom (y=440..472)
    def draw_letter_k(start_x, y, h):
        # Vertical bar
        draw_thick_line(start_x, y, start_x, y + h, 6.0, 223, 231, 224, 255)
        # Upper arm
        draw_thick_line(start_x, y + h*0.5, start_x + 24, y, 6.0, 223, 231, 224, 255)
        # Lower leg
        draw_thick_line(start_x + 6, y + h*0.42, start_x + 24, y + h, 6.0, 223, 231, 224, 255)

    def draw_letter_a(start_x, y, h):
        # Left diagonal
        draw_thick_line(start_x + 13, y, start_x, y + h, 6.0, 223, 231, 224, 255)
        # Right diagonal
        draw_thick_line(start_x + 13, y, start_x + 26, y + h, 6.0, 223, 231, 224, 255)
        # Horizontal crossbar
        draw_thick_line(start_x + 6, y + h*0.62, start_x + 20, y + h*0.62, 5.0, 223, 231, 224, 255)

    def draw_letter_g(start_x, y, h):
        # Top bar
        draw_thick_line(start_x + 6, y, start_x + 24, y, 6.0, 223, 231, 224, 255)
        # Left vertical
        draw_thick_line(start_x, y + 6, start_x, y + h - 6, 6.0, 223, 231, 224, 255)
        # Bottom bar
        draw_thick_line(start_x + 6, y + h, start_x + 24, y + h, 6.0, 223, 231, 224, 255)
        # Right lower vertical
        draw_thick_line(start_x + 24, y + h*0.5, start_x + 24, y + h - 4, 6.0, 223, 231, 224, 255)
        # Inward spur
        draw_thick_line(start_x + 12, y + h*0.5, start_x + 24, y + h*0.5, 5.5, 223, 231, 224, 255)

    def draw_letter_e(start_x, y, h):
        # Vertical backbone
        draw_thick_line(start_x, y, start_x, y + h, 6.0, 223, 231, 224, 255)
        # Top bar
        draw_thick_line(start_x, y, start_x + 22, y, 6.0, 223, 231, 224, 255)
        # Middle bar
        draw_thick_line(start_x, y + h*0.5, start_x + 16, y + h*0.5, 5.5, 223, 231, 224, 255)
        # Bottom bar
        draw_thick_line(start_x, y + h, start_x + 22, y + h, 6.0, 223, 231, 224, 255)

    ty = 442.0
    th = 32.0
    # Centered: spacing ~42px apart
    draw_letter_k(162.0, ty, th)
    draw_letter_a(216.0, ty, th)
    draw_letter_g(272.0, ty, th)
    draw_letter_e(326.0, ty, th)

    return pixels

def generate_svg():
    """Generate scalable vector SVG of the KAGE emblem."""
    svg_content = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%">
  <defs>
    <radialGradient id="kageBg" cx="50%" cy="45%" r="65%">
      <stop offset="0%" stop-color="#141a22"/>
      <stop offset="60%" stop-color="#080c10"/>
      <stop offset="100%" stop-color="#040608"/>
    </radialGradient>
    <radialGradient id="kageSun" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#ff4a36"/>
      <stop offset="65%" stop-color="#e0231c"/>
      <stop offset="100%" stop-color="#a01410"/>
    </radialGradient>
    <filter id="crimsonAura" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="16" result="blur"/>
      <feComposite in="SourceGraphic" in2="blur" operator="over"/>
    </filter>
  </defs>

  <!-- Squircle Base Frame -->
  <rect width="512" height="512" rx="112" fill="url(#kageBg)"/>
  <rect width="506" height="506" x="3" y="3" rx="109" fill="none" stroke="rgba(224, 35, 28, 0.45)" stroke-width="4"/>

  <!-- Glowing Crimson Sun -->
  <circle cx="256" cy="248" r="118" fill="url(#kageSun)" filter="url(#crimsonAura)"/>

  <!-- Sacred Kyoto Torii Gate -->
  <g stroke="#ffffff" stroke-linecap="round" stroke-linejoin="round">
    <!-- Top Kasagi Main Beam -->
    <path d="M 72 148 Q 256 128 440 148" stroke="#ffffff" stroke-width="22" fill="none"/>
    <path d="M 64 140 Q 256 120 448 140" stroke="#dfe7e0" stroke-width="6" stroke-opacity="0.8" fill="none"/>
    <!-- Secondary Crossbeam Nuki -->
    <line x1="108" y1="206" x2="404" y2="206" stroke="#ffffff" stroke-width="16"/>
    <!-- Dual Pillars Hashira -->
    <line x1="164" y1="138" x2="150" y2="396" stroke="#ffffff" stroke-width="20"/>
    <line x1="348" y1="138" x2="362" y2="396" stroke="#ffffff" stroke-width="20"/>
    <!-- Central Tablet Gakuzuka -->
    <rect x="246" y="142" width="20" height="64" fill="#ffffff" stroke="none"/>
    <!-- Base Pedestals Kamebara -->
    <line x1="134" y1="398" x2="168" y2="398" stroke="#dfe7e0" stroke-width="14"/>
    <line x1="344" y1="398" x2="378" y2="398" stroke="#dfe7e0" stroke-width="14"/>
  </g>

  <!-- Typography -->
  <text x="256" y="472" font-family="'Onest', 'JetBrains Mono', system-ui, sans-serif" font-size="34" font-weight="800" fill="#dfe7e0" text-anchor="middle" letter-spacing="14">KAGE</text>
</svg>
"""
    return svg_content

def generate_manifest():
    """Generate Progressive Web App (PWA) webmanifest."""
    manifest_data = """{
  "name": "KAGE // 影の道 — SuperBan OS",
  "short_name": "KAGE",
  "description": "Tactical Sovereign Telegram OS & Kyoto War Room Mission Control",
  "start_url": "/",
  "id": "/",
  "scope": "/",
  "display": "standalone",
  "orientation": "any",
  "background_color": "#05070a",
  "theme_color": "#05070a",
  "categories": ["utilities", "productivity", "security"],
  "icons": [
    {
      "src": "/static/icon-192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/static/icon-512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/static/icon-512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "maskable"
    },
    {
      "src": "/static/icon.svg",
      "sizes": "512x512",
      "type": "image/svg+xml",
      "purpose": "any"
    }
  ]
}
"""
    return manifest_data

def main():
    print("🎨 Rendering Master KAGE 512x512 Emblem...")
    master_rgba = render_master_icon()

    print("📦 Generating PNG icon sizes...")
    png_512 = make_png(512, 512, master_rgba)
    (STATIC_DIR / "icon-512.png").write_bytes(png_512)
    print("  ✅ Created web/static/icon-512.png")

    rgba_192 = downsample(master_rgba, 512, 512, 192, 192)
    png_192 = make_png(192, 192, rgba_192)
    (STATIC_DIR / "icon-192.png").write_bytes(png_192)
    print("  ✅ Created web/static/icon-192.png")

    rgba_180 = downsample(master_rgba, 512, 512, 180, 180)
    png_180 = make_png(180, 180, rgba_180)
    (STATIC_DIR / "apple-touch-icon.png").write_bytes(png_180)
    (STATIC_DIR / "apple-touch-icon-precomposed.png").write_bytes(png_180)
    print("  ✅ Created web/static/apple-touch-icon.png (180x180)")

    rgba_32 = downsample(master_rgba, 512, 512, 32, 32)
    png_32 = make_png(32, 32, rgba_32)
    (STATIC_DIR / "favicon-32x32.png").write_bytes(png_32)
    print("  ✅ Created web/static/favicon-32x32.png")

    rgba_16 = downsample(master_rgba, 512, 512, 16, 16)
    png_16 = make_png(16, 16, rgba_16)
    (STATIC_DIR / "favicon-16x16.png").write_bytes(png_16)
    print("  ✅ Created web/static/favicon-16x16.png")

    ico_data = make_ico(png_32, png_16)
    (STATIC_DIR / "favicon.ico").write_bytes(ico_data)
    print("  ✅ Created web/static/favicon.ico")

    (STATIC_DIR / "icon.svg").write_text(generate_svg(), encoding="utf-8")
    print("  ✅ Created web/static/icon.svg")

    (STATIC_DIR / "manifest.json").write_text(generate_manifest(), encoding="utf-8")
    print("  ✅ Created web/static/manifest.json")

    print("\n🏆 ALL KAGE LOGO AND PWA ASSETS GENERATED SUCCESSFULLY!")

if __name__ == "__main__":
    main()
