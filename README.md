# Renns Android Kitchen

**Open, edit, and rebuild Android firmware on Windows.**

Created by **rennsproject!**

Renns Android Kitchen (RAK) is a desktop app for working with Android ROMs and partition images. You can inspect OTA payloads and super images, edit EROFS/EXT partitions, unpack boot images, work on APKs, and package partition images for flashing.

RAK can write supported changes directly into an EROFS image, without unpacking the entire partition first.

## Edit EROFS without unpacking it

Right-click a non-sparse EROFS `.img` and choose **Mount**. RAK shows its contents as a virtual folder in the project tree, including file permissions and SELinux contexts. It can read compressed EROFS contents too. Opening the mount does not extract a second copy of the partition.

From that mounted view you can:

- Open and save existing text files recognized by the editor.
- Change file permissions and supported SELinux contexts.
- Extract a selected file or folder.
- Delete files and folders from the image.
- Check space usage and compact an EROFS image after deletions.

For example, open an existing `.prop` file inside `system.img`, change a value, and save it. RAK writes that change back to `system.img`.

If an edited file no longer fits in its original space, RAK can append data and update the image's references, so the `.img` may grow. Non-sparse EXT images also support direct editing through the mounted view.

## Supported workflows

| Area | What you can do |
| --- | --- |
| **EROFS and EXT2/3/4** | Browse an image in place, extract it to a folder, edit files and metadata, and rebuild an edited filesystem as EROFS or EXT4. |
| **Android sparse images** | Browse a supported filesystem inside a sparse image, convert sparse images to raw, or convert raw images to sparse. |
| **`super.img`** | Inspect logical partitions and extract the partitions you need. |
| **`payload.bin`** | View the partition list and extract selected OTA payload partitions. |
| **Brotli and block OTA files** | Decode `.img.br`, `.new.dat`, and `.new.dat.br` to an image or folder; export rebuilt filesystems as raw images, Brotli images, `new.dat`, or `new.dat.br`. |
| **`boot.img` and `vendor_boot.img`** | Unpack the image, work on its extracted contents and ramdisk, then repack it. |
| **APK files** | Decompile, rebuild, sign, or remove signatures when the required tools are available. |
| **Archives** | Browse and extract formats such as ZIP, RAR, 7z, TAR, and Zstandard, including files inside supported Unisoc/Spreadtrum `.pac` firmware packages. |
| **Flashable packages** | Assemble a flashable ZIP from one or more partition images. |

RAK identifies supported image types from their contents, so the available actions follow the file it detects. You can also select several items to batch unpack or repack filesystem images, or convert sparse images.

## Inside the workspace

- **File tree and editor.** Create, move, copy, rename, and delete files in an unpacked partition; edit text in the built-in Monaco editor; inspect images, audio, video, archives, and binary data without leaving RAK.
- **Android filesystem metadata.** Edit permissions and SELinux contexts, create symlinks, and set up filesystem metadata for a partition folder. A NEOCAT scan is available for recognized filesystem roots.
- **NeoBranch.** Keep separate sets of changes for an unpacked filesystem and switch between them while retaining the original partition files.
- **Terminal and CLI.** Use the integrated terminal or the packaged `rak` command for repeatable file and image operations. The image commands include listing, reading, extracting, writing, changing permissions and SELinux contexts, deleting, and reporting space usage.
- **ADB and Fastboot tools.** Inspect a connected device, queue partition flashes, manage logical partitions, and reboot into supported modes when the required binaries are installed.
- **Interface.** Switch themes and use RAK in any of its nine supported languages.

## Get started

RAK is built for Windows 10 and 11.

1. Download the Windows release package. Keep the extracted folder together and launch `run.exe`. If the package is an MSIX, install it and start RAK from Windows instead.
2. Create or open a project and add your firmware files, such as `payload.bin`, `super.img`, or partition `.img` files.
3. Right-click a file to see the actions available for its format. Choose **Mount** on a supported image to inspect it in place, or **Unpack Image** to work with an extracted folder.
4. Edit the files you need. Repack an extracted filesystem or build a flashable ZIP when you're done.

Some operations use helper binaries managed by RAK. Install them through the app's **Packages** window when needed; the corresponding actions appear once the tools are available.

### Command line

The packaged `run.exe` also exposes the RAK CLI:

```powershell
.\run.exe --rak-cli help
.\run.exe --rak-cli img ls "C:\ROM\system.img"
.\run.exe --rak-cli img cat "C:\ROM\system.img" system/build.prop
```

The release folder also includes a `rak.bat` launcher. After you open a project, RAK can register `rak` on the user PATH. Application data and managed helper binaries are stored under `%LOCALAPPDATA%\RennsAndroidKitchen`.

## Current limits

- Mounted sparse images are read-only. Convert one to raw before editing its contents directly.
- The mounted editor works with existing, recognized text files. Creating files or folders, renaming them, and copying new files into a mounted image require the unpack/edit/repack workflow for now.
- Some EROFS layouts and features do not support direct writes. An edit that adds blocks can increase image size; on images with an AVB footer, it can remove that footer and its signature. Check the resulting image and its verification requirements before flashing.
- Direct edits change the image you opened. Keep an untouched copy if you need to return to the original.

