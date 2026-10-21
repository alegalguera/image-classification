# Photo Sorter

Sorts a folder of research photos into category folders for you (coral, sponge, diver, people, or whatever you
choose) and gives them tidy names like `coral_001.jpg`. It runs on your own computer for free, learns from
example photos you give it, and every move can be undone.

You need a Mac or a Windows PC, an internet connection for the first install, and about 3 GB of free space.

## 1. Install (once)

### Mac

1. On this page, click the green **Code** button → **Download ZIP**, then double-click the ZIP to unpack it.
2. In the unpacked folder, open **deploy** and double-click **install.command**.
   If your Mac refuses to open it: go to **System Settings → Privacy & Security**, click **Open Anyway**, and
   double-click it again.
3. Wait for **✓ photo-sorter is installed**. The first time takes a few minutes.

### Windows (not tested yet)

The same steps, but right-click **install.ps1** → **Run with PowerShell**.

Either way, you now have a **Photo Sorter** icon on your Desktop. To update later, download again and run the installer again.

## 2. First start

1. Double-click **Photo Sorter**. It opens in your web browser. A small window opens too: keep it open while you
   work; closing it stops Photo Sorter.
2. **Choose your photo folder.** Click through your folders. If you use Google Drive for Desktop, your Drive
   folders are listed too.
3. **Choose where new photos arrive:** an *Inbox* folder (created for you) or the folder itself.
4. **Choose your categories:** use the folders already there, or start empty and add a few words like
   *Coral*, *Sponge*, *Diver*, *People*.

## 3. Sorting photos

1. Put new photos in the inbox.
2. Click **Sort**. Nothing is moved yet; Photo Sorter only suggests.
3. Check the piles. Photos it is unsure about wait in **Needs review**. Click **▶ Decide** and press a number
   key to choose the category for each one (← → to skip, Ctrl/⌘ + Z to undo).
4. Click **File** to move the photos into their folders.

Changed your mind? Click **Undo**, or open **History** and use **Move to…** on any photo.

## 4. Make it smarter

- On the **Categories** page, add a few describing words to each category, for example
  *Sponge: sponge fragments on ropes underwater*.
- Give each category 5 to 20 example photos: click 🖼 next to a category, then drop photos in or search the web.
- Photos you file also count as examples, so it gets better as you use it.
- Words can't describe everything. Specific people, or species that look alike, need example photos.

## Good to know

- **Private:** your photos stay on your computer or in your Drive. The image model runs on your computer.
- **Careful:** photos it is unsure about are never filed without you.
- **Shared folders:** the category list is saved inside the photo folder (`CATEGORIES.md`), so colleagues who
  open the same shared folder use the same categories.
- **Another folder:** click the folder name at the top right of the page.

## Problems?

| What you see | What to do |
| --- | --- |
| The installer shows **✗** | It says where the details are. Send that file, or a screenshot, to whoever helps you with Photo Sorter. |
| The page doesn't open or says it can't connect | Double-click **Photo Sorter** again. |
| Almost everything lands in **Needs review** | Add describing words and example photos to your categories (step 4). |
| You want to paste a Google Drive link instead of using Drive for Desktop | This needs a one-time setup by whoever manages Photo Sorter: see the [advanced guide](docs/ADVANCED.md#4-google-drive-setup-one-time-by-the-admin). |

## For admins and developers

Google Drive links, hosting on Google Cloud, the command line, settings and how it works are in the
[advanced guide](docs/ADVANCED.md).
