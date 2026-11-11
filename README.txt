IMAGE FIXER  (Windows)
======================
Made by Imranul Hoque Sabbir.
Clean many text pictures (WhatsApp photos, scans) and print them. One big card per picture - slide to the next.
 Top bar (dropdown menus):  Select v | Move v | Eyedropper | Color Brush | Erase v | Crop v      Right side tabs: Adjust | Color | Shadow | Text | Picture | Aa;  Save v is at the top right, next to Settings
 - Select v:    Quick select, Brush, Box, Ellipse, Freehand, Color range (click a color). Options bar: New / Add / Subtract /
                Intersect, All / None / Inverse, Grow / Shrink. Shift = add, Alt = subtract.
 - EVERYTHING acts only inside the selection (brightness, contrast, saturation, hue, warmth, color change, shadow removal,
   sharpness, erase). No selection = whole picture. Example: Color range on the red part, Hue +120 -> red becomes green.
 - Color range 'Preview' tick: shows the selection as black and white (white = selected, grey = partly, black = not), like Photoshop; move Fuzziness to see what changes.
 - Stamp removal is smart: a coloured stamp over writing of ANY other colour (black, grey, blue ink, red ...) is separated from the writing
                (the stamp colour is learned from the selected pixels, the writing colour from the writing around the stamp), so the letters
                under the stamp are rebuilt and the faint halo of the stamp goes too. Writing with the SAME colour as the stamp cannot be separated.
 - Delete key:  deletes the selection (filled from the paper around it).   Fill color (Color tab) fills it with a color.
 - Move v:      drag = move, Alt + drag = copy, Ctrl+T = Transform (resize / rotate, Enter = apply; Alt + drag inside Transform = place a COPY, the original stays). Hold Shift while dragging = dead straight (sideways, up / down or diagonal).
 - Color Brush (P): paints with the fill color (swatch in the options bar / Color tab). Select something first = the color goes ONLY
                inside the selection; no selection = paint anywhere on the page. Size, Opacity, Softness. One stroke = one Ctrl+Z step.
 - Eyedropper:  click a color -> slider (fade / stronger) and "Change to..." (recolor, shading stays). Respects the selection.
 - Shadow tab:  One-click removal (Strength, Paper whiteness, Text darkness, Shadow size, Even out the light, Shadow depth,
                Keep paper color, Clean the paper, Color-Gray-B&W) AND Manual shadow removal that works with no click:
                Even out the light, Shadow size, Lift shadows, Whiten paper, Darken text. Select a part first = fix only that part.
 - Text tab:    Text sharpness, Line & shape sharpness, Faint strokes, Text darkness, Clean specks.
 - Erase v:     Brush / Box / Ellipse / Freehand: filled from around. Alt + click = copy from that spot.
 - Crop v:      AUTO CROP (key A), Crop (C), Straighten (S, 4 corners), Crop to circle / oval / rounded box / the selection shape, Rotate left / right.
                AUTO CROP finds the paper / ID card / book page / newspaper in the photo (on a plain, wooden, cloth or busy background),
                cuts it out, removes the camera angle and keeps the real shape of the sheet.  There is ONE auto crop option: it puts
                the 4 corners on the page it found, you can drag a corner if needed, then Enter crops and straightens (Ctrl+Z undoes it).
                A page that has almost the colour of the table (white on light grey, cream on wood ...) is found by its colour step,
                even when the printed text has stronger edges than the page edge.  In Crop / Straighten, the button "Find the page
                automatically" does the same.
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
 T new text box.  Text boxes / placed pictures: with Hand (H) or Move (V) just click one and drag it (Alt + drag = copy, double click = edit the text,
   handles = resize / rotate). In any other tool hold Ctrl and click it. Click an empty place = done; it stays editable any time.
 Ctrl + wheel zoom, Ctrl +/- zoom, Ctrl+0 fit, Ctrl+1 100%     Left / Right previous / next picture
 WHEEL goes to what is under the pointer: over the picture = up / down, Shift + wheel = left / right, Ctrl + wheel = zoom;
 over the thumbnails (bottom) = previous / next picture, one by one; over the side panel = scrolls the panel.
 Ctrl+S save, Ctrl+Shift+S save all edited, Ctrl+C copy picture

LAYERS (own tab: "Layers", right side after Aa): one list with every overlay picture and text box, with a small picture of each.
 DRAG a row up or down to change which one is in front (top of the list = front; the main picture is always at the bottom).
 The tick box shows / hides a layer. Click = select it, double click = edit. Buttons below: Edit, Copy, Remove.
 Keys: Ctrl+Alt+Up / Down (one step), Ctrl+Alt+Shift+Up / Down (to the very top / bottom).
 Picture tab = only add a picture + its background settings. Aa tab = text settings.
 Text boxes are named "Text box 1, 2 ..." (not by their words). Changing color / size / font / shadow in the Aa tab shows on the page at once.
 Overlay pictures and text boxes are drawn on the screen live at their own full sharpness (also when zoomed in), not baked into the
 page's smaller on-screen copy, so you can judge the edit. Save / print always use the full-size picture.
 Every shortcut is listed in Settings > Keyboard shortcuts...

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
  Right-click a thumbnail for the same as a menu, plus: Duplicate (a copy of the picture as it looks now, put right behind it),
  Add as overlay (the picture, as it looks now, is placed as an overlay picture on the picture you are working on) and Print.
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
  Settings > Print program: choose any program (.exe).  Ctrl+P (or Print...) then opens the picture(s) in it at full resolution, with
  all edits, nothing is saved: two or more selected thumbnails = all of them, otherwise only the picture you are working on.
  Image Fixer then goes to the tray and sleeps (old undo pictures go to the disk, memory is given back, economy mode); your pictures
  and edits are kept.  Click the tray icon to continue.  This sleep can be switched off in the same menu.  Without a chosen
  program Ctrl+P opens the normal Windows print window.
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

NEW (round 6): you SEE that everything works
- UPDATE: no text message for every change any more (toast / shortcut chip).  A light flash over the changed area shows that the
  work was done.  Warnings and errors still show text.  To get the old toasts back: feedback.py > SHOW_MESSAGES = True.
- Toast at the bottom of the picture for every action: a ring turns while the work runs, then it closes and a green tick pops up
  (Copied, Moved, Erased, Deleted, Cropped, Rotated, Selected, Saved ...), with the details (how many px, which direction).
- Changed area flashes: a wave around what was moved / copied / selected / painted, a light sweep over what was erased or deleted,
  a soft flash over the whole picture for crop, rotate, undo and redo.
- While dragging a chip follows the mouse: Move +120, +40 px / Copy / Resize 340 x 120 px (120%) / Rotate +15 deg / crop size /
  selection size. Holding Shift or Alt shows what the key does (Add, Take out, Copy, Dead straight).
- Move: the old place stays as a dashed ghost with a thread to the new place; Alt = copy shows a '+' badge and the copy cursor.
- Transform: the mouse cursor changes on every handle (resize arrows, rotate arrow, move, copy).
- Shortcuts show their keys on top of the picture (Ctrl + Z  Undo ...); Undo / Redo say WHAT they undo and how many steps are left.
- Choosing a tool shows its name, a short help line and its letter key. Zoom shows a % badge. Every button gives a soft wave when pressed.
- Dragging files over the window dims it and shows a 'Drop to add' zone.
- Settings > About shows who made the program.

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

TEXT BOX  (Aa tab, or the "T  Text box" button in the top bar)
- Writes Bangla and English text on the page.  A text box is a picture layer: drag = move, corner handles = resize (the font
  size follows), round handle = rotate, Alt + drag or the "Duplicate" button = copy, Delete = remove, double click = edit text,
  and it has opacity, order (up / down), undo, save, print like any placed picture.  The text stays editable.
- Typing: "Normal" uses your own keyboard (Avro Keyboard, Bijoy Unicode, paste ...).  "Phonetic typing" turns English letters into
  Bangla while you type (ami = আমি, bangla = বাংলা, kolom = কলম; o = no sign, O = ো, T D N S R = ট ড ণ ষ ড়, ng = ং, ^ = ঁ, ` = keep apart).
- Fonts: Bangla and English, basic and stylish, whatever this computer has (list is grouped).  "Add a font file..." adds any .ttf /
  .otf / .ttc and remembers it; fonts copied into the "fonts" folder next to ImageFixer.exe (or %APPDATA%\\ImageFixer\\fonts) load at every start.
- Bijoy (ANSI) fonts such as SutonnyMJ work: text typed with a Bijoy keyboard (Avwg evsjvq) is shown as it is; Unicode Bangla is converted
  for the font automatically.  "Bijoy code -> Unicode" turns pasted Bijoy text into real Bangla.  Conjuncts that are not in the built-in
  table are written with a visible hasant (&).
- Look: size (pt, as on A4), Bold / Italic / Underline / Strike, UPPERCASE / lowercase / Small caps / Capitalize, Left / Center / Right /
  Justify, text color, opacity, letter spacing, line spacing, box width (wrapping), outline, shadow and a highlight box.  The last look
  you used is used for the next new text box.

TEXT BOX  (Aa tab, or the "T  Text box" button in the top bar)
- Writes Bangla and English text on the page.  A text box is a picture layer: drag = move, corner handles = resize (the font
  size follows), round handle = rotate, Alt + drag or the "Duplicate" button = copy, Delete = remove, double click = edit text,
  and it has opacity, order (up / down), undo, save, print like any placed picture.  The text stays editable.
- Typing: "Normal" uses your own keyboard (Avro Keyboard, Bijoy Unicode, paste ...).  "Phonetic typing" turns English letters into
  Bangla while you type (ami = আমি, bangla = বাংলা, kolom = কলম; o = no sign, O = ো, T D N S R = ট ড ণ ষ ড়, ng = ং, ^ = ঁ, ` = keep apart).
- Fonts: Bangla and English, basic and stylish, whatever this computer has (list is grouped).  "Add a font file..." adds any .ttf /
  .otf / .ttc and remembers it; fonts copied into the "fonts" folder next to ImageFixer.exe load at every start.
- Bijoy (ANSI) fonts such as SutonnyMJ work: text typed with a Bijoy keyboard (Avwg evsjvq) is shown as it is; Unicode Bangla is
  converted for the font automatically.  "Bijoy code -> Unicode" turns pasted Bijoy text into real Bangla.  Conjuncts that are not in
  the built-in table are written with a visible hasant (&).
- Bangla + English like MS Word: Bangla letters and English letters get their own font automatically.  With a Bijoy font (SutonnyMJ ...)
  English letters are drawn with an English font (Arial ...), so switching the keyboard to English never gives Bangla shapes.
  "Bijoy (ANSI) font" is OFF for every new text box and for every font you pick; only you turn it on (it is not remembered).
  With it on, only the Bangla letters are converted to Bijoy; English stays English.  Text with no Bangla letter at all is
  treated as typed with a Bijoy keyboard (Avwg = আমি) and shown as it is.
- Typing is smooth: the picture of the text is made a moment (0.2 s) after the last key, not for every key.
- Look: size (pt, as on A4), Bold / Italic / Underline / Strike, UPPERCASE / lowercase / Small caps / Capitalize, Left / Center /
  Right / Justify, text color, opacity, letter spacing, line spacing, box width (wrapping), outline, shadow and a highlight box.
  The last look you used is used for the next new text box.

COMPARE / PREVIEW / UPSCALE  (top right of a picture)
 - Compare (key Q; Shift+Q = slider / side by side): the original and the edited picture together. Click or press again to switch it off.
 - Preview (key F): "real quality".  While you edit, a big picture is shown a little smaller so the program stays fast.  Preview shows the
   full-resolution result - every real pixel, exactly what Save, Copy (Ctrl+C) and drag-out to another program give.  Ctrl+1 = 100%.
   Wheel / drag still move the picture.  Any change of the picture switches Preview off.  (Save / Copy / drag always use the full picture.)
 - Upscale (key U): makes the picture bigger and keeps it sharp.  Every click = one step (Settings > Upscale step: x1.25, x1.5, x2).
   Ctrl+Z undoes it.  Limit: 16000 px on the long side.

KEYS OF EVERY TOOL  (also listed in Settings > Keyboard shortcuts)
 Select: B brush, M box, Shift+M ellipse, L freehand, W color range, Shift+D deselect brush.   Move: V, Shift+V copy, Ctrl+T transform.
 Erase: E brush, Shift+E box, Ctrl+E ellipse, Alt+E freehand.   Crop: A auto crop (find the page + fix corners), C crop, S straighten,
 Shift+C circle, Ctrl+Shift+C oval, Alt+C rounded box, Alt+X selection shape, R / Shift+R turn 90 degrees.
 Others: I eyedropper, P color brush, H hand, T text box, Q compare, Shift+Q compare mode, F real-quality preview, U upscale, Ctrl+B panel.

NEW (less memory): old Undo / Redo steps live on the disk
- Only the newest 2 steps of a picture stay in memory; older ones are written (lossless, raw pixels) to a temporary
  folder and read back by Ctrl+Z / Ctrl+Y.  Saved, copied and printed pictures are exactly as sharp as before.
- The history is now deeper (up to 40 different pictures per page instead of 12) and uses far less RAM.
- If the disk has less than 2 GB free, or writing fails, the program silently goes back to keeping steps in memory.
- Temporary files are removed when a picture is closed, when the program exits, and at the next start after a crash
  (folders named ImageFixer_undo_* in the Windows temp folder).
