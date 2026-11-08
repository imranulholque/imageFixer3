IMAGE FIXER  (Windows)
======================
Clean many text pictures (WhatsApp photos, scans) and print them. One big card per picture - slide to the next.
 Top bar (dropdown menus):  Select v | Move v | Eyedropper | Color Brush | Erase v | Crop v      Right side tabs: Adjust | Color | Shadow | Text | Picture | Save
 - Select v:    Quick select, Brush, Box, Ellipse, Freehand, Color range (click a color). Options bar: New / Add / Subtract /
                Intersect, All / None / Inverse, Grow / Shrink. Shift = add, Alt = subtract.
 - EVERYTHING acts only inside the selection (brightness, contrast, saturation, hue, warmth, color change, shadow removal,
   sharpness, erase). No selection = whole picture. Example: Color range on the red part, Hue +120 -> red becomes green.
 - Color range 'Preview' tick: shows the selection as black and white (white = selected, grey = partly, black = not), like Photoshop; move Fuzziness to see what changes.
 - Stamp removal is smart: a coloured stamp over writing of ANY other colour (black, grey, blue ink, red ...) is separated from the writing
                (the stamp colour is learned from the selected pixels, the writing colour from the writing around the stamp), so the letters
                under the stamp are rebuilt and the faint halo of the stamp goes too. Writing with the SAME colour as the stamp cannot be separated.
 - Delete key:  deletes the selection (filled from the paper around it).   Fill color (Color tab) fills it with a color.
 - Move v:      drag = move, Alt + drag = copy, Ctrl+T = Transform (resize / rotate, Enter = apply).
 - Color Brush (P): paints with the fill color (swatch in the options bar / Color tab). Select something first = the color goes ONLY
                inside the selection; no selection = paint anywhere on the page. Size, Opacity, Softness. One stroke = one Ctrl+Z step.
 - Eyedropper:  click a color -> slider (fade / stronger) and "Change to..." (recolor, shading stays). Respects the selection.
 - Shadow tab:  One-click removal (Strength, Paper whiteness, Text darkness, Shadow size, Even out the light, Shadow depth,
                Keep paper color, Clean the paper, Color-Gray-B&W) AND Manual shadow removal that works with no click:
                Even out the light, Shadow size, Lift shadows, Whiten paper, Darken text. Select a part first = fix only that part.
 - Text tab:    Text sharpness, Line & shape sharpness, Faint strokes, Text darkness, Clean specks.
 - Erase v:     Brush / Box / Ellipse / Freehand: filled from around. Alt + click = copy from that spot.
 - Crop v:      Crop, Straighten (4 corners), Crop to circle / oval / rounded box / the selection shape, Rotate left / right.
                A picture you placed (signature, stamp ...): select it, then pick any crop - it is cropped alone and becomes
                transparent outside the shape. (Click outside the picture first to crop the page instead.)
Slow work shows a progress bar at the bottom; the window stays usable.

PDF FILES
 Open, drag in, or paste (Ctrl+V) a PDF like a picture: every page becomes its own picture (its own card), like Photoshop.
 Pages are drawn at about 200 dpi. Save puts them in the save folder (the PDF itself is never changed); 'Save all as PDF' makes a new PDF.

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
 V move, M box, L freehand, W quick select, B brush select, E erase, C crop, I eyedropper, P color brush, H hand (Space + drag = pan)
 Ctrl + wheel zoom, Ctrl +/- zoom, Ctrl+0 fit, Ctrl+1 100%     Left / Right previous / next picture
 Ctrl+S save, Ctrl+Shift+S save all edited, Ctrl+C copy picture

SETTINGS (top right): start with Windows, Send to entry, save folder, Auto update.
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

NEW (round 4)
- Thumbnails: click, Ctrl+click, Shift+click, drag a box, Ctrl+A to select. Del removes the selected pictures from the list
  (files on the disk are never touched), Ctrl+C copies them, dragging takes all selected pictures to another program.
  Right-click a thumbnail for the same as a menu.
- Pictures open in the background (progress bar, window never freezes); adding / saving / copying many pictures is done in small
  steps or in a worker thread. Settings > "Picture detail" sets how sharp the on-screen picture is (Automatic = from your screen);
  Settings > "Interface size" scales the text and buttons. Toolbars wrap instead of being cut off on a small window.
- Saturation is now a real saturation: 0 = gray picture, 100 = as it is, 200 = twice as strong; brightness and hue stay the same,
  gray / black / white never change, strong colors do not burn out. New Vibrance slider (weak colors change more than strong ones).
- Shadow: manual sliders work without the one-click button; the one-click got 4 more options. Existing settings are unchanged.
- Placed pictures (signature, stamp ...) can be cropped to a circle, oval, rounded box, a box or any selected shape.
- Compare button (top right of a picture, only shown after you changed it): original and edited together, with a slider or side by side. Wheel = zoom, drag = move.

NEW (round 5)
- Save works like Photoshop: Save (Ctrl+S) replaces the original file (asked once; a copy of the original is kept for Reset / Compare
  while the picture is open). A picture from the phone / clipboard has no original: it goes to the save folder, which is asked once and
  then remembered (change it with Save as... or the folder button). Save as... = folder, name, format JPG / PNG / PDF.
  Save all edited... = replace the originals OR a folder (Browse...), remembered. Print... (Ctrl+P).
  Edited copies made for copy / drag / print are temporary: they are removed with the picture and when the program exits.
- Incoming pictures (drag, Ctrl+V, phone / QR): in the PICTURE tab they become an overlay on the current page, in any other tab a new main picture.
- Phone: new page (Scan / Send / Scan a saved photo, drag corners AND sides, Original / Clean / Gray / B&W, turn, auto next page),
  a much better paper finder for busy backgrounds. The phone window is now a dropdown under the button.
- Compare is shown IN the picture (slider or side by side); click it again to switch it off.
- Select: Color range has Fuzziness (updates live after the click, optional 'Near the click'); new Deselect brush (Select menu) takes
  parts out of a selection accurately; the clicked color becomes the fill color; Fill / Delete / eyedropper / hex in one group;
  'Remove a stamp or mark' (keep dark writing) is its own group at the top of the Color tab (Color tab = stamp removal, Color range & fill, Picked colors).
- Shadow tab: Newspaper / book photo -> white page (one click + slider, both directions); 'keep photos and faces' removed.
- Text options are stronger at 100% (faint writing gets dark, the paper stays white).
- Overlay background cut: paper noise is measured, specks removed (new sliders Keep faint strokes / Remove specks); pictures keep their
  full resolution (up to 4800 px) and are upscaled with a good filter when shown larger.
- Warp (Transform) has a 5 x 5 net: corners, middle of every side and the middle of the picture.
- Undo during a Transform steps back inside it and finally cancels it (untransform).

AUTO UPDATE  (Settings > Auto update)
 - ON (default): a few seconds after start, and every 6 hours while the program is open, it looks on GitHub for a newer build.
   When there is one you get a notification (tray), then a question "Update now / Later". Only after "Update now" it downloads the
   new version, closes, copies it over the program folder and starts again. Your settings are kept. Unsaved pictures are asked about first.
 - OFF: it never looks and never shows anything about updates. ("Check for updates now..." still works when you click it yourself.)
 - How it works: every build that finishes on the main branch is published as a GitHub Release  build-<number>  (file ImageFixer-windows.zip).
   The build writes its own number and your repository name into the program (buildinfo.py), so nothing needs to be set up.
 - Needs: a PUBLIC repository (a private one cannot be read by the program without a login), and the program folder must be writable
   (C:\Tools\ImageFixer is fine, C:\Program Files is not). Details of a failed update: %APPDATA%\ImageFixer\update.log
 - Repository Settings > Actions > General > Workflow permissions must allow "Read and write permissions" (the build publishes the Release).
 - Test the update logic: python test_updater.py
