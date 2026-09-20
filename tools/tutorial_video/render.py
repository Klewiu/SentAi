"""Build narrated, captioned MP4 tutorials from the captured xoaila screens."""

from __future__ import annotations

import argparse
import subprocess
import textwrap
import wave
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from content import SCENES


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts" / "tutorial_videos"
SCREENS = ARTIFACTS / "screens"
WORK = ARTIFACTS / "work"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
NARRATOR = Path(__file__).with_name("narrate.ps1")
PIPER = ROOT / ".venv" / "Scripts" / "piper.exe"
PIPER_VOICES = ROOT / ".cache" / "tutorial_voices"
FONT_REGULAR = Path(r"C:\Windows\Fonts\segoeui.ttf")
FONT_BOLD = Path(r"C:\Windows\Fonts\seguisb.ttf")

WIDTH, HEIGHT = 1920, 1080
NAVY = "#284b63"
TEAL = "#5ca197"
PALE = "#eaf4f2"
INK = "#243746"


def font(size: int, bold: bool = False):
    return ImageFont.truetype(str(FONT_BOLD if bold else FONT_REGULAR), size)


def fit_cover(source: Image.Image, size: tuple[int, int]):
    ratio = max(size[0] / source.width, size[1] / source.height)
    scaled = source.resize((round(source.width * ratio), round(source.height * ratio)), Image.Resampling.LANCZOS)
    left = (scaled.width - size[0]) // 2
    top = (scaled.height - size[1]) // 2
    return scaled.crop((left, top, left + size[0], top + size[1]))


def wrapped_lines(draw, text, used_font, max_width):
    words = text.split()
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textbbox((0, 0), trial, font=used_font)[2] <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def gradient_background():
    image = Image.new("RGB", (WIDTH, HEIGHT), NAVY)
    pixels = image.load()
    left = (40, 75, 99)
    right = (65, 137, 128)
    for x in range(WIDTH):
        t = x / (WIDTH - 1)
        color = tuple(round(a * (1 - t) + b * t) for a, b in zip(left, right))
        for y in range(HEIGHT):
            pixels[x, y] = color
    return image


def make_slide(language, index, screenshot_name, title, narration, output):
    if screenshot_name is None:
        canvas = gradient_background()
        draw = ImageDraw.Draw(canvas, "RGBA")
        draw.ellipse((1400, -240, 2080, 440), fill=(255, 255, 255, 16))
        draw.ellipse((-180, 700, 520, 1400), fill=(92, 161, 151, 70))
        draw.text((150, 120), "xoaila.", font=font(54, True), fill="white")
        draw.rounded_rectangle((150, 235, 410, 280), 22, fill=TEAL)
        label = "PORADNIK WIDEO" if language == "pl" else "VIDEO GUIDE"
        draw.text((178, 243), label, font=font(20, True), fill="white")
        title_lines = wrapped_lines(draw, title, font(72, True), 1480)
        y = 365
        for line in title_lines:
            draw.text((150, y), line, font=font(72, True), fill="white")
            y += 88
        subtitle = "Sprzedaż i instrukcja krok po kroku" if language == "pl" else "Product overview and step-by-step tutorial"
        draw.text((155, y + 30), subtitle, font=font(34), fill="#d8ece8")
        body_y = y + 105
        for line in wrapped_lines(draw, narration, font(27), 1420)[:4]:
            draw.text((155, body_y), line, font=font(27), fill="white")
            body_y += 39
        draw.text((155, 970), "xoaila · AI-ready company data", font=font(22), fill="#d8ece8")
        canvas.save(output)
        return

    source = Image.open(SCREENS / screenshot_name).convert("RGB")
    background = fit_cover(source, (WIDTH, HEIGHT)).filter(ImageFilter.GaussianBlur(22))
    background = ImageEnhance.Brightness(background).enhance(0.64)
    canvas = background.convert("RGBA")
    main = source.resize((1728, 1080), Image.Resampling.LANCZOS)
    canvas.alpha_composite(main.convert("RGBA"), (96, 0))
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle((38, 30, 610, 98), 26, fill=(40, 75, 99, 238), outline=(92, 161, 151, 255), width=2)
    draw.text((68, 45), f"xoaila  ·  {index:02d}", font=font(27, True), fill="white")
    draw.rounded_rectangle((65, 765, 1855, 1045), 26, fill=(25, 48, 64, 235), outline=(124, 193, 181, 220), width=2)
    draw.text((105, 795), title, font=font(39, True), fill="#9fe0d5")
    body_font = font(29)
    lines = wrapped_lines(draw, narration, body_font, 1680)
    y = 855
    for line in lines[:5]:
        draw.text((105, y), line, font=body_font, fill="white")
        y += 39
    canvas.convert("RGB").save(output, quality=94)


def narrate(language, index, text, output):
    text_path = WORK / language / f"voice_{index:02d}.txt"
    text_path.write_text(text, encoding="utf-8")
    model = PIPER_VOICES / ("pl_PL-gosia-medium.onnx" if language == "pl" else "en_GB-cori-medium.onnx")
    subprocess.run(
        [str(PIPER), "--model", str(model), "--input-file", str(text_path),
         "--output-file", str(output), "--length-scale", "1.04", "--sentence-silence", "0.22"],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def wav_duration(path):
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def timestamp(seconds):
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def build(language, silent=False, music=None):
    language_work = WORK / language
    language_work.mkdir(parents=True, exist_ok=True)
    pieces, subtitles, transcript = [], [], []
    elapsed = 0.0
    for index, (screen, title, narration) in enumerate(SCENES[language], 1):
        slide = language_work / f"slide_{index:02d}.jpg"
        voice = language_work / f"voice_{index:02d}.wav"
        video = language_work / f"scene_{index:02d}.mp4"
        make_slide(language, index, screen, title, narration, slide)
        if silent:
            duration = max(6.0, len(narration.split()) / 2.8)
        else:
            if voice.exists():
                voice.unlink()
            narrate(language, index, narration, voice)
            duration = wav_duration(voice) + 0.7
        fade_out = max(duration - 0.35, 0.4)
        audio_input = ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"] if silent else ["-i", str(voice)]
        filter_complex = f"[0:v]fade=t=in:st=0:d=0.3,fade=t=out:st={fade_out:.3f}:d=0.3[v]"
        if not silent:
            filter_complex += ";[1:a]apad=pad_dur=0.7[a]"
        audio_map = "1:a" if silent else "[a]"
        subprocess.run([
            FFMPEG, "-y", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-i", str(slide),
            *audio_input, "-filter_complex", filter_complex, "-map", "[v]", "-map", audio_map,
            "-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(video)
        ], check=True)
        pieces.append(video)
        subtitles.append(f"{index}\n{timestamp(elapsed)} --> {timestamp(elapsed + duration)}\n{title}\n")
        transcript.append(f"## {title}\n\n{narration}\n")
        elapsed += duration

    concat = language_work / "concat.txt"
    concat.write_text("".join(f"file '{piece.as_posix()}'\n" for piece in pieces), encoding="utf-8")
    final = ARTIFACTS / f"xoaila_tutorial_{language.upper()}.mp4"
    assembled = language_work / "assembled.mp4" if music else final
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(assembled)], check=True)
    if music:
        fade_out = max(elapsed - 3.0, 0.0)
        subprocess.run([
            FFMPEG, "-y", "-loglevel", "error", "-i", str(assembled), "-stream_loop", "-1", "-i", str(music),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-af", f"volume=0.45,afade=t=in:st=0:d=2,afade=t=out:st={fade_out:.3f}:d=3,alimiter=limit=0.85:level=false",
            "-t", f"{elapsed:.3f}", "-movflags", "+faststart", str(final)
        ], check=True)
    (ARTIFACTS / f"xoaila_tutorial_{language.upper()}.srt").write_text("\n".join(subtitles), encoding="utf-8-sig")
    (ARTIFACTS / f"xoaila_tutorial_{language.upper()}_script.md").write_text("\n".join(transcript), encoding="utf-8")
    print(f"{final.relative_to(ROOT)} ({elapsed:.1f} seconds)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("language", choices=("pl", "en", "all"), nargs="?", default="all")
    parser.add_argument("--silent", action="store_true", help="Build with a silent audio track and embedded captions")
    parser.add_argument("--music", type=Path, help="Replace the audio track with looped background music")
    args = parser.parse_args()
    for selected in ("pl", "en") if args.language == "all" else (args.language,):
        build(selected, silent=args.silent, music=args.music)
