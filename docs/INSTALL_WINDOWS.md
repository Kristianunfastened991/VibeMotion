# Install VibeMotion On Windows

These steps are for a fresh Windows machine after downloading the project from
GitHub.

![Install VibeMotion from GitHub](assets/readme-install-steps.svg)

## Short Version

1. Download ZIP from GitHub.
2. Extract the ZIP.
3. Double-click `Launch-VibeMotion.bat`.
4. Keep the terminal open.
5. Wait for the browser to open.

You do not need Git or VS Code for normal use.

## Download

1. Open the GitHub repository page.
2. Click the green `Code` button.
3. Click `Download ZIP`.
4. Extract the ZIP into a normal folder, for example `Desktop\VibeMotion`.
5. Do not run the app directly from inside the ZIP file.

## First Launch

1. Open the extracted folder.
2. Double-click `Launch-VibeMotion.bat`.
3. Keep the terminal window open.
4. The first launch can take a long time.

On the first launch VibeMotion prepares the local environment:

![First launch checks](assets/readme-setup-checks.svg)

- creates `.env` from `.env.example` if needed;
- finds or installs Python 3.11;
- creates `.venv`;
- installs Python dependencies;
- installs the pinned CUDA PyTorch runtime used by LTX;
- checks FFmpeg and ffprobe and tries to install FFmpeg with `winget` if missing;
- checks or installs Ollama;
- pulls the text and vision Ollama models configured in `.env`;
- downloads/caches the faster-whisper STT model;
- registers the local Figma plugin;
- checks or downloads LTX model files into the local `models/` folder.

The `models/` folder is local runtime data. It is not part of the GitHub
repository.

## What To Do First In The App

![Choose the simplest VibeMotion path](assets/readme-user-paths.svg)

| Goal | First action |
| --- | --- |
| Edit a normal video | Click `Upload video`. |
| Use a Figma design | Run `VibeMotion Export` in Figma, then click `Update from Figma Space`. |
| Animate one image layer with LTX | Select the image layer and open `Animate with LTX 2.3`. |

Figma and LTX are optional. The app can still edit normal videos without them.

## Normal Launch

After setup finishes, run `Launch-VibeMotion.bat` again if the terminal asks you
to restart. On normal launches the app starts faster and opens:

```text
http://127.0.0.1:8010/app/index.html
```

Leave the terminal window open while using VibeMotion. Closing the browser tab
does not stop the local server. To stop the app, close the terminal window or run
`Stop-VibeMotion.bat`.

## Connect Figma

![Connect Figma to VibeMotion](assets/readme-figma-setup.svg)

1. Start VibeMotion with `Launch-VibeMotion.bat`.
2. Open your design file in Figma Desktop.
3. In Figma, open `Plugins` -> `Development` -> `VibeMotion Export`.
4. Keep the plugin server set to `http://127.0.0.1:8010`.
5. Click `Send selection to VibeMotion` or `Send page to VibeMotion`.
6. In VibeMotion, click `Update from Figma Space`.
7. Drag imported frames into the timeline or select layers for motion/LTX.

If the plugin does not appear, close Figma, run `Launch-VibeMotion.bat` again,
reopen Figma, and check `Plugins` -> `Development`. Manual fallback: import
`figma-plugin/manifest.json` through Figma's development plugin menu.

## If Setup Fails

Run `Launch-VibeMotion-Visible.bat` to keep the server window visible and read the
error message.

Common requirements:

- Windows 10/11;
- Python 3.11 or newer;
- NVIDIA GPU and current driver for LTX generation;
- enough free disk space for Python packages and local model weights;
- internet access on the first launch.

If LTX cannot run because CUDA is unavailable or VRAM is too low, the basic
editor, Figma import, timeline, and non-LTX render features can still be used.

## Common Questions

**Can I close the terminal?**
No. Leave it open while using VibeMotion.

**Why is first launch slow?**
It may install packages and download local models. Later launches are faster.

**Do I need Figma?**
No. Figma is only needed for Figma frame/layer import.

**Do I need an NVIDIA GPU?**
Only for LTX generation. Basic editing works without it.
