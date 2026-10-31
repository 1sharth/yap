# Voice Recordings Archive

This directory stores all captured voice audio clips from the `dictate` / `yap` CLI workflow.

- **Audio format**: 16 kHz 16-bit PCM Mono WAV (lossless representation of what Whisper hears).
- **Naming format**: `YYYY-MM-DD_HH-MM-SS.wav`
- **Transcript linkage**: Each entry in `../thinking.txt` links directly to its corresponding audio file here.

## Playing Audio in Obsidian
Obsidian natively supports playing WAV files. You can open any audio file directly, or embed an audio player in any Markdown note:
```markdown
![[recordings/2026-10-31_15-45-00.wav]]
```
