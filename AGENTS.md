# Agent Guidelines for Yap

## UI/UX Standards (Strict Anti-Slop Policy)

When making any frontend or design modifications to this repository:
1. **Adhere to the `brand-ui-ux` skill**: Located globally at `~/.gemini/config/skills/brand-ui-ux/SKILL.md`.
2. **Zero AI Slop**:
   - Never introduce rainbow / purple-to-cyan text gradients.
   - Never add emoji clutter (`✨`, `🚀`, `⚡`, `🎙️`, `📋`) to buttons, headers, or cards.
   - Never add neon glow halos (`box-shadow: 0 0 20px ...`) or heavy blur glassmorphism.
   - Keep 1px structural borders, deep obsidian/zinc background palettes, high information density, and clean monospace metadata badges.
3. **Architecture Integrity**:
   - Backend runs on FastAPI via `whisper_server.py`.
   - Audio recordings are saved in `recordings/` as 16 kHz Mono WAV.
   - Database operations stay in `db.py` (SQLite WAL mode).
   - CLI dictation stays fast in `dictate.py`.

## Approved shared brand

For this requested redesign, `../BRAND_UI_UX.md` supersedes the older obsidian-only
palette above and the skill's palette/font defaults. Yap's signature accent is **ink blue**
(`#284B9F` light / `#A9BCF2` dark). Preserve warm light/dark surfaces, System/Light/Dark
selection, Geist UI, Geist Mono metadata, and Newsreader reading text with fallbacks.

## Local identity / deployment blocker

Client IDs are deliberately caller-supplied for local use. Anyone sending `admin` can
access all clients' data. **Before deploying or exposing Yap beyond localhost, replace
this with real authentication and server-side authorization.** Also review filesystem
path transcription, CORS, shutdown, and the shared transcript backup. Do not describe
client ID filtering as authentication or deployment-ready tenant isolation.
