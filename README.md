# Image Scanner

A small Windows app for digitising old photo prints quickly:

- **One key to scan.** Press **Space** (or F5). The scan runs in the background, so you can keep tagging the previous batch while the scanner works.
- **Auto-crop.** Lay several photos on the glass. Each one is found, straightened and saved as its own JPEG.
- **Fast multi-select tagging.** Select photos (click, Shift/Ctrl-click, **N** for the latest scan, Ctrl+A), then press **1–9** to toggle a quick tag or **T** to type a new one.
- **To-do list with a required year.** New photos land in the to-do list. Give them a year (**Y**), then press **D** and they are written to the sorted folder, one subfolder per year.
- **Tags and year go into the files.** Keywords and the date are embedded as standard XMP and EXIF metadata, so PhotoPrism, Windows Explorer and other photo tools pick them up.

![screenshot](docs/screenshot.png)

## Setup (Windows)

1. Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/windows/) (tick "Add python.exe to PATH").
2. Install your scanner's normal Windows driver. If the scanner works in *Windows Fax and Scan*, it works here.
3. Download or clone this repository and double-click **`run.bat`**.
   The first start creates a `.venv` folder and installs the dependencies, which takes a minute.

Or from a terminal:

```bat
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m scanner_app
```

Scans are saved to `Pictures\Scans` by default and finished photos to `Pictures\Scans\Sorted`. Change them with **Scan folder…** and **Sorted folder…** in the toolbar.

## Workflow

1. Put a few photos on the scanner glass with a little space between them.
2. Optionally fill in **Year for new scans** and **Tags for new scans** (for example `1985` and `Album 3`); every photo from the next scans gets them.
3. Press **Space**. While it scans, work on the photos from the previous scan.
4. When the scan finishes, its photos appear in the **To do** list. If nothing else was selected they are selected for you; otherwise press **N**.
5. Press **Y**, type the year and Enter. Add other tags with **1–9** or **T**.
6. Press **D**. The photos are written to `Sorted\<year>\` and leave the to-do list. Photos without a year stay in the list and stay selected so you can press **Y**.
7. Swap the photos on the glass and press **Space** again.

Done photos can still be edited in the **Done** view: changing tags updates the sorted copy, changing the year moves it to the right folder, and **Shift+D** takes it back to the to-do list.

### Keys

| Key | Action |
| --- | --- |
| Space / F5 | Scan |
| 1 – 9 | Toggle quick tag 1–9 on the selected photos (adds to all; if all already have it, removes) |
| T | Type a tag for the selected photos. Enter applies it. Use commas for several. New tags become quick tags. |
| Y | Type the year for the selected photos. `1985`, `85`, or `?` when the year is unknown |
| D | Done: write the selected photos to their year folder |
| Shift+D | Move the selected photos back to the to-do list |
| N | Select the photos from the latest scan |
| Ctrl+A / Esc | Select all / none |
| R / Shift+R | Rotate selected photos right / left |
| Del | Delete selected photos (the raw scan is kept) |
| Enter / double-click | Open in the default image viewer |

Type `untagged` or `no year` in the filter box to find photos that still need them.

## Folders

```
Scans/                   the scan folder (Scan folder… in the toolbar)
  library.json           year, tags and to-do state for every photo
  raw/                   full, uncropped scans (PNG)
  photos/                working copy of every photo
  Sorted/                the sorted folder (Sorted folder… in the toolbar)
    1985/1985_scan_20260926_131500_01.jpg
    1987/...
    Unknown year/...
```

Point **Sorted folder…** at a folder PhotoPrism imports or indexes (for example its `originals` folder) and finished photos show up there already organised by year.

## Metadata (PhotoPrism)

Each sorted JPEG carries:

| Field | Value | Read by |
| --- | --- | --- |
| XMP `dc:subject` | the tags | PhotoPrism keywords, Lightroom, digiKam |
| EXIF `DateTimeOriginal`, XMP `photoshop:DateCreated` | 1 January of the year | PhotoPrism "taken" date (otherwise it would use the scan date) |
| EXIF `XPKeywords` | the tags | Windows Explorer "Tags" column |
| EXIF resolution | the scan dpi | printing at the original size |

Photos with an unknown year get no date.

## Scan progress

The Windows scanning API the app uses (WIA automation) does not report progress during a scan. The progress bar therefore shows an estimate based on how long the previous scan with the same scanner, resolution and colour setting took; the very first scan shows a busy animation. The demo scanner reports real progress.

## Resolution

150, 300, 450, 600 and 1200 dpi are offered. Many scanners don't support 450 dpi directly; the app then scans at the next higher resolution the driver offers (usually 600) and scales the result down to 450.

## Auto-crop tips

- Leave a finger's width of space between photos and away from the glass edges.
- Prints with a white border on a white scanner lid can lose that border. For those, put a sheet of black paper over the photos before closing the lid; the border is then kept.
- If a scan has no detectable photos, the whole scan is kept as one image. Untick **Auto-crop** to always keep the full scan.

## Other options

```bat
.venv\Scripts\python -m scanner_app --demo                 rem fake scanner, to try the app
.venv\Scripts\python -m scanner_app --from-folder D:\old   rem auto-crop existing scans in a folder
.venv\Scripts\python -m scanner_app --output D:\Scans --sorted D:\Photos
```

## Development

The code is plain Python:

- `scanner_app/scanner.py`: WIA scanning (Windows Image Acquisition via pywin32) plus demo and folder stand-ins
- `scanner_app/cropping.py`: finding and straightening photos with OpenCV
- `scanner_app/library.py`: saving photos, year, tags and the sorted folder
- `scanner_app/metadata.py`: writing XMP/EXIF metadata into JPEGs
- `scanner_app/gui.py`: the Qt window and hotkeys

Tests run on any OS (the GUI tests use Qt's offscreen platform):

```
pip install -r requirements-dev.txt
pytest
```

To build a standalone `.exe`: `pip install pyinstaller` then `pyinstaller --windowed --name ImageScanner -p . scanner_app/__main__.py`.
