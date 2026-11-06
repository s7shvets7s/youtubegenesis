# Installing the studio (once per machine or workspace)

The studio is a small Node + Python toolkit that ships inside this plugin, in the `kit/` folder of the `make-2d-video` skill. Copy it into a writable workspace folder, because the plugin folder may be read-only and the studio needs `node_modules`, projects and renders next to it.

## 1. Locate the kit
- The kit is `kit/` inside the `make-2d-video` skill's base directory. Stage commands (`new-video`, `video-styles`, `render-video`, `video-sound`) reach it at `../make-2d-video/kit/` from their own base directory.
- If the path is unclear, search for it: `find / -path "*make-2d-video/kit/render.mjs" 2>/dev/null | head -1` (Linux/macOS/Cowork VM), or `Get-ChildItem -Recurse -Filter render.mjs -Path $HOME\.claude | Select-Object -First 1` (PowerShell).

## 2. Choose the workspace and copy
- Use the user's connected project folder if there is one, otherwise the current working directory. Create `2d-video/` there.
- If `2d-video/` already exists with `render.mjs` inside, it's already installed. Skip to step 4 (doctor), and never overwrite the user's `projects/` folder.
- Copy all of `kit/` into it: `cp -r "<kit>/." 2d-video/` (bash) or `Copy-Item -Recurse "<kit>\*" 2d-video\` (PowerShell).
- Run `python tools/verify_kit.py` inside `2d-video/`. It must report 29/29. If anything is missing or mismatched, copy those files again.

## 3. Install dependencies
Tell the user, in one short list, what is being installed and why: ffmpeg (video/audio encoding), Node.js packages (renderer, Three.js, fonts), a headless Chromium (draws the frames), and numpy, scipy and faster-whisper (sound synthesis and timestamps).

| Need | Windows | macOS | Linux |
|---|---|---|---|
| ffmpeg + ffprobe | `winget install --id Gyan.FFmpeg -e` (then open a new terminal) | `brew install ffmpeg` | `sudo apt-get install -y ffmpeg` |
| Node.js ≥ 18 | `winget install OpenJS.NodeJS.LTS` | `brew install node` | nodesource or distro package |
| Python ≥ 3.9 | python.org installer | `brew install python` | distro package |
| JS packages | `npm install` (inside 2d-video) | same | same |
| Headless Chromium | `npx playwright install chromium` | same | same (`npx playwright install-deps chromium` if libraries are missing) |
| Python packages | `python -m pip install numpy scipy faster-whisper` | same | add `--break-system-packages` on managed Pythons |

## 4. Check and self-test
1. `python tools/doctor.py`. Use `python3` where `python` isn't Python 3, and `py -3` on some Windows setups. Repeat until every line is OK. `python tools/doctor.py --install` runs the npm, playwright and pip fixes automatically.
2. Self-test: `node render.mjs --project examples/reaction-time --sheet --scale 0.3`. It should write `examples/reaction-time/build/sheets/sheet_01.png` with 14 finished frames (café scenes, giant faces, a 3D orbit, blueprint charts, a title card) and print no errors. Open the PNG to confirm, then delete `examples/reaction-time/build/`.
3. Whisper downloads its model the first time it runs (~75 MB for `base`, ~500 MB for `small`). That needs internet access to huggingface.co.

## Notes
- On Linux servers and the Cowork VM there's no GPU. Chromium uses SwiftShader automatically (`--gl swiftshader` is the Linux default), so everything works, just slower.
- Disk: ~600 MB for node_modules + Chromium, plus renders (a 10-minute 1080p video is ~150–300 MB).
- Updating: when the plugin is updated, copy the new `kit/` over the existing install. It never touches `projects/`, so the user's videos are safe. Custom styles added to `engine/styles.js` would need re-adding, so check first.
