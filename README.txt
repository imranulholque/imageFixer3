IMAGE FIXER  (Windows)
======================
Clean many text pictures (WhatsApp photos, scans) and print them. One big card per picture - slide to the next.
 Top bar (dropdown menus):  Select v | Move v | Eyedropper | Erase v | Crop v      Right side tabs: Adjust | Shadow | Text | Save
 - Select v:    Quick select, Brush, Box, Ellipse, Freehand, Color range (click a color). Options bar: New / Add / Subtract /
                Intersect, All / None / Inverse, Grow / Shrink. Shift = add, Alt = subtract.
 - EVERYTHING acts only inside the selection (brightness, contrast, saturation, hue, warmth, color change, shadow removal,
   sharpness, erase). No selection = whole picture. Example: Color range on the red part, Hue +120 -> red becomes green.
 - Delete key:  deletes the selection (filled from the paper around it).   Fill color (Adjust tab) fills it with a color.
 - Move v:      drag = move, Alt + drag = copy, Ctrl+T = Transform (resize / rotate, Enter = apply).
 - Eyedropper:  click a color -> slider (fade / stronger) and "Change to..." (recolor, shading stays). Respects the selection.
 - Shadow tab:  one click, plus Strength / Paper whiteness / Text darkness / Shadow size / Color-Gray-B&W.
 - Text tab:    Text sharpness, Line & shape sharpness, Faint strokes, Text darkness, Clean specks.
 - Erase v:     Brush / Box / Ellipse / Freehand: filled from around. Alt + click = copy from that spot.
 - Crop v:      Crop, Straighten (4 corners), Rotate left / right.
Slow work shows a progress bar at the bottom; the window stays usable.

SAVING
 Save / Save as (JPG, PNG, WEBP, BMP, TIF, PDF). "Save all edited" saves the edited pictures.
 "Save all as PDF" = one PDF with every open picture, one page each, in the order of the thumbnails
 (use the arrows at the bottom to change a page's place). Page size: same shape as the picture, A4 or Letter.

PHONE AS A SCANNER  ("Add from phone" at the top)
 Tabs: Same Wi-Fi (QR code) or Internet (public link through localhost.run, needs Windows' OpenSSH client; start/stop it yourself).
 Received files are saved in the folder shown under 'Save received files to'. Scan the QR code with the phone (same Wi-Fi). A page opens in the phone's browser - no app needed:
  Choose file : send pictures from the phone.
  Scan        : photograph a paper -> the paper is found and straightened automatically; drag the 4 corners if
                needed -> "Use this page" -> "Add more" for the next page -> "Send to desktop".
                Pages can be reordered, redone or deleted before sending. They arrive in order as separate pictures.
 The link contains a secret code that is new every time the program starts (or when you press "New code"); only
 devices on your local network can use it and only this program's page is reachable. The first time, Windows may ask
 to allow the program on private networks - allow it. If the Wi-Fi changes, the QR code updates by itself.

HOW TO GET THE PROGRAM
 1. Make a new GitHub repository and upload everything in this folder (keep the folders, including .github).
 2. Actions tab -> "Build" -> latest run -> Artifacts -> ImageFixer -> download, unzip.
 3. Put the unzipped folder somewhere permanent (for example C:\Tools\ImageFixer) and run ImageFixer.exe.
    (Do not move the folder later without starting the program once from the new place.)

ADDING PICTURES
 - Choose files button, or drag & drop, or Ctrl+V (after copying picture files in Explorer or a picture from anywhere).
 - Explorer: select pictures -> right-click -> Send to -> Image Fixer
   (Windows 11: right-click -> Show more options -> Send to).

KEYS
 Ctrl+Z undo (step by step) ... Ctrl+Y / Ctrl+Shift+Z redo     Del delete selection      Ctrl+T transform
 Ctrl+A all, Ctrl+D none, Ctrl+Shift+I inverse                 Enter / Esc apply / cancel crop or transform
 V move, M box, L freehand, W quick select, B brush select, E erase, C crop, I eyedropper, H hand (Space + drag = pan)
 Ctrl + wheel zoom, Ctrl +/- zoom, Ctrl+0 fit, Ctrl+1 100%     Left / Right previous / next picture
 Ctrl+S save, Ctrl+Shift+S save all edited, Ctrl+C copy picture

SETTINGS (top right): start with Windows, Send to entry, save folder.
Closing the window keeps the program in the tray; use the tray icon -> Exit to quit.
Problems are written to  %APPDATA%\ImageFixer\error.log

NEW (round 3)
- Shadow options stay live after one-click removal; "Keep photos / faces untouched" is on by default.
- Quick select removed. Freehand = real lasso (closes itself). Transform has a round rotate handle.
- Color picking is hue/chroma aware (red no longer takes dark pixels with a faint red cast).
- Deleting a stamp keeps the dark signature under it ("Keep dark writing under a removed stamp").
- "Apply last setting to all pictures" copies only the last changed setting.
- Phone: Local network (plain http://IP:PORT, 4-digit port box) and Internet (only while that tab is open).
  On the local network no secret link is needed; the Internet link keeps a secret path.

NEW (round 4)
- Transform: buttons Scale/Rotate, Skew, Distort, Perspective, Warp (16-point net to bend a curved line straight),
  90-degree turns and flips. Keys: [ ] rotate 1 deg, Ctrl+[ ] 15 deg, Ctrl+Alt+Left/Right 90 deg, arrows move (Shift = 10 px).
- Photo detection now needs skin tones (a face), so watermarks / big stamps are not treated as photos.
  Photos get only a soft light correction in shadow removal, no ink / contrast changes.
- Erase: the soft edge of removed writing is removed too (no grey ghost); the fill uses only real paper colour.
