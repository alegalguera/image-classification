# Photo Sorter

**Sorts research photos into folders automatically, on your own computer.**

## What it is

Photo Sorter is a free app for research teams that take lots of photos, for example on a coral reef project.
You tell it which categories you use, and it files your photos into the right folders.

## What it does

- **Sorts** the photos in a folder into your categories: coral, sponge, diver, people, or whatever you choose.
- **Files and renames** them neatly: `Coral/coral_001.jpg`, `Coral/coral_002.jpg`, and so on.
- **Asks you when it isn't sure**, instead of guessing.
- **Learns** from example photos, and from every photo you file.
- **Can undo** every move.

## How it works

```text
📁 your photo folder   →   ▶ Sort   →   👀 you check   →   ✓ File   →   📁 Coral/coral_001.jpg
```

1. You choose the folder that has your photos, and list your categories in plain words, for example
   *Sponge: sponge fragments on ropes underwater*.
2. You press **Sort**. An image-recognition model (free, made by Google) looks at each photo and compares it with
   your words and with example photos. It runs on your computer: your photos are never uploaded.
3. You check the suggestions and press **File**. The photos move into category folders inside that same folder.
4. Later, add new photos to the folder and press **Sort** again. Photos already filed stay in their category folders.

**Your photos can be in any folder on your computer.** If your team keeps them in Google Drive, install
[Google Drive for Desktop](https://www.google.com/drive/download/) (free, from Google). Your Drive folders then show
up on your computer like any other folder, and Photo Sorter sorts them right there, for everyone who shares them.
The category list is saved inside the photo folder too, so the whole team sorts the same way.

## Install

You need a **Mac with an Apple chip** (M1 or newer) or a **Windows 10 or 11 PC**, about 3 GB of free space and an
internet connection. Nothing else: the installer brings its own copy of Python and everything it needs, keeps it all
inside your user folder, and doesn't need an administrator password.

### Mac

1. Open **Terminal**: press ⌘ + Space, type *Terminal* and press Enter.
2. Copy this line, paste it into Terminal and press Enter:

   ```bash
   curl -LsSf https://raw.githubusercontent.com/alegalguera/image-classification/main/install.sh | bash
   ```

3. It shows what it will install and where, then asks before it starts. Press Enter and wait for
   **✓ Photo Sorter is installed**. The first time takes a few minutes.

### Windows

1. Open **PowerShell**: press the Windows key, type *PowerShell* and press Enter. You don't need *Run as administrator*.
2. Copy this line, paste it into PowerShell (right-click pastes) and press Enter:

   ```powershell
   powershell -ExecutionPolicy ByPass -NoProfile -c "irm https://raw.githubusercontent.com/alegalguera/image-classification/main/install.ps1 | iex"
   ```

3. It shows what it will install and where, then asks before it starts. Windows may ask once for permission to add
   a free Microsoft component that Photo Sorter needs.

Either way, you now have a **Photo Sorter** icon on your Desktop. To update, paste the same line again.
You can read exactly what the installers do: [install.sh](install.sh) (Mac) and [install.ps1](install.ps1) (Windows).

## How to use it

### The first time

1. Double-click **Photo Sorter** on your Desktop. It opens in your web browser. A small window opens too: keep it
   open while you work, because closing it stops Photo Sorter.
2. **Choose the folder that has your photos.** With Google Drive for Desktop, your Drive folders are listed too.
3. **Choose your categories:** use the folders already there, pick a ready-made list (such as
   *Coral reef restoration*), or start empty.

### Every day

1. Add new photos to your photo folder.
2. Press **Sort**. Nothing moves yet; Photo Sorter only suggests.
3. Check the piles. Photos it isn't sure about wait in **Needs review**: press **▶ Decide**, then a number key to
   choose each photo's category (← → to skip, Ctrl/⌘ + Z to undo).
4. Press **File** to move the photos into their category folders.

Changed your mind? Press **Undo**, or open **History** and use **Move to…** on any photo.

### Make it smarter

- On the **Categories** page, describe each category in a few words.
- Give each category 5 to 20 example photos: click 🖼 next to it, then drop photos in or search the web.
- Photos you file count as examples too, so it gets better as you use it.
- Some things can't be described in words, like specific people or species that look alike. Those need example photos.

## Problems?

| What you see | What to do |
| --- | --- |
| The installer shows **✗** | It says where the details are. Send that file, or a screenshot, to whoever helps you with Photo Sorter. |
| Windows says a script "is not digitally signed" | Don't run a downloaded file: paste the line from **Install** into PowerShell instead. |
| "This Mac has an Intel processor" | Photo Sorter needs a Mac with an Apple chip (M1 or newer) for now. |
| The page doesn't open, or says it can't connect | Double-click **Photo Sorter** again. |
| Almost everything lands in **Needs review** | Describe your categories and add example photos (see *Make it smarter*). |

## Remove it

Your photos, their folders and your category lists are never touched. Paste this on a Mac:

```bash
curl -LsSf https://raw.githubusercontent.com/alegalguera/image-classification/main/uninstall.sh | bash
```

or this in PowerShell on Windows:

```powershell
powershell -ExecutionPolicy ByPass -NoProfile -c "irm https://raw.githubusercontent.com/alegalguera/image-classification/main/uninstall.ps1 | iex"
```

## More

- [Advanced guide](docs/ADVANCED.md): pasting Google Drive links instead of using Drive for Desktop, hosting on
  Google Cloud, the command line, settings, and how it works in detail.
- License: [MIT](LICENSE). Free to use, change and share.
