"""
Generate test fixtures and icon templates for Universal Game MCP (UGM).
Creates realistic game frames and icon templates for Hearts of Iron IV offline testing.
"""

import os
import cv2
import numpy as np


def ensure_dirs():
    os.makedirs("ugm/adapters/hoi4/icons", exist_ok=True)
    os.makedirs("tests/fixtures/icons", exist_ok=True)


def create_factory_icon(bg_color=(50, 50, 50), roof_color=(80, 160, 240)):
    """Creates a 24x24 factory silhouette icon."""
    icon = np.zeros((24, 24, 3), dtype=np.uint8)
    icon[:] = bg_color

    # Building base
    cv2.rectangle(icon, (3, 11), (21, 21), (180, 180, 180), -1)
    # Chimneys / saw-tooth roof
    pts1 = np.array([[3, 11], [8, 5], [8, 11]], np.int32)
    pts2 = np.array([[9, 11], [14, 5], [14, 11]], np.int32)
    pts3 = np.array([[15, 11], [20, 5], [20, 11]], np.int32)
    cv2.fillPoly(icon, [pts1, pts2, pts3], roof_color)
    # Small door
    cv2.rectangle(icon, (9, 15), (14, 21), (40, 40, 40), -1)
    return icon


def create_political_power_icon():
    """Creates a 24x24 bird / wreath eagle symbol icon."""
    icon = np.zeros((24, 24, 3), dtype=np.uint8)
    icon[:] = (40, 40, 40)
    # Circle emblem
    cv2.circle(icon, (12, 12), 9, (30, 180, 220), 2)
    # Star / eagle in center
    cv2.line(icon, (12, 6), (12, 18), (50, 220, 255), 2)
    cv2.line(icon, (7, 10), (17, 10), (50, 220, 255), 2)
    return icon


def create_stability_icon():
    """Creates a 24x24 balance scale icon."""
    icon = np.zeros((24, 24, 3), dtype=np.uint8)
    icon[:] = (40, 40, 40)
    # Scale center post
    cv2.line(icon, (12, 4), (12, 20), (200, 200, 200), 2)
    cv2.line(icon, (5, 8), (19, 8), (200, 200, 200), 2)
    # Left and right pans
    cv2.circle(icon, (6, 14), 3, (100, 200, 100), -1)
    cv2.circle(icon, (18, 14), 3, (100, 200, 100), -1)
    return icon


def create_pause_button():
    """Creates a 28x20 pause indicator icon with two vertical bars."""
    icon = np.zeros((20, 28, 3), dtype=np.uint8)
    icon[:] = (25, 25, 30)
    # Red background alert box
    cv2.rectangle(icon, (1, 1), (26, 18), (30, 30, 160), -1)
    # Two pause bars (yellow/white)
    cv2.rectangle(icon, (8, 4), (11, 15), (50, 240, 240), -1)
    cv2.rectangle(icon, (16, 4), (19, 15), (50, 240, 240), -1)
    return icon


def create_hoi4_topbar_fixture():
    """
    Creates a realistic 1920x1080 game frame with HOI4 top bar,
    map background, and statistics.
    """
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    # Map ocean/land background
    frame[:] = (70, 50, 40)
    # Add subtle map texture
    cv2.rectangle(frame, (300, 200), (1200, 900), (80, 110, 70), -1)
    cv2.circle(frame, (800, 600), 200, (60, 90, 50), -1)

    # Topbar background (dark steel gradient)
    cv2.rectangle(frame, (0, 0), (1920, 50), (28, 28, 30), -1)
    cv2.line(frame, (0, 50), (1920, 50), (60, 60, 65), 2)

    # Insert icons and text
    pp_icon = create_political_power_icon()
    stab_icon = create_stability_icon()
    civ_icon = create_factory_icon(roof_color=(80, 160, 240))
    pause_btn = create_pause_button()

    # 1. Political Power
    frame[12:36, 75:99] = pp_icon
    cv2.putText(frame, "150", (105, 33), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (230, 230, 230), 2)

    # 2. Stability
    frame[12:36, 185:209] = stab_icon
    cv2.putText(frame, "75%", (215, 33), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (230, 230, 230), 2)

    # 3. War Support
    cv2.putText(frame, "WS", (300, 33), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 200, 255), 2)
    cv2.putText(frame, "80%", (335, 33), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (230, 230, 230), 2)

    # 4. Manpower
    cv2.putText(frame, "MP", (415, 33), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 200, 100), 2)
    cv2.putText(frame, "1.25M", (450, 33), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (230, 230, 230), 2)

    # 5. Factories
    frame[12:36, 535:559] = civ_icon
    cv2.putText(frame, "45/60", (565, 33), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (230, 230, 230), 2)

    # Date and Pause indicator
    cv2.putText(frame, "1 Jan, 1936", (1600, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (240, 240, 240), 2)
    frame[55:75, 1630:1658] = pause_btn
    cv2.putText(frame, "PAUSED", (1665, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (50, 50, 240), 2)

    return frame


def create_hoi4_event_dialog_fixture():
    """Creates a frame containing a center event dialogue modal."""
    frame = create_hoi4_topbar_fixture()

    # Center modal dialog (600x400)
    dx, dy, dw, dh = 660, 300, 600, 400
    cv2.rectangle(frame, (dx, dy), (dx + dw, dy + dh), (35, 38, 42), -1)
    cv2.rectangle(frame, (dx, dy), (dx + dw, dy + dh), (120, 100, 70), 3)

    # Dialog Title
    cv2.putText(frame, "Treaty of Munich", (dx + 160, dy + 50), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (220, 200, 150), 2)

    # Body description
    cv2.putText(frame, "Representatives have concluded negotiations.", (dx + 40, dy + 120), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
    cv2.putText(frame, "The proposed terms require territorial cession.", (dx + 40, dy + 155), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)

    # Option Button 1: Refuse demands
    btn1_x, btn1_y, btn1_w, btn1_h = dx + 50, dy + 240, 500, 40
    cv2.rectangle(frame, (btn1_x, btn1_y), (btn1_x + btn1_w, btn1_y + btn1_h), (55, 60, 68), -1)
    cv2.rectangle(frame, (btn1_x, btn1_y), (btn1_x + btn1_w, btn1_y + btn1_h), (160, 140, 90), 2)
    cv2.putText(frame, "We will never surrender!", (btn1_x + 120, btn1_y + 27), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (240, 240, 240), 2)

    # Option Button 2: Accept terms
    btn2_x, btn2_y, btn2_w, btn2_h = dx + 50, dy + 310, 500, 40
    cv2.rectangle(frame, (btn2_x, btn2_y), (btn2_x + btn2_w, btn2_y + btn2_h), (55, 60, 68), -1)
    cv2.rectangle(frame, (btn2_x, btn2_y), (btn2_x + btn2_w, btn2_y + btn2_h), (160, 140, 90), 2)
    cv2.putText(frame, "Accept the inevitable terms.", (btn2_x + 110, btn2_y + 27), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (240, 240, 240), 2)

    return frame


def main():
    ensure_dirs()

    # Generate icons
    civ_factory = create_factory_icon(roof_color=(80, 160, 240))
    mil_factory = create_factory_icon(roof_color=(60, 60, 220))
    pp_icon = create_political_power_icon()
    stab_icon = create_stability_icon()
    pause_btn = create_pause_button()

    icons = {
        "civilian_factory.png": civ_factory,
        "military_factory.png": mil_factory,
        "political_power.png": pp_icon,
        "stability.png": stab_icon,
        "pause_btn.png": pause_btn,
    }

    for name, img in icons.items():
        cv2.imwrite(os.path.join("ugm/adapters/hoi4/icons", name), img)
        cv2.imwrite(os.path.join("tests/fixtures/icons", name), img)

    # Generate game fixtures
    topbar_frame = create_hoi4_topbar_fixture()
    dialog_frame = create_hoi4_event_dialog_fixture()

    cv2.imwrite("tests/fixtures/hoi4_topbar.png", topbar_frame)
    cv2.imwrite("tests/fixtures/hoi4_dialog.png", dialog_frame)

    print("Fixtures and icon templates generated successfully!")


if __name__ == "__main__":
    main()
