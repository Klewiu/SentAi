# Xoaila tutorial videos

This folder contains two customer-facing walkthroughs generated from the real local interface and an isolated demonstration database:

- `xoaila_tutorial_PL.mp4` — Polish interface, embedded Polish captions and background music.
- `xoaila_tutorial_EN.mp4` — English interface, embedded English captions and background music.
- Matching `.srt` files — editable captions for YouTube, Vimeo or another player.
- Matching `_script.md` files — ready narration scripts for review or voice generation.

The example company, email addresses, URLs, Stripe identifiers and subscription are fictional. The videos explain that structured publication improves machine readability but does not guarantee indexing, recommendations or citations by an AI provider.

To regenerate the current caption-and-music versions, install `tools/tutorial_video/requirements.txt`, start the isolated tutorial server, run `tools/tutorial_video/capture.py`, then use:

```powershell
python tools/tutorial_video/render.py all --silent --music media/soundsurfer.mp3
```

This uses `soundsurfer.mp3` as the only audio track. Video encoding uses the FFmpeg binary supplied by `imageio-ffmpeg`.

Piper remains available only for an optional local voice-over build. If one is ever needed, download its voices with:

```powershell
python -m piper.download_voices --download-dir .cache/tutorial_voices pl_PL-gosia-medium en_GB-cori-medium
```

The voice models are needed only while building the videos. The finished MP4 files under `apps/dashboard/static/videos/` are the assets deployed by Django.
