# Photo Sorter

**Sorts research photos into folders automatically, on your own computer.**

## What it is

Photo Sorter is a free app for research teams that take lots of photos, for example on a coral reef project.
You tell it which categories you use, and it files new photos into the right folders for you.

## What it does

- **Sorts** each new photo into one of your categories: coral, sponge, diver, people, or whatever you choose.
- **Files and renames** photos neatly: `Coral/coral_001.jpg`, `Coral/coral_002.jpg`, and so on.
- **Asks you when it isn't sure**, instead of guessing.
- **Learns** from example photos, and from every photo you file.
- **Can undo** every move.
- **Works with Google Drive** folders your team shares.

## How it works

```text
📥 Inbox   →   ▶ Sort   →   👀 you check   →   ✓ File   →   📁 Coral/coral_001.jpg
```

1. You choose a photo folder and list your categories in plain words, for example
   *Sponge: sponge fragments on ropes underwater*.
2. You put new photos in its **Inbox**.
3. When you press **Sort**, an image-recognition model (free, made by Google) looks at each photo and compares
   it with your words and with example photos. It runs on your computer: photos are never uploaded.
4. You check the suggestions and press **File**. The photos move into their category folders.

The category list is saved inside the photo folder, so colleagues who open the same shared folder sort the same way.

## Install

You need a Mac or a Windows PC, an internet connection for the first install, and about 3 GB of free space.

### Mac

1. Click the green **Code** button at the top of this page → **Download ZIP**, then double-click the ZIP to unpack it.
2. Open the unpacked folder → **deploy** → double-click **install.command**.
   If your Mac refuses to open it: go to **System Settings → Privacy & Security**, click **Open Anyway**, and
   double-click it again.
3. The installer shows what it will put on your computer and where, then asks before it starts. Press Enter and
   wait for **✓ Photo Sorter is installed**. The first time takes a few minutes.

### Windows (not tested yet)

The same steps, but right-click **install.ps1** → **Run with PowerShell**.

You now have a **Photo Sorter** icon on your Desktop. To update, run the installer again. To remove everything,
double-click **uninstall.command** (Windows: **uninstall.ps1**); your photos are never touched.

## How to use it

### The first time

1. Double-click **Photo Sorter** on your Desktop. It opens in your web browser. A small window opens too: keep it
   open while you work, because closing it stops Photo Sorter.
2. **Choose your photo folder.** If you use Google Drive for Desktop, your Drive folders are listed too.
3. **Choose where new photos arrive:** an *Inbox* folder (created for you) or the folder itself.
4. **Choose your categories:** use the folders already there, pick a ready-made list (such as
   *Coral reef restoration*), or start empty.

### Every day

1. Put new photos in the Inbox.
2. Press **Sort**. Nothing moves yet; Photo Sorter only suggests.
3. Check the piles. Photos it isn't sure about wait in **Needs review**: press **▶ Decide**, then a number key to
   choose each photo's category (← → to skip, Ctrl/⌘ + Z to undo).
4. Press **File** to move the photos into their folders.

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
| The page doesn't open, or says it can't connect | Double-click **Photo Sorter** again. |
| Almost everything lands in **Needs review** | Describe your categories and add example photos (see *Make it smarter*). |
| You want to paste a Google Drive link instead of using Drive for Desktop | This needs a one-time setup by whoever manages Photo Sorter: see the [advanced guide](docs/ADVANCED.md#4-google-drive-setup-one-time-by-the-admin). |

## More

- [Advanced guide](docs/ADVANCED.md): Google Drive links, hosting on Google Cloud, the command line, settings,
  and how it works in detail.
- License: [MIT](LICENSE). Free to use, change and share.
