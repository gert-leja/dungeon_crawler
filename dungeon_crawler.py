import pygame
import math
import random
import sys
import json
import os
import threading
import urllib.request
import webbrowser

try:
    import cv2
    import numpy as np
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False

pygame.mixer.pre_init(44100, -16, 2, 512)   # macOS needs explicit buffer size
pygame.init()
pygame.mixer.init()

# ── Platform detection ────────────────────────────────────────────────────────
IS_MACOS   = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
_PLATFORM_TAG = "macos" if IS_MACOS else "win"   # used by version-check zip filter

# ── Asset path helper ─────────────────────────────────────────────────────────
#
# When running as a plain .py script:
#   ASSET_DIR  = folder containing dungeon_crawler.py   (read-only assets live here)
#   DATA_DIR   = same folder                             (leaderboard.json goes here)
#
# When frozen by PyInstaller (--onefile or --onedir):
#   sys._MEIPASS = temp unpacked bundle  → read-only assets live here
#   macOS  : ~/Library/Application Support/DungeonCrawler  → user data lives here
#   Windows: folder next to the .exe                       → user data lives here

def _get_asset_dir():
    """Directory that holds the bundled read-only assets (sounds, images, video)."""
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

def _get_data_dir():
    """Writable directory for user data that persists across runs."""
    if getattr(sys, "frozen", False):
        if IS_MACOS:
            base = os.path.expanduser("~/Library/Application Support/DungeonCrawler")
            os.makedirs(base, exist_ok=True)
            return base
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

ASSET_DIR = _get_asset_dir()
DATA_DIR  = _get_data_dir()

def asset(filename):
    """Return full path to a read-only asset inside the assets/ folder."""
    return os.path.join(ASSET_DIR, "assets", filename)

def _make_font(size, bold=False):
    """Return a monospace SysFont that works on Windows and macOS.
    Tries Consolas (Windows) → Menlo (macOS) → Courier New → DejaVu Sans Mono."""
    return pygame.font.SysFont(
        "consolas, menlo, couriernew, dejavusansmono", size, bold=bold)

# ── Music manager ─────────────────────────────────────────────────────────────

class Music:
    """
    Centralised music controller. All tracks live in the same folder as the
    script. Missing files are silently skipped so the game still runs without
    any audio assets.

    Expected filenames (OGG recommended, MP3/WAV also accepted):
        menu.ogg            – main menu / username screen
        shop.ogg            – weapon shop overlay
        battle.ogg          – generic wave music
        corruption_wave.ogg – plays during corruption (elite) waves
        boss_malachar.ogg   – Malachar the Undying
        boss_vexara.ogg     – Vexara the Hex-Weaver
        boss_gorvak.ogg     – Gorvak Ironhide
        boss_seraphix.ogg   – Seraphix the Fallen
        boss_nyxoth.ogg     – Nyxoth the Abyssal

    Place icon file in "assets" folder:
        icon.png            – window icon (32×32 or 64×64 recommended)
    """

    # Map boss names (lowercase, no spaces) to track filenames
    BOSS_TRACKS = {
        "malachar the undying":  "boss_malachar",
        "vexara the hex-weaver": "boss_vexara",
        "gorvak ironhide":       "boss_gorvak",
        "seraphix the fallen":   "boss_seraphix",
        "nyxoth the abyssal":    "boss_nyxoth",
    }

    # Candidates tried in order when loading a track
    EXTENSIONS = [".ogg", ".mp3", ".wav"]

    # Per-track gain multipliers — adjust these to level-match all music.
    # 1.0 = full user volume; 0.7 = 30% quieter than full; etc.
    # Raise a value if a track is too quiet, lower it if too loud.
    TRACK_GAIN = {
        "menu":             0.85,
        "shop":             0.80,
        "battle":           0.90,
        "corruption_wave":  0.85,
        "boss_malachar":    0.85,
        "boss_vexara":      0.85,
        "boss_gorvak":      0.85,
        "boss_seraphix":    0.85,
        "boss_nyxoth":      0.85,
    }

    def __init__(self):
        self._current          = None   # track key currently loaded/playing
        self._resume_offset_s  = 0.0   # file-position offset from the last seek
        self.enabled  = pygame.mixer.get_init() is not None
        self.volume   = 0.5
        if self.enabled:
            pygame.mixer.music.set_volume(self.volume)

    def _find(self, base):
        """Return the first existing file matching base + any extension, or None."""
        for ext in self.EXTENSIONS:
            path = asset(base + ext)
            if os.path.isfile(path):
                return path
        return None

    def _effective_volume(self, key=None):
        """Return the actual pygame volume for the given track key (or current track)."""
        k    = key if key is not None else self._current
        gain = self.TRACK_GAIN.get(k, 1.0) if k else 1.0
        return max(0.0, min(1.0, self.volume * gain))

    def play(self, key, loops=-1, fadein=800):
        """Play a track by key (e.g. 'menu', 'battle', 'boss_malachar').
        Does nothing if already playing the same track or file not found."""
        if not self.enabled or key == self._current:
            return
        path = self._find(key)
        if path is None:
            if self._current is not None:
                pygame.mixer.music.stop()
                self._current = None
            return
        try:
            pygame.mixer.music.fadeout(400)
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(self._effective_volume(key))
            pygame.mixer.music.play(loops, fade_ms=fadein)
            pygame.mixer.music.set_endevent(pygame.USEREVENT + 1)
            self._current         = key
            self._resume_offset_s = 0.0
        except pygame.error as e:
            print(f"[Music] Could not play '{path}': {e}")

    def on_track_end(self):
        """Call this when pygame.USEREVENT+1 fires (track looped / ended).
        Resets the accumulated position so the next pause_resume seeks from 0."""
        self._resume_offset_s = 0.0

    def play_boss(self, boss_name):
        """Look up the boss-specific track and play it."""
        key = self.BOSS_TRACKS.get(boss_name.lower())
        if key:
            self.play(key)
        else:
            self.play("battle")

    def stop(self, fadeout=600):
        if not self.enabled: return
        pygame.mixer.music.fadeout(fadeout)
        self._current = None

    def pause_resume(self):
        """Snapshot the current track key and true file position so
        unpause_resume() can reload it and seek back to exactly this point.
        Call this BEFORE loading any other track."""
        if not self.enabled:
            return
        self._paused_key = self._current
        elapsed = max(0.0, pygame.mixer.music.get_pos() / 1000.0)
        self._paused_pos_s = self._resume_offset_s + elapsed

    def unpause_resume(self):
        """Reload the track saved by pause_resume() and seek to the saved position.
        Falls back to playing from the start if seeking is not supported."""
        if not self.enabled:
            return
        key   = getattr(self, "_paused_key",  None)
        pos_s = getattr(self, "_paused_pos_s", 0.0)
        self._paused_key   = None
        self._paused_pos_s = 0.0
        if not key:
            return
        path = self._find(key)
        if path is None:
            return
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(self._effective_volume(key))
            try:
                pygame.mixer.music.play(-1, start=pos_s)
                self._current         = key
                self._resume_offset_s = pos_s
            except pygame.error:
                pygame.mixer.music.play(-1)
                self._current         = key
                self._resume_offset_s = 0.0
        except pygame.error as e:
            print(f"[Music] Could not resume '{path}': {e}")

    def set_volume(self, vol):
        self.volume = max(0.0, min(1.0, vol))
        if self.enabled:
            # Apply per-track gain so the current track's level stays correct
            pygame.mixer.music.set_volume(self._effective_volume())


# Global music instance — created once, shared everywhere
MUSIC = Music()

# ── Sound Effects manager ─────────────────────────────────────────────────────

class SFX:
    """
    Centralised sound-effects controller using pygame.mixer.Sound channels.
    All files live in the same folder as the script.
    Missing files are silently skipped.

    Expected filenames (WAV recommended, OGG/MP3 also work):
        sfx_shoot.wav                   – player fires a bullet
        sfx_shoot_rapid.wav             – rapid-fire variant (Storm Pistol)
        sfx_player_hit.wav              – player takes damage
        sfx_player_dash.wav             – player dashes
        sfx_enemy_death.wav             – regular enemy dies
        sfx_boss_death.wav              – boss dies
        sfx_boss_spawn.wav              – boss materialises (thud at intro frame 30)
        sfx_goblin_dash.wav             – goblin starts a dash
        sfx_orc_spin.wav                – orc rage spin triggers
        sfx_mage_blink.wav              – mage teleports
        sfx_dragon_orb.wav              – dragon drops a fire orb
        sfx_slime_spit.wav              – slime spits a blob
        sfx_level_up.wav                – player levels up
        sfx_shoot_corrupted_homing.wav  – Corrupted Seeker fires a homing bolt
        sfx_achievement.wav             – achievement unlocked toast
    """

    EXTENSIONS = [".wav", ".ogg", ".mp3"]

    def __init__(self):
        self.enabled = pygame.mixer.get_init() is not None
        self.volume  = 0.6
        self._cache  = {}   # key -> pygame.mixer.Sound or None

    def _load(self, key):
        if key in self._cache:
            return self._cache[key]
        for ext in self.EXTENSIONS:
            path = asset(f"sfx_{key}{ext}")
            if os.path.isfile(path):
                try:
                    snd = pygame.mixer.Sound(path)
                    snd.set_volume(self.volume)
                    self._cache[key] = snd
                    return snd
                except pygame.error as e:
                    print(f"[SFX] Could not load sfx_{key}: {e}")
        self._cache[key] = None   # remember miss so we don't retry every frame
        return None

    def play(self, key, volume_scale=1.0):
        """Play a sound effect by key. volume_scale lets callers quieten one-offs."""
        if not self.enabled:
            return
        snd = self._load(key)
        if snd:
            snd.set_volume(min(1.0, self.volume * volume_scale))
            snd.play()

    def set_volume(self, vol):
        self.volume = max(0.0, min(1.0, vol))
        # Update all already-loaded sounds
        for snd in self._cache.values():
            if snd:
                snd.set_volume(self.volume)


# Global SFX instance
SOUNDS = SFX()

# ── Menu background video ─────────────────────────────────────────────────────

class MenuVideo:
    """
    Plays a looping video as the menu background using OpenCV.
    Falls back gracefully if cv2 is not installed or the file is missing.

    Place the video file next to the script:
        menu_bg.mp4   (or .avi / .webm / .mov)

    Install OpenCV if needed:
        pip install opencv-python
    """
    EXTENSIONS = [".mp4", ".avi", ".webm", ".mov"]

    def __init__(self):
        self._cap    = None
        self._surf   = None
        self._loaded = False
        if not _CV2_AVAILABLE:
            print("[MenuVideo] cv2 not installed — video background disabled. "
                  "Run: pip install opencv-python")
            return
        for ext in self.EXTENSIONS:
            path = asset(f"menu_bg{ext}")
            if os.path.isfile(path):
                cap = cv2.VideoCapture(path)
                if cap.isOpened():
                    self._cap    = cap
                    self._loaded = True
                    print(f"[MenuVideo] Loaded {path}")
                    break
                cap.release()
        if not self._loaded:
            print("[MenuVideo] No menu_bg video found — place menu_bg.mp4 next to the script.")

    def next_frame(self, target_w, target_h):
        """Return a pygame Surface of the next video frame, or None if unavailable."""
        if not self._loaded or self._cap is None:
            return None
        ok, frame = self._cap.read()
        if not ok:
            # Loop: rewind to start
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._cap.read()
            if not ok:
                return None
        # cv2 gives BGR — convert to RGB
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        # Scale to screen size
        fh, fw = frame.shape[:2]
        if fw != target_w or fh != target_h:
            frame = cv2.resize(frame, (target_w, target_h),
                               interpolation=cv2.INTER_LINEAR)
        # Convert numpy array → pygame Surface
        surf = pygame.surfarray.make_surface(frame.swapaxes(0, 1))
        return surf

    def release(self):
        if self._cap:
            self._cap.release()
            self._cap = None

# ── Leaderboard ───────────────────────────────────────────────────────────────

LEADERBOARD_FILE    = os.path.join(DATA_DIR, "leaderboard.json")
LEADERBOARD_HC_FILE = os.path.join(DATA_DIR, "leaderboard_hardcore.json")
LEADERBOARD_MAX  = 10

class Leaderboard:
    def __init__(self, hardcore=False):
        self.hardcore = hardcore
        self._file    = LEADERBOARD_HC_FILE if hardcore else LEADERBOARD_FILE
        self.entries  = self._load()

    def _load(self):
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                data = json.load(f)
            entries = []
            for e in data:
                if all(k in e for k in ("name", "wave", "level", "kills", "bosses")):
                    entries.append(e)
            return entries[:LEADERBOARD_MAX]
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self):
        try:
            with open(self._file, "w", encoding="utf-8") as f:
                json.dump(self.entries, f, indent=2)
        except Exception as e:
            print(f"[Leaderboard] Could not save: {e}")

    def submit(self, name, wave, level, kills, bosses):
        """Insert a new run. Returns the rank (1-based) if it made top 10, else None."""
        entry = {"name": name, "wave": wave, "level": level,
                 "kills": kills, "bosses": bosses}
        self.entries.append(entry)
        self.entries.sort(key=lambda e: (e["wave"], e["level"], e["kills"]), reverse=True)
        self.entries = self.entries[:LEADERBOARD_MAX]
        self._save()
        try:
            return self.entries.index(entry) + 1
        except ValueError:
            return None

    def draw(self, surf, fonts, x, y, w, highlight_name=None, t=0):
        """Draw the leaderboard table at (x,y) with width w. Returns height used."""
        col = (255, 100, 40) if self.hardcore else YELLOW
        if self.hardcore:
            # Animated flaming skulls flanking the title text
            title_text = fonts["large"].render("HARDCORE TOP 10", True, col)
            skull_size = 14
            skull_w    = skull_size * 2 + 4
            gap        = 8
            total_w    = skull_w + gap + title_text.get_width() + gap + skull_w
            tx         = x + w // 2 - total_w // 2
            ty         = y + title_text.get_height() // 2
            draw_flaming_skull(surf, tx + skull_size, ty, t, size=skull_size)
            surf.blit(title_text, (tx + skull_w + gap, y))
            draw_flaming_skull(surf, tx + skull_w + gap + title_text.get_width() + gap + skull_size,
                               ty, t + 15, size=skull_size)
        else:
            title = fonts["large"].render("TOP 10 LEADERBOARD", True, col)
            surf.blit(title, (x + w // 2 - title.get_width() // 2, y))
        y += 38

        pygame.draw.line(surf, (70, 70, 90), (x, y), (x + w, y), 1)
        col_x = [x + 4, x + 32, x + 170, x + 270, x + 340, x + 410]
        headers = ["#", "Name", "Wave", "Level", "Kills", "Bosses"]
        for cx, hdr in zip(col_x, headers):
            hs = fonts["tiny"].render(hdr, True, GRAY)
            surf.blit(hs, (cx, y + 4))
        y += 22
        pygame.draw.line(surf, (70, 70, 90), (x, y), (x + w, y), 1)
        y += 4

        if not self.entries:
            empty = fonts["small"].render("No runs yet - be the first!", True, GRAY)
            surf.blit(empty, (x + w // 2 - empty.get_width() // 2, y + 12))
            return y + 40

        for i, e in enumerate(self.entries):
            row_y = y + i * 26
            is_highlight = (highlight_name and e["name"] == highlight_name)
            if is_highlight:
                hl = pygame.Surface((w, 24), pygame.SRCALPHA)
                hl.fill((255, 215, 0, 35))
                surf.blit(hl, (x, row_y))

            rank_col = YELLOW if i == 0 else (CYAN if i == 1 else (ORANGE if i == 2 else GRAY))
            text_col = YELLOW if is_highlight else WHITE

            rank_s  = fonts["small"].render(f"{i+1}", True, rank_col)
            name_s  = fonts["small"].render(e["name"][:14], True, text_col)
            wave_s  = fonts["small"].render(str(e["wave"]), True, text_col)
            level_s = fonts["small"].render(str(e["level"]), True, text_col)
            kills_s = fonts["small"].render(str(e["kills"]), True, text_col)
            boss_s  = fonts["small"].render(str(e["bosses"]), True, text_col)

            for cx, surf_s in zip(col_x, [rank_s, name_s, wave_s, level_s, kills_s, boss_s]):
                surf.blit(surf_s, (cx, row_y + 2))

            if i < len(self.entries) - 1:
                pygame.draw.line(surf, (40, 40, 56),
                                 (x, row_y + 25), (x + w, row_y + 25), 1)

        return y + len(self.entries) * 26 + 8

# ── Token Wallet ──────────────────────────────────────────────────────────────

TOKEN_FILE = os.path.join(DATA_DIR, "tokens.json")

class TokenWallet:
    """Persistent token + cosmetic + title storage."""

    def __init__(self):
        data          = self._load_raw()
        self.total    = max(0, int(data.get("tokens", 0)))
        saved_owned   = data.get("owned_cosmetics", ["default"])
        self.owned_cosmetics  = set(saved_owned) | {"default"}
        self.active_cosmetic  = data.get("active_cosmetic", "default")
        self.owned_titles     = set(data.get("owned_titles", ["none"])) | {"none"}
        self.active_title     = data.get("active_title", "none")
        self.cases            = max(0, int(data.get("cases", 0)))
        self.seraphix_kills   = max(0, int(data.get("seraphix_kills", 0)))
        self.nyxoth_kills     = max(0, int(data.get("nyxoth_kills", 0)))
        self.vexara_kills     = max(0, int(data.get("vexara_kills", 0)))
        self.malachar_kills   = max(0, int(data.get("malachar_kills", 0)))
        self.gorvak_kills     = max(0, int(data.get("gorvak_kills", 0)))

    def sync_to_player(self, player):
        """Push cosmetic + title data onto a Player after COSMETICS/TITLES are defined."""
        valid_ids = {c["id"] for c in COSMETICS} | {"default"}
        self.owned_cosmetics = self.owned_cosmetics & valid_ids | {"default"}
        if self.active_cosmetic not in self.owned_cosmetics:
            self.active_cosmetic = "default"
        player.owned_cosmetics = set(self.owned_cosmetics)
        player.active_cosmetic = self.active_cosmetic
        valid_title_ids = {t["id"] for t in TITLES}
        self.owned_titles = (self.owned_titles & valid_title_ids) | {"none"}
        if self.active_title not in self.owned_titles:
            self.active_title = "none"
        player.owned_titles  = set(self.owned_titles)
        player.active_title  = self.active_title

    def _load_raw(self):
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self):
        try:
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "tokens":          self.total,
                    "owned_cosmetics": list(self.owned_cosmetics),
                    "active_cosmetic": self.active_cosmetic,
                    "owned_titles":    list(self.owned_titles),
                    "active_title":    self.active_title,
                    "cases":           self.cases,
                    "seraphix_kills":  self.seraphix_kills,
                    "nyxoth_kills":    self.nyxoth_kills,
                    "vexara_kills":    self.vexara_kills,
                    "malachar_kills":  self.malachar_kills,
                    "gorvak_kills":    self.gorvak_kills,
                }, f, indent=2)
        except Exception as e:
            print(f"[Tokens] Could not save: {e}")

    def add_case(self, amount=1):
        self.cases += amount
        self._save()

    def spend_case(self):
        """Spend one case. Returns True if successful."""
        if self.cases > 0:
            self.cases -= 1
            self._save()
            return True
        return False

    def earn(self, amount=1):
        self.total += amount
        self._save()

    def spend(self, amount):
        if self.total >= amount:
            self.total -= amount
            self._save()
            return True
        return False

    def unlock_cosmetic(self, cosm_id):
        self.owned_cosmetics.add(cosm_id)
        self._save()

    def equip_cosmetic(self, cosm_id):
        if cosm_id in self.owned_cosmetics:
            self.active_cosmetic = cosm_id
            self._save()

    def unlock_title(self, title_id):
        self.owned_titles.add(title_id)
        self._save()

    def equip_title(self, title_id):
        if title_id in self.owned_titles:
            self.active_title = title_id
            self._save()

    def record_seraphix_kill(self):
        self.seraphix_kills += 1; self._save()

    def record_nyxoth_kill(self):
        self.nyxoth_kills += 1; self._save()

    def record_vexara_kill(self):
        self.vexara_kills += 1; self._save()

    def record_malachar_kill(self):
        self.malachar_kills += 1; self._save()

    def record_gorvak_kill(self):
        self.gorvak_kills += 1; self._save()


# Global wallet — loaded once, shared by all runs in a session
TOKENS = TokenWallet()

# ── Game settings (persisted next to exe) ─────────────────────────────────────

SETTINGS_FILE  = os.path.join(DATA_DIR, "settings.json")
FIRST_RUN_FILE = os.path.join(DATA_DIR, "first_run.json")
CHECKPOINT_FILE = os.path.join(DATA_DIR, "checkpoint.json")   # legacy, unused now
PROFILE_FILE    = os.path.join(DATA_DIR, "profile.json")
NUM_SAVE_SLOTS  = 5

def _slot_path(slot):
    """Return the file path for save slot 1-5."""
    return os.path.join(DATA_DIR, f"save_slot_{slot}.json")

class GameSettings:
    """Persists quality, volume, display settings across launches."""

    def __init__(self):
        data = self._load()
        self.quality            = data.get("quality",            "high")
        self.music_volume       = float(data.get("music_volume",  0.5))
        self.sounds_volume      = float(data.get("sounds_volume", 0.6))
        self.player_health_bar  = bool(data.get("player_health_bar", False))
        self.fullscreen         = bool(data.get("fullscreen", False))

    def _load(self):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "quality":           self.quality,
                    "music_volume":      MUSIC.volume,
                    "sounds_volume":     SOUNDS.volume,
                    "player_health_bar": self.player_health_bar,
                    "fullscreen":        self.fullscreen,
                }, f, indent=2)
        except Exception as e:
            print(f"[Settings] Could not save: {e}")

    @property
    def low(self):
        return self.quality == "low"


GAME_SETTINGS = GameSettings()
MUSIC.set_volume(GAME_SETTINGS.music_volume)
SOUNDS.set_volume(GAME_SETTINGS.sounds_volume)

# ── Constants ────────────────────────────────────────────────────────────────
SW, SH = 1280, 720
FPS = 60
WHITE  = (255,255,255); BLACK  = (0,0,0);     RED    = (220, 50, 50)
GREEN  = (50, 200, 80);  BLUE   = (50, 120, 220); YELLOW = (255, 215, 0)
ORANGE = (255, 140, 0);  PURPLE = (160, 50, 200); CYAN   = (0, 200, 220)
GRAY   = (100, 100, 110); DARK  = (18, 18, 28);   PANEL  = (28, 28, 42)
WAVE_BREAK_SECS = 10
GAME_VERSION    = "45.0b10"

# ── Achievement definitions ──────────────────────────────────────────────────
# cat: "bosses"|"levels"|"waves"|"kills"|"cosmetics"|"weapons"|"hardcore"|"meta"
ACHIEVEMENTS = [
    # Boss kills
    {"id":"kill_malachar",   "name":"The Unkillable Killed","desc":"Defeat Malachar the Undying",              "cat":"bosses",   "tokens":1},
    {"id":"kill_vexara",     "name":"Hex Broken",           "desc":"Defeat Vexara the Hex-Weaver",             "cat":"bosses",   "tokens":1},
    {"id":"kill_gorvak",     "name":"Iron Shattered",       "desc":"Defeat Gorvak Ironhide",                   "cat":"bosses",   "tokens":1},
    {"id":"kill_seraphix",   "name":"Angel Fallen",         "desc":"Defeat Seraphix the Fallen",               "cat":"bosses",   "tokens":1},
    {"id":"kill_nyxoth",     "name":"Abyss Conquered",      "desc":"Defeat Nyxoth the Abyssal",                "cat":"bosses",   "tokens":1},
    {"id":"kill_all_bosses", "name":"Boss of Bosses",       "desc":"Defeat all 5 bosses in a single run",      "cat":"bosses",   "tokens":3},
    # Levels
    {"id":"level_10", "name":"Rising Fighter",  "desc":"Reach level 10",                        "cat":"levels","tokens":1},
    {"id":"level_25", "name":"Seasoned Warrior", "desc":"Reach level 25",                        "cat":"levels","tokens":1},
    {"id":"level_35", "name":"Battle-Hardened",  "desc":"Reach level 35",                        "cat":"levels","tokens":2},
    {"id":"level_50", "name":"Veteran Slayer",   "desc":"Reach level 50",                        "cat":"levels","tokens":2},
    {"id":"level_75", "name":"Dungeon Legend",   "desc":"Reach level 75",                        "cat":"levels","tokens":3},
    {"id":"level_85", "name":"Apex Predator",    "desc":"Reach level 85",                        "cat":"levels","tokens":3},
    {"id":"level_95", "name":"Near Untouchable", "desc":"Reach level 95",                        "cat":"levels","tokens":4},
    {"id":"level_99", "name":"Maximum Power",    "desc":"Reach the maximum level — 99",          "cat":"levels","tokens":5},
    # Wave milestones (cleared on boss wave)
    {"id":"wave_10",  "name":"First Reckoning",   "desc":"Clear wave 10",   "cat":"waves","tokens":1},
    {"id":"wave_20",  "name":"Relentless",         "desc":"Clear wave 20",   "cat":"waves","tokens":1},
    {"id":"wave_30",  "name":"Into the Deep",      "desc":"Clear wave 30",   "cat":"waves","tokens":1},
    {"id":"wave_40",  "name":"No Rest",            "desc":"Clear wave 40",   "cat":"waves","tokens":2},
    {"id":"wave_50",  "name":"Halfway to Madness", "desc":"Clear wave 50",   "cat":"waves","tokens":2},
    {"id":"wave_60",  "name":"Dungeon Conqueror",  "desc":"Clear wave 60",   "cat":"waves","tokens":2},
    {"id":"wave_70",  "name":"Unyielding",         "desc":"Clear wave 70",   "cat":"waves","tokens":3},
    {"id":"wave_80",  "name":"Siege Breaker",      "desc":"Clear wave 80",   "cat":"waves","tokens":3},
    {"id":"wave_90",  "name":"Doom Incarnate",     "desc":"Clear wave 90",   "cat":"waves","tokens":4},
    {"id":"wave_100", "name":"Endless Slaughter",  "desc":"Clear wave 100",  "cat":"waves","tokens":5},
    # Enemy kills (cumulative across all runs)
    {"id":"kills_500",    "name":"First Blood",       "desc":"Kill 500 enemies total",       "cat":"kills","tokens":1},
    {"id":"kills_1000",   "name":"Bloodthirsty",      "desc":"Kill 1,000 enemies total",     "cat":"kills","tokens":1},
    {"id":"kills_5000",   "name":"Mass Executioner",  "desc":"Kill 5,000 enemies total",     "cat":"kills","tokens":2},
    {"id":"kills_10000",  "name":"Serial Slayer",     "desc":"Kill 10,000 enemies total",    "cat":"kills","tokens":2},
    {"id":"kills_50000",  "name":"Unstoppable Force", "desc":"Kill 50,000 enemies total",    "cat":"kills","tokens":3},
    {"id":"kills_100000", "name":"Slayer Master",     "desc":"Kill 100,000 enemies — true mastery","cat":"kills","tokens":5},
    # Cosmetics
    {"id":"cosm_wings",     "name":"Wings of the Fallen","desc":"Unlock the Seraph Wings cosmetic",  "cat":"cosmetics","tokens":2},
    {"id":"cosm_blackhole", "name":"Event Horizon",      "desc":"Unlock the Black Hole cosmetic",    "cat":"cosmetics","tokens":2},
    {"id":"cosm_hexweaver", "name":"Chaos Theory",       "desc":"Unlock the Hex Weaver cosmetic",    "cat":"cosmetics","tokens":2},
    {"id":"cosm_lavalord",  "name":"Born of Fire",       "desc":"Unlock the Lava Lord cosmetic",     "cat":"cosmetics","tokens":2},
    {"id":"cosm_ironhide",  "name":"Iron Legacy",        "desc":"Unlock the Ironhide cosmetic",      "cat":"cosmetics","tokens":2},
    {"id":"cosm_all",       "name":"Collector",          "desc":"Unlock every cosmetic",             "cat":"cosmetics","tokens":5},
    # Weapons
    {"id":"weapon_master",  "name":"Arsenal Complete",   "desc":"Own all weapons in a single run",   "cat":"weapons","tokens":3},
    # Gold collected (cumulative across all runs)
    {"id":"gold_50000",   "name":"Coin Hoarder",      "desc":"Collect 50,000 gold total",       "cat":"gold","tokens":1},
    {"id":"gold_100000",  "name":"Gold Rush",          "desc":"Collect 100,000 gold total",      "cat":"gold","tokens":2},
    {"id":"gold_250000",  "name":"Treasure Hunter",    "desc":"Collect 250,000 gold total",      "cat":"gold","tokens":2},
    {"id":"gold_500000",  "name":"Vault Breaker",      "desc":"Collect 500,000 gold total",      "cat":"gold","tokens":3},
    {"id":"gold_1000000", "name":"The Gilded Reaper",  "desc":"Collect 1,000,000 gold total — legendary wealth", "cat":"gold","tokens":5},
    # Hardcore
    {"id":"hc_kill_malachar",   "name":"[HC] The Unkillable Killed","desc":"Defeat Malachar in Hardcore",            "cat":"hardcore","tokens":2},
    {"id":"hc_kill_vexara",     "name":"[HC] Hex Broken",           "desc":"Defeat Vexara in Hardcore",              "cat":"hardcore","tokens":2},
    {"id":"hc_kill_gorvak",     "name":"[HC] Iron Shattered",       "desc":"Defeat Gorvak in Hardcore",              "cat":"hardcore","tokens":2},
    {"id":"hc_kill_seraphix",   "name":"[HC] Angel Fallen",         "desc":"Defeat Seraphix in Hardcore",            "cat":"hardcore","tokens":2},
    {"id":"hc_kill_nyxoth",     "name":"[HC] Abyss Conquered",      "desc":"Defeat Nyxoth in Hardcore",              "cat":"hardcore","tokens":2},
    {"id":"hc_kill_all_bosses", "name":"[HC] Boss of Bosses",       "desc":"Defeat all 5 bosses in a Hardcore run",  "cat":"hardcore","tokens":5},
    {"id":"hc_wave_10",  "name":"[HC] First Reckoning",   "desc":"Clear wave 10 in Hardcore",  "cat":"hardcore","tokens":2},
    {"id":"hc_wave_20",  "name":"[HC] Relentless",        "desc":"Clear wave 20 in Hardcore",  "cat":"hardcore","tokens":2},
    {"id":"hc_wave_30",  "name":"[HC] Into the Deep",     "desc":"Clear wave 30 in Hardcore",  "cat":"hardcore","tokens":2},
    {"id":"hc_wave_40",  "name":"[HC] No Rest",           "desc":"Clear wave 40 in Hardcore",  "cat":"hardcore","tokens":3},
    {"id":"hc_wave_50",  "name":"[HC] Halfway to Madness","desc":"Clear wave 50 in Hardcore",  "cat":"hardcore","tokens":3},
    {"id":"hc_kills_500",    "name":"[HC] First Blood",       "desc":"Kill 500 enemies in Hardcore",   "cat":"hardcore","tokens":2},
    {"id":"hc_kills_1000",   "name":"[HC] Bloodthirsty",      "desc":"Kill 1,000 enemies in Hardcore", "cat":"hardcore","tokens":2},
    {"id":"hc_kills_5000",   "name":"[HC] Mass Executioner",  "desc":"Kill 5,000 enemies in Hardcore", "cat":"hardcore","tokens":3},
    {"id":"hc_weapon_master","name":"[HC] Arsenal Complete",  "desc":"Own all weapons in a Hardcore run","cat":"hardcore","tokens":4},
    {"id":"hc_survivor",     "name":"Hardcore Survivor",      "desc":"Complete any Hardcore run (any wave)","cat":"hardcore","tokens":3},
    # Meta
    {"id":"all_achievements","name":"True Legend",
     "desc":"Unlock every achievement including all Hardcore ones — grants the True Legend cosmetic",
     "cat":"meta","tokens":0},
]
ACHIEVEMENT_IDS = {a["id"] for a in ACHIEVEMENTS}


class Profile:
    """Global player profile — username, avatar, account XP/level, achievements."""
    XP_PER_LEVEL = 5  # flat, no scaling

    def __init__(self):
        data = self._load()
        self.username           = data.get("username", "")
        self.image_path         = data.get("image_path", "")
        self.account_xp         = max(0, int(data.get("account_xp", 0)))
        self.unlocked           = set(data.get("unlocked_achievements", []))
        self.total_kills        = max(0, int(data.get("total_kills", 0)))
        self.hc_total_kills     = max(0, int(data.get("hc_total_kills", 0)))
        self.total_gold         = max(0, int(data.get("total_gold", 0)))
        self.max_wave_reached   = max(0, int(data.get("max_wave_reached", 0)))
        self.hc_max_wave_reached = max(0, int(data.get("hc_max_wave_reached", 0)))
        self._avatar_surf       = None
        self._avatar_path_cache = None

    @property
    def account_level(self):
        return self.account_xp // self.XP_PER_LEVEL

    def exists(self):
        return bool(self.username.strip())

    def _load(self):
        try:
            with open(PROFILE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save(self):
        try:
            with open(PROFILE_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "username":              self.username,
                    "image_path":            self.image_path,
                    "account_xp":            self.account_xp,
                    "unlocked_achievements": list(self.unlocked),
                    "total_kills":           self.total_kills,
                    "hc_total_kills":        self.hc_total_kills,
                    "total_gold":            self.total_gold,
                    "max_wave_reached":      self.max_wave_reached,
                    "hc_max_wave_reached":   self.hc_max_wave_reached,
                }, f, indent=2)
        except Exception as e:
            print(f"[Profile] Could not save: {e}")

    def unlock(self, ach_id):
        """Unlock an achievement. Awards tokens + 1 account XP (2 for hardcore).
        Returns True if newly unlocked."""
        if ach_id in self.unlocked or ach_id not in ACHIEVEMENT_IDS:
            return False
        self.unlocked.add(ach_id)
        ach    = next(a for a in ACHIEVEMENTS if a["id"] == ach_id)
        tokens = ach.get("tokens", 0)
        if tokens > 0:
            TOKENS.earn(tokens)
        # 1 XP for normal achievements, 2 XP for hardcore achievements
        xp_gain = 2 if ach.get("cat") == "hardcore" else 1
        self.account_xp += xp_gain
        # Grant any cosmetics tied to this achievement
        for cosm in COSMETICS:
            if cosm.get("achievement_unlock") == ach_id:
                TOKENS.unlock_cosmetic(cosm["id"])
                # Sync to any live player via TOKENS (next sync_to_player call will pick it up)
                # Also push directly into owned_cosmetics on the game's player if available
                import __main__
                live_game = getattr(__main__, "_live_game", None)
                if live_game and hasattr(live_game, "player"):
                    live_game.player.owned_cosmetics.add(cosm["id"])
        self.save()
        return True

    def get_progress(self, ach_id):
        """Return (current, maximum) for achievements with numeric progress, or None."""
        # Wave achievements
        WAVE_MAP = {
            "wave_10":10,"wave_20":20,"wave_30":30,"wave_40":40,"wave_50":50,
            "wave_60":60,"wave_70":70,"wave_80":80,"wave_90":90,"wave_100":100,
        }
        if ach_id in WAVE_MAP:
            return (min(self.max_wave_reached, WAVE_MAP[ach_id]), WAVE_MAP[ach_id])
        # HC wave — only show if a HC run has been played (hc_total_kills > 0 or hc_max_wave > 0)
        HC_WAVE_MAP = {
            "hc_wave_10":10,"hc_wave_20":20,"hc_wave_30":30,"hc_wave_40":40,"hc_wave_50":50,
        }
        if ach_id in HC_WAVE_MAP:
            if self.hc_max_wave_reached == 0 and self.hc_total_kills == 0:
                return None   # no HC run played yet — hide progress entirely
            return (min(self.hc_max_wave_reached, HC_WAVE_MAP[ach_id]), HC_WAVE_MAP[ach_id])
        # Kill achievements
        KILL_MAP = {
            "kills_500":500,"kills_1000":1000,"kills_5000":5000,
            "kills_10000":10000,"kills_50000":50000,"kills_100000":100000,
        }
        if ach_id in KILL_MAP:
            return (min(self.total_kills, KILL_MAP[ach_id]), KILL_MAP[ach_id])
        HC_KILL_MAP = {
            "hc_kills_500":500,"hc_kills_1000":1000,"hc_kills_5000":5000,
        }
        if ach_id in HC_KILL_MAP:
            if self.hc_total_kills == 0:
                return None
            return (min(self.hc_total_kills, HC_KILL_MAP[ach_id]), HC_KILL_MAP[ach_id])
        # Gold achievements
        GOLD_MAP = {
            "gold_50000":50000,"gold_100000":100000,"gold_250000":250000,
            "gold_500000":500000,"gold_1000000":1000000,
        }
        if ach_id in GOLD_MAP:
            return (min(self.total_gold, GOLD_MAP[ach_id]), GOLD_MAP[ach_id])
        return None

    def get_avatar(self):
        """Return a cached 64×64 Surface, or None if no image set."""
        if self.image_path and os.path.isfile(self.image_path):
            if self._avatar_path_cache != self.image_path:
                try:
                    raw = pygame.image.load(self.image_path).convert()
                    self._avatar_surf       = pygame.transform.smoothscale(raw, (64, 64))
                    self._avatar_path_cache = self.image_path
                except pygame.error:
                    self._avatar_surf = None
        else:
            self._avatar_surf = None
        return self._avatar_surf

    def check_achievements(self, game):
        """Check all criteria, unlock newly-met achievements.
        Returns list of newly-unlocked achievement ids."""
        p   = game.player
        hc  = game.hardcore
        new = []

        def _try(aid):
            if self.unlock(aid):
                new.append(aid)

        bosses = getattr(game, "_bosses_killed_names", set())
        all5   = {"Malachar the Undying", "Vexara the Hex-Weaver",
                  "Gorvak Ironhide", "Seraphix the Fallen", "Nyxoth the Abyssal"}

        # Boss kills
        if "Malachar the Undying"  in bosses: _try("kill_malachar")
        if "Vexara the Hex-Weaver" in bosses: _try("kill_vexara")
        if "Gorvak Ironhide"       in bosses: _try("kill_gorvak")
        if "Seraphix the Fallen"   in bosses: _try("kill_seraphix")
        if "Nyxoth the Abyssal"    in bosses: _try("kill_nyxoth")
        if bosses >= all5:                    _try("kill_all_bosses")
        if hc:
            if "Malachar the Undying"  in bosses: _try("hc_kill_malachar")
            if "Vexara the Hex-Weaver" in bosses: _try("hc_kill_vexara")
            if "Gorvak Ironhide"       in bosses: _try("hc_kill_gorvak")
            if "Seraphix the Fallen"   in bosses: _try("hc_kill_seraphix")
            if "Nyxoth the Abyssal"    in bosses: _try("hc_kill_nyxoth")
            if bosses >= all5:                    _try("hc_kill_all_bosses")

        # Levels
        for lv, aid in [(10,"level_10"),(25,"level_25"),(35,"level_35"),(50,"level_50"),
                         (75,"level_75"),(85,"level_85"),(95,"level_95"),(99,"level_99")]:
            if p.level >= lv: _try(aid)

        # Waves — update high-water marks
        w = game.wave
        self.max_wave_reached = max(self.max_wave_reached, w)
        if hc:
            self.hc_max_wave_reached = max(self.hc_max_wave_reached, w)
        for wt, aid, hc_aid in [
            (10,"wave_10","hc_wave_10"),(20,"wave_20","hc_wave_20"),
            (30,"wave_30","hc_wave_30"),(40,"wave_40","hc_wave_40"),
            (50,"wave_50","hc_wave_50"),(60,"wave_60",None),
            (70,"wave_70",None),(80,"wave_80",None),(90,"wave_90",None),(100,"wave_100",None),
        ]:
            if self.max_wave_reached >= wt:
                _try(aid)
                if hc and hc_aid: _try(hc_aid)

        # Kills — only add the delta since the last check, never the full run total
        kills_delta = p.kill_count - game._ach_kills_credited
        if kills_delta > 0:
            self.total_kills    += kills_delta
            if hc: self.hc_total_kills += kills_delta
            game._ach_kills_credited = p.kill_count
        for kt, aid in [(500,"kills_500"),(1000,"kills_1000"),(5000,"kills_5000"),
                         (10000,"kills_10000"),(50000,"kills_50000"),(100000,"kills_100000")]:
            if self.total_kills >= kt: _try(aid)
        for kt, aid in [(500,"hc_kills_500"),(1000,"hc_kills_1000"),(5000,"hc_kills_5000")]:
            if self.hc_total_kills >= kt: _try(aid)

        # Gold — only add the delta since the last check
        gold_delta = p.gold - game._ach_gold_credited
        if gold_delta > 0:
            self.total_gold         += gold_delta
            game._ach_gold_credited  = p.gold
        for gt, aid in [(50000,"gold_50000"),(100000,"gold_100000"),(250000,"gold_250000"),
                         (500000,"gold_500000"),(1000000,"gold_1000000")]:
            if self.total_gold >= gt: _try(aid)

        # Cosmetics
        oc = TOKENS.owned_cosmetics
        for cosm_id, aid in [("wings","cosm_wings"),("blackhole","cosm_blackhole"),
                               ("hexweaver","cosm_hexweaver"),("lavalord","cosm_lavalord"),
                               ("ironhide","cosm_ironhide")]:
            if cosm_id in oc: _try(aid)
        if {c["id"] for c in COSMETICS} <= oc: _try("cosm_all")

        # Weapon master
        all_widx = list(range(len(WEAPONS))) + [1000 + i for i in range(len(SPECIAL_WEAPONS))]
        if all(wi in p.owned_weapons for wi in all_widx):
            _try("weapon_master")
            if hc: _try("hc_weapon_master")

        # Hardcore survivor
        if hc: _try("hc_survivor")

        # Meta
        all_except_meta = {a["id"] for a in ACHIEVEMENTS if a["id"] != "all_achievements"}
        if all_except_meta <= self.unlocked: _try("all_achievements")

        # Always save so accumulators (kills, gold, max_wave) persist even when
        # no achievement is unlocked this check.
        self.save()
        return new


PROFILE = Profile()

# ── Online version check ──────────────────────────────────────────────────────
_GH_API_URL  = ""#"https://api.github.com/repos/gert-leja/dungeon_crawler/releases"
_update_info = {}   # filled by background thread: {"version", "url", "notes"}

def _fetch_latest_release():
    print(f"[VersionCheck] Starting check — current version: {GAME_VERSION}")
    print(f"[VersionCheck] Fetching: {_GH_API_URL}")
    try:
        req = urllib.request.Request(
            _GH_API_URL,
            headers={"User-Agent": "DungeonCrawlerVersionCheck/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = resp.status
            print(f"[VersionCheck] HTTP {status} — response received")
            raw  = resp.read().decode()
            data = json.loads(raw)

            # /releases returns a list sorted newest-first — take the first entry
            # which includes pre-releases, unlike /releases/latest which skips them
            if not data:
                print("[VersionCheck] No releases found on this repo.")
                return
            release = data[0]

            tag   = release.get("tag_name", "").lstrip("v")
            notes = release.get("name", "")
            is_pre = release.get("prerelease", False)
            print(f"[VersionCheck] Latest release tag: '{release.get('tag_name', '')}' → stripped: '{tag}'"
                  f"  (pre-release: {is_pre})")
            print(f"[VersionCheck] Release title: '{notes}'")

            assets = release.get("assets", [])
            print(f"[VersionCheck] Assets found: {len(assets)}")
            for a in assets:
                print(f"[VersionCheck]   asset: {a['name']} ({a.get('size', 0)} bytes)")

            # Prefer the asset whose name contains the platform tag ("win" or "macos")
            # before ".zip", e.g. DungeonCrawler_42.0_win.zip / DungeonCrawler_42.0_macos.zip
            def _is_platform_zip(name):
                n = name.lower()
                return n.endswith(".zip") and _PLATFORM_TAG in n

            zip_url = next(
                (a["browser_download_url"] for a in assets if _is_platform_zip(a["name"])),
                # Fall back to any .zip if no platform-specific one exists
                next(
                    (a["browser_download_url"] for a in assets
                     if a["name"].lower().endswith(".zip")),
                    release.get("html_url", "")
                )
            )
            print(f"[VersionCheck] Platform tag: '{_PLATFORM_TAG}'")
            print(f"[VersionCheck] Download URL resolved: {zip_url}")

            _update_info["version"] = tag
            _update_info["url"]     = zip_url
            _update_info["notes"]   = notes
            _update_info["prerelease"] = is_pre

            if tag == GAME_VERSION:
                print(f"[VersionCheck] Up to date — no banner will show.")
            else:
                pre_tag = " (pre-release)" if is_pre else ""
                print(f"[VersionCheck] Update available: '{GAME_VERSION}' → '{tag}'{pre_tag} — banner will show.")

    except urllib.error.HTTPError as e:
        print(f"[VersionCheck] HTTP error {e.code}: {e.reason}")
        if e.code == 404:
            print("[VersionCheck]   → Repo not found or no releases yet. Check YOUR_USERNAME/YOUR_REPO in _GH_API_URL.")
        elif e.code == 403:
            print("[VersionCheck]   → Rate limited or missing User-Agent header.")
    except urllib.error.URLError as e:
        print(f"[VersionCheck] Network error: {e.reason}")
        print("[VersionCheck]   → No internet connection or DNS failure.")
    except json.JSONDecodeError as e:
        print(f"[VersionCheck] Failed to parse response as JSON: {e}")
    except Exception as e:
        print(f"[VersionCheck] Unexpected error: {type(e).__name__}: {e}")

threading.Thread(target=_fetch_latest_release, daemon=True).start()

# ── Patch notes (2 most recent, newest first) ─────────────────────────────────
# Each entry: {"version": str, "date": str, "changes": [("category", "text"), ...]}
# Categories: "added", "changed", "fixed", "removed"
PATCH_NOTES = [
    {
        "version": "45.0",
        "date":    "26-03-2026",
        "changes": [
            ("added",   "Achievements :O"),
            ("added",   "Global profile! WOW!"),
            ("added",   "some other stuff."),
        ],
    },
    {
        "version": "44.0",
        "date":    "26-03-2026",
        "changes": [
            ("added",   "New Hardcore mode, this will delete your save upon death and no HP drops will spawn from enemies."),
            ("added",   "Difficulty options now selectable after picking username."),
            ("added",   "Two different leaderboards, one for normal mode and one for hardcore mode."),
            ("added",   "Checkpoint functionality, the game saves after every wave, and just before a new wave starts, and is loadable from 'Load Game' in the main menu."),
            ("added",   "Volume equaliser to help control any volume spikes in different songs."),            
            ("changed",   "Main Menu has been reworked, buttons are now in a more organised location."),
            ("fixed",   "Settings menu during a paused game not working."),
            ("fixed",   "Hardcore mode visuals not appearing as intended."),
        ],
    },
]
# ── Cosmetics ─────────────────────────────────────────────────────────────────
# Each cosmetic: id (str), name, description, cost (tokens), pattern key
# Patterns are drawn procedurally in Player.draw — no image files needed.
# Add new entries here and handle their pattern key in Player.draw.

COSMETICS = [
    {
        "id":      "default",
        "name":    "Default",
        "desc":    "The classic look",
        "cost":    0,
        "pattern": "default",
        "preview": CYAN,
    },
    {
        "id":      "gold",
        "name":    "Gilded",
        "desc":    "Worth its weight in gold",
        "cost":    1,
        "pattern": "gold",
        "preview": YELLOW,
    },
    {
        "id":      "fire",
        "name":    "Inferno",
        "desc":    "Burns with inner flame",
        "cost":    3,
        "pattern": "fire",
        "preview": (255, 100, 30),
    },
    {
        "id":      "frost",
        "name":    "Frostbite",
        "desc":    "Icy crystalline shell",
        "cost":    3,
        "pattern": "frost",
        "preview": (120, 200, 255),
    },
    {
        "id":      "void",
        "name":    "Void Walker",
        "desc":    "Consumed by darkness",
        "cost":    5,
        "pattern": "void",
        "preview": (100, 0, 180),
    },
    {
        "id":      "storm",
        "name":    "Stormborn",
        "desc":    "Crackling with lightning",
        "cost":    8,
        "pattern": "storm",
        "preview": (180, 220, 255),
    },
    {
        "id":      "wings",
        "name":    "Seraph Wings",
        "desc":    "Wings of a Fallen Angel",
        "cost":    10,
        "pattern": "wings",
        "preview": (220, 200, 255),
        "req_seraphix_kills": 15,
    },
    {
        "id":      "blackhole",
        "name":    "Black Hole",
        "desc":    "There is no light here",
        "cost":    10,
        "pattern": "blackhole",
        "preview": (20, 0, 60),
        "req_nyxoth_kills": 15,
    },
    {
        "id":      "hexweaver",
        "name":    "Hex Weaver",
        "desc":    "Chaos orbits those who dare",
        "cost":    12,
        "pattern": "hexweaver",
        "preview": (140, 0, 200),
        "req_vexara_kills": 15,
    },
    {
        "id":      "lavalord",
        "name":    "Lava Lord",
        "desc":    "Burn your enemies",
        "cost":    12,
        "pattern": "lavalord",
        "preview": (180, 40, 0),
        "req_malachar_kills": 15,
    },
    {
        "id":      "ironhide",
        "name":    "Ironhide",
        "desc":    "Forged from Gorvak's armour",
        "cost":    12,
        "pattern": "ironhide",
        "preview": (75, 88, 100),
        "req_gorvak_kills": 15,
    },
    {
        "id":         "true_legend",
        "name":       "True Legend",
        "desc":       "Awarded to those who unlock every achievement",
        "cost":       0,
        "pattern":    "true_legend",
        "preview":    (255, 80, 200),
        "achievement_unlock": "all_achievements",   # granted by achievement, not bought
    },
]

# ── Title definitions ─────────────────────────────────────────────────────────
TITLES = [
    {"id": "none",           "name": "(No Title)",         "desc": "Bare name, no title",              "cost": 0,  "col": (150, 150, 150)},
    {"id": "adventurer",     "name": "The Adventurer",     "desc": "A classic warrior's title",        "cost": 2,  "col": (100, 200, 255)},
    {"id": "dungeon_walker", "name": "Dungeon Crawler",    "desc": "You know these halls well",        "cost": 3,  "col": (140, 200, 140)},
    {"id": "boss_slayer",    "name": "Boss Slayer",        "desc": "Bosses fear your name",            "cost": 5,  "col": (255, 140, 60)},
    {"id": "undying",        "name": "The Undying",        "desc": "Death has tried and failed",       "cost": 6,  "col": (220, 80,  80)},
    {"id": "shadow",         "name": "Shadow Master",      "desc": "Unseen until it's too late",     "cost": 6,  "col": (120, 80,  200)},
    {"id": "gilded",         "name": "The Gilded",         "desc": "Wealth beyond measure",            "cost": 7,  "col": (255, 215, 0)},
    {"id": "void_touched",   "name": "Void-Touched",       "desc": "Marked by the abyss",              "cost": 8,  "col": (160, 40,  255)},
    {"id": "warlord",        "name": "Warlord",            "desc": "Armies answer your call",          "cost": 10, "col": (255, 80,  80)},
    {"id": "storm_caller",   "name": "Storm Caller",       "desc": "Lightning bends to your will",    "cost": 10, "col": (140, 200, 255)},
    {"id": "ironclad",       "name": "The Ironclad",       "desc": "Unbreakable, unbeatable",          "cost": 12, "col": (160, 180, 200)},
    {"id": "legend",         "name": "Legend",             "desc": "Few have reached this status",    "cost": 15, "col": (255, 215, 0)},
    {"id": "champion",       "name": "Champion of the Deep","desc": "Master of the dungeon",           "cost": 18, "col": (80,  220, 255)},
    {"id": "harbinger",      "name": "Harbinger of Doom",  "desc": "The end comes with you",           "cost": 20, "col": (220, 60,  60)},
]

# ── Case pool ─────────────────────────────────────────────────────────────────
RARITY_COMMON    = ("Common",    (160, 160, 160))
RARITY_UNCOMMON  = ("Uncommon",  (80,  200, 100))
RARITY_RARE      = ("Rare",      (80,  140, 255))
RARITY_EPIC      = ("Epic",      (180, 60,  255))
RARITY_LEGENDARY = ("Legendary", (255, 160, 20))

# ── The Original Collection — case-exclusive cosmetics ───────────────────────
# These cosmetics are NOT in the token shop; only obtainable by opening cases.
CASE_COSMETICS = [
    # ── Common — solid colour variants ──────────────────────────────────────
    {"id":"case_red",       "name":"Crimson",         "desc":"Bold red — a warrior's colour",       "cost":0,"pattern":"case_red",       "preview":(220,  50,  50)},
    {"id":"case_green",     "name":"Forest",           "desc":"Deep forest green",                   "cost":0,"pattern":"case_green",     "preview":( 40, 180,  80)},
    {"id":"case_purple",    "name":"Amethyst",         "desc":"Rich purple glow",                    "cost":0,"pattern":"case_purple",    "preview":(160,  60, 220)},
    {"id":"case_orange",    "name":"Ember",            "desc":"Warm orange ember",                   "cost":0,"pattern":"case_orange",    "preview":(255, 130,  20)},
    {"id":"case_pink",      "name":"Rose",             "desc":"Soft rose pink",                      "cost":0,"pattern":"case_pink",      "preview":(255, 100, 180)},
    # ── Uncommon — simple animated patterns ─────────────────────────────────
    {"id":"case_stripes",   "name":"Striped",          "desc":"Rotating colour stripes",             "cost":0,"pattern":"case_stripes",   "preview":( 80, 200, 255)},
    {"id":"case_pulse",     "name":"Pulse",            "desc":"Pulsing concentric rings",            "cost":0,"pattern":"case_pulse",     "preview":(200,  80, 255)},
    {"id":"case_checker",   "name":"Checkered",        "desc":"Animated chequered pattern",          "cost":0,"pattern":"case_checker",   "preview":(200, 200,  60)},
    # ── Rare — moving patterns ───────────────────────────────────────────────
    {"id":"case_wave",      "name":"Wave Rider",       "desc":"Rippling wave pattern",               "cost":0,"pattern":"case_wave",      "preview":( 40, 180, 255)},
    {"id":"case_spiral",    "name":"Spiral Blaze",     "desc":"Spinning spiral pattern",             "cost":0,"pattern":"case_spiral",    "preview":(255,  80, 160)},
    {"id":"case_plasma",    "name":"Plasma Core",      "desc":"Crackling plasma surface",            "cost":0,"pattern":"case_plasma",    "preview":( 60, 220, 200)},
    # ── Epic — moving patterns + orbiting projectiles ────────────────────────
    {"id":"case_nova",      "name":"Nova Burst",       "desc":"Exploding star pattern with orbs",   "cost":0,"pattern":"case_nova",      "preview":(255, 200,  40)},
    {"id":"case_vortex",    "name":"Vortex",           "desc":"Swirling dark vortex with shards",   "cost":0,"pattern":"case_vortex",    "preview":( 80,  40, 200)},
    {"id":"case_aurora",    "name":"Aurora",           "desc":"Shimmering aurora with particles",    "cost":0,"pattern":"case_aurora",    "preview":( 40, 220, 160)},
    # ── Legendary — pulsing orange with colour-cycling projectiles ───────────
    {"id":"case_infernal",  "name":"Infernal Core",    "desc":"Pulsing orange core with prismatic orbiting projectiles that cycle through the full spectrum", "cost":0,"pattern":"case_infernal","preview":(255, 120,  20)},
]
CASE_COSMETIC_IDS = {c["id"] for c in CASE_COSMETICS}

# Pool used for rolling — one entry per card slot, with rarity + weight
# Weights: Common ~500, Uncommon ~280, Rare ~160, Epic ~50, Legendary ~10
CASE_POOL = [
    # Common
    {"cosm_id":"case_red",      "rarity":RARITY_COMMON,    "weight":110},
    {"cosm_id":"case_green",    "rarity":RARITY_COMMON,    "weight":110},
    {"cosm_id":"case_purple",   "rarity":RARITY_COMMON,    "weight":100},
    {"cosm_id":"case_orange",   "rarity":RARITY_COMMON,    "weight": 95},
    {"cosm_id":"case_pink",     "rarity":RARITY_COMMON,    "weight": 85},
    # Uncommon
    {"cosm_id":"case_stripes",  "rarity":RARITY_UNCOMMON,  "weight":100},
    {"cosm_id":"case_pulse",    "rarity":RARITY_UNCOMMON,  "weight": 95},
    {"cosm_id":"case_checker",  "rarity":RARITY_UNCOMMON,  "weight": 85},
    # Rare
    {"cosm_id":"case_wave",     "rarity":RARITY_RARE,      "weight": 60},
    {"cosm_id":"case_spiral",   "rarity":RARITY_RARE,      "weight": 55},
    {"cosm_id":"case_plasma",   "rarity":RARITY_RARE,      "weight": 45},
    # Epic
    {"cosm_id":"case_nova",     "rarity":RARITY_EPIC,      "weight": 20},
    {"cosm_id":"case_vortex",   "rarity":RARITY_EPIC,      "weight": 18},
    {"cosm_id":"case_aurora",   "rarity":RARITY_EPIC,      "weight": 12},
    # Legendary
    {"cosm_id":"case_infernal", "rarity":RARITY_LEGENDARY, "weight": 10},
]
_CASE_TOTAL_WEIGHT = sum(e["weight"] for e in CASE_POOL)


def roll_case():
    """Weighted random pick from CASE_POOL. Returns the pool entry dict."""
    r = random.randint(0, _CASE_TOTAL_WEIGHT - 1)
    acc = 0
    for entry in CASE_POOL:
        acc += entry["weight"]
        if r < acc:
            return entry
    return CASE_POOL[-1]

# Add, remove, or edit tips freely — they cycle one at a time.
MENU_TIPS = [
    "Bosses drop 1 Token when defeated — spend them in the Token Shop!",
    "If you are struggling, try to get the enemy's health below 0 before your health reaches 0, works every time!",
    "Complete multiple Corruption Waves to unlock Corrupted weapons!",
    "Corrupted enemies drop more gold and XP than regular ones, but are also more difficult.",
    "You get a choice of 3 perk cards every 5 waves, they can make your run easier, if you get lucky!",
    "Avoid staying in one spot for too long, enemies will try to corner you at any chance!",
    "Gorvak Ironhide summons minions, watch your step :)",
    "If the Nail Gun is not doing it anymore, the Weapon Shop sells additional weapons.",
]

# ── Credits ───────────────────────────────────────────────────────────────────
CREDITS = {
    "License": [
        "This game is Licensed under the GNU General Public License v3.0,",
        "please read LICENSE.txt for more information."
    ],
    "Lead Developer": [
        "Gert L.",
    ],
    "Music & SFX": [
        "DISCLAIMER: Some of the music or SFX may have been AI generated.",
        "",
        "Music:",
        "pixabay.com - daub_audo - Shot In The Dark",
        "Bensound.com - Telune (WGVQEPS9APL09KEB) - Nassau",
        "pixabay.com - psychronic - Rhythym of the Deep",
        "John Dungeon - Malachar",
        "John Dungeon - Nyxoth",
        "John Dungeon - Seraphix",
        "John Dungeon - Gorvak",
        "breakingcopyright.com - Silverman Sound Studios - The Medieval Banquet",
        "breakingcopyright.com - Scott Buckley - Legionnaire",
        "",
        "SFX:",
        "pixabay.com - VoiceBosch - The Moses (Laser Cannon)",
        "freesound.org - aust_paul - possiblelazer",
        "freesound.org - zuzek06 - slimejump",
        "freesound.org - qubodup - cloud-poof",
        "freesound.org - theogobbo - pgj-breach",
        "freesound.org - uzbazur - low-frequency-stomp",
        "freesound.org - shinephoenixstormcrow 320655 rhodesmas - level-up-01",
        "freesound.org - marregheriti - shotgun",
        "freesound.org - hadahector - electric-woosh",
        "freesound.org - michael grinnell - laser-shot",
        "freesound.org - qubodup - m16-single-shot-4",
        "freesound.org - nsstudios - laser3",
        "freesound.org - syna max - monster_death_scream",
        "freesound.org - raclure - damage-sound-effect",
        "freesound.org - gprosser - splat",
        "freesound.org - mokasza - fast-whoosh",
        "freesound.org - j1987 - spinning_firework_2",
        "pixabay.com - xg7ssi08yr - laser",
    ],
    "Playtesters": [
        "kayleigh1w1",
        "neptunecat1",
    ],
}

WEAPONS = [
    # fire_interval = frames between shots when holding mouse (at 60fps)
    {
        "name": "Nail Gun",        "damage": 14,  "speed": 0.55, "range": 380,
        "cost": 0,    "req_lvl": 1,  "color": GRAY,
        "proj_size": 4,  "behaviour": "single",   "fire_interval": 12,
        "desc": "Fast reliable single shot",
    },
    {
        "name": "Twin Blaster",    "damage": 18,  "speed": 0.50, "range": 420,
        "cost": 80,   "req_lvl": 3,  "color": CYAN,
        "proj_size": 5,  "behaviour": "twin",     "fire_interval": 14,
        "desc": "Two parallel bolts",
    },
    {
        "name": "Scattershot",    "damage": 28,  "speed": 0.54, "range": 350,
        "cost": 180,  "req_lvl": 5,  "color": ORANGE,
        "proj_size": 5,  "behaviour": "shotgun",  "fire_interval": 60,
        "desc": "5-bullet spread",
    },
    {
        "name": "Plasma Cannon",   "damage": 70,  "speed": 0.44, "range": 520,
        "cost": 320,  "req_lvl": 7,  "color": (100, 80, 255),
        "proj_size": 12, "behaviour": "single",   "fire_interval": 32,
        "desc": "Massive high-damage bolt",
    },
    {
        "name": "Tri-Laser",       "damage": 32,  "speed": 0.54, "range": 480,
        "cost": 500,  "req_lvl": 10, "color": (0, 255, 180),
        "proj_size": 6,  "behaviour": "spread3",  "fire_interval": 16,
        "desc": "Three beams in a fan",
    },
    {
        "name": "Void Orbiter",    "damage": 55,  "speed": 0.50, "range": 750,
        "cost": 750,  "req_lvl": 12, "color": (160, 0, 220),
        "proj_size": 8,  "behaviour": "orbit",    "fire_interval": 120,
        "desc": "Orbs orbit outwards",
    },
    {
        "name": "Storm Pistol",    "damage": 20,  "speed": 0.62, "range": 300,
        "cost": 1000, "req_lvl": 16, "color": (80, 200, 255),
        "proj_size": 4,  "behaviour": "rapid",    "fire_interval": 7,
        "desc": "Rapid-fire auto",
    },
    {
        "name": "Seeker Array",    "damage": 50,  "speed": 0.44, "range": 600,
        "cost": 1300, "req_lvl": 20, "color": (255, 80, 180),
        "proj_size": 7,  "behaviour": "homing",   "fire_interval": 28,
        "desc": "Homing bullets seek enemies",
    },
    {
        "name": "Oblivion Cannon", "damage": 90,  "speed": 0.56, "range": 480,
        "cost": 2000, "req_lvl": 25, "color": YELLOW,
        "proj_size": 10, "behaviour": "penta",    "fire_interval": 24,
        "desc": "5-way spread, pierces enemies",
    },
]

# ── Special / secret weapons (shown on Weapons page 2) ────────────────────────
# unlock_condition: dict with type and value, checked in shop draw/buy logic
SPECIAL_WEAPONS = [
    {
        "name":      "Corrupted Seeker",
        "damage":    85,
        "speed":     0.40,
        "range":     700,
        "cost":      2500,
        "req_lvl":   15,
        "color":     (140, 0, 200),
        "proj_size": 9,
        "behaviour": "corrupted_homing",
        "fire_interval": 40,
        "desc":      "The Corruption is real",
        "unlock_type":  "corruption_waves",
        "unlock_value": 5,
        "unlock_hint":  "Clear 5 Corruption Waves to unlock",
    },
]

ENEMY_TYPES = [
    # Slime — splits into two tiny slimes on death, bounces erratically
    {"name": "Slime",    "base_hp": 35,  "base_dmg": 6,  "base_spd": 2.4,  "gold": 3,  "color": GREEN,         "size": 14, "xp": 12,  "behaviour": "bounce"},
    # Goblin — fast, dashes at player every few seconds
    {"name": "Goblin",   "base_hp": 55,  "base_dmg": 10, "base_spd": 1.8,  "gold": 6,  "color": ORANGE,        "size": 15, "xp": 22,  "behaviour": "dash"},
    # Orc — slow tank that briefly spins and fires 6 projectiles when hurt below 50 %
    {"name": "Orc",      "base_hp": 140, "base_dmg": 18, "base_spd": 1.05, "gold": 12, "color": RED,           "size": 22, "xp": 45,  "behaviour": "tank"},
    # Mage — keeps distance, fires 3-way spread, occasionally blinks sideways
    {"name": "Mage",     "base_hp": 75,  "base_dmg": 22, "base_spd": 0.85, "gold": 16, "color": PURPLE,        "size": 17, "xp": 60,  "behaviour": "mage"},
    # Dragon — slow, fires aimed double-shot + drops a lingering fire orb on tile
    {"name": "Dragon",   "base_hp": 420, "base_dmg": 35, "base_spd": 0.65, "gold": 55, "color": (180, 30, 30), "size": 30, "xp": 200, "behaviour": "dragon"},
]

def elite_wave_chance(player_level):
    """Returns the probability (0-1) that a wave is an elite wave.
    0.5% at level 1, scaling linearly up to 25% at level 25."""
    min_chance = 0.005   # 0.5% at level 1
    max_chance = 0.25    # 25% at level 25
    level_cap  = Player.LEVEL_CAP   # 25
    t = max(0, min(1, (player_level - 1) / (level_cap - 1)))
    return min_chance + t * (max_chance - min_chance)

# ── Elite variant definitions (one per base enemy type, same order) ────────────
# These override colour, size, and stat multipliers. Behaviours are extended
# in Enemy.update via self.elite flag.
ELITE_VARIANTS = [
    # Elite Slime  → "Plague Slime"  — toxic green/black, splits into 3 elite splinters, acid trail
    {"name": "Plague Slime",  "color": (30, 180, 60),   "glow": (0, 80, 0),    "hp_mult": 2.0, "dmg_mult": 1.3, "spd_mult": 1.3, "size_add": 4},
    # Elite Goblin → "Shadow Stalker" — dark purple, double-dashes, leaves shadow traps
    {"name": "Shadow Stalker","color": (80, 20, 140),   "glow": (160, 0, 255), "hp_mult": 1.8, "dmg_mult": 1.4, "spd_mult": 1.5, "size_add": 2},
    # Elite Orc    → "Berserker Orc" — dark red/gold, permanent rage aura, 3-shot cannon
    {"name": "Berserker Orc", "color": (160, 20, 0),    "glow": (255, 120, 0), "hp_mult": 2.2, "dmg_mult": 1.6, "spd_mult": 1.2, "size_add": 6},
    # Elite Mage   → "Void Mage"     — deep blue/white, 5-way spread, rapid blink-fire combos
    {"name": "Void Mage",     "color": (10, 10, 120),   "glow": (100, 180, 255),"hp_mult": 1.7,"dmg_mult": 1.5, "spd_mult": 1.3, "size_add": 2},
    # Elite Dragon → "Inferno Drake" — black/orange, lays 3 fire orbs, fires 4-way spread
    {"name": "Inferno Drake", "color": (20, 10, 10),    "glow": (255, 80, 0),  "hp_mult": 2.5, "dmg_mult": 1.8, "spd_mult": 1.15,"size_add": 8},
]
# hp/dmg/spd are BASE values at level 1. Scale formula applied on top.
BOSS_TYPES = [
    {
        "name":    "Malachar the Undying",
        "title":   "The Unkillable",
        "color":   (180, 20, 20),
        "size":    46,
        "base_hp": 1650,
        "base_dmg": 18,
        "base_spd": 1.1,
        "gold":    400,
        "xp":      600,
        "pattern": "charge",
        "proj_col": (220, 60, 60),
    },
    {
        "name":    "Vexara the Hex-Weaver",
        "title":   "Mistress of Chaos",
        "color":   (140, 0, 200),
        "size":    40,
        "base_hp": 2200,
        "base_dmg": 17,
        "base_spd": 0.9,
        "gold":    380,
        "xp":      580,
        "pattern": "spiral",
        "proj_col": (200, 80, 255),
    },
    {
        "name":    "Gorvak Ironhide",
        "title":   "The Unbreakable",
        "color":   (80, 130, 80),
        "size":    52,
        "base_hp": 2450,
        "base_dmg": 16,
        "base_spd": 0.65,
        "gold":    500,
        "xp":      700,
        "pattern": "burst",
        "proj_col": (120, 200, 120),
    },
    {
        "name":    "Seraphix the Fallen",
        "title":   "Angel of Ruin",
        "color":   (220, 180, 30),
        "size":    44,
        "base_hp": 1400,
        "base_dmg": 16,
        "base_spd": 1.3,
        "gold":    420,
        "xp":      650,
        "pattern": "orbit",
        "proj_col": (255, 220, 60),
    },
    {
        "name":    "Nyxoth the Abyssal",
        "title":   "Devourer of Light",
        "color":   (20, 20, 60),
        "size":    50,
        "base_hp": 2050,
        "base_dmg": 18,
        "base_spd": 0.75,
        "gold":    550,
        "xp":      800,
        "pattern": "homing",
        "proj_col": (60, 60, 180),
    },
]

# ── Perk definitions ─────────────────────────────────────────────────────────
# Each perk: stat key, display label, per-pick bonus, icon, color, description
ALL_PERKS = [
    {"key": "dmg_pct",    "label": "Damage",      "bonus": 0.08, "icon": "+",  "color": ORANGE,       "desc": "+8% bullet damage"},
    {"key": "defense",    "label": "Defense",     "bonus": 0.08, "icon": "D",  "color": BLUE,         "desc": "-8% damage taken"},
    {"key": "speed_pct",  "label": "Speed",       "bonus": 0.06, "icon": ">>", "color": CYAN,         "desc": "+6% move speed"},
    {"key": "hp_regen",   "label": "Lifesteal",    "bonus": 0.1,  "icon": "+",  "color": RED,        "desc": "+0.1 HP per hit on enemy"},
    {"key": "max_hp_pct", "label": "Vitality",    "bonus": 0.10, "icon": "H",  "color": GREEN,          "desc": "+10% max HP"},
    {"key": "gold_pct",   "label": "Greed",       "bonus": 0.15, "icon": "G",  "color": YELLOW,       "desc": "+15% gold drops"},
    {"key": "range_pct",  "label": "Range",       "bonus": 0.10, "icon": "~",  "color": PURPLE,       "desc": "+10% bullet range"},
    {"key": "fire_rate",  "label": "Fire Rate",   "bonus": 0.08, "icon": "*",  "color": (255,100,30), "desc": "+8% fire rate"},
    {"key": "dash",       "label": "Dash Mastery","bonus": 1,    "icon": "Z",  "color": (80,220,255), "desc": "-15% dash cooldown, +1 charge"},
]

# ── helpers ──────────────────────────────────────────────────────────────────

def _hsv_to_rgb(h, s, v):
    """Convert HSV (h=0-360, s=0-1, v=0-1) to an (r,g,b) tuple with 0-255 range."""
    h = h % 360
    c  = v * s
    x  = c * (1 - abs((h / 60) % 2 - 1))
    m  = v - c
    if   h < 60:  r, g, b = c, x, 0
    elif h < 120: r, g, b = x, c, 0
    elif h < 180: r, g, b = 0, c, x
    elif h < 240: r, g, b = 0, x, c
    elif h < 300: r, g, b = x, 0, c
    else:          r, g, b = c, 0, x
    return (int((r + m) * 255), int((g + m) * 255), int((b + m) * 255))


def lerp_color(c1, c2, t):
    return tuple(max(0, min(255, int(c1[i] + (c2[i] - c1[i]) * t))) for i in range(3))

def draw_bar(surf, x, y, w, h, val, maxval, col, bg=(40, 40, 55)):
    pygame.draw.rect(surf, bg, (x, y, w, h), border_radius=4)
    if maxval > 0:
        fill = max(0, int(w * val / maxval))
        if fill > 0:
            pygame.draw.rect(surf, col, (x, y, fill, h), border_radius=4)
    pygame.draw.rect(surf, (80, 80, 100), (x, y, w, h), 1, border_radius=4)


def draw_skull(surf, cx, cy, size=10, col=(200, 195, 180)):
    """Draw a simple static skull (no flames) centred at (cx, cy)."""
    s = size
    shad = tuple(max(0, c - 60) for c in col)
    # Cranium
    pygame.draw.ellipse(surf, shad, (cx - s + 1, cy - int(s * 0.9) + 1, s * 2, int(s * 1.5)))
    pygame.draw.ellipse(surf, col,  (cx - s,     cy - int(s * 0.9),     s * 2, int(s * 1.5)))
    # Jaw
    jw = int(s * 1.2); jh = int(s * 0.55)
    pygame.draw.rect(surf, col, (cx - jw // 2, cy + int(s * 0.3), jw, jh), border_radius=int(s * 0.2))
    # Eye sockets
    for ex in (cx - int(s * 0.38), cx + int(s * 0.38)):
        pygame.draw.ellipse(surf, (25, 20, 15),
                            (ex - int(s * 0.34), cy - int(s * 0.18),
                             int(s * 0.68), int(s * 0.55)))
    # Nose
    pygame.draw.rect(surf, (50, 40, 35),
                     (cx - max(2, int(s * 0.15)), cy + int(s * 0.1),
                      max(3, int(s * 0.30)), max(3, int(s * 0.28))), border_radius=1)
    # Teeth
    tw = max(2, int(s * 0.22)); th = max(2, int(s * 0.25))
    tg = int(s * 0.05)
    ty2 = cy + int(s * 0.35)
    tot = tw * 3 + tg * 2
    for ti in range(3):
        tx2 = cx - tot // 2 + ti * (tw + tg)
        pygame.draw.rect(surf, (180, 175, 160), (tx2, ty2, tw, th), border_radius=1)


def draw_flaming_skull(surf, cx, cy, t, size=10):
    """
    Draw a procedural animated flaming skull centred at (cx, cy).
    t = frame counter (integer).  size controls overall scale.
    Returns the pixel width of the drawn skull (for layout).
    """
    s = size  # shorthand

    # ── Flames (drawn behind skull) ──────────────────────────────────────────
    flame_cols = [
        (255, 60,  0,  180),
        (255, 140, 0,  160),
        (255, 210, 40, 130),
    ]
    # Three flame tongues — left, centre, right
    for fi, (fx_off, fw_scale, fh_scale, phase) in enumerate([
        (-int(s * 0.4), 0.5, 0.9, 0.0),
        (0,             0.7, 1.2, 0.4),
        (int(s * 0.4),  0.5, 0.9, 0.8),
    ]):
        flicker = math.sin(t * 0.18 + phase * math.pi * 2) * 0.18 + 0.82  # 0.64–1.0
        fh = int(s * fh_scale * flicker)
        fw = max(3, int(s * fw_scale))
        fx = cx + fx_off - fw // 2
        fy = cy - int(s * 0.6) - fh  # sit above skull top

        # Draw each tongue as three layers (outer dim → inner bright)
        for li, (r, g, b, a) in enumerate(flame_cols):
            layer_shrink = li * 2
            lfw = max(2, fw - layer_shrink * 2)
            lfh = max(2, fh - layer_shrink)
            lfs = pygame.Surface((lfw + 2, lfh + 2), pygame.SRCALPHA)
            # Teardrop-ish shape: ellipse squished to a point at top
            pygame.draw.ellipse(lfs, (r, g, b, a), (0, lfh // 3, lfw, lfh * 2 // 3))
            pygame.draw.ellipse(lfs, (r, g, b, max(0, a - 40)),
                                (lfw // 4, 0, lfw // 2, lfh * 2 // 3))
            surf.blit(lfs, (fx + layer_shrink, fy + layer_shrink))

    # ── Skull cranium ─────────────────────────────────────────────────────────
    skull_col  = (220, 215, 200)
    shadow_col = (140, 135, 120)
    pygame.draw.ellipse(surf, shadow_col,
                        (cx - s + 2, cy - int(s * 0.9) + 2, s * 2, int(s * 1.5)))
    pygame.draw.ellipse(surf, skull_col,
                        (cx - s, cy - int(s * 0.9), s * 2, int(s * 1.5)))

    # ── Jaw ──────────────────────────────────────────────────────────────────
    jaw_w = int(s * 1.2)
    jaw_h = int(s * 0.55)
    pygame.draw.rect(surf, skull_col,
                     (cx - jaw_w // 2, cy + int(s * 0.3), jaw_w, jaw_h),
                     border_radius=int(s * 0.2))

    # ── Eye sockets (dark hollows) ────────────────────────────────────────────
    eye_y    = cy - int(s * 0.15)
    eye_rx   = int(s * 0.38)
    eye_r    = int(s * 0.32)
    eye_col  = (20, 15, 10)
    # Pulsing glow inside eyes — flickers orange
    glow_a = int(120 + math.sin(t * 0.20) * 80)
    glow_a = max(0, min(255, glow_a))
    for ex in (cx - int(s * 0.38), cx + int(s * 0.38)):
        gs = pygame.Surface((eye_r * 2 + 4, eye_r * 2 + 4), pygame.SRCALPHA)
        pygame.draw.ellipse(gs, (255, 120, 0, glow_a), (0, 0, eye_r * 2 + 4, eye_r * 2 + 4))
        surf.blit(gs, (ex - eye_r - 2, eye_y - eye_r - 2))
        pygame.draw.ellipse(surf, eye_col,
                            (ex - eye_rx, eye_y - eye_r, eye_rx * 2, eye_r * 2))

    # ── Nasal cavity ─────────────────────────────────────────────────────────
    nose_w = max(3, int(s * 0.25))
    nose_h = max(3, int(s * 0.28))
    pygame.draw.rect(surf, (60, 50, 40),
                     (cx - nose_w // 2, cy + int(s * 0.1), nose_w, nose_h),
                     border_radius=2)

    # ── Teeth ────────────────────────────────────────────────────────────────
    tooth_w = max(2, int(s * 0.22))
    tooth_h = max(3, int(s * 0.28))
    tooth_gap = int(s * 0.05)
    tooth_y = cy + int(s * 0.35)
    teeth_total = tooth_w * 3 + tooth_gap * 2
    for ti in range(3):
        tx = cx - teeth_total // 2 + ti * (tooth_w + tooth_gap)
        pygame.draw.rect(surf, (200, 195, 180),
                         (tx, tooth_y, tooth_w, tooth_h), border_radius=2)

    # Return approximate pixel width for layout
    return s * 2 + 4

# ── Gold coin (physical pickup) ───────────────────────────────────────────────

class GoldCoin:
    PICKUP_RADIUS = 40
    BOB_SPEED     = 0.08
    LIFETIME      = 600   # 10 seconds at 60fps

    def __init__(self, x, y, amount):
        self.x = float(x)
        self.y = float(y)
        self.amount  = amount
        self.alive   = True
        self.bob_t   = random.uniform(0, math.pi * 2)
        angle = random.uniform(0, math.pi * 2)
        spd   = random.uniform(1.5, 3.5)
        self.vx       = math.cos(angle) * spd
        self.vy       = math.sin(angle) * spd
        self.friction = 0.88
        self.radius   = 5 if amount < 20 else (7 if amount < 60 else 10)
        self.life_timer = 0   # counts up; auto-collected at LIFETIME

    def update(self, player):
        self.vx *= self.friction
        self.vy *= self.friction
        self.x  += self.vx
        self.y  += self.vy
        self.bob_t += self.BOB_SPEED
        self.life_timer += 1
        dist = math.hypot(self.x - player.x, self.y - player.y)
        if dist < self.PICKUP_RADIUS or self.life_timer >= self.LIFETIME:
            bonus = int(self.amount * player.perk("gold_pct"))
            player.gold += self.amount + bonus
            self.alive = False

    def draw(self, surf, cam):
        bob_y = int(self.y - cam[1] + math.sin(self.bob_t) * 2)
        sx    = int(self.x - cam[0])
        pygame.draw.ellipse(surf, (10, 10, 20),
                            (sx - self.radius, bob_y + self.radius - 2,
                             self.radius * 2, self.radius))
        pygame.draw.circle(surf, YELLOW,       (sx, bob_y), self.radius)
        pygame.draw.circle(surf, (200, 160, 0),(sx, bob_y), self.radius - 1)
        pygame.draw.circle(surf, (255, 235, 80),
                           (sx - self.radius // 3, bob_y - self.radius // 3),
                           max(1, self.radius // 3))
        pygame.draw.circle(surf, (180, 130, 0),(sx, bob_y), self.radius, 1)

# ── HP Orb (health pickup dropped by enemies) ────────────────────────────────

class HpOrb:
    PICKUP_RADIUS = 32
    BOB_SPEED     = 0.10
    LIFETIME      = 600   # 10 seconds at 60fps

    def __init__(self, x, y, amount):
        self.x       = float(x)
        self.y       = float(y)
        self.amount  = amount
        self.alive   = True
        self.bob_t   = random.uniform(0, math.pi * 2)
        angle        = random.uniform(0, math.pi * 2)
        spd          = random.uniform(1.0, 2.5)
        self.vx      = math.cos(angle) * spd
        self.vy      = math.sin(angle) * spd
        self.friction = 0.90
        self.life_timer = 0   # counts up; auto-collected at LIFETIME

    def update(self, player):
        self.vx   *= self.friction
        self.vy   *= self.friction
        self.x    += self.vx
        self.y    += self.vy
        self.bob_t += self.BOB_SPEED
        self.life_timer += 1
        if (math.hypot(self.x - player.x, self.y - player.y) < self.PICKUP_RADIUS
                or self.life_timer >= self.LIFETIME):
            player.hp  = min(player.max_hp, player.hp + self.amount)
            self.alive = False

    def draw(self, surf, cam):
        sx    = int(self.x - cam[0])
        bob_y = int(self.y - cam[1] + math.sin(self.bob_t) * 2.5)
        r = 7
        # Drop shadow
        pygame.draw.ellipse(surf, (10, 10, 20),
                            (sx - r, bob_y + r - 2, r * 2, r))
        # Outer glow ring
        pygame.draw.circle(surf, (120, 240, 120), (sx, bob_y), r + 2, 1)
        # Body
        pygame.draw.circle(surf, (50, 200, 70),  (sx, bob_y), r)
        pygame.draw.circle(surf, (120, 255, 140), (sx, bob_y), r - 2)
        # Bright specular highlight
        pygame.draw.circle(surf, (220, 255, 220),
                           (sx - r // 3, bob_y - r // 3), max(1, r // 3))

# ── Particle ──────────────────────────────────────────────────────────────────

class Particle:
    def __init__(self, x, y, color, vx=None, vy=None):
        self.x = x; self.y = y
        self.color    = color
        self.vx       = vx if vx is not None else random.uniform(-2, 2)
        self.vy       = vy if vy is not None else random.uniform(-3, 0)
        self.life     = random.randint(20, 40)
        self.max_life = self.life
        self.size     = random.randint(2, 5)

    def update(self):
        self.x += self.vx; self.y += self.vy
        self.vy += 0.15
        self.life -= 1

    def draw(self, surf, cam):
        t         = self.life / self.max_life
        alpha_col = lerp_color(self.color, BLACK, 1 - t)
        s         = max(1, int(self.size * t))
        pygame.draw.circle(surf, alpha_col,
                           (int(self.x - cam[0]), int(self.y - cam[1])), s)

# ── Projectile ────────────────────────────────────────────────────────────────

class Projectile:
    # Pixel speed multiplier — higher = snappier bullets
    SPD_SCALE = 22

    def __init__(self, x, y, dx, dy, dmg, spd, rng, col, size, owner="player"):
        self.x = x; self.y = y
        self.dmg      = dmg
        mag           = math.hypot(dx, dy) or 1
        self.vx       = dx / mag * spd * self.SPD_SCALE
        self.vy       = dy / mag * spd * self.SPD_SCALE
        self.col      = col; self.size = size
        self.owner    = owner
        self.dist     = 0
        self.max_dist = rng
        self.alive    = True

    def update(self):
        self.x    += self.vx; self.y += self.vy
        self.dist += math.hypot(self.vx, self.vy)
        if self.dist >= self.max_dist:
            self.alive = False

    def draw(self, surf, cam):
        sx = int(self.x - cam[0]); sy = int(self.y - cam[1])
        # Low quality: skip glow surface allocation — plain streak + tip only
        if GAME_SETTINGS.low:
            pygame.draw.circle(surf, self.col, (sx, sy), self.size)
            return
        # Draw as a streak: a line behind the bullet + bright tip
        speed  = math.hypot(self.vx, self.vy) or 1
        # Tail stretches 2.5× the bullet size behind travel direction
        tail_len = max(self.size * 2, int(self.size * 2.5))
        tx = sx - int(self.vx / speed * tail_len)
        ty = sy - int(self.vy / speed * tail_len)
        # Dim tail
        tail_col = lerp_color(self.col, BLACK, 0.55)
        pygame.draw.line(surf, tail_col, (tx, ty), (sx, sy), max(1, self.size - 2))
        # Bright tip
        pygame.draw.circle(surf, self.col, (sx, sy), self.size)
        # Small glow on tip only
        gs = self.size + 3
        gsurf = pygame.Surface((gs * 2, gs * 2), pygame.SRCALPHA)
        pygame.draw.circle(gsurf, (*self.col, 60), (gs, gs), gs)
        surf.blit(gsurf, (sx - gs, sy - gs))

# ── Player Homing Projectile ──────────────────────────────────────────────────

class PlayerHomingProjectile(Projectile):
    """Seeks the nearest enemy after a short arming delay."""
    def __init__(self, x, y, dx, dy, dmg, spd, rng, col, size, enemies_ref):
        super().__init__(x, y, dx, dy, dmg, spd, rng, col, size, owner="player")
        self.enemies_ref = enemies_ref
        self.turn_rate   = 0.06
        self.arm_delay   = 12   # frames before homing kicks in

    def update(self):
        self.arm_delay = max(0, self.arm_delay - 1)
        if self.arm_delay == 0:
            # Find nearest living enemy — includes boss and Vexara clone
            best_dist = 1e9; target = None
            targets = list(self.enemies_ref)
            targets += [b for b in getattr(self, '_boss_ref', []) if b and b.alive]
            for e in targets:
                if e.alive:
                    d = math.hypot(e.x - self.x, e.y - self.y)
                    if d < best_dist:
                        best_dist = d; target = e
            if target:
                tx = target.x - self.x; ty = target.y - self.y
                mag = math.hypot(tx, ty) or 1
                tx /= mag; ty /= mag
                cmag = math.hypot(self.vx, self.vy) or 1
                cx = self.vx / cmag; cy = self.vy / cmag
                nx = cx + tx * self.turn_rate
                ny = cy + ty * self.turn_rate
                nm = math.hypot(nx, ny) or 1
                self.vx = nx / nm * cmag
                self.vy = ny / nm * cmag
        self.x    += self.vx; self.y += self.vy
        self.dist += math.hypot(self.vx, self.vy)
        if self.dist >= self.max_dist:
            self.alive = False

class CorruptedHomingProjectile(PlayerHomingProjectile):
    """More aggressive homing — locks on faster and has a shorter arm delay.
    Draws with a dark purple/void colour trail."""
    def __init__(self, x, y, dx, dy, dmg, spd, rng, col, size, enemies_ref):
        super().__init__(x, y, dx, dy, dmg, spd, rng, col, size, enemies_ref)
        self.turn_rate = 0.18   # much tighter than 0.06
        self.arm_delay = 6      # arms faster

    def draw(self, surf, cam):
        sx = int(self.x - cam[0]); sy = int(self.y - cam[1])
        speed = math.hypot(self.vx, self.vy) or 1
        tail_len = max(self.size * 2, int(self.size * 3.5))
        tx = sx - int(self.vx / speed * tail_len)
        ty = sy - int(self.vy / speed * tail_len)
        # Dark void tail
        pygame.draw.line(surf, (60, 0, 100), (tx, ty), (sx, sy), max(1, self.size - 1))
        # Bright corrupted tip
        pygame.draw.circle(surf, self.col, (sx, sy), self.size)
        # Void glow
        gs = self.size + 5
        gsurf = pygame.Surface((gs * 2, gs * 2), pygame.SRCALPHA)
        r2 = max(0, min(255, self.col[0]))
        g2 = max(0, min(255, self.col[1]))
        b2 = max(0, min(255, self.col[2]))
        pygame.draw.circle(gsurf, (r2, g2, b2, 80), (gs, gs), gs)
        surf.blit(gsurf, (sx - gs, sy - gs))

class VoidOrbiterOrb:
    """
    Void Orbiter orb — orbits the player and slowly spirals outward while the
    mouse is held.  When the player releases LMB (or the orb reaches max_orbit_r)
    it detaches and flies outward as a normal Projectile, then expires at max_dist.
    """
    ORBIT_START_R = 30      # pixels from player centre at spawn
    ORBIT_EXPAND  = 0.9     # pixels per frame added to orbit radius while held
    MAX_ORBIT_R   = 180     # max orbit radius before auto-detach
    SPIN_RATE     = 0.09    # radians per frame

    def __init__(self, player, angle, dmg, spd, max_dist, col, size, orb_idx, orb_count):
        self.player     = player        # reference to Player — used while orbiting
        self.angle      = angle         # current orbital angle
        self.dmg        = dmg
        self.spd_mag    = spd * Projectile.SPD_SCALE
        self.max_dist   = max_dist
        self.col        = col
        self.size       = size
        self.orb_idx    = orb_idx
        self.orb_count  = orb_count
        self.orbit_r    = self.ORBIT_START_R + orb_idx * (self.ORBIT_START_R / max(1, orb_count))
        self.orbiting   = True          # True = still circling player
        self.alive      = True
        self.owner      = "player"
        # Position (set each frame while orbiting)
        self.x = player.x + math.cos(angle) * self.orbit_r
        self.y = player.y + math.sin(angle) * self.orbit_r
        # Velocity used once detached
        self.vx = 0.0
        self.vy = 0.0
        self.dist = 0.0

    def update(self, mouse_held=False):
        if self.orbiting:
            self.angle    += self.SPIN_RATE
            self.orbit_r  += self.ORBIT_EXPAND
            self.x = self.player.x + math.cos(self.angle) * self.orbit_r
            self.y = self.player.y + math.sin(self.angle) * self.orbit_r
            # Detach if mouse released or max orbit radius reached
            if not mouse_held or self.orbit_r >= self.MAX_ORBIT_R:
                self.orbiting = False
                self.vx = math.cos(self.angle) * self.spd_mag
                self.vy = math.sin(self.angle) * self.spd_mag
        else:
            self.x    += self.vx
            self.y    += self.vy
            self.dist += self.spd_mag
            if self.dist >= self.max_dist:
                self.alive = False

    def draw(self, surf, cam):
        sx = int(self.x - cam[0]); sy = int(self.y - cam[1])
        col = self.col
        # Outer glow
        gs = self.size + 5
        gsurf = pygame.Surface((gs * 2, gs * 2), pygame.SRCALPHA)
        glow_alpha = 90 if self.orbiting else 55
        pygame.draw.circle(gsurf, (*col, glow_alpha), (gs, gs), gs)
        surf.blit(gsurf, (sx - gs, sy - gs))
        # Core orb
        pygame.draw.circle(surf, col, (sx, sy), self.size)
        # Bright inner
        inner = lerp_color(col, (255, 255, 255), 0.45)
        pygame.draw.circle(surf, inner, (sx, sy), max(2, self.size - 3))
        # Trail line back toward player while orbiting
        if self.orbiting:
            px = int(self.player.x - cam[0])
            py = int(self.player.y - cam[1])
            trail_col = (*lerp_color(col, (0, 0, 0), 0.6), 80)
            tsurf = pygame.Surface((abs(sx - px) + 4, abs(sy - py) + 4), pygame.SRCALPHA)
            ox = min(sx, px); oy = min(sy, py)
            pygame.draw.line(tsurf, trail_col,
                             (sx - ox, sy - oy), (px - ox, py - oy), 1)
            surf.blit(tsurf, (ox, oy))

# ── Pierce Projectile ─────────────────────────────────────────────────────────

class PierceProjectile(Projectile):
    """Passes through enemies (hits each only once)."""
    def __init__(self, x, y, dx, dy, dmg, spd, rng, col, size):
        super().__init__(x, y, dx, dy, dmg, spd, rng, col, size, owner="player")
        self.hit_ids = set()   # enemy ids already struck

# ── FloatingText ──────────────────────────────────────────────────────────────

class FloatingText:
    def __init__(self, x, y, text, color, size=18):
        self.x = x; self.y = y
        self.text     = text; self.color = color; self.size = size
        self.life     = 60
        self.max_life = 60

    def update(self):
        self.y    -= 1.2
        self.life -= 1

    def draw(self, surf, cam, font):
        t   = self.life / self.max_life
        col = lerp_color(self.color, WHITE, 0.3)
        txt = font.render(self.text, True, col)
        txt.set_alpha(int(255 * t))
        surf.blit(txt, (int(self.x - cam[0]) - txt.get_width() // 2,
                        int(self.y - cam[1])))

# ── Player ────────────────────────────────────────────────────────────────────

class Player:
    def __init__(self, x, y, username="Player"):
        self.x = float(x); self.y = float(y)
        self.username       = username
        self.size           = 18
        self.speed          = 4.0
        self.level          = 1
        self.xp             = 0
        self.xp_to_next     = 100
        self.max_hp         = 100
        self.hp             = 100
        self.gold           = 0
        self.weapon_idx     = 0
        self.shoot_cooldown = 0
        self.iframes        = 0
        self.owned_weapons  = [0]
        self.kill_count     = 0
        self.corruption_waves_cleared = 0
        self.owned_cosmetics  = set(TOKENS.owned_cosmetics)
        self.active_cosmetic  = TOKENS.active_cosmetic
        self.owned_titles     = set(TOKENS.owned_titles)
        self.active_title     = TOKENS.active_title
        self._cosm_tick       = 0
        TOKENS.sync_to_player(self)
        # Perks: dict of key -> total stacked bonus (float)
        self.perks          = {}   # e.g. {"dmg_pct": 0.16, "defense": 0.08}
        self.regen_timer    = 0    # counts up to 180 (3s at 60fps)

        # Dash
        # Base values: 2 charges, 420-frame cooldown per charge (~7s), 14-frame duration
        self.DASH_BASE_CD   = 420
        self.DASH_DURATION  = 14
        self.DASH_SPEED     = 11.0
        self.dash_charges   = 2        # charges currently available
        self.dash_cds       = [0, 0]   # per-charge cooldown counters (count up to DASH_CD)
        self.dash_timer     = 0        # frames left in current dash
        self.dash_vx        = 0.0
        self.dash_vy        = 0.0
        self.dash_trail     = []       # list of (x, y, alpha) for afterimage
        self.hurt_flash     = 0        # counts down only on damage, drives cosmetic flicker
        self.lifesteal_acc  = 0.0      # accumulates fractional lifesteal HP until a whole point is ready

        # Void Orbiter state
        self.void_orbs       = []      # active VoidOrbiterOrbs currently orbiting
        self.mouse_hold_frames = 0     # frames LMB has been continuously held

    @property
    def weapon(self):
        # Special weapons use indices 1000+
        if self.weapon_idx >= 1000:
            return SPECIAL_WEAPONS[self.weapon_idx - 1000]
        return WEAPONS[self.weapon_idx]

    def xp_for_level(self, lvl):
        return int(100 * (lvl ** 1.4))

    def perk(self, key):
        """Return total stacked bonus for a perk key, 0.0 if not owned."""
        return self.perks.get(key, 0.0)

    def apply_perk(self, key, bonus):
        self.perks[key] = self.perks.get(key, 0.0) + bonus
        # If vitality perk, immediately update max_hp
        if key == "max_hp_pct":
            self.max_hp = int((100 + self.level * 15) * (1 + self.perk("max_hp_pct")))
            self.hp = min(self.hp + 20, self.max_hp)

    LEVEL_CAP = 25

    def gain_xp(self, amount):
        if self.level >= self.LEVEL_CAP:
            return False   # already at cap — no more levelling
        self.xp += amount
        leveled  = False
        while self.xp >= self.xp_to_next and self.level < self.LEVEL_CAP:
            self.xp         -= self.xp_to_next
            self.level      += 1
            self.xp_to_next  = self.xp_for_level(self.level)
            self.max_hp      = int((100 + self.level * 15) * (1 + self.perk("max_hp_pct")))
            self.hp          = min(self.hp + 40, self.max_hp)
            leveled = True
        if self.level >= self.LEVEL_CAP:
            self.xp = 0   # no overflow XP at cap
        return leveled

    def take_damage(self, dmg):
        if self.iframes > 0:
            return False
        reduced = int(dmg * (1.0 - min(0.75, self.perk("defense"))))
        self.hp       -= max(1, reduced)
        self.iframes   = 45
        self.hurt_flash = 45   # drives cosmetic flicker — only set on real damage
        return True

    def can_shoot(self):
        return self.shoot_cooldown <= 0

    def shoot(self, dx, dy, projectiles, enemies_ref=None):
        w      = self.weapon
        spd    = w["speed"] * (1 + self.perk("fire_rate"))
        dmg    = int((w["damage"] + self.level * 2) * (1 + self.perk("dmg_pct")))
        rng    = w["range"] * (1 + self.perk("range_pct"))
        col    = w["color"]
        sz     = w["proj_size"]
        beh    = w["behaviour"]
        mag    = math.hypot(dx, dy) or 1
        base_a = math.atan2(dy, dx)

        def _proj(angle, dmg_=None, spd_=None, rng_=None, sz_=None):
            a = angle
            projectiles.append(Projectile(
                self.x, self.y, math.cos(a), math.sin(a),
                dmg_ or dmg, spd_ or spd, rng_ or rng, col, sz_ or sz))

        if beh == "single":
            _proj(base_a)

        elif beh == "twin":
            # Two parallel bolts offset perpendicular to aim
            perp = base_a + math.pi / 2
            off  = 8
            for sign in (-1, 1):
                ox = math.cos(perp) * off * sign
                oy = math.sin(perp) * off * sign
                projectiles.append(Projectile(
                    self.x + ox, self.y + oy,
                    math.cos(base_a), math.sin(base_a),
                    dmg, spd, rng, col, sz))

        elif beh == "shotgun":
            # 5-bullet spread, slightly randomised angles
            for i in range(5):
                spread = -0.38 + i * 0.19 + random.uniform(-0.04, 0.04)
                _proj(base_a + spread)

        elif beh == "spread3":
            for off in (-0.25, 0, 0.25):
                _proj(base_a + off)

        elif beh == "rapid":
            # Single fast shot with tiny random spread for feel
            _proj(base_a + random.uniform(-0.04, 0.04))

        elif beh == "orbit":
            # Spawn 6 orbs that orbit the player and spiral outward while held
            count = 6
            for i in range(count):
                ang = base_a + (math.pi * 2 / count * i)
                self.void_orbs.append(VoidOrbiterOrb(
                    self, ang, dmg, spd, rng, col, sz, i, count))

        elif beh == "homing":
            # 3 homing seekers fanned slightly
            boss_targets = getattr(self, '_boss_ref', [])
            for off in (-0.2, 0, 0.2):
                p = PlayerHomingProjectile(
                    self.x, self.y,
                    math.cos(base_a + off), math.sin(base_a + off),
                    dmg, spd, rng, col, sz,
                    enemies_ref or [])
                p._boss_ref = boss_targets
                projectiles.append(p)

        elif beh == "corrupted_homing":
            # 4 aggressive homing bolts spread at wider fan angles
            boss_targets = getattr(self, '_boss_ref', [])
            for off in (-0.30, -0.10, 0.10, 0.30):
                p = CorruptedHomingProjectile(
                    self.x, self.y,
                    math.cos(base_a + off), math.sin(base_a + off),
                    dmg, spd, rng, col, sz,
                    enemies_ref or [])
                p._boss_ref = boss_targets
                projectiles.append(p)

        elif beh == "penta":
            # 5-way spread that pierces
            for i in range(5):
                a = base_a + (-0.4 + i * 0.2)
                projectiles.append(PierceProjectile(
                    self.x, self.y, math.cos(a), math.sin(a),
                    dmg, spd, rng, col, sz))

        self.shoot_cooldown = max(1, int(
            w["fire_interval"] / (1 + self.perk("fire_rate"))))
        # Per-weapon shoot sound
        _sfx_scale = {
            "rapid":             0.28,
            "corrupted_homing":  0.55,
        }.get(beh, 0.5)
        SOUNDS.play(f"shoot_{beh}", volume_scale=_sfx_scale)

    def _dash_cd(self):
        """Effective cooldown per charge, reduced by dash perk stacks."""
        stacks = int(self.perk("dash") + 0.5)
        return max(120, int(self.DASH_BASE_CD * (0.85 ** stacks)))

    def _dash_max_charges(self):
        """Total charges available: 2 base + 1 per perk stack."""
        stacks = int(self.perk("dash") + 0.5)
        return 2 + stacks

    def try_dash(self, dx, dy):
        """Attempt to use a dash charge. dx/dy is the intended direction."""
        # Find a ready charge slot
        for i in range(len(self.dash_cds)):
            if self.dash_cds[i] <= 0:
                self.dash_cds[i] = self._dash_cd()
                mag = math.hypot(dx, dy) or 1
                self.dash_vx = dx / mag * self.DASH_SPEED
                self.dash_vy = dy / mag * self.DASH_SPEED
                self.dash_timer = self.DASH_DURATION
                self.iframes = self.DASH_DURATION + 6
                SOUNDS.play("player_dash")
                return True
        return False

    def update(self, keys, mx, my, cam, projectiles, world_bounds):
        # Ensure charge list matches current max (can grow with perks)
        max_charges = self._dash_max_charges()
        while len(self.dash_cds) < max_charges:
            self.dash_cds.append(0)

        # Tick cooldowns
        for i in range(len(self.dash_cds)):
            if self.dash_cds[i] > 0:
                self.dash_cds[i] -= 1

        dx = dy = 0
        if keys[pygame.K_w] or keys[pygame.K_UP]:    dy -= 1
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:  dy += 1
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:  dx -= 1
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]: dx += 1
        if dx and dy:
            dx *= 0.707; dy *= 0.707

        # Dash movement overrides normal movement
        if self.dash_timer > 0:
            self.x += self.dash_vx
            self.y += self.dash_vy
            self.dash_timer -= 1
            self.dash_trail.append((self.x, self.y, 200))
        else:
            spd = self.speed * (1 + self.perk("speed_pct"))
            self.x = max(self.size, min(world_bounds[0] - self.size, self.x + dx * spd))
            self.y = max(self.size, min(world_bounds[1] - self.size, self.y + dy * spd))

        self.x = max(self.size, min(world_bounds[0] - self.size, self.x))
        self.y = max(self.size, min(world_bounds[1] - self.size, self.y))

        # Fade trail
        self.dash_trail = [(tx, ty, a - 22) for tx, ty, a in self.dash_trail if a > 0]

        if self.shoot_cooldown > 0:
            self.shoot_cooldown -= 1
        mouse_held = pygame.mouse.get_pressed()[0]
        if mouse_held:
            self.mouse_hold_frames += 1
        else:
            self.mouse_hold_frames = 0
        if self.shoot_cooldown == 0 and mouse_held:
            wx = mx + cam[0]; wy = my + cam[1]
            ddx = wx - self.x; ddy = wy - self.y
            if math.hypot(ddx, ddy) > 5:
                self.shoot(ddx, ddy, projectiles, enemies_ref=getattr(self, '_enemies_ref', []))

        # Update void orbs — pass current mouse state so they know when to detach
        still_orbiting = []
        for orb in self.void_orbs:
            orb.update(bool(mouse_held))
            if orb.orbiting:
                still_orbiting.append(orb)
            elif orb.alive:
                projectiles.append(orb)   # detached — hand off to main projectile list
            # dead orbs (dist exceeded) are simply dropped
        self.void_orbs = still_orbiting
        if self.iframes > 0:
            self.iframes -= 1
        if self.hurt_flash > 0:
            self.hurt_flash -= 1

    def draw(self, surf, cam, font_small, font_tiny):
        sx = int(self.x - cam[0]); sy = int(self.y - cam[1])
        self._cosm_tick += 1

        # Dash afterimage trail
        for tx, ty, alpha in self.dash_trail:
            trail_s = pygame.Surface((self.size * 2, self.size * 2), pygame.SRCALPHA)
            pygame.draw.circle(trail_s, (80, 220, 255, max(0, min(255, int(alpha)))),
                               (self.size, self.size), self.size)
            surf.blit(trail_s, (int(tx - cam[0]) - self.size,
                                int(ty - cam[1]) - self.size))

        # Void Orbiter orbs — draw while they're still orbiting the player
        for orb in self.void_orbs:
            orb.draw(surf, cam)

        # Shadow
        pygame.draw.ellipse(surf, (10, 10, 20),
                            (sx - self.size, sy + self.size - 4, self.size * 2, 10))
        hurt = self.hurt_flash > 0 and self.hurt_flash % 6 < 3
        pat  = self.active_cosmetic
        t    = self._cosm_tick

        if hurt:
            # For wings cosmetic, still draw the wings — only the orb goes white
            if pat == "wings":
                flap      = math.sin(t * 0.08)
                flap_ang  = flap * 0.20

                quill_offsets = [-0.55, -0.28, 0.0, 0.28, 0.52]
                quill_lengths = [self.size + 28, self.size + 36, self.size + 42,
                                 self.size + 36, self.size + 26]
                quill_widths  = [4, 5, 6, 5, 4]

                for side in (-1, 1):
                    base   = math.pi if side == -1 else 0.0
                    root_x = sx + int(math.cos(base) * (self.size - 4))
                    root_y = sy
                    for qi, (qoff, qlen, qw) in enumerate(zip(quill_offsets, quill_lengths, quill_widths)):
                        flap_bias = (2 - qi) * 0.10
                        ang   = base + (qoff + flap_ang * flap_bias) * side
                        tip_x = sx + int(math.cos(ang) * qlen)
                        tip_y = sy + int(math.sin(ang) * qlen) - int(flap * 8)
                        pygame.draw.line(surf, (100, 60, 180), (root_x, root_y), (tip_x, tip_y), qw + 2)
                        pygame.draw.line(surf, (210, 185, 255), (root_x, root_y), (tip_x, tip_y), qw)
                        if qi == 2:
                            pygame.draw.line(surf, (240, 230, 255), (root_x, root_y), (tip_x, tip_y), 2)
                        steps = 5
                        for bi in range(1, steps):
                            frac  = bi / steps
                            bx    = int(root_x + (tip_x - root_x) * frac)
                            by    = int(root_y + (tip_y - root_y) * frac)
                            barb_len = int((1 - frac) * 10 + 3)
                            perp  = ang + math.pi / 2
                            for bsign in (-1, 1):
                                ex_b = bx + int(math.cos(perp) * barb_len * bsign)
                                ey_b = by + int(math.sin(perp) * barb_len * bsign)
                                pygame.draw.line(surf, (200, 180, 255, int(140 * (1 - frac))),
                                                 (bx, by), (ex_b, ey_b), 1)
                    tip_ang = base + (quill_offsets[2] + flap_ang) * side
                    rim_x   = sx + int(math.cos(tip_ang) * (quill_lengths[2] + 4))
                    rim_y   = sy + int(math.sin(tip_ang) * quill_lengths[2]) - int(flap * 8)
                    pygame.draw.circle(surf, (230, 210, 255), (rim_x, rim_y), 3)
                rng_state = t // 8
                for sp in range(6):
                    spark_seed = int(rng_state * 17 + sp * 31) & 0xFFFF
                    spark_r    = (spark_seed % 22) + self.size
                    spark_ang  = (spark_seed % 628) / 100.0
                    spark_side = 1 if sp % 2 == 0 else -1
                    spx = sx + spark_side * int(math.cos(spark_ang) * spark_r)
                    spy = sy + int(math.sin(spark_ang) * spark_r * 0.55)
                    spark_alpha = ((t + sp * 7) % 30)
                    if spark_alpha < 15:
                        sc = min(255, 160 + spark_alpha * 6)
                        pygame.draw.circle(surf, (sc, sc, 255), (spx, spy), 2)
            elif pat == "blackhole":
                # Keep accretion disc during hurt — only orb flashes white
                np_bh  = math.sin(t * 0.05) * 0.5 + 0.5
                spin_a = t * 0.04
                for ring_idx in range(2):
                    ring_r   = self.size + 10 + ring_idx * 8
                    dots     = 8 + ring_idx * 3
                    ring_ang = spin_a * (1 if ring_idx == 0 else -1) * (1 + ring_idx * 0.5)
                    disc_col = lerp_color((60, 0, 140), (180, 60, 255), np_bh)
                    for di in range(dots):
                        a   = ring_ang + (math.pi * 2 / dots) * di
                        drx = sx + int(math.cos(a) * ring_r)
                        dry = sy + int(math.sin(a) * ring_r * 0.38)
                        pygame.draw.circle(surf, disc_col, (drx, dry), max(1, 3 - ring_idx))
            elif pat == "hexweaver":
                # Keep orbiting projectiles during hurt
                spin1 = t * 0.055
                spin2 = t * -0.038
                orb_r1 = self.size + 22
                for oi in range(6):
                    a   = spin1 + (math.pi * 2 / 6) * oi
                    ox2 = sx + int(math.cos(a) * orb_r1)
                    oy2 = sy + int(math.sin(a) * orb_r1)
                    pygame.draw.circle(surf, (200, 80, 255), (ox2, oy2), 5)
                    pygame.draw.circle(surf, WHITE, (ox2, oy2), 2)
                orb_r2 = self.size + 10
                for oi in range(3):
                    a   = spin2 + (math.pi * 2 / 3) * oi
                    ox2 = sx + int(math.cos(a) * orb_r2)
                    oy2 = sy + int(math.sin(a) * orb_r2)
                    pygame.draw.circle(surf, (160, 0, 220), (ox2, oy2), 3)
            elif pat == "lavalord":
                # Keep orbit ring and cracks visible during hurt
                spin_a = t * 0.04
                orb_r  = self.size + 14
                orb_r2 = int(orb_r * 0.4)
                for di in range(24):
                    a   = spin_a + (math.pi * 2 / 24) * di
                    drx = sx + int(math.cos(a) * orb_r)
                    dry = sy + int(math.sin(a) * orb_r2)
                    pygame.draw.circle(surf, (255, 140, 20), (drx, dry), 2)
            elif pat == "ironhide":
                # Keep spinning lantern embers and pauldrons visible during hurt — orb flashes white
                spin_outer = t * 0.030
                spin_inner = t * -0.048
                orb_r_out  = self.size + 20
                orb_r_in   = self.size + 12
                for oi in range(6):
                    a   = spin_outer + (math.pi * 2 / 6) * oi
                    ex2 = sx + int(math.cos(a) * orb_r_out)
                    ey2 = sy + int(math.sin(a) * orb_r_out)
                    pygame.draw.circle(surf, (220, 110, 0), (ex2, ey2), 3)
                for oi in range(4):
                    a   = spin_inner + (math.pi * 2 / 4) * oi
                    ix2 = sx + int(math.cos(a) * orb_r_in)
                    iy2 = sy + int(math.sin(a) * orb_r_in)
                    pygame.draw.circle(surf, (180, 80, 0), (ix2, iy2), 2)
                for side in (-1, 1):
                    px2 = sx + side * int(self.size * 0.82)
                    pygame.draw.circle(surf, (48, 55, 62), (px2, sy - 3), int(self.size * 0.38))
                    pygame.draw.circle(surf, (75, 85, 96), (px2, sy - 3), int(self.size * 0.38), 2)
            elif pat == "true_legend":
                # Keep rainbow orbit particles during hurt flash
                orb_r = self.size + 18
                for oi in range(8):
                    ang_p  = (t * 0.07) + (math.pi * 2 / 8) * oi
                    hue_p  = (t * 2 + oi * 45) % 360
                    rc, gc2, bc2 = _hsv_to_rgb(hue_p, 1.0, 1.0)
                    px2 = sx + int(math.cos(ang_p) * orb_r)
                    py2 = sy + int(math.sin(ang_p) * orb_r)
                    pygame.draw.circle(surf, (rc, gc2, bc2), (px2, py2), 4)
            pygame.draw.circle(surf, WHITE, (sx, sy), self.size)
        elif pat == "default":
            pygame.draw.circle(surf, CYAN, (sx, sy), self.size)
        elif pat == "fire":
            pygame.draw.circle(surf, (200, 60, 10), (sx, sy), self.size)
            # Animated inner flame flicker
            fr = max(4, self.size - 6 + int(math.sin(t * 0.25) * 3))
            pygame.draw.circle(surf, (255, 160, 20), (sx, sy), fr)
            pygame.draw.circle(surf, (255, 240, 80), (sx, sy - 2), max(2, fr - 5))
        elif pat == "frost":
            pygame.draw.circle(surf, (40, 120, 200), (sx, sy), self.size)
            # Six crystal lines
            for i in range(6):
                a = math.pi / 3 * i + t * 0.01
                ex2 = sx + int(math.cos(a) * (self.size - 3))
                ey2 = sy + int(math.sin(a) * (self.size - 3))
                pygame.draw.line(surf, (180, 230, 255), (sx, sy), (ex2, ey2), 1)
            pygame.draw.circle(surf, (200, 240, 255), (sx, sy), self.size // 2)
        elif pat == "void":
            pygame.draw.circle(surf, (30, 0, 60), (sx, sy), self.size)
            # Pulsing inner ring
            pr = int(self.size * 0.55 + math.sin(t * 0.18) * 3)
            pygame.draw.circle(surf, (140, 0, 220), (sx, sy), max(3, pr), 2)
            pygame.draw.circle(surf, (60, 0, 100), (sx, sy), self.size // 3)
        elif pat == "gold":
            pygame.draw.circle(surf, (200, 150, 0), (sx, sy), self.size)
            pygame.draw.circle(surf, (255, 215, 0), (sx, sy), self.size, 3)
            pygame.draw.circle(surf, (255, 240, 100), (sx, sy), self.size // 2)
        elif pat == "storm":
            pygame.draw.circle(surf, (60, 80, 160), (sx, sy), self.size)
            # Rotating lightning arc dots
            for i in range(4):
                a = math.pi / 2 * i + t * 0.12
                lx = sx + int(math.cos(a) * (self.size - 4))
                ly = sy + int(math.sin(a) * (self.size - 4))
                pygame.draw.circle(surf, (200, 230, 255), (lx, ly), 3)
            pygame.draw.circle(surf, (150, 200, 255), (sx, sy), self.size // 3)

            # ── Lightning bolts radiating outward ─────────────────────────────
            # Each bolt fires on a fixed interval staggered per bolt index,
            # travels outward for a short duration, then resets.
            BOLT_COUNT    = 5       # number of bolts simultaneously in play
            BOLT_INTERVAL = 18      # frames between each bolt firing
            BOLT_LIFE     = 10      # frames a bolt is visible
            BOLT_REACH    = 28      # max distance from player edge in pixels
            BOLT_SEGS     = 4       # jagged segments per bolt

            for bi in range(BOLT_COUNT):
                # Stagger bolt timings so they don't all fire at once
                phase = (t + bi * (BOLT_INTERVAL // BOLT_COUNT)) % BOLT_INTERVAL
                if phase >= BOLT_LIFE:
                    continue   # this bolt is currently dormant

                progress = phase / BOLT_LIFE   # 0→1 over the bolt's life

                # Deterministic random angle and jitter seed from tick + bolt index
                seed = (t // BOLT_INTERVAL) * BOLT_COUNT + bi
                rng_ang  = ((seed * 2654435761) & 0xFFFF) / 0xFFFF * math.pi * 2
                base_len = self.size + int(progress * BOLT_REACH)

                # Build the jagged bolt as a series of displaced midpoints
                start_x = sx + int(math.cos(rng_ang) * self.size)
                start_y = sy + int(math.sin(rng_ang) * self.size)
                end_x   = sx + int(math.cos(rng_ang) * (self.size + base_len))
                end_y   = sy + int(math.sin(rng_ang) * (self.size + base_len))

                pts = [(start_x, start_y)]
                for si in range(1, BOLT_SEGS):
                    frac      = si / BOLT_SEGS
                    mx_pt     = start_x + int((end_x - start_x) * frac)
                    my_pt     = start_y + int((end_y - start_y) * frac)
                    # Perpendicular jitter — deterministic per segment
                    jitter_seed = ((seed * 1234567 + si * 987654) & 0x7FFF)
                    jitter = ((jitter_seed % 13) - 6)   # -6 to +6 pixels
                    perp_x = -int(math.sin(rng_ang) * jitter)
                    perp_y =  int(math.cos(rng_ang) * jitter)
                    pts.append((mx_pt + perp_x, my_pt + perp_y))
                pts.append((end_x, end_y))

                # Fade out toward end of life
                alpha = int(255 * (1.0 - progress ** 1.5))

                # Draw the bolt — outer glow then bright core
                for p0, p1 in zip(pts, pts[1:]):
                    glow_s = pygame.Surface(
                        (abs(p1[0]-p0[0]) + 10, abs(p1[1]-p0[1]) + 10), pygame.SRCALPHA)
                    ox2 = min(p0[0], p1[0]) - 5
                    oy2 = min(p0[1], p1[1]) - 5
                    pygame.draw.line(surf, (100, 180, 255, max(0, alpha // 3)),
                                     p0, p1, 3)   # soft outer glow
                    pygame.draw.line(surf, (220, 240, 255, max(0, alpha)),
                                     p0, p1, 1)   # bright white-blue core
        elif pat == "wings":
            # ── Draw wings BEHIND the orb first ──────────────────────────────
            flap      = math.sin(t * 0.08)          # -1 to 1, slow gentle flap
            flap_ang  = flap * 0.20                  # max ±0.20 rad tilt

            # Wing feathers: each wing has 5 quills fanning outward
            # Quill angles are relative to straight-sideways (math.pi for left, 0 for right)
            quill_offsets = [-0.55, -0.28, 0.0, 0.28, 0.52]  # fan spread angles
            quill_lengths = [self.size + 28, self.size + 36, self.size + 42,
                             self.size + 36, self.size + 26]
            quill_widths  = [4, 5, 6, 5, 4]

            for side in (-1, 1):
                base = math.pi if side == -1 else 0.0   # left wing points left, right points right
                root_x = sx + int(math.cos(base) * (self.size - 4))
                root_y = sy

                for qi, (qoff, qlen, qw) in enumerate(zip(quill_offsets, quill_lengths, quill_widths)):
                    # Apply flap: upper quills tilt more
                    flap_bias = (2 - qi) * 0.10   # quills 0,1 tip up on flap-up
                    ang = base + (qoff + flap_ang * flap_bias) * side

                    tip_x = sx + int(math.cos(ang) * qlen)
                    tip_y = sy + int(math.sin(ang) * qlen) - int(flap * 8)

                    # Draw thick outer quill (dark purple base)
                    pygame.draw.line(surf, (100, 60, 180),
                                     (root_x, root_y), (tip_x, tip_y), qw + 2)
                    # Mid quill (bright lavender)
                    pygame.draw.line(surf, (210, 185, 255),
                                     (root_x, root_y), (tip_x, tip_y), qw)
                    # Inner highlight (white core on centre quill)
                    if qi == 2:
                        pygame.draw.line(surf, (240, 230, 255),
                                         (root_x, root_y), (tip_x, tip_y), 2)

                    # Feather barbs — short perpendicular lines along each quill
                    steps = 5
                    for bi in range(1, steps):
                        frac = bi / steps
                        bx = int(root_x + (tip_x - root_x) * frac)
                        by = int(root_y + (tip_y - root_y) * frac)
                        barb_len = int((1 - frac) * 10 + 3)
                        perp = ang + math.pi / 2
                        for bsign in (-1, 1):
                            ex_b = bx + int(math.cos(perp) * barb_len * bsign)
                            ey_b = by + int(math.sin(perp) * barb_len * bsign)
                            barb_alpha = int(140 * (1 - frac))
                            pygame.draw.line(surf, (200, 180, 255, barb_alpha),
                                             (bx, by), (ex_b, ey_b), 1)

                # Glowing edge rim along the outermost quill tip
                tip_ang = base + (quill_offsets[2] + flap_ang) * side
                rim_x = sx + int(math.cos(tip_ang) * (quill_lengths[2] + 4))
                rim_y = sy + int(math.sin(tip_ang) * quill_lengths[2]) - int(flap * 8)
                pygame.draw.circle(surf, (230, 210, 255), (rim_x, rim_y), 3)

            # Shimmer sparkles around the wings
            rng_state = t // 8   # changes every 8 ticks for a twinkling effect
            for sp in range(6):
                spark_seed = int(rng_state * 17 + sp * 31) & 0xFFFF
                spark_r    = (spark_seed % 22) + self.size
                spark_ang  = (spark_seed % 628) / 100.0
                spark_side = 1 if sp % 2 == 0 else -1
                spx = sx + spark_side * int(math.cos(spark_ang) * spark_r)
                spy = sy + int(math.sin(spark_ang) * spark_r * 0.55)
                spark_alpha = ((t + sp * 7) % 30)
                if spark_alpha < 15:
                    sc = min(255, 160 + spark_alpha * 6)
                    pygame.draw.circle(surf, (sc, sc, 255), (spx, spy), 2)

            # ── Now draw the orb on top ───────────────────────────────────────
            # Core orb — no glow circles
            pygame.draw.circle(surf, (160, 130, 210), (sx, sy), self.size)
        elif pat == "blackhole":
            t_bh   = self._cosm_tick
            np_bh  = math.sin(t_bh * 0.05) * 0.5 + 0.5   # 0..1 pulse
            spin_a = t_bh * 0.04   # rotation

            # Accretion disc — two counter-rotating rings of dots
            for ring_idx in range(2):
                ring_r   = self.size + 10 + ring_idx * 8
                dots     = 8 + ring_idx * 3
                spin_dir = 1 if ring_idx == 0 else -1
                ring_ang = spin_a * spin_dir * (1 + ring_idx * 0.5)
                disc_col = lerp_color((60, 0, 140), (180, 60, 255), np_bh)
                for di in range(dots):
                    a   = ring_ang + (math.pi * 2 / dots) * di
                    drx = sx + int(math.cos(a) * ring_r)
                    dry = sy + int(math.sin(a) * ring_r * 0.38)
                    dr  = max(1, 3 - ring_idx)
                    gs2 = pygame.Surface((dr * 4, dr * 4), pygame.SRCALPHA)
                    pygame.draw.circle(gs2, (*disc_col, 150), (dr * 2, dr * 2), dr * 2)
                    surf.blit(gs2, (drx - dr * 2, dry - dr * 2))
                    pygame.draw.circle(surf, disc_col, (drx, dry), dr)

            # Event horizon glow
            hz_r = self.size + 4 + int(np_bh * 4)
            hs   = pygame.Surface((hz_r * 2 + 8, hz_r * 2 + 8), pygame.SRCALPHA)
            pygame.draw.circle(hs, (20, 0, 60, int(100 + np_bh * 60)),
                               (hz_r + 4, hz_r + 4), hz_r)
            surf.blit(hs, (sx - hz_r - 4, sy - hz_r - 4))

            # Pure black void with gravitational lens ring
            pygame.draw.circle(surf, (0, 0, 0), (sx, sy), self.size)
            pygame.draw.circle(surf, lerp_color((40, 0, 100), (120, 0, 200), np_bh),
                               (sx, sy), self.size, 2)
            pygame.draw.circle(surf, lerp_color((80, 0, 160), (200, 80, 255), np_bh),
                               (sx, sy), int(self.size * 0.82), 1)

        elif pat == "hexweaver":
            # ── Hex Weaver — orbiting chaos projectiles + pulsing hex body ─────
            t_hx  = self._cosm_tick
            pulse = math.sin(t_hx * 0.07) * 0.5 + 0.5
            spin1 = t_hx * 0.055    # outer ring — clockwise
            spin2 = t_hx * -0.038   # inner ring — counter-clockwise

            # Outer ring: 6 orbiting "projectile" blobs
            orb_r1 = self.size + 22
            for oi in range(6):
                a    = spin1 + (math.pi * 2 / 6) * oi
                ox2  = sx + int(math.cos(a) * orb_r1)
                oy2  = sy + int(math.sin(a) * orb_r1)
                # Trailing tail (2 ghost dots behind each)
                for ghost in range(1, 3):
                    ga   = a - ghost * 0.22
                    gx2  = sx + int(math.cos(ga) * orb_r1)
                    gy2  = sy + int(math.sin(ga) * orb_r1)
                    gc   = lerp_color((180, 0, 255), (80, 0, 140), ghost * 0.5)
                    pygame.draw.circle(surf, gc, (gx2, gy2), max(1, 4 - ghost))
                # Main orb blob
                orb_col = lerp_color((200, 80, 255), (255, 120, 255), pulse)
                pygame.draw.circle(surf, orb_col, (ox2, oy2), 5)
                pygame.draw.circle(surf, WHITE, (ox2, oy2), 2)

            # Inner ring: 3 smaller spinning hex dots
            orb_r2 = self.size + 10
            for oi in range(3):
                a    = spin2 + (math.pi * 2 / 3) * oi
                ox2  = sx + int(math.cos(a) * orb_r2)
                oy2  = sy + int(math.sin(a) * orb_r2)
                ic   = lerp_color((140, 0, 220), (220, 60, 255), pulse)
                pygame.draw.circle(surf, ic, (ox2, oy2), 3)

            # Body — pulsing purple/magenta with hex pattern
            body_col = lerp_color((80, 0, 160), (140, 0, 200), pulse)
            pygame.draw.circle(surf, body_col, (sx, sy), self.size)
            # 6-point hex outline on body
            for hi in range(6):
                ha  = (math.pi / 3) * hi + spin1 * 0.2
                hx2 = sx + int(math.cos(ha) * (self.size - 3))
                hy2 = sy + int(math.sin(ha) * (self.size - 3))
                pygame.draw.circle(surf, lerp_color((200, 60, 255), (255, 160, 255), pulse), (hx2, hy2), 2)
            # Pulsing inner glow ring
            ir = max(3, int(self.size * 0.55 + math.sin(t_hx * 0.09) * 3))
            pygame.draw.circle(surf, lerp_color((180, 0, 255), (255, 80, 255), pulse), (sx, sy), ir, 2)

        elif pat == "lavalord":
            # ── Lava Lord — lava cracks on body + spinning pulsing orbit line ──
            t_ll   = self._cosm_tick
            lp     = math.sin(t_ll * 0.06) * 0.5 + 0.5
            spin_a = t_ll * 0.04

            # Spinning elliptical orbit ring (drawn first, behind body)
            orb_r  = self.size + 14
            orb_r2 = int(orb_r * 0.4)   # squashed vertical radius
            ring_pulse = math.sin(t_ll * 0.1) * 0.5 + 0.5
            ring_w = max(1, int(2 + ring_pulse * 2))
            ring_col = lerp_color((200, 60, 0), (255, 200, 40), ring_pulse)
            # Draw orbit as a series of dots forming an ellipse
            for di in range(24):
                a   = spin_a + (math.pi * 2 / 24) * di
                drx = sx + int(math.cos(a) * orb_r)
                dry = sy + int(math.sin(a) * orb_r2)
                dr  = max(1, ring_w if di % 3 != 0 else ring_w + 1)
                bright = 1.0 if di % 6 == 0 else 0.5
                dc  = lerp_color(ring_col, WHITE, bright * 0.4)
                pygame.draw.circle(surf, dc, (drx, dry), dr)

            # Outer heat glow rings (concentric, no surface alloc)
            glow_r = self.size + 8 + int(lp * 5)
            for gi in range(3):
                gr = glow_r - gi * 3
                if gr > self.size:
                    gc2 = (max(0, min(255, int(160 + lp * 60 - gi * 30))),
                           max(0, min(255, int(40 + lp * 30 - gi * 10))), 0)
                    pygame.draw.circle(surf, gc2, (sx, sy), gr, 2)

            # Dark crust body
            crust = lerp_color((50, 12, 0), (90, 22, 0), lp)
            pygame.draw.circle(surf, crust, (sx, sy), self.size)

            # Lava crack lines
            for ci in range(6):
                crack_a   = spin_a * 0.3 + ci * (math.pi / 3)
                crack_len = int(self.size * 0.72 + math.sin(t_ll * 0.05 + ci) * self.size * 0.18)
                cx1 = sx + int(math.cos(crack_a) * 3)
                cy1 = sy + int(math.sin(crack_a) * 3)
                cx2 = sx + int(math.cos(crack_a) * crack_len)
                cy2 = sy + int(math.sin(crack_a) * crack_len)
                cc  = lerp_color((210, 70, 0), (255, 210, 40), lp)
                pygame.draw.line(surf, cc, (cx1, cy1), (cx2, cy2), 2)

            # Molten core
            core_r   = int(self.size * 0.42 + lp * self.size * 0.12)
            core_col = lerp_color((255, 120, 0), (255, 240, 80), lp)
            pygame.draw.circle(surf, core_col, (sx, sy), core_r)

        elif pat == "ironhide":
            # ── Ironhide — Gorvak-style plate armour + warm lantern glow ─────────
            t_ih  = self._cosm_tick
            pulse = math.sin(t_ih * 0.045) * 0.5 + 0.5   # slow amber pulse

            # ── Lantern orbit — spinning amber embers drawn behind everything ──────
            # Outer ring: 6 embers rotating clockwise, each leaving a 3-dot tail
            spin_outer = t_ih * 0.030          # clockwise
            spin_inner = t_ih * -0.048         # counter-clockwise
            orb_r_out  = self.size + 20
            orb_r_in   = self.size + 12
            for oi in range(6):
                a = spin_outer + (math.pi * 2 / 6) * oi
                # Ghost tail (3 dots fading behind each ember)
                for ghost in range(1, 4):
                    ga  = a - ghost * 0.18
                    gx2 = sx + int(math.cos(ga) * orb_r_out)
                    gy2 = sy + int(math.sin(ga) * orb_r_out)
                    fade = max(0, int(pulse * 80) - ghost * 22)
                    if fade > 8:
                        pygame.draw.circle(surf, (min(255, 140 + fade), min(255, fade // 2), 0),
                                           (gx2, gy2), max(1, 3 - ghost))
                # Main ember dot — brightness breathes with pulse
                ex2 = sx + int(math.cos(a) * orb_r_out)
                ey2 = sy + int(math.sin(a) * orb_r_out)
                ec  = (min(255, 200 + int(pulse * 55)),
                       min(255, 100 + int(pulse * 40)),
                       max(0,   int(pulse * 10)))
                pygame.draw.circle(surf, ec, (ex2, ey2), max(2, 3 + int(pulse)))
            # Inner ring: 4 smaller embers counter-rotating, no tails
            for oi in range(4):
                a   = spin_inner + (math.pi * 2 / 4) * oi
                ix2 = sx + int(math.cos(a) * orb_r_in)
                iy2 = sy + int(math.sin(a) * orb_r_in)
                ic  = (min(255, 170 + int(pulse * 40)),
                       min(255, 80  + int(pulse * 30)), 0)
                pygame.draw.circle(surf, ic, (ix2, iy2), 2)

            # ── Pauldrons (shoulder guards) — draw behind the body ───────────────
            for side in (-1, 1):
                px2 = sx + side * int(self.size * 0.82)
                pygame.draw.circle(surf, (48, 55, 62), (px2, sy - 3), int(self.size * 0.38))
                pygame.draw.circle(surf, (75, 85, 96), (px2, sy - 3), int(self.size * 0.38), 2)
                # Three rivets on each pauldron
                for ri in range(3):
                    ra  = (ri - 1) * 0.35 * side
                    rrx = px2 + int(math.cos(ra) * int(self.size * 0.20))
                    rry = sy - 3 + int(math.sin(ra) * int(self.size * 0.20))
                    pygame.draw.circle(surf, (100, 112, 124), (rrx, rry), 2)

            # ── Chestplate body ───────────────────────────────────────────────────
            pygame.draw.circle(surf, (50, 58, 66), (sx, sy), int(self.size * 0.92))
            # Vertical chest ridge
            pygame.draw.line(surf, (88, 100, 112),
                             (sx, sy - int(self.size * 0.70)),
                             (sx, sy + int(self.size * 0.60)), 2)
            # Horizontal belt line
            pygame.draw.line(surf, (68, 78, 88),
                             (sx - int(self.size * 0.68), sy + int(self.size * 0.20)),
                             (sx + int(self.size * 0.68), sy + int(self.size * 0.20)), 2)

            # ── Helmet arc ───────────────────────────────────────────────────────
            helm_r = int(self.size * 0.62)
            pygame.draw.arc(surf, (62, 72, 82),
                            (sx - helm_r, sy - helm_r, helm_r * 2, helm_r * 2),
                            math.pi * 0.10, math.pi * 0.90, int(self.size * 0.20))

            # ── Visor slit — glows green, dims to amber when hurt ─────────────────
            visor_y   = sy - int(self.size * 0.28)
            visor_col = lerp_color((80, 180, 80), (200, 140, 30), pulse * 0.25)
            pygame.draw.rect(surf, visor_col,
                             (sx - int(self.size * 0.30), visor_y - 2,
                              int(self.size * 0.60), 5),
                             border_radius=2)
            # Glowing eye dots through the visor
            for ex_off in (-int(self.size * 0.13), int(self.size * 0.13)):
                pygame.draw.circle(surf, visor_col, (sx + ex_off, visor_y), 2)

            # ── Pulsing steel rim ─────────────────────────────────────────────────
            rim_col = lerp_color((78, 88, 100), (120, 138, 155), pulse)
            pygame.draw.circle(surf, rim_col, (sx, sy), self.size, 3)

        elif pat == "true_legend":
            # ── True Legend — rotating rainbow strips + orbiting particles ─────
            # 5 diagonal rainbow strips that scroll across the body
            NUM_STRIPS  = 5
            STRIP_SPEED = 1.2   # pixels per frame
            scroll_off  = (t * STRIP_SPEED) % (self.size * 2)   # wraps every full width

            # Draw onto a temp surface then mask to the circle
            body_size = self.size + 2
            body_surf = pygame.Surface((body_size * 2, body_size * 2), pygame.SRCALPHA)
            for si in range(NUM_STRIPS * 2):   # double-up so we fill across the circle
                hue       = (t * 1.5 + si * (360 / NUM_STRIPS)) % 360
                strip_col = _hsv_to_rgb(hue, 0.9, 1.0)
                strip_w   = max(3, (body_size * 2) // NUM_STRIPS - 1)
                x_pos     = int(si * strip_w - scroll_off) % (body_size * 2 + strip_w) - strip_w
                # Diagonal strip: draw as a parallelogram using a polygon
                pts = [
                    (x_pos,           0),
                    (x_pos + strip_w, 0),
                    (x_pos + strip_w - body_size // 2, body_size * 2),
                    (x_pos           - body_size // 2, body_size * 2),
                ]
                try:
                    pygame.draw.polygon(body_surf, strip_col, pts)
                except Exception:
                    pass
            # Clip to circle
            mask_surf = pygame.Surface((body_size * 2, body_size * 2), pygame.SRCALPHA)
            pygame.draw.circle(mask_surf, (255, 255, 255, 255), (body_size, body_size), self.size)
            body_surf.blit(mask_surf, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            surf.blit(body_surf, (sx - body_size, sy - body_size))

            # Bright white ring outline with rainbow shimmer
            ring_hue = (t * 3) % 360
            pygame.draw.circle(surf, _hsv_to_rgb(ring_hue, 0.6, 1.0), (sx, sy), self.size, 3)

            # Orbiting rainbow particles (8 particles, two rings)
            for ring_i, (orb_r, n_orbs, spd) in enumerate([(self.size + 14, 8, 0.07), (self.size + 24, 6, -0.05)]):
                for oi in range(n_orbs):
                    ang_p  = (t * spd) + (math.pi * 2 / n_orbs) * oi
                    hue_p  = (t * 2 + ring_i * 60 + oi * (360 // n_orbs)) % 360
                    rc, gc2, bc2 = _hsv_to_rgb(hue_p, 1.0, 1.0)
                    px2 = sx + int(math.cos(ang_p) * orb_r)
                    py2 = sy + int(math.sin(ang_p) * orb_r)
                    pygame.draw.circle(surf, (rc, gc2, bc2), (px2, py2), 4 if ring_i == 0 else 3)
                    # Small glow dot inside
                    pygame.draw.circle(surf, WHITE, (px2, py2), 2)

        # ── Case-exclusive cosmetics ──────────────────────────────────────────
        elif pat in ("case_red","case_green","case_purple","case_orange","case_pink"):
            col_map = {"case_red":(220,50,50),"case_green":(40,180,80),
                       "case_purple":(160,60,220),"case_orange":(255,130,20),"case_pink":(255,100,180)}
            c = col_map[pat]
            pygame.draw.circle(surf, c, (sx, sy), self.size)
            # Soft inner glow
            pulse = math.sin(t * 0.06) * 0.3 + 0.7
            pygame.draw.circle(surf, tuple(min(255,int(v*pulse)) for v in c), (sx,sy), int(self.size*0.55))

        elif pat == "case_stripes":
            # Rotating diagonal colour stripes
            body_size = self.size + 1
            bs = pygame.Surface((body_size*2, body_size*2), pygame.SRCALPHA)
            for si in range(6):
                hue = (t * 2 + si * 60) % 360
                sc  = _hsv_to_rgb(hue, 0.8, 1.0)
                sw2 = body_size * 2 // 6
                xp  = int(si * sw2 - (t * 1.0) % (body_size * 2))
                pts = [(xp,0),(xp+sw2,0),(xp+sw2-body_size//3,body_size*2),(xp-body_size//3,body_size*2)]
                try: pygame.draw.polygon(bs, sc, pts)
                except: pass
            ms = pygame.Surface((body_size*2, body_size*2), pygame.SRCALPHA)
            pygame.draw.circle(ms, (255,255,255,255), (body_size,body_size), self.size)
            bs.blit(ms,(0,0),special_flags=pygame.BLEND_RGBA_MULT)
            surf.blit(bs,(sx-body_size,sy-body_size))

        elif pat == "case_pulse":
            # Pulsing concentric rings
            pygame.draw.circle(surf, (160,40,220),(sx,sy), self.size)
            for ri in range(3):
                phase = (t * 0.08 + ri * 0.33) % 1.0
                r2 = int(self.size * phase)
                alpha = max(0, int(200 * (1 - phase)))
                if r2 > 0 and alpha > 0:
                    rs = pygame.Surface((r2*2+2,r2*2+2), pygame.SRCALPHA)
                    pygame.draw.circle(rs,(200,80,255,alpha),(r2+1,r2+1),r2,2)
                    surf.blit(rs,(sx-r2-1,sy-r2-1))

        elif pat == "case_checker":
            # Animated chequered pattern clipped to circle
            body_size = self.size + 1
            bs = pygame.Surface((body_size*2, body_size*2), pygame.SRCALPHA)
            cell = max(6, body_size//3)
            shift = int(t * 0.8) % (cell * 2)
            for gx in range(-cell, body_size*2 + cell, cell):
                for gy in range(-cell, body_size*2 + cell, cell):
                    rx = (gx + shift) // cell; ry = (gy + shift) // cell
                    col2 = (200,180,40) if (rx+ry) % 2 == 0 else (60,50,10)
                    pygame.draw.rect(bs, col2, (gx, gy, cell, cell))
            ms = pygame.Surface((body_size*2,body_size*2),pygame.SRCALPHA)
            pygame.draw.circle(ms,(255,255,255,255),(body_size,body_size),self.size)
            bs.blit(ms,(0,0),special_flags=pygame.BLEND_RGBA_MULT)
            surf.blit(bs,(sx-body_size,sy-body_size))

        elif pat == "case_wave":
            # Rippling wave bands
            pygame.draw.circle(surf,(20,100,200),(sx,sy),self.size)
            for wi in range(4):
                phase = (t * 0.07 + wi * 0.25) % 1.0
                r2 = int(self.size * 0.3 + self.size * 0.7 * phase)
                alpha = max(0,int(220*(1-phase)))
                if r2 > 0 and alpha > 0:
                    rs = pygame.Surface((r2*2+2,r2*2+2),pygame.SRCALPHA)
                    pygame.draw.circle(rs,(40,200,255,alpha),(r2+1,r2+1),r2,3)
                    surf.blit(rs,(sx-r2-1,sy-r2-1))

        elif pat == "case_spiral":
            # Spinning spiral segments
            pygame.draw.circle(surf,(180,20,100),(sx,sy),self.size)
            for si in range(8):
                ang = t * 0.09 + si * math.pi / 4
                for rr in range(4,self.size,4):
                    hue = (t * 3 + rr * 8 + si * 45) % 360
                    sc = _hsv_to_rgb(hue,0.9,1.0)
                    px2 = sx + int(math.cos(ang + rr * 0.12) * rr)
                    py2 = sy + int(math.sin(ang + rr * 0.12) * rr)
                    pygame.draw.circle(surf, sc, (px2,py2), 2)

        elif pat == "case_plasma":
            # Crackling plasma — grid of shimmering cells
            pygame.draw.circle(surf,(20,160,140),(sx,sy),self.size)
            cell = 7
            for gx in range(sx-self.size, sx+self.size, cell):
                for gy in range(sy-self.size, sy+self.size, cell):
                    if math.hypot(gx+cell//2-sx, gy+cell//2-sy) < self.size - 2:
                        seed = int(gx*31 + gy*17 + t*5) & 0xFF
                        if seed > 200:
                            ec = (60, 255, 220) if seed > 230 else (20,200,160)
                            pygame.draw.rect(surf, ec, (gx,gy,cell-1,cell-1))

        elif pat == "case_nova":
            # Exploding star burst + orbiting gold orbs
            pygame.draw.circle(surf,(200,160,20),(sx,sy),self.size)
            for si in range(8):
                ang = t * 0.06 + si * math.pi / 4
                for rr in range(2, self.size, 4):
                    flick = math.sin(t * 0.15 + si * 1.1 + rr * 0.3)
                    if flick > 0.3:
                        px2 = sx + int(math.cos(ang) * rr)
                        py2 = sy + int(math.sin(ang) * rr)
                        pygame.draw.circle(surf,(255,230,60),(px2,py2),2)
            orb_r = self.size + 16
            for oi in range(6):
                oa = t * 0.08 + oi * math.pi / 3
                pygame.draw.circle(surf,(255,200,40),(sx+int(math.cos(oa)*orb_r),sy+int(math.sin(oa)*orb_r)),5)
                pygame.draw.circle(surf,WHITE,(sx+int(math.cos(oa)*orb_r),sy+int(math.sin(oa)*orb_r)),2)

        elif pat == "case_vortex":
            # Dark swirling vortex + shard projectiles
            pygame.draw.circle(surf,(30,15,80),(sx,sy),self.size)
            for ri in range(self.size, 2, -3):
                ang = t * 0.10 + ri * 0.25
                tc2 = lerp_color((30,15,80),(120,40,220), 1 - ri/self.size)
                px2 = sx + int(math.cos(ang) * ri * 0.5)
                py2 = sy + int(math.sin(ang) * ri * 0.5)
                pygame.draw.circle(surf, tc2, (px2,py2), max(1,ri//5))
            orb_r = self.size + 14
            for oi in range(5):
                oa = t * -0.09 + oi * math.pi * 2 / 5
                pygame.draw.circle(surf,(160,60,255),(sx+int(math.cos(oa)*orb_r),sy+int(math.sin(oa)*orb_r)),4)

        elif pat == "case_aurora":
            # Shimmering aurora curtains + drifting particles
            pygame.draw.circle(surf,(10,80,60),(sx,sy),self.size)
            for bi in range(5):
                hue = (t * 1.5 + bi * 30) % 120 + 120  # greens and cyans
                bc  = _hsv_to_rgb(hue,0.8,1.0)
                bx2 = sx - self.size + bi * (self.size*2//5)
                wave = int(math.sin(t * 0.1 + bi * 1.2) * 5)
                pygame.draw.line(surf,bc,(bx2,sy-self.size+wave),(bx2,sy+self.size+wave),3)
            orb_r = self.size + 18
            for oi in range(7):
                oa = t * 0.05 + oi * math.pi * 2 / 7
                hue = (t * 3 + oi * 51) % 120 + 100
                oc = _hsv_to_rgb(hue, 0.7, 1.0)
                pygame.draw.circle(surf, oc,(sx+int(math.cos(oa)*orb_r),sy+int(math.sin(oa)*orb_r)),4)

        elif pat == "case_infernal":
            # Pulsing orange body + colour-cycling projectiles (purple→pink→red→orange→yellow→blue→purple)
            pulse = math.sin(t * 0.07) * 0.4 + 0.6
            body_col = (int(255*pulse), int(100*pulse), int(10*pulse))
            glow_col = (int(255*pulse), int(160*pulse), int(40*pulse))
            pygame.draw.circle(surf, body_col, (sx,sy), self.size)
            # Inner pulsing core
            pygame.draw.circle(surf, glow_col, (sx,sy), int(self.size * 0.55))
            # Outer pulsing ring
            ring_alpha = int(180 * pulse)
            rs = pygame.Surface((self.size*2+8,self.size*2+8),pygame.SRCALPHA)
            pygame.draw.circle(rs,(*glow_col,ring_alpha),(self.size+4,self.size+4),self.size+2,4)
            surf.blit(rs,(sx-self.size-4,sy-self.size-4))
            # Colour-cycling orbiting projectiles — hue cycles through a specific path
            # purple(270)→pink(320)→red(0)→orange(30)→yellow(60)→blue(220)→purple(270)
            # We map this as hue = f(t) smoothly
            hue_path = [270, 320, 360, 390, 420, 580, 630]  # extended degrees
            hue_cycle = (t * 1.8) % (hue_path[-1] - hue_path[0])
            raw_h = hue_path[0] + hue_cycle
            proj_col = _hsv_to_rgb(raw_h % 360, 1.0, 1.0)
            for oi in range(8):
                oa = t * 0.09 + oi * math.pi / 4
                orb_r = self.size + 20
                px2 = sx + int(math.cos(oa)*orb_r); py2 = sy + int(math.sin(oa)*orb_r)
                pygame.draw.circle(surf, proj_col, (px2,py2), 5)
                pygame.draw.circle(surf, WHITE,    (px2,py2), 2)

        else:
            pygame.draw.circle(surf, CYAN, (sx, sy), self.size)

        pygame.draw.circle(surf, WHITE, (sx, sy), self.size, 2)

        # Eye
        mx_, my_ = pygame.mouse.get_pos()
        ang = math.atan2(my_ - sy, mx_ - sx)
        ex  = sx + int(math.cos(ang) * 8)
        ey  = sy + int(math.sin(ang) * 8)
        pygame.draw.circle(surf, DARK, (ex, ey), 5)

        # Username label (and optional title) above player
        name_surf = font_small.render(self.username, True, CYAN)
        name_x    = sx - name_surf.get_width() // 2
        name_y    = sy - self.size - 22
        pad       = 4

        # If player has an active title, draw it on a line above the username
        title_obj = None
        if self.active_title and self.active_title != "none":
            title_obj = next((t for t in TITLES if t["id"] == self.active_title), None)

        if title_obj:
            title_surf = font_small.render(title_obj["name"], True, title_obj["col"])
            title_x    = sx - title_surf.get_width() // 2
            title_y    = name_y - title_surf.get_height() - 2
            # Background covers both rows
            total_h  = title_surf.get_height() + 2 + name_surf.get_height() + 2
            total_w  = max(title_surf.get_width(), name_surf.get_width()) + pad * 2
            bg_surf  = pygame.Surface((total_w, total_h), pygame.SRCALPHA)
            bg_surf.fill((0, 0, 0, 140))
            surf.blit(bg_surf, (sx - total_w // 2, title_y - 2))
            surf.blit(title_surf, (title_x, title_y))
            surf.blit(name_surf, (name_x, name_y))
        else:
            bg_surf = pygame.Surface(
                (name_surf.get_width() + pad * 2, name_surf.get_height() + 2),
                pygame.SRCALPHA)
            bg_surf.fill((0, 0, 0, 140))
            surf.blit(bg_surf, (name_x - pad, name_y - 2))
            surf.blit(name_surf, (name_x, name_y))

        # Optional player health bar (shown when enabled in settings)
        if GAME_SETTINGS.player_health_bar:
            bw = self.size * 2 + 16
            bx = sx - bw // 2
            by = sy - self.size - 10
            draw_bar(surf, bx, by, bw, 5, self.hp, self.max_hp, (50, 220, 80))

# ── Enemy ─────────────────────────────────────────────────────────────────────

class Enemy:
    def __init__(self, x, y, etype_idx, player_level, is_splinter=False, is_elite=False):
        et          = ENEMY_TYPES[etype_idx]
        self.etype  = etype_idx
        self.name   = et["name"]
        self.color  = et["color"]
        self.size   = et["size"]
        self.behaviour = et["behaviour"]
        self.is_splinter = is_splinter

        # Elite variant — explicitly set by spawner, splinters are never elite
        self.elite = (is_elite and not is_splinter)
        if self.elite:
            ev = ELITE_VARIANTS[etype_idx]
            self.name     = ev["name"]
            self.color    = ev["color"]
            self.glow_col = ev["glow"]
            self.size     = et["size"] + ev["size_add"]
        else:
            self.glow_col = None

        # Level-based scale
        scale = 1 + (player_level - 1) * 0.15
        if is_splinter:
            scale *= 0.45

        if self.elite:
            ev = ELITE_VARIANTS[etype_idx]
            self.max_hp    = int(et["base_hp"] * scale * ev["hp_mult"])
            self.hp        = self.max_hp
            self.dmg       = int(et["base_dmg"] * scale * ev["dmg_mult"])
            self.gold_drop = int(et["gold"] * (1 + (player_level - 1) * 0.05) * 1.8)
            self.xp_drop   = int(et["xp"] * scale * 1.5)
            self.speed     = et["base_spd"] * (1 + player_level * 0.012) * ev["spd_mult"]
        else:
            self.max_hp    = int(et["base_hp"] * scale)
            self.hp        = self.max_hp
            self.dmg       = int(et["base_dmg"] * scale)
            self.gold_drop = int(et["gold"] * (1 + (player_level - 1) * 0.05))
            self.xp_drop   = int(et["xp"] * scale)
            self.speed     = et["base_spd"] * (1 + player_level * 0.012)
        self.x         = float(x); self.y = float(y)
        self.alive     = True
        self.hurt_flash = 0

        # ── Shared timers ───────────────────────────────────────────────────
        self.shoot_cd    = random.randint(40, 90)   # stagger so they don't all fire at once
        self.ability_cd  = random.randint(60, 180)

        # ── Behaviour-specific state ────────────────────────────────────────
        # bounce (Slime)
        self.bounce_vx   = random.uniform(-1.2, 1.2)
        self.bounce_vy   = random.uniform(-1.2, 1.2)
        self.split_done  = False

        # dash (Goblin)
        self.dash_vx = 0.0; self.dash_vy = 0.0
        self.dash_timer  = 0      # frames remaining in dash
        self.dash_trail  = []     # list of (x, y, alpha) for afterimage

        # tank (Orc) — rage spin after first hit below 50 %
        self.rage_triggered = False
        self.spin_timer     = 0

        # mage — blink cooldown
        self.blink_cd    = random.randint(240, 420)

        # dragon — lingering fire orbs list handled in Game, flag drops here
        self.drop_orb    = False   # set True when Dragon fires; Game reads & clears

        # generic
        self.warn_flash  = 0   # visual telegraph before ability fires

        # ── Elite-specific state ─────────────────────────────────────────────
        self.elite_cd    = random.randint(80, 160)   # elite special ability timer
        self.acid_trail  = []    # Plague Slime: list of (x, y, life)
        self.shadow_trap = []    # Shadow Stalker: list of (x, y, life) ground traps

    # ── helpers ──────────────────────────────────────────────────────────────

    def _fire_single(self, projectiles, tx, ty, dmg_mult=1.0, spd=0.10,
                     rng=340, col=None, size=7):
        dx = tx - self.x; dy = ty - self.y
        mag = math.hypot(dx, dy) or 1
        projectiles.append(Projectile(self.x, self.y, dx / mag, dy / mag,
            int(self.dmg * dmg_mult), spd, rng,
            col or self.color, size, owner="enemy"))

    def _fire_spread(self, projectiles, dx, dy, count, spread_rad,
                     dmg_mult=1.0, spd=0.10, rng=340, col=None, size=7):
        base = math.atan2(dy, dx)
        for i in range(count):
            if count == 1:
                offset = 0
            else:
                offset = -spread_rad + (2 * spread_rad / (count - 1)) * i
            a = base + offset
            projectiles.append(Projectile(
                self.x, self.y, math.cos(a), math.sin(a),
                int(self.dmg * dmg_mult), spd, rng,
                col or self.color, size, owner="enemy"))

    def _fire_ring(self, projectiles, count, dmg_mult=0.7, spd=0.09,
                   rng=280, col=None, size=6):
        for i in range(count):
            a = math.pi * 2 / count * i
            projectiles.append(Projectile(
                self.x, self.y, math.cos(a), math.sin(a),
                int(self.dmg * dmg_mult), spd, rng,
                col or self.color, size, owner="enemy"))

    # ── update ───────────────────────────────────────────────────────────────

    def update(self, player, projectiles, world_bounds):
        if not self.alive:
            return

        dx   = player.x - self.x
        dy   = player.y - self.y
        dist = math.hypot(dx, dy) or 1

        if self.hurt_flash  > 0: self.hurt_flash  -= 1
        if self.warn_flash  > 0: self.warn_flash   -= 1
        if self.ability_cd  > 0: self.ability_cd   -= 1
        if self.shoot_cd    > 0: self.shoot_cd      -= 1

        # ── BOUNCE (Slime) ────────────────────────────────────────────────────
        if self.behaviour == "bounce":
            # Drift erratically but also bias toward player
            self.bounce_vx += dx / dist * 0.04
            self.bounce_vy += dy / dist * 0.04
            mag = math.hypot(self.bounce_vx, self.bounce_vy) or 1
            if mag > self.speed:
                self.bounce_vx = self.bounce_vx / mag * self.speed
                self.bounce_vy = self.bounce_vy / mag * self.speed
            self.x += self.bounce_vx
            self.y += self.bounce_vy
            # Bounce off walls
            if self.x <= self.size or self.x >= world_bounds[0] - self.size:
                self.bounce_vx *= -1
            if self.y <= self.size or self.y >= world_bounds[1] - self.size:
                self.bounce_vy *= -1
            # Spit a slow blob every ~3s when within range
            if self.shoot_cd == 0 and dist < 320 and not self.is_splinter:
                self._fire_single(projectiles, player.x, player.y,
                                   dmg_mult=0.8, spd=0.07, rng=300,
                                   col=(60, 200, 60), size=8)
                self.shoot_cd = 110 if not self.elite else 65
                SOUNDS.play("slime_spit", volume_scale=0.6)
            # Elite: leave acid trail
            if self.elite:
                self.acid_trail.append([self.x, self.y, 80])
                self.acid_trail = [[ax, ay, al - 1] for ax, ay, al in self.acid_trail if al > 0]

        # ── DASH (Goblin) ─────────────────────────────────────────────────────
        elif self.behaviour == "dash":
            if self.dash_timer > 0:
                self.x += self.dash_vx
                self.y += self.dash_vy
                self.dash_timer -= 1
                self.dash_trail.append((self.x, self.y, 180))
            else:
                # Normal approach
                if dist > self.size + player.size:
                    self.x += dx / dist * self.speed
                    self.y += dy / dist * self.speed
                # Dash charge: warn for 18 frames then launch
                if self.ability_cd == 18:
                    self.warn_flash = 18
                if self.ability_cd == 0 and dist < 380:
                    self.dash_vx = dx / dist * self.speed * 5.2
                    self.dash_vy = dy / dist * self.speed * 5.2
                    self.dash_timer = 14 if not self.elite else 20
                    self.ability_cd = random.randint(
                        90 if not self.elite else 55,
                        150 if not self.elite else 100)
                    SOUNDS.play("goblin_dash", volume_scale=0.7)
                    # Elite: drop shadow trap at current position
                    if self.elite:
                        self.shadow_trap.append([self.x, self.y, 180])
            # Fade trail
            self.dash_trail = [(tx, ty, a - 18) for tx, ty, a in self.dash_trail if a > 0]
            # Elite: fade shadow traps
            if self.elite:
                self.shadow_trap = [[sx2, sy2, sl - 1] for sx2, sy2, sl in self.shadow_trap if sl > 0]

        # ── TANK (Orc) ────────────────────────────────────────────────────────
        elif self.behaviour == "tank":
            if self.spin_timer > 0:
                # Spin-fire: one projectile per 4 frames in a rotating sweep
                if self.spin_timer % 4 == 0:
                    angle = (self.spin_timer / 4) * (math.pi * 2 / 8)
                    self._fire_single(projectiles,
                                       self.x + math.cos(angle) * 50,
                                       self.y + math.sin(angle) * 50,
                                       dmg_mult=0.65, spd=0.09, rng=260,
                                       col=(220, 80, 40), size=8)
                self.spin_timer -= 1
            else:
                # Slow approach
                if dist > self.size + player.size:
                    self.x += dx / dist * self.speed
                    self.y += dy / dist * self.speed
                # Trigger rage spin below 50 % HP — once per life
                if not self.rage_triggered and self.hp < self.max_hp * 0.5:
                    self.rage_triggered = True
                    self.spin_timer = 32 if not self.elite else 52
                    self.warn_flash = 20
                    SOUNDS.play("orc_spin", volume_scale=0.8)
                # Slow straight shot every ~4s when close
                if self.shoot_cd == 0 and dist < 300:
                    if self.elite:
                        # 3-shot cannon burst
                        self._fire_spread(projectiles, dx, dy, 3, 0.22,
                                          dmg_mult=0.9, spd=0.12, rng=340,
                                          col=(255, 80, 0), size=11)
                    else:
                        self._fire_single(projectiles, player.x, player.y,
                                           dmg_mult=1.0, spd=0.09, rng=300,
                                           col=(200, 60, 30), size=10)
                    self.shoot_cd = 140 if not self.elite else 90

        # ── MAGE ─────────────────────────────────────────────────────────────
        elif self.behaviour == "mage":
            # Keep distance
            preferred = 260
            if dist < preferred - 30:
                self.x -= dx / dist * self.speed * 0.9
                self.y -= dy / dist * self.speed * 0.9
            elif dist > preferred + 60:
                self.x += dx / dist * self.speed
                self.y += dy / dist * self.speed

            # 3-way spread shot every ~2.5s (5-way for elite)
            if self.shoot_cd == 0 and dist < 400:
                if self.elite:
                    self._fire_spread(projectiles, dx, dy, 5, 0.40,
                                       dmg_mult=0.85, spd=0.14, rng=420,
                                       col=(80, 160, 255), size=8)
                else:
                    self._fire_spread(projectiles, dx, dy, 3, 0.30,
                                       dmg_mult=0.9, spd=0.11, rng=380,
                                       col=(180, 60, 255), size=7)
                self.shoot_cd = 100 if not self.elite else 65

            # Blink
            if self.blink_cd > 0:
                self.blink_cd -= 1
            if self.blink_cd == 0 and dist < 350:
                perp_x = -dy / dist
                perp_y =  dx / dist
                side    = random.choice([-1, 1])
                blink_dist = 130 if not self.elite else 200
                nx = self.x + perp_x * side * blink_dist
                ny = self.y + perp_y * side * blink_dist
                nx = max(self.size, min(world_bounds[0] - self.size, nx))
                ny = max(self.size, min(world_bounds[1] - self.size, ny))
                self.x = nx; self.y = ny
                self.blink_cd = random.randint(
                    300 if not self.elite else 160,
                    480 if not self.elite else 280)
                SOUNDS.play("mage_blink", volume_scale=0.7)
                # Fire from blink destination — elite fires 3-shot burst
                if self.elite:
                    ddx2 = player.x - self.x; ddy2 = player.y - self.y
                    self._fire_spread(projectiles, ddx2, ddy2, 3, 0.20,
                                       dmg_mult=1.2, spd=0.15, rng=460,
                                       col=(120, 200, 255), size=9)
                else:
                    self._fire_single(projectiles, player.x, player.y,
                                       dmg_mult=1.1, spd=0.12, rng=420,
                                       col=(255, 100, 255), size=8)

        # ── DRAGON ───────────────────────────────────────────────────────────
        elif self.behaviour == "dragon":
            # Slow approach, stop when in comfortable range
            if dist > 200:
                self.x += dx / dist * self.speed
                self.y += dy / dist * self.speed

            # Double-shot aimed burst every ~3.5s (4-way for elite)
            if self.shoot_cd == 0 and dist < 450:
                if self.elite:
                    self._fire_spread(projectiles, dx, dy, 4, 0.22,
                                       dmg_mult=1.0, spd=0.13, rng=440,
                                       col=(255, 60, 0), size=11)
                else:
                    self._fire_spread(projectiles, dx, dy, 2, 0.18,
                                       dmg_mult=1.0, spd=0.11, rng=400,
                                       col=(255, 90, 20), size=10)
                self.shoot_cd = 130 if not self.elite else 85
                self.drop_orb = True
                SOUNDS.play("dragon_orb", volume_scale=0.7)

            # Occasional ring burst
            if self.ability_cd == 0:
                ring_count = 6 if not self.elite else 10
                self._fire_ring(projectiles, ring_count, dmg_mult=0.6,
                                 spd=0.08, rng=260, col=(255, 50, 0), size=7)
                self.ability_cd = random.randint(
                    200 if not self.elite else 130,
                    320 if not self.elite else 220)

        # Clamp to world
        self.x = max(self.size, min(world_bounds[0] - self.size, self.x))
        self.y = max(self.size, min(world_bounds[1] - self.size, self.y))

        # Melee contact for non-ranged non-dragon (dragon uses projectiles)
        if self.behaviour in ("bounce", "dash", "tank"):
            if dist < self.size + player.size:
                player.take_damage(self.dmg)

    def take_damage(self, dmg):
        self.hp         -= dmg
        self.hurt_flash  = 8
        if self.hp <= 0:
            self.alive = False

    def draw(self, surf, cam):
        if not self.alive:
            return
        sx = int(self.x - cam[0]); sy = int(self.y - cam[1])

        # Goblin dash trail
        if self.behaviour == "dash":
            for tx, ty, alpha in self.dash_trail:
                trail_s = pygame.Surface((self.size * 2, self.size * 2), pygame.SRCALPHA)
                pygame.draw.circle(trail_s, (*self.color, alpha),
                                   (self.size, self.size), self.size)
                surf.blit(trail_s, (int(tx - cam[0]) - self.size,
                                     int(ty - cam[1]) - self.size))

        # Warn flash ring (telegraph for dash/spin)
        if self.warn_flash > 0:
            warn_r = self.size + 10 + (18 - self.warn_flash)
            ws = pygame.Surface((warn_r * 2 + 4, warn_r * 2 + 4), pygame.SRCALPHA)
            alpha = int(200 * self.warn_flash / 18)
            pygame.draw.circle(ws, (255, 255, 80, alpha),
                               (warn_r + 2, warn_r + 2), warn_r, 2)
            surf.blit(ws, (sx - warn_r - 2, sy - warn_r - 2))

        # Shadow
        pygame.draw.ellipse(surf, (10, 10, 20),
                            (sx - self.size, sy + self.size - 4, self.size * 2, 8))

        # Elite: draw acid trail (Plague Slime) or shadow traps (Shadow Stalker)
        if self.elite:
            if hasattr(self, 'acid_trail'):
                for ax, ay, al in self.acid_trail:
                    asx = int(ax - cam[0]); asy = int(ay - cam[1])
                    alpha = max(0, min(255, int(al * 3)))
                    ts = pygame.Surface((14, 14), pygame.SRCALPHA)
                    pygame.draw.circle(ts, (40, 200, 40, alpha), (7, 7), 5)
                    surf.blit(ts, (asx - 7, asy - 7))
            if hasattr(self, 'shadow_trap'):
                for sx2, sy2, sl in self.shadow_trap:
                    tsx = int(sx2 - cam[0]); tsy = int(sy2 - cam[1])
                    alpha = max(0, min(200, sl))
                    ts = pygame.Surface((28, 28), pygame.SRCALPHA)
                    pygame.draw.circle(ts, (120, 0, 200, alpha), (14, 14), 10, 2)
                    pygame.draw.line(ts, (80, 0, 140, alpha), (14, 4), (14, 24), 1)
                    pygame.draw.line(ts, (80, 0, 140, alpha), (4, 14), (24, 14), 1)
                    surf.blit(ts, (tsx - 14, tsy - 14))

        # Body
        flash = self.hurt_flash % 4 < 2 and self.hurt_flash > 0
        col   = WHITE if flash else self.color

        # Elite glow ring — pulsing outer ring in glow colour
        if self.elite and not flash and self.glow_col:
            pulse_r = self.size + 4 + int(math.sin(pygame.time.get_ticks() * 0.006 + self.x) * 2)
            gs = pygame.Surface((pulse_r * 2 + 6, pulse_r * 2 + 6), pygame.SRCALPHA)
            pygame.draw.circle(gs, (*self.glow_col, 140),
                               (pulse_r + 3, pulse_r + 3), pulse_r, 3)
            surf.blit(gs, (sx - pulse_r - 3, sy - pulse_r - 3))

        # Spinning orc gets a visual tell
        if self.behaviour == "tank" and self.spin_timer > 0:
            spin_s = pygame.Surface((self.size * 3, self.size * 3), pygame.SRCALPHA)
            pygame.draw.circle(spin_s, (*self.color, 100),
                               (self.size + self.size // 2,
                                self.size + self.size // 2),
                               self.size + 6, 3)
            surf.blit(spin_s, (sx - self.size - self.size // 2,
                                sy - self.size - self.size // 2))

        pygame.draw.circle(surf, col, (sx, sy), self.size)
        pygame.draw.circle(surf, WHITE, (sx, sy), self.size, 2)

        # Mage inner glow dot
        if self.behaviour == "mage":
            pygame.draw.circle(surf, (200, 100, 255) if not self.elite else (100, 180, 255),
                               (sx, sy), self.size // 2)

        # Dragon inner flame
        if self.behaviour == "dragon":
            pygame.draw.circle(surf, (255, 120, 20) if not self.elite else (255, 60, 0),
                               (sx, sy), self.size // 2)

        # Elite inner pattern
        if self.elite and not flash:
            t = pygame.time.get_ticks() * 0.004
            if self.behaviour == "bounce":     # Plague Slime: pulsing toxic core
                core_r = max(3, self.size // 3 + int(math.sin(t * 2) * 2))
                pygame.draw.circle(surf, (0, 255, 80), (sx, sy), core_r)
            elif self.behaviour == "dash":     # Shadow Stalker: X slash marks
                arm = self.size // 2
                pygame.draw.line(surf, (180, 0, 255), (sx - arm, sy - arm), (sx + arm, sy + arm), 2)
                pygame.draw.line(surf, (180, 0, 255), (sx + arm, sy - arm), (sx - arm, sy + arm), 2)
            elif self.behaviour == "tank":     # Berserker Orc: gold spinning cross
                for i in range(4):
                    a = t + i * math.pi / 2
                    ex3 = sx + int(math.cos(a) * (self.size - 6))
                    ey3 = sy + int(math.sin(a) * (self.size - 6))
                    pygame.draw.line(surf, (255, 180, 0), (sx, sy), (ex3, ey3), 2)
            elif self.behaviour == "mage":     # Void Mage: rotating star
                for i in range(6):
                    a = t * 1.5 + i * math.pi / 3
                    ex3 = sx + int(math.cos(a) * (self.size - 5))
                    ey3 = sy + int(math.sin(a) * (self.size - 5))
                    pygame.draw.circle(surf, (150, 220, 255), (ex3, ey3), 2)
            elif self.behaviour == "dragon":   # Inferno Drake: ring of fire dots
                for i in range(8):
                    a = t + i * math.pi / 4
                    ex3 = sx + int(math.cos(a) * (self.size - 8))
                    ey3 = sy + int(math.sin(a) * (self.size - 8))
                    pygame.draw.circle(surf, (255, 100, 0), (ex3, ey3), 3)

        # Elite name tag — shown above HP bar in glow colour
        bw = self.size * 2 + 8
        if self.elite and self.glow_col:
            bar_col = self.glow_col
        else:
            bar_col = GREEN
        draw_bar(surf, sx - bw // 2, sy - self.size - 10, bw, 5,
                 self.hp, self.max_hp, bar_col)

# ── Fire Orb (Dragon lingering hazard) ───────────────────────────────────────

class FireOrb:
    LIFETIME = 180   # 3 seconds

    def __init__(self, x, y, dmg):
        self.x = float(x); self.y = float(y)
        self.dmg    = dmg
        self.life   = self.LIFETIME
        self.radius = 18
        self.pulse  = 0.0
        self.hit_cd = 0   # so it doesn't damage every single frame

    def update(self, player):
        self.life   -= 1
        self.pulse  += 0.15
        if self.hit_cd > 0: self.hit_cd -= 1
        if self.life <= 0:
            return False
        if (self.hit_cd == 0 and
                math.hypot(player.x - self.x, player.y - self.y) < self.radius + player.size):
            player.take_damage(self.dmg)
            self.hit_cd = 40   # hurt every ~0.7s
        return True

    def draw(self, surf, cam):
        sx = int(self.x - cam[0]); sy = int(self.y - cam[1])
        t  = self.life / self.LIFETIME
        r  = int(self.radius + math.sin(self.pulse) * 3)
        alpha = int(180 * t)
        s = pygame.Surface((r * 2 + 8, r * 2 + 8), pygame.SRCALPHA)
        pygame.draw.circle(s, (255, 80, 10, alpha),  (r + 4, r + 4), r)
        pygame.draw.circle(s, (255, 200, 50, alpha), (r + 4, r + 4), max(1, r - 5))
        surf.blit(s, (sx - r - 4, sy - r - 4))

# ── Nyxoth Fire Bomb ──────────────────────────────────────────────────────────

class NyxFireBomb:
    """
    A large void-fire circle that burns on the ground for a few seconds.
    In enraged mode it 'falls from the sky' with a warning shadow + drop animation.
    """
    LIFETIME    = 240   # 4 seconds on ground
    FALL_FRAMES = 40    # frames of fall animation before landing
    RADIUS      = 48    # ground burn radius

    def __init__(self, x, y, dmg, falling=False):
        self.x       = float(x); self.y = float(y)
        self.dmg     = dmg
        self.life    = self.LIFETIME
        self.pulse   = 0.0
        self.hit_cd  = 0
        self.falling = falling
        self.fall_t  = self.FALL_FRAMES if falling else 0   # counts down
        self.alive   = True

    def update(self, player):
        self.pulse += 0.12
        if self.hit_cd > 0:
            self.hit_cd -= 1
        if self.falling and self.fall_t > 0:
            self.fall_t -= 1
            return True   # still falling — no ground damage yet
        self.life -= 1
        if self.life <= 0:
            self.alive = False
            return False
        if (self.hit_cd == 0 and
                math.hypot(player.x - self.x, player.y - self.y) < self.RADIUS + player.size):
            player.take_damage(self.dmg)
            self.hit_cd = 70   # hurt every ~1.2s (was 35)
        return True

    def draw(self, surf, cam):
        sx = int(self.x - cam[0]); sy = int(self.y - cam[1])

        if self.falling and self.fall_t > 0:
            # Warning shadow on ground + falling orb from above
            t = 1 - self.fall_t / self.FALL_FRAMES   # 0→1 as it falls
            # Shadow grows as bomb approaches — red tint
            shad_r = int(self.RADIUS * t)
            if shad_r > 2:
                shad_s = pygame.Surface((shad_r * 2 + 4, shad_r * 2 + 4), pygame.SRCALPHA)
                pygame.draw.circle(shad_s, (180, 0, 0, int(160 * t)),
                                   (shad_r + 2, shad_r + 2), shad_r)
                surf.blit(shad_s, (sx - shad_r - 2, sy - shad_r - 2))
            # Falling orb — red/orange
            fall_offset = int((1 - t) * 220)
            orb_sy = sy - fall_offset
            orb_r  = max(4, int(10 + t * (self.RADIUS - 10)))
            os2 = pygame.Surface((orb_r * 2 + 8, orb_r * 2 + 8), pygame.SRCALPHA)
            pygame.draw.circle(os2, (180, 0, 0, 220),   (orb_r + 4, orb_r + 4), orb_r)
            pygame.draw.circle(os2, (255, 80, 0, 180),  (orb_r + 4, orb_r + 4),
                               max(2, orb_r - 4))
            surf.blit(os2, (sx - orb_r - 4, orb_sy - orb_r - 4))
            return

        # Ground burn — red/orange palette
        ground_t = self.life / self.LIFETIME   # 1→0
        r    = int(self.RADIUS + math.sin(self.pulse) * 4)
        # Outer red glow
        glow_r = r + 18
        gs = pygame.Surface((glow_r * 2 + 4, glow_r * 2 + 4), pygame.SRCALPHA)
        pygame.draw.circle(gs, (160, 0, 0, int(80 * ground_t)),
                           (glow_r + 2, glow_r + 2), glow_r)
        surf.blit(gs, (sx - glow_r - 2, sy - glow_r - 2))
        # Core fire circle — concentric red/orange rings
        bs = pygame.Surface((r * 2 + 8, r * 2 + 8), pygame.SRCALPHA)
        pygame.draw.circle(bs, (120, 0, 0,   int(200 * ground_t)), (r + 4, r + 4), r)
        pygame.draw.circle(bs, (200, 30, 0,  int(160 * ground_t)), (r + 4, r + 4),
                           max(2, int(r * 0.75)))
        pygame.draw.circle(bs, (255, 100, 0, int(120 * ground_t)), (r + 4, r + 4),
                           max(2, int(r * 0.45)))
        pygame.draw.circle(bs, (255, 220, 80, int(80 * ground_t)), (r + 4, r + 4),
                           max(2, int(r * 0.2)))
        surf.blit(bs, (sx - r - 4, sy - r - 4))
        # Flickering edge sparks — red/orange
        for i in range(6):
            spark_a = self.pulse * 1.3 + i * math.pi / 3
            spark_r = r + int(math.sin(self.pulse * 2 + i) * 8) + 4
            spx = sx + int(math.cos(spark_a) * spark_r)
            spy = sy + int(math.sin(spark_a) * spark_r)
            pygame.draw.circle(surf, (255, 80, 0), (spx, spy), 2)


# ── Homing Projectile (boss-only) ────────────────────────────────────────────

class HomingProjectile(Projectile):
    def __init__(self, x, y, dx, dy, dmg, col, target):
        super().__init__(x, y, dx, dy, dmg, 0.18, 600, col, 9, owner="enemy")
        self.target    = target
        self.turn_rate = 0.09
        self.age       = 0

    def update(self):
        self.age += 1
        # Only home after a short travel time so it doesn't snap instantly
        if self.age > 20 and self.target:
            tx  = self.target.x - self.x
            ty  = self.target.y - self.y
            mag = math.hypot(tx, ty) or 1
            tx /= mag; ty /= mag
            cmag = math.hypot(self.vx, self.vy) or 1
            cx   = self.vx / cmag; cy = self.vy / cmag
            nx   = cx + tx * self.turn_rate
            ny   = cy + ty * self.turn_rate
            nm   = math.hypot(nx, ny) or 1
            self.vx = nx / nm * cmag
            self.vy = ny / nm * cmag
        self.x    += self.vx
        self.y    += self.vy
        self.dist += math.hypot(self.vx, self.vy)
        if self.dist >= self.max_dist:
            self.alive = False

# ── Boss ──────────────────────────────────────────────────────────────────────

class Boss:
    ENRAGE_THRESHOLD = 0.35   # goes enrage below 35% HP

    def __init__(self, x, y, btype_idx, player_level):
        bt = BOSS_TYPES[btype_idx]
        self.name     = bt["name"]
        self.title    = bt["title"]
        self.color    = bt["color"]
        self.size     = bt["size"]
        self.pattern  = bt["pattern"]
        self.proj_col = bt["proj_col"]

        # Scaling: aggressive curve so bosses stay scary at high levels
        scale          = 1 + (player_level - 1) * 0.22
        self.max_hp    = int(bt["base_hp"] * scale)
        self.hp        = self.max_hp
        self.base_dmg  = int(bt["base_dmg"] * scale)
        self.speed     = bt["base_spd"] * (1 + player_level * 0.015)
        self.gold_drop = int(bt["gold"] * (1 + (player_level - 1) * 0.06))
        self.xp_drop   = int(bt["xp"] * scale)

        self.x = float(x); self.y = float(y)
        self.alive      = True
        self.hurt_flash = 0
        self.enraged    = False

        # Pattern timers / state
        self.atk_cd      = 90
        self.spiral_ang  = 0.0
        self.charge_vx   = 0.0
        self.charge_vy   = 0.0
        self.charge_timer = 0
        self.orbit_ang   = 0.0
        self.orbit_dir   = random.choice([-1, 1])

        # Seraphix dash state
        self.seraph_dash_cd    = random.randint(60, 120)
        self.seraph_dash_vx    = 0.0
        self.seraph_dash_vy    = 0.0
        self.seraph_dash_timer = 0

        # Gorvak minion state
        self.minions    = []   # list of GorvakMinion
        self.summon_cd  = 120  # frames until first summon

        # Shake effect on hurt
        self.shake_x = 0; self.shake_y = 0
        self.shake_t = 0

        # Vexara phase-2 state
        self.vex_split_done  = False   # True once the split has fired
        self.vex_clone       = None    # reference to the clone Boss (primary only)
        self.is_vex_clone    = False   # True if this IS the clone
        self.vex_pulse_t     = 0       # colour pulse timer
        self._trigger_vex_split = False  # set by take_damage, consumed by game loop

        # Vexara teleport state (phase 1 only — not enraged, not clone)
        self.vex_tp_cd       = random.randint(240, 360)  # frames until next teleport
        self.vex_tp_warning  = 0    # countdown for pre-blink warning flash (40 frames)
        self.vex_tp_flash    = 0    # post-blink flash countdown (20 frames)
        self.vex_tp_old_x    = 0.0  # position before teleport (for afterimage)
        self.vex_tp_old_y    = 0.0

        # Nyxoth state
        self.nyx_homing_cd  = random.randint(60, 100)   # cooldown between homing bursts
        self.nyx_bomb_cd    = random.randint(180, 280)  # cooldown between firebombs
        self.nyx_pulse_t    = 0                          # visual pulse timer

        # Malachar lava/fire state
        self.mal_fire_particles     = []   # visual-only fire sparks [x, y, vx, vy, life, max_life, size]
        self.mal_pulse_t            = 0    # lava texture pulse timer
        self.mal_spin_timer         = 0    # > 0 while spin attack is playing
        self.mal_spin_ang           = 0.0  # current spin angle
        self.mal_last_spin_pct      = 5    # tracks last 20% threshold crossed (starts at 5 so first trigger is at 80%)
        self._trigger_mal_spin      = False  # consumed by game loop for SFX

    @property
    def dmg(self):
        return int(self.base_dmg * (1.6 if self.enraged else 1.0))

    def take_damage(self, dmg):
        self.hp         -= dmg
        self.hurt_flash  = 10
        self.shake_t     = 8
        if self.hp <= 0:
            self.hp    = 0
            self.alive = False
        elif not self.enraged and not getattr(self, '_pending_enrage', False) and self.hp / self.max_hp < self.ENRAGE_THRESHOLD:
            self._pending_enrage = True   # game loop reads this and starts the anim
        # Vexara phase 2: split at 50% HP (primary only, once)
        if (self.pattern == "spiral" and not self.is_vex_clone
                and not self.vex_split_done
                and self.hp / self.max_hp <= 0.5 and self.alive):
            self.vex_split_done = True
            self._trigger_vex_split = True   # flag picked up by game loop
        # Malachar: spin attack every time HP crosses a new 10% threshold downward
        if self.pattern == "charge" and self.alive and self.mal_spin_timer == 0:
            cur_pct = int(self.hp / self.max_hp * 5)   # 4→0 as HP drops (triggers at 80%, 60%, 40%, 20%)
            if cur_pct < self.mal_last_spin_pct:
                self.mal_last_spin_pct = cur_pct
                self.mal_spin_timer    = 60
                self.mal_spin_ang      = 0.0
                self._trigger_mal_spin = True

    def _fire(self, projectiles, dx, dy, dmg_mult=1.0, size=None, spd=0.12, rng=500):
        mag = math.hypot(dx, dy) or 1
        dx /= mag; dy /= mag
        projectiles.append(
            Projectile(self.x, self.y, dx, dy,
                       int(self.dmg * dmg_mult), spd, rng,
                       self.proj_col, size or 10, owner="enemy"))

    def update(self, player, projectiles, world_bounds):
        if not self.alive:
            return

        dx   = player.x - self.x
        dy   = player.y - self.y
        dist = math.hypot(dx, dy)

        # Shake decay
        if self.shake_t > 0:
            self.shake_t -= 1
            self.shake_x  = random.randint(-4, 4) if self.shake_t > 0 else 0
            self.shake_y  = random.randint(-4, 4) if self.shake_t > 0 else 0

        speed_mult = 1.5 if self.enraged else 1.0
        cd_mult    = 0.6 if self.enraged else 1.0   # faster attacks when enraged

        # ── Pattern: charge ────────────────────────────────────────────────────
        if self.pattern == "charge":
            # ── Spin attack (triggered at each 20% HP threshold) ──────────────
            if self.mal_spin_timer > 0:
                self.mal_spin_timer -= 1
                if self.mal_spin_timer % 4 == 0:
                    bullet_count = 8 if self.enraged else 6
                    steps        = 60 // 4
                    step_idx     = (60 - self.mal_spin_timer) // 4
                    self.mal_spin_ang = (step_idx / steps) * math.pi * 2
                    for bi in range(bullet_count):
                        a = self.mal_spin_ang + (math.pi * 2 / bullet_count) * bi
                        spd_s = 0.28 if self.enraged else 0.22
                        projectiles.append(
                            Projectile(self.x, self.y,
                                       math.cos(a), math.sin(a),
                                       int(self.dmg * 0.8), spd_s, 900,
                                       (255, 120, 30), 11, owner="enemy"))
                return

            if self.charge_timer > 0:
                self.x += self.charge_vx
                self.y += self.charge_vy
                self.charge_timer -= 1
            else:
                # Normal approach
                if dist > self.size + 60:
                    self.x += dx / dist * self.speed * speed_mult
                    self.y += dy / dist * self.speed * speed_mult
                self.atk_cd -= 1
                if self.atk_cd <= 0:
                    self.atk_cd = int(110 * cd_mult)
                    if self.enraged:
                        # Enraged: 5-way cone, faster cooldown, faster bullets
                        self.atk_cd = int(65 * cd_mult)
                        for off in [-0.45, -0.22, 0, 0.22, 0.45]:
                            ang = math.atan2(dy, dx) + off
                            projectiles.append(
                                Projectile(self.x, self.y, math.cos(ang), math.sin(ang),
                                           int(self.dmg), 0.34, 1400,
                                           self.proj_col, 10, owner="enemy"))
                    else:
                        # Normal: 3-way cone
                        for off in [-0.25, 0, 0.25]:
                            ang = math.atan2(dy, dx) + off
                            projectiles.append(
                                Projectile(self.x, self.y, math.cos(ang), math.sin(ang),
                                           int(self.dmg), 0.30, 1400,
                                           self.proj_col, 10, owner="enemy"))
                    # Charge — slightly shorter in enraged mode to avoid overrunning the player
                    mag2           = dist or 1
                    dash_mult      = 6.5 if self.enraged else 8.0
                    self.charge_vx = dx / mag2 * self.speed * dash_mult
                    self.charge_vy = dy / mag2 * self.speed * dash_mult
                    self.charge_timer = 24

        # ── Pattern: spiral ───────────────────────────────────────────────────
        elif self.pattern == "spiral":
            # Pulse proj_col between purple and pink for Vexara
            self.vex_pulse_t += 1
            pulse = math.sin(self.vex_pulse_t * 0.07) * 0.5 + 0.5  # 0..1
            self.proj_col = lerp_color((180, 0, 255), (255, 80, 200), pulse)

            if dist > self.size + 100:
                self.x += dx / dist * self.speed * speed_mult
                self.y += dy / dist * self.speed * speed_mult
            self.atk_cd -= 1
            if self.atk_cd <= 0:
                # Phase 2: both primary and clone are always enraged-style (6 arms)
                phase2 = self.vex_split_done or self.is_vex_clone
                self.atk_cd = int((6 if phase2 else 8) * cd_mult)
                arms = 6 if (self.enraged or phase2) else 4
                for arm in range(arms):
                    ang = self.spiral_ang + (math.pi * 2 / arms) * arm
                    self._fire(projectiles, math.cos(ang), math.sin(ang), size=8, spd=0.19)
                self.spiral_ang += 0.22

            # ── Vexara teleport (phase 1 only: not enraged, not clone) ────────
            if not self.enraged and not self.is_vex_clone and not self.vex_split_done:
                # Warning phase — flash before teleporting
                if self.vex_tp_warning > 0:
                    self.vex_tp_warning -= 1
                    if self.vex_tp_warning == 0:
                        # Execute teleport — jump to within 160–260px of player
                        self.vex_tp_old_x = self.x
                        self.vex_tp_old_y = self.y
                        tp_ang = random.uniform(0, math.pi * 2)
                        tp_r   = random.randint(160, 260)
                        nx = player.x + math.cos(tp_ang) * tp_r
                        ny = player.y + math.sin(tp_ang) * tp_r
                        self.x = max(self.size, min(world_bounds[0] - self.size, nx))
                        self.y = max(self.size, min(world_bounds[1] - self.size, ny))
                        self.vex_tp_flash = 20
                        self.vex_tp_cd    = random.randint(300, 480)
                        SOUNDS.play("mage_blink", volume_scale=0.9)
                elif self.vex_tp_cd > 0:
                    self.vex_tp_cd -= 1
                else:
                    # Start warning phase
                    self.vex_tp_warning = 40

            # Decay post-blink flash
            if self.vex_tp_flash > 0:
                self.vex_tp_flash -= 1

        # ── Pattern: burst (Gorvak) ───────────────────────────────────────────
        elif self.pattern == "burst":
            # Remove dead minions
            self.minions = [m for m in self.minions if m.alive]

            # Movement — enraged moves noticeably faster
            move_spd = self.speed * speed_mult * (1.6 if self.enraged else 1.0)
            if dist > self.size + 80:
                self.x += dx / dist * move_spd
                self.y += dy / dist * move_spd

            # Summon cooldown
            if self.summon_cd > 0:
                self.summon_cd -= 1
            if self.summon_cd == 0 and len(self.minions) < 6:
                ang = random.uniform(0, math.pi * 2)
                r   = random.randint(100, 220)
                mx_ = max(40, min(world_bounds[0] - 40, self.x + math.cos(ang) * r))
                my_ = max(40, min(world_bounds[1] - 40, self.y + math.sin(ang) * r))
                self.minions.append(GorvakMinion(mx_, my_, player.level, self))
                self.summon_cd = random.randint(
                    160 if not self.enraged else 100,
                    240 if not self.enraged else 160)

            # Update minions
            for m in self.minions:
                m.update(player, projectiles, world_bounds)

            # Main burst attack — more frequent when enraged
            self.atk_cd -= 1
            if self.atk_cd <= 0:
                self.atk_cd = int((180 if not self.enraged else 130) * cd_mult)
                count = 16 if not self.enraged else 24
                for i in range(count):
                    ang = (math.pi * 2 / count) * i
                    self._fire(projectiles, math.cos(ang), math.sin(ang),
                               dmg_mult=0.85, size=12)

        # ── Pattern: orbit (Seraphix) ─────────────────────────────────────────
        elif self.pattern == "orbit":
            # ── Dash movement ─────────────────────────────────────────────────
            if self.seraph_dash_timer > 0:
                self.x += self.seraph_dash_vx
                self.y += self.seraph_dash_vy
                self.seraph_dash_timer -= 1
            else:
                # Normal orbit movement around the player
                orbit_r = 200
                self.orbit_ang += 0.020 * self.orbit_dir * speed_mult
                target_x = player.x + math.cos(self.orbit_ang) * orbit_r
                target_y = player.y + math.sin(self.orbit_ang) * orbit_r
                ox = target_x - self.x; oy = target_y - self.y
                omag = math.hypot(ox, oy) or 1
                self.x += ox / omag * self.speed * speed_mult * 2.2
                self.y += oy / omag * self.speed * speed_mult * 2.2

                # Dash cooldown
                if self.seraph_dash_cd > 0:
                    self.seraph_dash_cd -= 1
                if self.seraph_dash_cd == 0:
                    if self.enraged:
                        # Enraged: dash straight at player
                        mag2 = dist or 1
                        self.seraph_dash_vx = dx / mag2 * self.speed * 7.0
                        self.seraph_dash_vy = dy / mag2 * self.speed * 7.0
                    else:
                        # Normal: dash perpendicular (sideways) in current orbit direction
                        perp_x = -math.sin(self.orbit_ang) * self.orbit_dir
                        perp_y =  math.cos(self.orbit_ang) * self.orbit_dir
                        self.seraph_dash_vx = perp_x * self.speed * 6.0
                        self.seraph_dash_vy = perp_y * self.speed * 6.0
                    self.seraph_dash_timer = 16
                    self.seraph_dash_cd    = random.randint(
                        int((140 if self.enraged else 75) * cd_mult),
                        int((200 if self.enraged else 120) * cd_mult))

            # ── Shooting ──────────────────────────────────────────────────────
            self.atk_cd -= 1
            if self.atk_cd <= 0:
                self.atk_cd = int(38 * cd_mult)
                # Base: 3-way fan toward player
                for off in (-0.28, 0, 0.28):
                    ang = math.atan2(dy, dx) + off
                    self._fire(projectiles, math.cos(ang), math.sin(ang),
                               size=10, spd=0.20, rng=1400)
                if self.enraged:
                    # Enraged: 5-way fan
                    for off in (-0.5, -0.25, 0.25, 0.5):
                        ang = math.atan2(dy, dx) + off
                        self._fire(projectiles, math.cos(ang), math.sin(ang),
                                   size=9, spd=0.20, rng=1400)

        # ── Pattern: homing ───────────────────────────────────────────────────
        elif self.pattern == "homing":
            self.nyx_pulse_t += 1

            if not self.enraged:
                # Phase 1: slowly approaches player
                if dist > self.size + 120:
                    self.x += dx / dist * self.speed * speed_mult
                    self.y += dy / dist * self.speed * speed_mult
            else:
                # Phase 2: flees from player — moves in the OPPOSITE direction (slower)
                if dist < 400:
                    self.x -= dx / dist * self.speed * speed_mult * 0.9
                    self.y -= dy / dist * self.speed * speed_mult * 0.9

            # ── Homing bullets / cone shots ───────────────────────────────────
            self.nyx_homing_cd -= 1
            if self.nyx_homing_cd <= 0:
                self.nyx_homing_cd = int((100 if self.enraged else 140) * cd_mult)
                if self.enraged:
                    # Enraged: 4-way cone aimed at player, no homing
                    base_ang = math.atan2(dy, dx)
                    for off in (-0.45, -0.15, 0.15, 0.45):
                        ang = base_ang + off
                        projectiles.append(
                            Projectile(self.x, self.y,
                                       math.cos(ang), math.sin(ang),
                                       self.dmg, 0.22, 600,
                                       self.proj_col, 10, owner="enemy"))
                else:
                    # Normal: 1 homing bullet from each of the 4 cardinal sides
                    for side_ang in (0, math.pi / 2, math.pi, 3 * math.pi / 2):
                        ox = math.cos(side_ang) * self.size
                        oy = math.sin(side_ang) * self.size
                        fire_dx = dx / (dist or 1) + math.cos(side_ang) * 0.2
                        fire_dy = dy / (dist or 1) + math.sin(side_ang) * 0.2
                        mag = math.hypot(fire_dx, fire_dy) or 1
                        h = HomingProjectile(
                            self.x + ox, self.y + oy,
                            fire_dx / mag, fire_dy / mag,
                            self.dmg, self.proj_col, player)
                        h.turn_rate = 0.09
                        projectiles.append(h)

            # ── Firebombs ──────────────────────────────────────────────────────
            self.nyx_bomb_cd -= 1
            if self.nyx_bomb_cd <= 0:
                self.nyx_bomb_cd = int((100 if self.enraged else 200) * cd_mult)
                if self.enraged:
                    # Enraged: bombs fall close to the player (80–180px)
                    count = random.randint(2, 4)
                    for _ in range(count):
                        bx = player.x + random.uniform(-180, 180)
                        by = player.y + random.uniform(-180, 180)
                        bx = max(60, min(world_bounds[0] - 60, bx))
                        by = max(60, min(world_bounds[1] - 60, by))
                        self.fire_orbs_pending = getattr(self, 'fire_orbs_pending', [])
                        self.fire_orbs_pending.append(
                            NyxFireBomb(bx, by, int(self.dmg * 0.3), falling=True))
                else:
                    # Normal: drop bomb near the player but at a safe distance (250–400px)
                    ang = random.uniform(0, math.pi * 2)
                    r   = random.randint(250, 400)
                    bx  = player.x + math.cos(ang) * r
                    by  = player.y + math.sin(ang) * r
                    bx  = max(60, min(world_bounds[0] - 60, bx))
                    by  = max(60, min(world_bounds[1] - 60, by))
                    self.fire_orbs_pending = getattr(self, 'fire_orbs_pending', [])
                    self.fire_orbs_pending.append(
                        NyxFireBomb(bx, by, int(self.dmg * 0.3), falling=False))

        # Clamp to world
        self.x = max(self.size, min(world_bounds[0] - self.size, self.x))
        self.y = max(self.size, min(world_bounds[1] - self.size, self.y))

        # Melee contact
        if dist < self.size + 18:
            player.take_damage(self.dmg)

        if self.hurt_flash > 0:
            self.hurt_flash -= 1

    def draw(self, surf, cam):
        if not self.alive:
            return
        sx = int(self.x - cam[0]) + self.shake_x
        sy = int(self.y - cam[1]) + self.shake_y

        # Enrage aura
        if self.enraged:
            aura_r = int(self.size * 1.6 + math.sin(pygame.time.get_ticks() * 0.008) * 6)
            for ai in range(4):
                ar = aura_r - ai * 4
                if ar > 0:
                    ac = (max(0, self.color[0] - ai * 15),
                          max(0, self.color[1] - ai * 15),
                          max(0, self.color[2] - ai * 15))
                    pygame.draw.circle(surf, ac, (sx, sy), ar, 2)

        # Shadow
        pygame.draw.ellipse(surf, (10, 10, 20),
                            (sx - self.size, sy + self.size - 6,
                             self.size * 2, int(self.size * 0.5)))

        # Define flash here so all pattern-specific body blocks can use it
        flash    = self.hurt_flash % 4 < 2 and self.hurt_flash > 0
        body_col = WHITE if flash else self.color

        # ── Nyxoth black hole visual ──────────────────────────────────────────
        if self.pattern == "homing":
            t_nyx   = pygame.time.get_ticks()
            np_t    = math.sin(t_nyx * 0.003) * 0.5 + 0.5   # 0..1 slow pulse
            spin_a  = t_nyx * 0.0015   # slow rotation

            # Accretion disc — two counter-rotating ellipses of dots (no per-dot alloc)
            for ring_idx in range(2):
                ring_r   = self.size + 18 + ring_idx * 14
                dots     = 10 + ring_idx * 4
                spin_dir = 1 if ring_idx == 0 else -1
                ring_ang = spin_a * spin_dir * (1 + ring_idx * 0.4)
                disc_col = lerp_color((80, 0, 180), (200, 80, 255), np_t)
                # Faded outer halo: draw a slightly larger circle in a darker colour
                halo_col = (max(0,disc_col[0]//3), 0, max(0,disc_col[2]//3))
                for di in range(dots):
                    a    = ring_ang + (math.pi * 2 / dots) * di
                    drx  = sx + int(math.cos(a) * ring_r)
                    dry  = sy + int(math.sin(a) * ring_r * 0.35)
                    dr   = max(2, 4 - ring_idx)
                    pygame.draw.circle(surf, halo_col, (drx, dry), dr + 2)   # halo — solid dark
                    pygame.draw.circle(surf, disc_col,  (drx, dry), dr)       # bright core

            # Event horizon glow — draw concentric circles, no surface alloc
            for gi in range(3):
                hr = self.size + 8 + int(np_t * 6) - gi * 2
                if hr > 0:
                    hc = max(0, int((120 + np_t * 60) * (1 - gi * 0.3)))
                    pygame.draw.circle(surf, (20, 0, max(0, 60 - gi * 10)), (sx, sy), hr, max(1, 3 - gi))

            # Body — pure black void with a faint purple edge
            pygame.draw.circle(surf, (0, 0, 0), (sx, sy), self.size)
            pygame.draw.circle(surf, lerp_color((40, 0, 100), (120, 0, 200), np_t),
                               (sx, sy), self.size, 3)
            pygame.draw.circle(surf, lerp_color((100, 0, 200), (220, 100, 255), np_t),
                               (sx, sy), int(self.size * 0.88), 2)

            if self.enraged:
                # Crackling energy rings — direct draw, no per-ring surface
                for ri in range(3):
                    er_r = self.size + 4 + ri * 8 + int(math.sin(t_nyx * 0.01 + ri) * 4)
                    ec   = max(0, int(60 + ri * 20))
                    ring_c = (max(0, min(255, 180 - ri * 20)), max(0, min(255, 60 - ri * 10)), 255)
                    pygame.draw.circle(surf, ring_c, (sx, sy), er_r, 2)

            # Skip the standard body draw — we drew it manually above
            _skip_standard_body = True
        elif self.pattern == "charge":
            # ── Malachar lava body ────────────────────────────────────────────
            t_mal   = pygame.time.get_ticks()
            self.mal_pulse_t += 1
            lp      = math.sin(self.mal_pulse_t * 0.06) * 0.5 + 0.5   # 0..1

            # Outer heat glow — draw concentric circles, no alloc
            glow_r   = self.size + 10 + int(lp * 8)
            glow_col = lerp_color((180, 40, 0), (255, 120, 0), lp)
            for gi in range(4):
                gr = glow_r - gi * 3
                if gr > self.size:
                    gc = (max(0, glow_col[0] - gi * 20),
                          max(0, glow_col[1] - gi * 8), 0)
                    pygame.draw.circle(surf, gc, (sx, sy), gr, 2)

            # Dark crust body
            crust_col = lerp_color((40, 10, 0), (80, 20, 0), lp)
            pygame.draw.circle(surf, crust_col, (sx, sy), self.size)

            # Lava cracks — rotating lines of bright orange on the surface
            for ci in range(6):
                crack_ang = t_mal * 0.0008 + ci * (math.pi / 3)
                crack_len = int(self.size * 0.7 + math.sin(t_mal * 0.004 + ci) * self.size * 0.2)
                cx1 = sx + int(math.cos(crack_ang) * 4)
                cy1 = sy + int(math.sin(crack_ang) * 4)
                cx2 = sx + int(math.cos(crack_ang) * crack_len)
                cy2 = sy + int(math.sin(crack_ang) * crack_len)
                crack_col = lerp_color((200, 60, 0), (255, 200, 40), lp)
                pygame.draw.line(surf, crack_col, (cx1, cy1), (cx2, cy2), 2)

            # Bright molten core
            core_r = int(self.size * 0.4 + lp * self.size * 0.15)
            core_col = lerp_color((255, 120, 0), (255, 240, 80), lp)
            pygame.draw.circle(surf, core_col, (sx, sy), core_r)

            # Spawn visual fire particles (non-damaging)
            if self.mal_pulse_t % 3 == 0:
                for _ in range(3):
                    ang_p = random.uniform(0, math.pi * 2)
                    spd_p = random.uniform(1.2, 3.0)
                    self.mal_fire_particles.append([
                        float(sx), float(sy),
                        math.cos(ang_p) * spd_p,
                        math.sin(ang_p) * spd_p - random.uniform(0.5, 2.0),
                        random.randint(12, 28),   # life
                        random.randint(12, 28),   # max_life
                        random.randint(3, 7),     # size
                    ])

            # Update + draw fire particles — direct draw, no per-particle surface
            alive_parts = []
            for fp in self.mal_fire_particles:
                fp[0] += fp[2]; fp[1] += fp[3]
                fp[3] += 0.08
                fp[4] -= 1
                if fp[4] > 0:
                    alive_parts.append(fp)
                    ft  = fp[4] / fp[5]
                    fsz = max(1, int(fp[6] * ft))
                    if ft > 0.6:
                        fc = lerp_color((255, 200, 40), (255, 100, 0), 1 - (ft - 0.6) / 0.4)
                    elif ft > 0.25:
                        fc = lerp_color((255, 100, 0), (180, 30, 0), 1 - (ft - 0.25) / 0.35)
                    else:
                        fc = lerp_color((180, 30, 0), (60, 15, 0), 1 - ft / 0.25)
                    # Draw solid dot — fade approximated by darkening the colour
                    pygame.draw.circle(surf, (int(fc[0]), int(fc[1]), int(fc[2])),
                                       (int(fp[0]), int(fp[1])), fsz)
            self.mal_fire_particles = alive_parts

            if flash:
                pygame.draw.circle(surf, WHITE, (sx, sy), self.size)

            _skip_standard_body = True
        else:
            _skip_standard_body = False

        # Standard body (skipped for Nyxoth and Malachar which draw their own)
        if not _skip_standard_body:
            pygame.draw.circle(surf, body_col, (sx, sy), self.size)

        # ── Gorvak Ironhide knight armour ─────────────────────────────────────
        if self.pattern == "burst":
            t_g   = pygame.time.get_ticks()
            pulse = math.sin(t_g * 0.004) * 0.5 + 0.5
            sz    = self.size

            # Armour glow — enraged turns it red/orange
            if self.enraged:
                aura_c = lerp_color((120, 20, 0), (200, 60, 0), pulse)
            else:
                aura_c = lerp_color((40, 50, 60), (70, 80, 90), pulse)
            for ai in range(3):
                ar = sz + 6 + ai * 5
                pygame.draw.circle(surf, aura_c, (sx, sy), ar, 2)

            # Pauldrons (shoulder guards) — two arcs left/right
            for side in (-1, 1):
                px3 = sx + side * int(sz * 0.82)
                pygame.draw.circle(surf, (55, 62, 70), (px3, sy - 4), int(sz * 0.38))
                pygame.draw.circle(surf, (80, 90, 100), (px3, sy - 4), int(sz * 0.38), 2)
                # Rivets on shoulder
                for ri in range(3):
                    ra  = (ri - 1) * 0.35 * side
                    rrx = px3 + int(math.cos(ra) * int(sz * 0.22))
                    rry = sy - 4 + int(math.sin(ra) * int(sz * 0.22))
                    pygame.draw.circle(surf, (100, 110, 120), (rrx, rry), 2)

            # Chestplate body — darker steel over the base circle
            pygame.draw.circle(surf, (50, 58, 66), (sx, sy), int(sz * 0.88))
            # Vertical chest ridge
            pygame.draw.line(surf, (90, 100, 112), (sx, sy - int(sz * 0.7)), (sx, sy + int(sz * 0.6)), 3)
            # Horizontal belt line
            pygame.draw.line(surf, (70, 78, 88), (sx - int(sz * 0.7), sy + int(sz * 0.2)),
                             (sx + int(sz * 0.7), sy + int(sz * 0.2)), 2)

            # Helmet visor — top of body
            helm_r = int(sz * 0.62)
            pygame.draw.arc(surf, (65, 74, 84),
                            (sx - helm_r, sy - helm_r, helm_r * 2, helm_r * 2),
                            math.pi * 0.1, math.pi * 0.9, int(sz * 0.22))
            # Visor slit
            visor_y = sy - int(sz * 0.28)
            visor_col = (255, 80, 20) if self.enraged else (80, 180, 80)
            pygame.draw.rect(surf, visor_col,
                             (sx - int(sz * 0.32), visor_y - 3, int(sz * 0.64), 6),
                             border_radius=3)
            # Glowing eyes through visor
            for ex2_off in (-int(sz * 0.14), int(sz * 0.14)):
                pygame.draw.circle(surf, visor_col, (sx + ex2_off, visor_y), 3)

            # Steel rim around body
            rim_col = lerp_color((80, 90, 100), (130, 145, 160), pulse)
            pygame.draw.circle(surf, rim_col, (sx, sy), sz, 3)

        # ── Seraphix wings (continued) ────────────────────────────────────────
        if self.pattern == "orbit":
            t = pygame.time.get_ticks() // 16
            flap      = math.sin(t * 0.08)
            flap_ang  = flap * 0.22
            glow_tick = math.sin(t * 0.12) * 0.5 + 0.5

            # Scale quills relative to boss size (self.size ≈ 44 for Seraphix)
            sz = self.size
            quill_offsets = [-0.60, -0.30, 0.0, 0.30, 0.58]
            quill_lengths = [sz + 52, sz + 66, sz + 78, sz + 66, sz + 50]
            quill_widths  = [5, 7, 9, 7, 5]
            wing_col_mid  = (255, 230, 120)   # golden-white to match Seraphix colour
            wing_col_base = (180, 120, 20)    # dark gold shadow

            for side in (-1, 1):
                base   = math.pi if side == -1 else 0.0
                root_x = sx + int(math.cos(base) * (sz - 6))
                root_y = sy

                for qi, (qoff, qlen, qw) in enumerate(zip(quill_offsets, quill_lengths, quill_widths)):
                    flap_bias = (2 - qi) * 0.10
                    ang   = base + (qoff + flap_ang * flap_bias) * side
                    tip_x = sx + int(math.cos(ang) * qlen)
                    tip_y = sy + int(math.sin(ang) * qlen) - int(flap * 12)

                    # Outer shadow quill
                    pygame.draw.line(surf, wing_col_base,
                                     (root_x, root_y), (tip_x, tip_y), qw + 3)
                    # Bright mid quill
                    pygame.draw.line(surf, wing_col_mid,
                                     (root_x, root_y), (tip_x, tip_y), qw)
                    # White highlight on centre quill
                    if qi == 2:
                        pygame.draw.line(surf, (255, 255, 220),
                                         (root_x, root_y), (tip_x, tip_y), 3)

                    # Feather barbs
                    steps = 6
                    for bi in range(1, steps):
                        frac     = bi / steps
                        bx       = int(root_x + (tip_x - root_x) * frac)
                        by       = int(root_y + (tip_y - root_y) * frac)
                        barb_len = int((1 - frac) * 16 + 4)
                        perp     = ang + math.pi / 2
                        for bsign in (-1, 1):
                            ex_b = bx + int(math.cos(perp) * barb_len * bsign)
                            ey_b = by + int(math.sin(perp) * barb_len * bsign)
                            barb_a = int(160 * (1 - frac))
                            pygame.draw.line(surf, (*wing_col_mid, barb_a),
                                             (bx, by), (ex_b, ey_b), 1)

                # Glowing tip dot on central quill
                tip_ang = base + (quill_offsets[2] + flap_ang) * side
                rim_x   = sx + int(math.cos(tip_ang) * (quill_lengths[2] + 6))
                rim_y   = sy + int(math.sin(tip_ang) * quill_lengths[2]) - int(flap * 12)
                pygame.draw.circle(surf, (255, 245, 180), (rim_x, rim_y), 4)

            # Golden shimmer sparkles
            rng_state = t // 8
            for sp in range(8):
                spark_seed = int(rng_state * 19 + sp * 37) & 0xFFFF
                spark_r    = (spark_seed % 36) + sz
                spark_ang  = (spark_seed % 628) / 100.0
                spark_side = 1 if sp % 2 == 0 else -1
                spx = sx + spark_side * int(math.cos(spark_ang) * spark_r)
                spy = sy + int(math.sin(spark_ang) * spark_r * 0.55)
                spark_alpha = ((t + sp * 7) % 30)
                if spark_alpha < 15:
                    sc = min(255, 180 + spark_alpha * 5)
                    pygame.draw.circle(surf, (sc, sc, 80), (spx, spy), 2)

        # ── Vexara spinning hex ring visual ──────────────────────────────────
        if self.pattern == "spiral":
            t     = pygame.time.get_ticks()
            pulse = math.sin(t * 0.004) * 0.5 + 0.5   # 0..1, slow cycle
            ring_col  = lerp_color((180, 0, 255), (255, 80, 200), pulse)
            ring_col2 = lerp_color((255, 80, 200), (120, 0, 180), pulse)
            spin  = t * 0.002   # rotation angle
            spin2 = t * 0.003 + math.pi / 6   # counter-rotating second ring

            # ── Teleport warning: pulsing concentric rings — no alloc ────────
            if self.vex_tp_warning > 0:
                warn_t = self.vex_tp_warning / 40.0   # 1→0
                for ri in range(3):
                    ring_r = int(self.size + 12 + ri * 14 + (1 - warn_t) * 20)
                    bri    = max(0, int(180 * warn_t * (1 - ri * 0.25)))
                    if bri > 8:
                        wc = lerp_color((255, 80, 200), (255, 255, 255), 1 - warn_t)
                        wc_dim = (max(0, min(255, int(wc[0] * bri / 180))),
                                  max(0, min(255, int(wc[1] * bri / 180))),
                                  max(0, min(255, int(wc[2] * bri / 180))))
                        pygame.draw.circle(surf, wc_dim, (sx, sy), ring_r,
                                           max(1, int(3 * warn_t)))
                # Body flickers — override body colour to flash white/purple
                flicker = int((1 - warn_t) * 6)
                if flicker % 2 == 0:
                    pygame.draw.circle(surf, (220, 160, 255), (sx, sy), self.size)
                else:
                    pygame.draw.circle(surf, self.color, (sx, sy), self.size)

            # ── Post-blink arrival flash: expanding ring at new position ──────
            if self.vex_tp_flash > 0:
                flash_t = self.vex_tp_flash / 20.0   # 1→0
                arr_r   = int(self.size + (1 - flash_t) * 60)
                arr_c   = (min(255, int(255 * flash_t)), min(255, int(180 * flash_t)),
                           min(255, int(255 * flash_t)))
                for ri in range(3):
                    if arr_r - ri > 0:
                        pygame.draw.circle(surf, arr_c, (sx, sy), arr_r - ri, 1)

                if not GAME_SETTINGS.low:
                    # Afterimage at old position — solid faded circle
                    old_sx = int(self.vex_tp_old_x - cam[0])
                    old_sy = int(self.vex_tp_old_y - cam[1])
                    aft_c  = (min(255, int(self.color[0] * flash_t)),
                              min(255, int(self.color[1] * flash_t)),
                              min(255, int(self.color[2] * flash_t)))
                    after_r = self.size + 4
                    pygame.draw.circle(surf, aft_c, (old_sx, old_sy), after_r)
                    pygame.draw.circle(surf, (min(255, int(200 * flash_t)),) * 3,
                                       (old_sx, old_sy), after_r, 2)

            if not GAME_SETTINGS.low:
                # Outer spinning hex dots — solid draws with a halo approximated by a larger dim circle
                for i in range(6):
                    a = spin + (math.pi * 2 / 6) * i
                    rx = sx + int(math.cos(a) * (self.size + 10))
                    ry = sy + int(math.sin(a) * (self.size + 10))
                    dot_r = int(4 + math.sin(t * 0.006 + i) * 2)
                    halo_c = (max(0, ring_col[0] // 3), 0, max(0, ring_col[2] // 3))
                    pygame.draw.circle(surf, halo_c,  (rx, ry), dot_r + 3)
                    pygame.draw.circle(surf, ring_col, (rx, ry), dot_r)

                # Inner counter-rotating triangle dots
                for i in range(3):
                    a = spin2 + (math.pi * 2 / 3) * i
                    rx = sx + int(math.cos(a) * int(self.size * 0.6))
                    ry = sy + int(math.sin(a) * int(self.size * 0.6))
                    pygame.draw.circle(surf, ring_col2, (rx, ry), 3)

                # Pulsing outer glow ring — solid concentric rings, no Surface alloc
                glow_r = self.size + 6 + int(pulse * 8)
                for gi in range(3):
                    gr = glow_r - gi
                    if gr > 0:
                        ga = max(0, int((40 + pulse * 40) * (1 - gi * 0.35)))
                        gc = (max(0, ring_col[0] - gi * 15), 0, max(0, ring_col[2] - gi * 15))
                        if ga > 6:
                            pygame.draw.circle(surf, gc, (sx, sy), gr, 2)
            else:
                # Low quality: single pulsing border ring instead of all dots/halos
                pygame.draw.circle(surf, ring_col, (sx, sy), self.size + 4, 2)

        # Inner ring + enraged pulse + outline (skipped for Nyxoth)
        if not _skip_standard_body:
            inner_col = lerp_color(self.color, WHITE, 0.4)
            pygame.draw.circle(surf, inner_col, (sx, sy), int(self.size * 0.55))
            if self.enraged:
                pulse_r = int(self.size * 0.3 + math.sin(pygame.time.get_ticks() * 0.012) * 4)
                pygame.draw.circle(surf, WHITE, (sx, sy), pulse_r)
            pygame.draw.circle(surf, WHITE, (sx, sy), self.size, 3)
            pygame.draw.circle(surf, self.color, (sx, sy), self.size + 4, 2)

        # Name plate above boss — font cached on first use
        if not hasattr(self, '_name_font'):
            self._name_font = _make_font(13, bold=True)
        font_s  = self._name_font
        name_s  = font_s.render(self.name, True, WHITE)
        title_s = font_s.render(f"[ {self.title} ]", True, self.color)
        nx = sx - name_s.get_width() // 2
        ny = sy - self.size - 32

        bg_w = max(name_s.get_width(), title_s.get_width()) + 12
        bg = pygame.Surface((bg_w, 34), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 160))
        surf.blit(bg, (sx - bg_w // 2, ny - 2))
        surf.blit(title_s, (sx - title_s.get_width() // 2, ny))
        surf.blit(name_s,  (sx - name_s.get_width()  // 2, ny + 16))

        # Wide HP bar
        bw = self.size * 3
        draw_bar(surf, sx - bw // 2, sy - self.size - 44, bw, 8,
                 self.hp, self.max_hp,
                 (220, 40, 40) if self.enraged else (200, 60, 200))

# ── Gorvak Minion ─────────────────────────────────────────────────────────────

class GorvakMinion:
    """
    A unique minion summoned by Gorvak Ironhide.
    Behaviour: teleports periodically near the boss (normal) or near the player
    (enraged), then fires a 3-way cone shot toward the player.
    """
    COLOR     = (100, 200, 100)
    SIZE      = 18
    MAX_HP    = 120   # scaled by player level on creation

    def __init__(self, x, y, player_level, boss):
        self.x = float(x); self.y = float(y)
        scale      = 1 + (player_level - 1) * 0.10
        self.max_hp = int(self.MAX_HP * scale)
        self.hp     = self.max_hp
        self.dmg    = int(14 * scale)
        self.boss   = boss   # reference for teleport anchor
        self.alive  = True
        self.hurt_flash  = 0
        # Enraged-phase spawns get a longer initial delay so they don't immediately
        # fire the moment they appear next to the player
        if boss.enraged:
            self.shoot_cd = random.randint(90, 140)
        else:
            self.shoot_cd = random.randint(30, 70)
        self.teleport_cd = random.randint(80, 150)
        # Blink flash (visual telegraph before teleport)
        self.blink_flash  = 0
        self.BLINK_WARNING = 20   # frames of telegraph before teleport fires

    def update(self, player, projectiles, world_bounds):
        if not self.alive:
            return

        if self.hurt_flash  > 0: self.hurt_flash  -= 1

        # ── Shoot ─────────────────────────────────────────────────────────────
        dx = player.x - self.x; dy = player.y - self.y
        dist = math.hypot(dx, dy) or 1
        if self.shoot_cd > 0:
            self.shoot_cd -= 1
        if self.shoot_cd == 0 and dist < 450:
            base_a = math.atan2(dy, dx)
            for off in (-0.28, 0, 0.28):
                a = base_a + off
                projectiles.append(Projectile(
                    self.x, self.y, math.cos(a), math.sin(a),
                    self.dmg, 0.14, 420,
                    (120, 220, 120), 7, owner="enemy"))
            self.shoot_cd = 70 if not self.boss.enraged else 45

        # ── Teleport ──────────────────────────────────────────────────────────
        if self.blink_flash > 0:
            self.blink_flash -= 1
            if self.blink_flash == 0:
                # Actually teleport
                if self.boss.enraged:
                    # Closer to player
                    ang = random.uniform(0, math.pi * 2)
                    r   = random.randint(120, 220)
                    nx  = player.x + math.cos(ang) * r
                    ny  = player.y + math.sin(ang) * r
                else:
                    # Near boss
                    ang = random.uniform(0, math.pi * 2)
                    r   = random.randint(80, 200)
                    nx  = self.boss.x + math.cos(ang) * r
                    ny  = self.boss.y + math.sin(ang) * r
                self.x = max(self.SIZE, min(world_bounds[0] - self.SIZE, nx))
                self.y = max(self.SIZE, min(world_bounds[1] - self.SIZE, ny))
                self.teleport_cd = random.randint(
                    60 if self.boss.enraged else 90,
                    120 if self.boss.enraged else 160)
                # After an enraged teleport (which lands near the player) give a
                # brief shoot delay so the arrival isn't an instant hit
                if self.boss.enraged:
                    self.shoot_cd = max(self.shoot_cd, random.randint(50, 75))
        else:
            if self.teleport_cd > 0:
                self.teleport_cd -= 1
            if self.teleport_cd == 0:
                self.blink_flash = self.BLINK_WARNING

    def take_damage(self, dmg):
        self.hp -= dmg
        self.hurt_flash = 8
        if self.hp <= 0:
            self.alive = False

    def draw(self, surf, cam):
        if not self.alive:
            return
        sx = int(self.x - cam[0]); sy = int(self.y - cam[1])
        sz = self.SIZE

        # Telegraph blink before teleport
        if self.blink_flash > 0:
            t = self.blink_flash / self.BLINK_WARNING
            warn_r = sz + 10 + int((1 - t) * 20)
            # Iron-grey warning ring
            warn_col = (max(0, min(255, int(140 * t))),
                        max(0, min(255, int(160 * t))),
                        max(0, min(255, int(180 * t))))
            pygame.draw.circle(surf, warn_col, (sx, sy), warn_r, 2)

        # Shadow
        pygame.draw.ellipse(surf, (8, 6, 5),
                            (sx - sz, sy + sz - 4, sz * 2, 8))

        hurt = self.hurt_flash % 4 < 2 and self.hurt_flash > 0
        if hurt:
            pygame.draw.circle(surf, WHITE, (sx, sy), sz)
            pygame.draw.circle(surf, WHITE, (sx, sy), sz, 2)
        else:
            # Mini knight armour
            # Body plate
            pygame.draw.circle(surf, (48, 55, 62), (sx, sy), sz)

            # Mini pauldrons
            for side in (-1, 1):
                mpx = sx + side * int(sz * 0.8)
                pygame.draw.circle(surf, (55, 62, 70), (mpx, sy - 2), int(sz * 0.35))
                pygame.draw.circle(surf, (75, 85, 95), (mpx, sy - 2), int(sz * 0.35), 1)

            # Chest ridge
            pygame.draw.line(surf, (85, 95, 108), (sx, sy - int(sz * 0.7)), (sx, sy + int(sz * 0.55)), 2)

            # Mini helmet top arc
            hm_r = int(sz * 0.6)
            pygame.draw.arc(surf, (62, 70, 80),
                            (sx - hm_r, sy - hm_r, hm_r * 2, hm_r * 2),
                            math.pi * 0.15, math.pi * 0.85, int(sz * 0.22))

            # Visor glow — green normally, red if boss enraged
            visor_col = (220, 60, 10) if self.boss.enraged else (60, 200, 80)
            visor_y   = sy - int(sz * 0.25)
            pygame.draw.rect(surf, visor_col,
                             (sx - int(sz * 0.3), visor_y - 2, int(sz * 0.6), 4),
                             border_radius=2)

            # Steel rim
            pygame.draw.circle(surf, (80, 90, 102), (sx, sy), sz, 2)

        # HP bar
        bw = sz * 2 + 8
        draw_bar(surf, sx - bw // 2, sy - sz - 10, bw, 4,
                 self.hp, self.max_hp, (80, 200, 80))


# ── Boss Intro Cinematic ──────────────────────────────────────────────────────

class BossIntro:
    """
    6-second boss spawn cinematic.
    Phases (all in frames at 60 fps):
      0-30   : fast dim — world darkens
      30-60  : boss materialises (scale 0 → 1) + first shockwave ring
      60-120 : 3 staggered shockwave pulses + ground crack lines
      120-240: name card slams down, holds
      240-300: fade out — darkness lifts, boss becomes active
    Total: 300 frames = 5 seconds
    """
    TOTAL   = 300
    DARK_IN = 30
    DARK_OUT_START = 240

    def __init__(self, boss, fonts):
        self.boss   = boss
        self.fonts  = fonts
        self.frame  = 0
        self.done   = False

        # Pre-generate crack lines radiating from boss centre
        self.cracks = []
        for _ in range(14):
            ang    = random.uniform(0, math.pi * 2)
            length = random.randint(60, 180)
            segs   = random.randint(3, 6)
            pts    = [(boss.x, boss.y)]
            cx, cy = boss.x, boss.y
            for s in range(segs):
                cx += math.cos(ang + random.uniform(-0.4, 0.4)) * (length / segs)
                cy += math.sin(ang + random.uniform(-0.4, 0.4)) * (length / segs)
                pts.append((cx, cy))
            self.cracks.append(pts)

        self.rings = [
            (30,  boss.color),
            (60,  WHITE),
            (90,  boss.color),
            (115, (255, 255, 100)),
        ]

        # Pre-allocate reusable surfaces — allocated once, reused every frame
        self._dark   = pygame.Surface((SW, SH), pygame.SRCALPHA)
        self._shared = pygame.Surface((SW, SH), pygame.SRCALPHA)  # cracks + rings
        # Pre-render the name card (it never changes content, only alpha/position)
        name_s  = fonts["huge"].render(boss.name,  True, WHITE)
        title_s = fonts["large"].render(f"[ {boss.title} ]", True, boss.color)
        cw = max(name_s.get_width(), title_s.get_width()) + 60
        ch = 110
        self._card      = pygame.Surface((cw, ch), pygame.SRCALPHA)
        self._card_cw   = cw
        self._card_ch   = ch
        self._name_s    = name_s
        self._title_s   = title_s

    def update(self):
        self.frame += 1
        if self.frame == 30:
            SOUNDS.play("boss_spawn")
        if self.frame >= self.TOTAL:
            self.done = True

    @property
    def active(self):
        return not self.done

    def draw(self, surf, cam, hud_draw_fn):
        f   = self.frame
        bsx = int(self.boss.x - cam[0])
        bsy = int(self.boss.y - cam[1])

        # ── Low quality: simple dark overlay + name card only ─────────────────
        if GAME_SETTINGS.low:
            dark_alpha = 160 if f < self.DARK_OUT_START else max(
                0, int(160 * (1 - (f - self.DARK_OUT_START) /
                              (self.TOTAL - self.DARK_OUT_START))))
            self._dark.fill((0, 0, 0, dark_alpha))
            surf.blit(self._dark, (0, 0))
            if f >= 30:
                scale_t  = min(1.0, (f - 30) / 60)
                cur_size = max(1, int(self.boss.size * scale_t))
                pygame.draw.circle(surf, self.boss.color, (bsx, bsy), cur_size)
                pygame.draw.circle(surf, WHITE, (bsx, bsy), cur_size, 2)
            if 120 <= f < self.DARK_OUT_START:
                self._draw_card(surf, f)
            hud_draw_fn()
            return

        # ── High quality (full cinematic) ─────────────────────────────────────
        if f < self.DARK_IN:
            dark_alpha = int(150 * f / self.DARK_IN)
        elif f < self.DARK_OUT_START:
            dark_alpha = 150
        else:
            t = (f - self.DARK_OUT_START) / (self.TOTAL - self.DARK_OUT_START)
            dark_alpha = int(150 * (1 - t))

        # Dark overlay — reuse pre-allocated surface
        self._dark.fill((0, 0, 0, dark_alpha))
        surf.blit(self._dark, (0, 0))

        # ── Shockwave rings — direct draw, no per-ring surface ────────────────
        for start, col in self.rings:
            age = f - start
            if age < 0 or age > 50:
                continue
            t     = age / 50
            r     = int(20 + t * 320)
            width = max(2, int(8 * (1 - t)))
            # Approximate alpha fade by blending colour toward black
            fade  = 1 - t
            rc = (max(0, int(col[0] * fade)),
                  max(0, int(col[1] * fade)),
                  max(0, int(col[2] * fade)))
            if r > 0:
                pygame.draw.circle(surf, rc, (bsx, bsy), r, width)

        # ── Ground crack lines — all batched onto one shared surface ──────────
        if 30 <= f:
            crack_t    = min(1.0, (f - 30) / 40)
            fade_t     = 1.0 if f < self.DARK_OUT_START else max(
                0.0, 1 - (f - self.DARK_OUT_START) / (self.TOTAL - self.DARK_OUT_START))
            alpha      = int(200 * fade_t)
            crack_col  = lerp_color(self.boss.color, (255, 200, 50), 0.4)
            draw_col   = (max(0, int(crack_col[0] * fade_t)),
                          max(0, int(crack_col[1] * fade_t)),
                          max(0, int(crack_col[2] * fade_t)))
            if alpha > 4:
                self._shared.fill((0, 0, 0, 0))
                for pts in self.cracks:
                    visible    = max(2, int(len(pts) * crack_t))
                    screen_pts = [(int(px - cam[0]), int(py - cam[1]))
                                  for px, py in pts[:visible]]
                    if len(screen_pts) >= 2:
                        pygame.draw.lines(self._shared, (*draw_col, alpha),
                                          False, screen_pts, 2)
                surf.blit(self._shared, (0, 0))

        # ── Boss materialise — direct draws, no glow surface ─────────────────
        if 30 <= f <= 90:
            scale_t  = (f - 30) / 60
            cur_size = int(self.boss.size * scale_t)
            if cur_size > 0:
                pulse  = int(30 * math.sin(f * 0.3))
                glow_r = max(1, cur_size + 20 + pulse)
                # Glow: concentric rings instead of alpha surface
                for gi in range(4):
                    gr = glow_r - gi * 4
                    if gr > cur_size:
                        gc = (max(0, self.boss.color[0] - gi * 20),
                              max(0, self.boss.color[1] - gi * 20),
                              max(0, self.boss.color[2] - gi * 20))
                        pygame.draw.circle(surf, gc, (bsx, bsy), gr, 2)
                pygame.draw.circle(surf, self.boss.color, (bsx, bsy), cur_size)
                pygame.draw.circle(surf, WHITE,            (bsx, bsy), cur_size, 3)
        elif f > 90:
            pulse      = int(12 * math.sin(f * 0.15))
            glow_r     = max(1, self.boss.size + 14 + pulse)
            fade_alpha = 90
            if f >= self.DARK_OUT_START:
                fade_alpha = max(0, int(90 * (1 - (f - self.DARK_OUT_START) /
                                              (self.TOTAL - self.DARK_OUT_START))))
            if fade_alpha > 8:
                for gi in range(3):
                    gr = glow_r - gi * 5
                    if gr > 0:
                        gc = (max(0, self.boss.color[0] - gi * 25),
                              max(0, self.boss.color[1] - gi * 25),
                              max(0, self.boss.color[2] - gi * 25))
                        pygame.draw.circle(surf, gc, (bsx, bsy), gr, 2)

        # ── Name card ─────────────────────────────────────────────────────────
        if 120 <= f < self.DARK_OUT_START:
            self._draw_card(surf, f)

        hud_draw_fn()

    def _draw_card(self, surf, f):
        card_age = f - 120
        slam_t   = min(1.0, card_age / 8)
        card_y   = int(SH // 2 - 80 + (1 - slam_t) * (-160))
        alpha    = min(255, card_age * 20)
        cw, ch   = self._card_cw, self._card_ch
        # Refill cached card surface directly — no copy needed
        self._card.fill((0, 0, 0, min(200, alpha)))
        pygame.draw.rect(self._card, (*self.boss.color, min(220, alpha)),
                         (0, 0, cw, ch), 3, border_radius=12)
        surf.blit(self._card, (SW // 2 - cw // 2, card_y))
        self._name_s.set_alpha(alpha)
        self._title_s.set_alpha(alpha)
        surf.blit(self._name_s,  (SW // 2 - self._name_s.get_width()  // 2, card_y + 18))
        surf.blit(self._title_s, (SW // 2 - self._title_s.get_width() // 2, card_y + 66))


# ── Boss Enrage Animation ─────────────────────────────────────────────────────

class BossEnrageAnim:
    """
    ~3-second cinematic that plays when a boss crosses the enrage threshold.
    During this window the boss is frozen and invulnerable.

    Phases (frames at 60 fps):
      0–20  : world darkens quickly
      20–80 : expanding shockwave rings + screen shake + cracks appear
      60–140: "ENRAGED!" card slams in and holds
      120–160: boss flashes white rapidly, shifts toward enrage tint
      160–180: darkness fades out, boss resumes
    Total: 180 frames = 3 seconds
    """
    TOTAL          = 180
    DARK_IN        = 20
    DARK_OUT_START = 160

    def __init__(self, boss, fonts):
        self.boss  = boss
        self.fonts = fonts
        self.frame = 0
        self.done  = False

        # Enrage tint — boss colour pushed toward angry red
        self.enrage_col = lerp_color(boss.color, (255, 40, 40), 0.65)

        # Pre-generate crack lines radiating from boss
        self.cracks = []
        for _ in range(10):
            ang   = random.uniform(0, math.pi * 2)
            pts   = [(float(boss.x), float(boss.y))]
            cx2, cy2 = float(boss.x), float(boss.y)
            segs  = random.randint(3, 5)
            seg_l = random.randint(40, 140) / segs
            for _ in range(segs):
                ang  += random.uniform(-0.5, 0.5)
                cx2  += math.cos(ang) * seg_l
                cy2  += math.sin(ang) * seg_l
                pts.append((cx2, cy2))
            self.cracks.append(pts)

        # Shockwave ring start frames
        self.rings = [(20, self.enrage_col), (36, WHITE),
                      (52, self.enrage_col), (68, (255, 255, 80))]

        # Reusable surfaces
        self._dark   = pygame.Surface((SW, SH), pygame.SRCALPHA)
        self._shared = pygame.Surface((SW, SH), pygame.SRCALPHA)

        # Pre-render text card (content never changes, only alpha/y)
        enrage_txt = fonts["huge"].render("ENRAGED!", True, (255, 60, 60))
        name_txt   = fonts["large"].render(boss.name, True, self.enrage_col)
        cw = max(enrage_txt.get_width(), name_txt.get_width()) + 60
        ch = 110
        self._card       = pygame.Surface((cw, ch), pygame.SRCALPHA)
        self._card_cw    = cw
        self._card_ch    = ch
        self._enrage_txt = enrage_txt
        self._name_txt   = name_txt

        # Screen-shake offsets (updated each frame)
        self._shake_x = 0
        self._shake_y = 0

    def update(self):
        self.frame += 1
        if 20 <= self.frame <= 80:
            intensity     = max(0, int(8 * (1 - (self.frame - 20) / 60)))
            self._shake_x = random.randint(-intensity, intensity)
            self._shake_y = random.randint(-intensity, intensity)
        else:
            self._shake_x = 0
            self._shake_y = 0
        if self.frame >= self.TOTAL:
            self.done = True

    @property
    def active(self):
        return not self.done

    def draw(self, surf, cam, hud_draw_fn):
        f  = self.frame
        bx = int(self.boss.x - cam[0]) + self._shake_x
        by = int(self.boss.y - cam[1]) + self._shake_y

        # ── Darkness ──────────────────────────────────────────────────────────
        if f < self.DARK_IN:
            dark_alpha = int(170 * f / self.DARK_IN)
        elif f < self.DARK_OUT_START:
            dark_alpha = 170
        else:
            t          = (f - self.DARK_OUT_START) / (self.TOTAL - self.DARK_OUT_START)
            dark_alpha = int(170 * (1 - t))
        self._dark.fill((0, 0, 0, dark_alpha))
        surf.blit(self._dark, (0, 0))

        # ── Low quality: skip rings, cracks, glow — just body + card ─────────
        if GAME_SETTINGS.low:
            if f >= 20:
                if 120 <= f < 160:
                    body_col = WHITE if (f // 4) % 2 == 0 else self.enrage_col
                else:
                    fade_in  = min(1.0, (f - 20) / 40)
                    body_col = lerp_color(self.boss.color, self.enrage_col, fade_in)
                pygame.draw.circle(surf, body_col, (bx, by), self.boss.size)
                pygame.draw.circle(surf, WHITE,    (bx, by), self.boss.size, 3)
            if 60 <= f < self.DARK_OUT_START:
                card_age = f - 60
                slam_t   = min(1.0, card_age / 8)
                card_y   = int(SH // 2 - 80 + (1 - slam_t) * (-160))
                alpha    = min(255, card_age * 20)
                cw, ch   = self._card_cw, self._card_ch
                self._card.fill((0, 0, 0, min(200, alpha)))
                pygame.draw.rect(self._card,
                                 (*self.enrage_col, min(220, alpha)),
                                 (0, 0, cw, ch), 3, border_radius=12)
                surf.blit(self._card, (SW // 2 - cw // 2, card_y))
                self._enrage_txt.set_alpha(alpha)
                self._name_txt.set_alpha(alpha)
                surf.blit(self._enrage_txt,
                          (SW // 2 - self._enrage_txt.get_width() // 2, card_y + 14))
                surf.blit(self._name_txt,
                          (SW // 2 - self._name_txt.get_width()  // 2, card_y + 66))
            hud_draw_fn()
            return
        self._dark.fill((0, 0, 0, dark_alpha))
        surf.blit(self._dark, (0, 0))

        # ── Shockwave rings ────────────────────────────────────────────────────
        for start, col in self.rings:
            age = f - start
            if age < 0 or age > 60:
                continue
            t     = age / 60
            r     = int(10 + t * 420)
            width = max(2, int(10 * (1 - t)))
            fade  = 1 - t
            rc    = (max(0, int(col[0] * fade)),
                     max(0, int(col[1] * fade)),
                     max(0, int(col[2] * fade)))
            if r > 0:
                pygame.draw.circle(surf, rc, (bx, by), r, width)

        # ── Ground cracks ──────────────────────────────────────────────────────
        if f >= 20:
            crack_t = min(1.0, (f - 20) / 30)
            fade_t  = (1.0 if f < self.DARK_OUT_START else
                       max(0.0, 1 - (f - self.DARK_OUT_START) /
                                    (self.TOTAL - self.DARK_OUT_START)))
            alpha   = int(220 * fade_t)
            if alpha > 4:
                self._shared.fill((0, 0, 0, 0))
                for pts in self.cracks:
                    visible    = max(2, int(len(pts) * crack_t))
                    screen_pts = [(int(px - cam[0]) + self._shake_x,
                                   int(py - cam[1]) + self._shake_y)
                                  for px, py in pts[:visible]]
                    if len(screen_pts) >= 2:
                        pygame.draw.lines(self._shared,
                                          (*self.enrage_col, alpha),
                                          False, screen_pts, 2)
                surf.blit(self._shared, (0, 0))

        # ── Boss body ──────────────────────────────────────────────────────────
        if f >= 20:
            if 120 <= f < 160:
                # Rapid white flash
                body_col = WHITE if (f // 4) % 2 == 0 else self.enrage_col
            else:
                fade_in  = min(1.0, (f - 20) / 40)
                body_col = lerp_color(self.boss.color, self.enrage_col, fade_in)

            # Pulsing glow rings
            pulse  = math.sin(f * 0.25) * 0.5 + 0.5
            glow_r = self.boss.size + int(10 + pulse * 20)
            for gi in range(4):
                gr = glow_r - gi * 5
                if gr > self.boss.size:
                    gc2 = (max(0, self.enrage_col[0] - gi * 25),
                           max(0, self.enrage_col[1] - gi * 25),
                           max(0, self.enrage_col[2] - gi * 25))
                    pygame.draw.circle(surf, gc2, (bx, by), gr, 2)
            pygame.draw.circle(surf, body_col, (bx, by), self.boss.size)
            pygame.draw.circle(surf, WHITE,    (bx, by), self.boss.size, 3)

        # ── Text card ──────────────────────────────────────────────────────────
        if 60 <= f < self.DARK_OUT_START:
            card_age = f - 60
            slam_t   = min(1.0, card_age / 8)
            card_y   = int(SH // 2 - 80 + (1 - slam_t) * (-160))
            alpha    = min(255, card_age * 20)
            cw, ch   = self._card_cw, self._card_ch
            self._card.fill((0, 0, 0, min(200, alpha)))
            pygame.draw.rect(self._card,
                             (*self.enrage_col, min(220, alpha)),
                             (0, 0, cw, ch), 3, border_radius=12)
            surf.blit(self._card, (SW // 2 - cw // 2, card_y))
            self._enrage_txt.set_alpha(alpha)
            self._name_txt.set_alpha(alpha)
            surf.blit(self._enrage_txt,
                      (SW // 2 - self._enrage_txt.get_width() // 2, card_y + 14))
            surf.blit(self._name_txt,
                      (SW // 2 - self._name_txt.get_width()  // 2, card_y + 66))

        hud_draw_fn()


def draw_token_coin(surf, cx, cy, r=8):
    """Draw a small gold token coin icon centred at (cx, cy) with radius r."""
    pygame.draw.circle(surf, (200, 150, 0),  (cx, cy), r)
    pygame.draw.circle(surf, (255, 200, 40), (cx, cy), r - 1)
    pygame.draw.circle(surf, (255, 230, 100),(cx, cy), max(1, r - 3))
    pygame.draw.circle(surf, (180, 130, 0),  (cx, cy), r, 1)


def _draw_cosmetic_preview(surf, pattern, preview_col, cx, cy, r):
    """Draw a small preview circle of a cosmetic pattern."""
    pygame.draw.circle(surf, preview_col, (cx, cy), r)
    if pattern == "fire":
        pygame.draw.circle(surf, (255, 160, 20), (cx, cy), r - 4)
        pygame.draw.circle(surf, (255, 240, 80), (cx, cy - 2), max(2, r - 8))
    elif pattern == "frost":
        for i in range(6):
            a = math.pi / 3 * i
            pygame.draw.line(surf, (200, 240, 255),
                             (cx, cy),
                             (cx + int(math.cos(a) * (r - 2)),
                              cy + int(math.sin(a) * (r - 2))), 1)
        pygame.draw.circle(surf, (200, 240, 255), (cx, cy), r // 2)
    elif pattern == "void":
        pygame.draw.circle(surf, (140, 0, 220), (cx, cy), r - 4, 2)
        pygame.draw.circle(surf, (60, 0, 100), (cx, cy), r // 3)
    elif pattern == "gold":
        pygame.draw.circle(surf, (255, 215, 0), (cx, cy), r, 3)
        pygame.draw.circle(surf, (255, 240, 100), (cx, cy), r // 2)
    elif pattern == "storm":
        for i in range(4):
            a = math.pi / 2 * i
            lx = cx + int(math.cos(a) * (r - 3))
            ly = cy + int(math.sin(a) * (r - 3))
            pygame.draw.circle(surf, (200, 230, 255), (lx, ly), 2)
        pygame.draw.circle(surf, (150, 200, 255), (cx, cy), r // 3)
    elif pattern == "wings":
        pygame.draw.circle(surf, (160, 130, 210), (cx, cy), r)
        pygame.draw.circle(surf, (220, 200, 255), (cx, cy), r // 2)
        for side in (-1, 1):
            base = math.pi if side == -1 else 0.0
            root_x = cx + side * (r - 2)
            for qi, (qoff, qlen) in enumerate(zip([-0.4, 0.0, 0.4],
                                                   [r + 8, r + 11, r + 8])):
                ang = base + qoff * side
                tip_x = cx + int(math.cos(ang) * qlen)
                tip_y = cy + int(math.sin(ang) * qlen)
                pygame.draw.line(surf, (100, 60, 180), (root_x, cy), (tip_x, tip_y), 3)
                pygame.draw.line(surf, (210, 185, 255), (root_x, cy), (tip_x, tip_y), 1)
    elif pattern == "blackhole":
        for i in range(8):
            a   = (math.pi * 2 / 8) * i
            drx = cx + int(math.cos(a) * (r + 4))
            dry = cy + int(math.sin(a) * (r + 4) * 0.38)
            pygame.draw.circle(surf, (140, 40, 220), (drx, dry), 2)
        pygame.draw.circle(surf, (0, 0, 0), (cx, cy), r)
        pygame.draw.circle(surf, (100, 0, 180), (cx, cy), r, 2)
        pygame.draw.circle(surf, (180, 60, 255), (cx, cy), int(r * 0.82), 1)
    elif pattern == "hexweaver":
        # 6 orbiting projectile dots
        for i in range(6):
            a   = (math.pi * 2 / 6) * i
            ox2 = cx + int(math.cos(a) * (r + 6))
            oy2 = cy + int(math.sin(a) * (r + 6))
            pygame.draw.circle(surf, (200, 80, 255), (ox2, oy2), 3)
        pygame.draw.circle(surf, (80, 0, 160), (cx, cy), r)
        # Hex outline dots
        for i in range(6):
            ha  = (math.pi / 3) * i
            hx2 = cx + int(math.cos(ha) * (r - 3))
            hy2 = cy + int(math.sin(ha) * (r - 3))
            pygame.draw.circle(surf, (220, 80, 255), (hx2, hy2), 2)
        pygame.draw.circle(surf, (180, 0, 255), (cx, cy), r // 2, 2)
    elif pattern == "lavalord":
        # Orbit ring as ellipse dots
        orb_pr = r + 5
        for i in range(12):
            a   = (math.pi * 2 / 12) * i
            drx = cx + int(math.cos(a) * orb_pr)
            dry = cy + int(math.sin(a) * orb_pr * 0.42)
            pygame.draw.circle(surf, (255, 140, 20), (drx, dry), 2)
        pygame.draw.circle(surf, (50, 12, 0), (cx, cy), r)
        # 6 crack lines
        for i in range(6):
            ca  = (math.pi / 3) * i
            ex2 = cx + int(math.cos(ca) * (r - 2))
            ey2 = cy + int(math.sin(ca) * (r - 2))
            pygame.draw.line(surf, (255, 160, 20), (cx, cy), (ex2, ey2), 1)
        pygame.draw.circle(surf, (255, 180, 40), (cx, cy), r // 3)
    elif pattern == "ironhide":
        # Steel armour plate body
        pygame.draw.circle(surf, (48, 55, 62), (cx, cy), r)
        # Mini pauldrons either side
        for side in (-1, 1):
            pygame.draw.circle(surf, (70, 80, 90),
                               (cx + side * (r - 2), cy - 2), r // 3)
            pygame.draw.circle(surf, (90, 102, 114),
                               (cx + side * (r - 2), cy - 2), r // 3, 1)
        # Chest ridge
        pygame.draw.line(surf, (90, 102, 114),
                         (cx, cy - r + 3), (cx, cy + r - 3), 1)
        # Visor slit (green)
        pygame.draw.rect(surf, (80, 180, 80),
                         (cx - r // 2, cy - r // 3 - 1, r, 3),
                         border_radius=1)
        # Orbiting amber ember dots (outer ring × 6, inner ring × 4)
        orb_r_out = r + 8
        orb_r_in  = r + 4
        for oi in range(6):
            a   = (math.pi * 2 / 6) * oi
            pygame.draw.circle(surf, (220, 120, 10),
                               (cx + int(math.cos(a) * orb_r_out),
                                cy + int(math.sin(a) * orb_r_out)), 2)
        for oi in range(4):
            a   = (math.pi * 2 / 4) * oi + math.pi / 4   # offset so they nestle between outer dots
            pygame.draw.circle(surf, (180, 80, 0),
                               (cx + int(math.cos(a) * orb_r_in),
                                cy + int(math.sin(a) * orb_r_in)), 1)
        # Steel rim
        pygame.draw.circle(surf, (95, 108, 120), (cx, cy), r, 2)
    elif pattern == "true_legend":
        # Rainbow-striped body + orbiting dots preview (static snapshot)
        for si in range(5):
            hue = (si * 72) % 360
            col = _hsv_to_rgb(hue, 0.9, 1.0)
            pygame.draw.arc(surf, col,
                            (cx - r, cy - r, r * 2, r * 2),
                            math.radians(si * 72), math.radians(si * 72 + 68), r)
        pygame.draw.circle(surf, (255, 255, 255), (cx, cy), r // 2)
        for oi in range(6):
            a   = (math.pi * 2 / 6) * oi
            hue = (oi * 60) % 360
            pygame.draw.circle(surf, _hsv_to_rgb(hue, 1.0, 1.0),
                               (cx + int(math.cos(a) * (r + 6)),
                                cy + int(math.sin(a) * (r + 6))), 3)
    elif pattern in ("case_red","case_green","case_purple","case_orange","case_pink"):
        pygame.draw.circle(surf, preview_col, (cx, cy), r)
        pygame.draw.circle(surf, tuple(min(255,v+60) for v in preview_col), (cx,cy), r//2)
    elif pattern == "case_stripes":
        for si in range(4):
            hue = si * 90
            sc = _hsv_to_rgb(hue, 0.8, 1.0)
            sw2 = r * 2 // 4
            pygame.draw.arc(surf, sc, (cx-r,cy-r,r*2,r*2), math.radians(si*90), math.radians(si*90+86), r)
    elif pattern == "case_pulse":
        pygame.draw.circle(surf, (160,40,220),(cx,cy),r)
        for ri2 in range(1, 3):
            pygame.draw.circle(surf, (200,80,255),(cx,cy), r*ri2//3, 2)
    elif pattern == "case_checker":
        for gx in range(0, r*2, r//2):
            for gy in range(0, r*2, r//2):
                col2 = (200,180,40) if (gx//( r//2)+gy//(r//2))%2==0 else (60,50,10)
                pygame.draw.rect(surf, col2, (cx-r+gx,cy-r+gy, r//2, r//2))
        pygame.draw.circle(surf, (0,0,0,0),(cx,cy),r)  # keep it readable
    elif pattern == "case_wave":
        pygame.draw.circle(surf,(20,100,200),(cx,cy),r)
        for ri2 in range(1,3):
            pygame.draw.circle(surf,(40,200,255),(cx,cy),r*ri2//3,2)
    elif pattern == "case_spiral":
        pygame.draw.circle(surf,(180,20,100),(cx,cy),r)
        for si in range(6):
            ang = si*math.pi/3
            pygame.draw.line(surf,_hsv_to_rgb(si*60,0.9,1.0),(cx,cy),(cx+int(math.cos(ang)*r),cy+int(math.sin(ang)*r)),2)
    elif pattern == "case_plasma":
        pygame.draw.circle(surf,(20,160,140),(cx,cy),r)
        pygame.draw.circle(surf,(60,255,220),(cx,cy),r//2,2)
    elif pattern == "case_nova":
        pygame.draw.circle(surf,(200,160,20),(cx,cy),r)
        for si in range(8):
            ang=si*math.pi/4
            pygame.draw.line(surf,(255,230,60),(cx,cy),(cx+int(math.cos(ang)*r),cy+int(math.sin(ang)*r)),2)
    elif pattern == "case_vortex":
        pygame.draw.circle(surf,(30,15,80),(cx,cy),r)
        pygame.draw.circle(surf,(120,40,220),(cx,cy),r//2,2)
        pygame.draw.circle(surf,(160,60,255),(cx,cy),r//4)
    elif pattern == "case_aurora":
        pygame.draw.circle(surf,(10,80,60),(cx,cy),r)
        for bi in range(4):
            hue=120+bi*15
            pygame.draw.line(surf,_hsv_to_rgb(hue,0.8,1.0),(cx-r+bi*(r//2),cy-r),(cx-r+bi*(r//2),cy+r),3)
    elif pattern == "case_infernal":
        pygame.draw.circle(surf,(255,100,10),(cx,cy),r)
        pygame.draw.circle(surf,(255,200,60),(cx,cy),r//2)
        for oi in range(5):
            ang=oi*math.pi*2/5
            pygame.draw.circle(surf,(255,120,20),(cx+int(math.cos(ang)*(r+6)),cy+int(math.sin(ang)*(r+6))),3)
    pygame.draw.circle(surf, WHITE, (cx, cy), r, 1)


# ── Shop ──────────────────────────────────────────────────────────────────────

class Shop:
    PAGE_WEAPONS  = 0
    PAGE_COSMETICS = 1
    PAGE_TITLES    = 2

    def __init__(self):
        self.open       = False
        self.selected   = 0
        self.page       = self.PAGE_WEAPONS
        self.cosm_page  = 0
        self.weap_page  = 0
        self.title_page = 0

    def toggle(self):
        self.open = not self.open
        if self.open:
            self.page       = self.PAGE_WEAPONS
            self.selected   = 0
            self.cosm_page  = 0
            self.weap_page  = 0
            self.title_page = 0

    def draw(self, surf, player, fonts):
        if not self.open:
            return
        overlay = pygame.Surface((SW, SH), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 170))
        surf.blit(overlay, (0, 0))
        pw, ph = 700, 680
        px, py = SW // 2 - pw // 2, SH // 2 - ph // 2
        pygame.draw.rect(surf, PANEL, (px, py, pw, ph), border_radius=12)
        pygame.draw.rect(surf, CYAN,  (px, py, pw, ph), 2,  border_radius=12)

        # ── Tab headers (3 tabs) ──────────────────────────────────────────────
        tab_w3 = pw // 3
        for ti, (tlabel, tcol, tpage) in enumerate([
            ("Weapons",          CYAN,            self.PAGE_WEAPONS),
            ("Cosmetics Shop",   (255, 200, 60),  self.PAGE_COSMETICS),
            ("Title Shop",       (180, 120, 255), self.PAGE_TITLES),
        ]):
            tx     = px + ti * tab_w3
            active = (tpage == self.page)
            tab_bg = lerp_color(PANEL, tcol, 0.18 if active else 0.04)
            br_l   = 10 if ti == 0 else 0
            br_r   = 10 if ti == 2 else 0
            pygame.draw.rect(surf, tab_bg, (tx, py, tab_w3, 48), border_radius=br_l)
            pygame.draw.rect(surf, tcol if active else GRAY,
                             (tx, py, tab_w3, 48), 2 if active else 1, border_radius=br_l)
            ts = fonts["small"].render(tlabel, True, tcol if active else GRAY)
            label_x = tx + tab_w3 // 2 - ts.get_width() // 2
            surf.blit(ts, (label_x, py + 16))
            icon_cx = label_x - 14; icon_cy = py + 24
            if ti == 0:
                sword_col = tcol if active else GRAY
                pygame.draw.line(surf, sword_col, (icon_cx, icon_cy - 8), (icon_cx, icon_cy + 8), 2)
                pygame.draw.line(surf, sword_col, (icon_cx - 5, icon_cy - 2), (icon_cx + 5, icon_cy - 2), 2)
                pygame.draw.circle(surf, sword_col, (icon_cx, icon_cy + 8), 3)
            elif ti == 1:
                draw_token_coin(surf, icon_cx, icon_cy, 7 if active else 6)
            else:
                # Crown icon for title shop
                tc2 = tcol if active else GRAY
                pts = [(icon_cx - 7, icon_cy + 5), (icon_cx - 7, icon_cy - 2),
                       (icon_cx - 3, icon_cy + 2), (icon_cx, icon_cy - 5),
                       (icon_cx + 3, icon_cy + 2), (icon_cx + 7, icon_cy - 2),
                       (icon_cx + 7, icon_cy + 5)]
                pygame.draw.lines(surf, tc2, False, pts, 2)

        # Divider below tabs
        pygame.draw.line(surf, CYAN, (px, py + 48), (px + pw, py + 48), 1)

        # Currency bar — gold left, token coin + count right
        surf.blit(fonts["small"].render(f"Gold: {player.gold}", True, YELLOW),
                  (px + 20, py + 56))
        draw_token_coin(surf, px + pw - 110, py + 63, 7)
        tok_s = fonts["small"].render(f"{TOKENS.total} Tokens", True, (255, 200, 60))
        surf.blit(tok_s, (px + pw - 95, py + 56))

        if self.page == self.PAGE_WEAPONS:
            self._draw_weapons(surf, player, fonts, px, py, pw, ph)
        elif self.page == self.PAGE_COSMETICS:
            self._draw_tokens(surf, player, fonts, px, py, pw, ph)
        else:
            self._draw_titles(surf, player, fonts, px, py, pw, ph)

    def _draw_weapons(self, surf, player, fonts, px, py, pw, ph):
        COR_COL   = (180, 0, 220)
        ITEMS_PER = 7
        cwc       = getattr(player, "corruption_waves_cleared", 0)

        # Build unified list: regular weapons first (idx 0-8), then special (idx 1000+)
        all_weapons = [(i, w, False) for i, w in enumerate(WEAPONS)]
        all_weapons += [(1000 + i, w, True) for i, w in enumerate(SPECIAL_WEAPONS)]

        total_pages = max(1, math.ceil(len(all_weapons) / ITEMS_PER))
        self.weap_page = max(0, min(self.weap_page, total_pages - 1))
        page_start = self.weap_page * ITEMS_PER
        page_items = all_weapons[page_start : page_start + ITEMS_PER]

        # Page indicator
        page_lbl = fonts["tiny"].render(
            f"Page {self.weap_page + 1} / {total_pages}", True, GRAY)
        surf.blit(page_lbl, (px + pw // 2 - page_lbl.get_width() // 2, py + 72))

        row_h   = 56
        rows_top = py + 90
        self._weap_rects = {}

        for slot, (widx, w, is_special) in enumerate(page_items):
            row_y      = rows_top + slot * row_h
            owned      = widx in player.owned_weapons
            equipped   = widx == player.weapon_idx
            can_afford = player.gold >= w["cost"]
            meets_lvl  = player.level >= w["req_lvl"]
            req_cw     = w.get("unlock_value", 0) if is_special else 0
            unlocked   = cwc >= req_cw
            cursor     = (widx == self.selected)

            if equipped:   bg_col = (50, 20, 60) if is_special else (40, 60, 40)
            elif cursor:   bg_col = (45, 15, 55) if is_special else (50, 50, 70)
            else:          bg_col = (30, 10, 44) if is_special else (35, 35, 52)
            row_rect = pygame.Rect(px + 16, row_y, pw - 32, row_h - 4)
            pygame.draw.rect(surf, bg_col,   row_rect, border_radius=8)
            if is_special:
                border_col = (255, 60, 220) if equipped else ((255, 150, 255) if cursor else (80, 0, 120))
            else:
                border_col = YELLOW if cursor else (CYAN if equipped else GRAY)
            pygame.draw.rect(surf, border_col, row_rect, 2 if cursor or equipped else 1, border_radius=8)

            # Colour dot (with corruption glow for special)
            dot_x = px + 40; dot_y = row_y + (row_h - 4) // 2
            pygame.draw.circle(surf, w["color"], (dot_x, dot_y), 12)
            if is_special:
                gs2 = pygame.Surface((30, 30), pygame.SRCALPHA)
                pygame.draw.circle(gs2, (180, 0, 220, 70), (15, 15), 15)
                surf.blit(gs2, (dot_x - 15, dot_y - 15))

            # Name + stats
            name_col = WHITE if (meets_lvl and unlocked) else (GRAY if not meets_lvl else (160, 80, 180))
            surf.blit(fonts["med"].render(w["name"], True, name_col), (px + 62, row_y + 4))
            surf.blit(fonts["small"].render(
                f"DMG:{w['damage']}  SPD:{w['speed']:.2f}  RNG:{w['range']}  {w.get('desc', '')}",
                True, (160, 160, 180)), (px + 62, row_y + 26))

            # Right-side: level req + action
            lvl_x = px + pw - 160
            if not unlocked and is_special:
                hl = fonts["tiny"].render(w.get("unlock_hint", ""), True, (180, 80, 220))
                surf.blit(hl, (px + pw - hl.get_width() - 16, row_y + 6))
                pt = fonts["small"].render(f"{cwc}/{req_cw}", True, (180, 80, 220))
                surf.blit(pt, (px + pw - pt.get_width() - 16, row_y + 26))
            elif equipped:
                surf.blit(fonts["small"].render("Equipped", True, (80, 220, 80) if not is_special else (180, 80, 255)),
                          (lvl_x, row_y + 14))
            elif owned:
                surf.blit(fonts["small"].render("[E] Equip", True, CYAN if not is_special else (140, 100, 255)),
                          (lvl_x, row_y + 14))
            elif not meets_lvl:
                surf.blit(fonts["small"].render(f"Req Lvl {w['req_lvl']}", True, RED), (lvl_x, row_y + 4))
                surf.blit(fonts["tiny"].render("LOCKED", True, RED), (lvl_x, row_y + 26))
            else:
                c_col = (255, 180, 255) if is_special else YELLOW
                if not can_afford: c_col = RED
                surf.blit(fonts["med"].render(f"{w['cost']}g", True, c_col), (lvl_x, row_y + 4))
                surf.blit(fonts["small"].render(f"Req Lvl {w['req_lvl']}", True, GREEN), (lvl_x - 40, row_y + 26))
                if can_afford:
                    surf.blit(fonts["small"].render("[B] Buy", True, (80, 220, 80) if not is_special else (180, 80, 255)),
                              (px + pw - 100, row_y + 26))

            self._weap_rects[slot] = (row_rect, widx, w, is_special, owned, equipped)

        # Prev / Next page buttons
        nav_y    = rows_top + ITEMS_PER * row_h + 4
        btn_w    = 110; btn_h = 30
        prev_rect = pygame.Rect(px + 16,             nav_y, btn_w, btn_h)
        next_rect = pygame.Rect(px + pw - 16 - btn_w, nav_y, btn_w, btn_h)
        for rect, label, enabled in [
            (prev_rect, "◄ Prev", self.weap_page > 0),
            (next_rect, "Next ►", self.weap_page < total_pages - 1),
        ]:
            col = CYAN if enabled else (50, 50, 70)
            bg  = lerp_color(PANEL, col, 0.15) if enabled else PANEL
            pygame.draw.rect(surf, bg,  rect, border_radius=7)
            pygame.draw.rect(surf, col, rect, 1, border_radius=7)
            lbl = fonts["small"].render(label, True, col)
            surf.blit(lbl, (rect.centerx - lbl.get_width() // 2,
                            rect.centery - lbl.get_height() // 2))
        self._weap_prev_rect = prev_rect if self.weap_page > 0 else None
        self._weap_next_rect = next_rect if self.weap_page < total_pages - 1 else None

        # Heal button
        heal_row_y = py + ph - 68
        can_heal   = player.gold >= 250 and player.hp < player.max_hp
        pygame.draw.rect(surf, (40, 60, 40) if can_heal else (40, 35, 35),
                         (px + 16, heal_row_y, pw - 32, 34), border_radius=8)
        pygame.draw.rect(surf, (80, 200, 80) if can_heal else (80, 60, 60),
                         (px + 16, heal_row_y, pw - 32, 34), 1, border_radius=8)
        pygame.draw.circle(surf, (200, 60, 60), (px + 40, heal_row_y + 17), 8)
        heal_lbl = fonts["small"].render("Heal  +10 HP  -  250g", True,
                                         (80, 220, 80) if can_heal else (100, 80, 80))
        surf.blit(heal_lbl, (px + 58, heal_row_y + heal_lbl.get_height() // 2 - 1))
        if not can_heal:
            reason = "Not enough gold" if player.gold < 1000 else "HP is full"
            rs = fonts["tiny"].render(reason, True, (120, 80, 80))
            surf.blit(rs, (px + pw - rs.get_width() - 20, heal_row_y + 10))
        else:
            surf.blit(fonts["small"].render("[H] Heal", True, (80, 220, 80)),
                      (px + pw - 120, heal_row_y + 9))
        self._heal_rect = pygame.Rect(px + 16, heal_row_y, pw - 32, 34)

        hint = fonts["small"].render(
            "[Q]Weapons [R]Cosmetics [T]Titles  |  UP/DOWN  |  ◄/► page  |  [B]buy [E]equip [H]heal  |  [TAB]close",
            True, GRAY)
        surf.blit(hint, (px + pw // 2 - hint.get_width() // 2, py + ph - 28))

    def _draw_tokens(self, surf, player, fonts, px, py, pw, ph):
        TOK_COL    = (255, 200, 60)
        ITEMS_PER  = 4
        items      = list(COSMETICS)
        total_pages = max(1, math.ceil(len(items) / ITEMS_PER))
        self.cosm_page = max(0, min(self.cosm_page, total_pages - 1))
        page_start  = self.cosm_page * ITEMS_PER
        page_items  = items[page_start : page_start + ITEMS_PER]

        # ── Title + page indicator ────────────────────────────────────────────
        surf.blit(fonts["med"].render("Cosmetics", True, TOK_COL),
                  (px + pw // 2 - fonts["med"].size("Cosmetics")[0] // 2, py + 72))
        page_lbl = fonts["tiny"].render(
            f"Page {self.cosm_page + 1} / {total_pages}", True, GRAY)
        surf.blit(page_lbl, (px + pw // 2 - page_lbl.get_width() // 2, py + 96))

        # ── Cosmetic rows ─────────────────────────────────────────────────────
        row_h      = 76
        rows_top   = py + 118

        self._cosm_rects = {}
        for slot, cosm in enumerate(page_items):
            global_i = page_start + slot
            row_y    = rows_top + slot * row_h
            owned    = cosm["id"] in player.owned_cosmetics
            equipped = cosm["id"] == player.active_cosmetic
            can_buy  = TOKENS.total >= cosm["cost"]
            req_seraph  = cosm.get("req_seraphix_kills", 0)
            req_nyx     = cosm.get("req_nyxoth_kills", 0)
            req_vex     = cosm.get("req_vexara_kills", 0)
            req_mal     = cosm.get("req_malachar_kills", 0)
            req_gorv    = cosm.get("req_gorvak_kills", 0)
            meets_kills = (TOKENS.seraphix_kills >= req_seraph and
                           TOKENS.nyxoth_kills   >= req_nyx     and
                           TOKENS.vexara_kills   >= req_vex     and
                           TOKENS.malachar_kills >= req_mal     and
                           TOKENS.gorvak_kills   >= req_gorv)
            cursor   = (global_i == self.selected if self.selected < 1000 else False)

            bg_col  = (40, 55, 40) if equipped else ((50, 45, 20) if cursor else (35, 35, 52))
            brd_col = TOK_COL if equipped else (
                (255, 230, 80) if cursor else (
                (80, 200, 80) if owned else (
                TOK_COL if (can_buy and meets_kills) else (60, 55, 30))))
            row_rect = pygame.Rect(px + 16, row_y, pw - 32, row_h - 6)
            pygame.draw.rect(surf, bg_col,  row_rect, border_radius=8)
            pygame.draw.rect(surf, brd_col, row_rect, 1, border_radius=8)

            # Preview circle
            _draw_cosmetic_preview(surf, cosm["pattern"], cosm["preview"],
                                   px + 50, row_y + (row_h - 6) // 2, 20)

            # Name + desc
            surf.blit(fonts["med"].render(cosm["name"], True, WHITE),
                      (px + 84, row_y + 10))
            surf.blit(fonts["small"].render(cosm["desc"], True, (160, 160, 180)),
                      (px + 84, row_y + 36))

            # Right-side status
            is_ach_reward = bool(cosm.get("achievement_unlock"))
            if equipped:
                surf.blit(fonts["small"].render("Equipped", True, (80, 220, 80)),
                          (px + pw - 130, row_y + 24))
            elif owned:
                surf.blit(fonts["small"].render("[E] Equip", True, CYAN),
                          (px + pw - 130, row_y + 24))
            elif is_ach_reward:
                surf.blit(fonts["small"].render("Achievement", True, (180, 120, 255)),
                          (px + pw - 150, row_y + 14))
                surf.blit(fonts["tiny"].render("reward only", True, (120, 80, 180)),
                          (px + pw - 150, row_y + 34))
            else:
                cost_col = (255, 200, 60) if (can_buy and meets_kills) else (100, 80, 40)
                draw_token_coin(surf, px + pw - 122, row_y + 18, 7)
                surf.blit(fonts["med"].render(str(cosm["cost"]), True, cost_col),
                          (px + pw - 112, row_y + 10))
                if (req_seraph > 0 or req_nyx > 0 or req_vex > 0 or req_mal > 0 or req_gorv > 0) and not meets_kills:
                    kills_col = (180, 120, 255)
                    if req_seraph > 0 and TOKENS.seraphix_kills < req_seraph:
                        hint_txt = f"Seraphix: {TOKENS.seraphix_kills}/{req_seraph}"
                    elif req_nyx > 0 and TOKENS.nyxoth_kills < req_nyx:
                        hint_txt = f"Nyxoth: {TOKENS.nyxoth_kills}/{req_nyx}"
                    elif req_vex > 0 and TOKENS.vexara_kills < req_vex:
                        hint_txt = f"Vexara: {TOKENS.vexara_kills}/{req_vex}"
                    elif req_gorv > 0 and TOKENS.gorvak_kills < req_gorv:
                        hint_txt = f"Gorvak: {TOKENS.gorvak_kills}/{req_gorv}"
                    else:
                        hint_txt = f"Malachar: {TOKENS.malachar_kills}/{req_mal}"
                    surf.blit(fonts["tiny"].render(hint_txt, True, kills_col),
                              (px + pw - 112, row_y + 36))
                elif can_buy and meets_kills:
                    surf.blit(fonts["small"].render("[B] Buy", True, (80, 220, 80)),
                              (px + pw - 112, row_y + 36))

            self._cosm_rects[slot] = (row_rect, cosm["id"], owned, equipped)

        # ── Prev / Next page buttons ──────────────────────────────────────────
        nav_y    = rows_top + ITEMS_PER * row_h + 6
        btn_w    = 110; btn_h = 32
        prev_rect = pygame.Rect(px + 16,             nav_y, btn_w, btn_h)
        next_rect = pygame.Rect(px + pw - 16 - btn_w, nav_y, btn_w, btn_h)

        for rect, label, enabled in [
            (prev_rect, "◄ Prev", self.cosm_page > 0),
            (next_rect, "Next ►", self.cosm_page < total_pages - 1),
        ]:
            col = CYAN if enabled else (50, 50, 70)
            bg  = lerp_color(PANEL, col, 0.15) if enabled else PANEL
            pygame.draw.rect(surf, bg,  rect, border_radius=7)
            pygame.draw.rect(surf, col, rect, 1, border_radius=7)
            lbl = fonts["small"].render(label, True, col)
            surf.blit(lbl, (rect.centerx - lbl.get_width() // 2,
                            rect.centery - lbl.get_height() // 2))

        # Store nav rects for click handling
        self._cosm_prev_rect = prev_rect if self.cosm_page > 0 else None
        self._cosm_next_rect = next_rect if self.cosm_page < total_pages - 1 else None

        # ── Token balance ─────────────────────────────────────────────────────
        bal_y = py + ph - 68
        pygame.draw.rect(surf, (40, 38, 20), (px + 16, bal_y, pw - 32, 36), border_radius=10)
        pygame.draw.rect(surf, TOK_COL,      (px + 16, bal_y, pw - 32, 36), 1, border_radius=10)
        bal_s = fonts["med"].render(f"Your Tokens:  {TOKENS.total}", True, TOK_COL)
        surf.blit(bal_s, (px + pw // 2 - bal_s.get_width() // 2, bal_y + 9))

        hint = fonts["small"].render(
            "[Q]Weapons [R]Cosmetics [T]Titles  |  UP/DOWN  |  ◄/► page  |  [B]buy [E]equip  |  [TAB]close", True, GRAY)
        surf.blit(hint, (px + pw // 2 - hint.get_width() // 2, py + ph - 28))

    def _draw_titles(self, surf, player, fonts, px, py, pw, ph):
        TIT_COL   = (180, 120, 255)
        ITEMS_PER = 6
        content_y = py + 80
        items     = TITLES
        total_pages = max(1, math.ceil(len(items) / ITEMS_PER))
        self.title_page = max(0, min(self.title_page, total_pages - 1))
        page_items = items[self.title_page * ITEMS_PER : (self.title_page + 1) * ITEMS_PER]

        self._title_rects = {}
        row_h  = 72; row_gap = 8
        for slot, title in enumerate(page_items):
            global_i = self.title_page * ITEMS_PER + slot
            ry       = content_y + slot * (row_h + row_gap)
            rr       = pygame.Rect(px + 20, ry, pw - 40, row_h)
            self._title_rects[slot] = (rr, title["id"])

            owned    = title["id"] in player.owned_titles
            equipped = title["id"] == player.active_title
            tc       = title["col"]
            hov      = rr.collidepoint(pygame.mouse.get_pos())
            sel      = (global_i == self.selected)

            bg = lerp_color(PANEL, tc, 0.28 if (sel or hov) else (0.14 if owned else 0.05))
            pygame.draw.rect(surf, bg, rr, border_radius=10)
            pygame.draw.rect(surf, tc if (sel or hov) else (GRAY if not owned else tc),
                             rr, 2 if (sel or equipped) else 1, border_radius=10)

            # Crown icon
            cx2 = rr.x + 30; cy2 = rr.centery
            crown_pts = [(cx2 - 10, cy2 + 7), (cx2 - 10, cy2 - 2),
                         (cx2 - 4,  cy2 + 4),  (cx2,      cy2 - 8),
                         (cx2 + 4,  cy2 + 4),  (cx2 + 10, cy2 - 2),
                         (cx2 + 10, cy2 + 7)]
            pygame.draw.lines(surf, tc if owned else GRAY, False, crown_pts, 2)
            pygame.draw.line(surf, tc if owned else GRAY,
                             (cx2 - 10, cy2 + 7), (cx2 + 10, cy2 + 7), 2)

            name_col = WHITE if owned else (90, 90, 100)
            nm = fonts["med"].render(title["name"], True, name_col)
            surf.blit(nm, (rr.x + 56, ry + 10))

            desc_col = GRAY if owned else (60, 60, 70)
            ds = fonts["small"].render(title["desc"], True, desc_col)
            surf.blit(ds, (rr.x + 56, ry + 34))

            # Cost / status
            if equipped:
                eq_s = fonts["small"].render("Equipped", True, TIT_COL)
                surf.blit(eq_s, (rr.right - eq_s.get_width() - 16, ry + 25))
            elif owned:
                eq_s = fonts["small"].render("Owned", True, (100, 200, 100))
                surf.blit(eq_s, (rr.right - eq_s.get_width() - 16, ry + 25))
            elif title["cost"] == 0:
                fr_s = fonts["small"].render("Free", True, GREEN)
                surf.blit(fr_s, (rr.right - fr_s.get_width() - 16, ry + 25))
            else:
                draw_token_coin(surf, rr.right - 50, ry + row_h // 2, 7)
                cost_s = fonts["small"].render(str(title["cost"]), True,
                                               (255, 200, 60) if TOKENS.total >= title["cost"] else RED)
                surf.blit(cost_s, (rr.right - 35, ry + row_h // 2 - cost_s.get_height() // 2))

        # Pagination
        if total_pages > 1:
            nav_y = py + ph - 68
            self._title_prev_rect = pygame.Rect(px + 20, nav_y, 120, 32)
            self._title_next_rect = pygame.Rect(px + pw - 140, nav_y, 120, 32)
            for rrect, label, enabled in [
                (self._title_prev_rect, "◄ Prev", self.title_page > 0),
                (self._title_next_rect, "Next ►", self.title_page < total_pages - 1),
            ]:
                col2 = TIT_COL if enabled else GRAY
                pygame.draw.rect(surf, lerp_color(PANEL, col2, 0.15), rrect, border_radius=8)
                pygame.draw.rect(surf, col2, rrect, 1, border_radius=8)
                ls = fonts["small"].render(label, True, col2)
                surf.blit(ls, (rrect.centerx - ls.get_width() // 2,
                               rrect.centery - ls.get_height() // 2))
            pg_s = fonts["small"].render(f"{self.title_page + 1}/{total_pages}", True, GRAY)
            surf.blit(pg_s, (px + pw // 2 - pg_s.get_width() // 2, nav_y + 8))
        else:
            self._title_prev_rect = pygame.Rect(0, 0, 1, 1)
            self._title_next_rect = pygame.Rect(0, 0, 1, 1)

        bal_y = py + ph - 28
        hint  = fonts["small"].render(
            "UP/DOWN select  |  [B] buy  |  [E] equip  |  [TAB] close", True, GRAY)
        surf.blit(hint, (px + pw // 2 - hint.get_width() // 2, bal_y))

    def handle_key(self, key, player, floating_texts):
        if not self.open:
            return
        # Tab switching
        if key == pygame.K_q:
            self.page = self.PAGE_WEAPONS;   self.selected = 0; return
        if key == pygame.K_r:
            self.page = self.PAGE_COSMETICS; self.selected = 0; return
        if key == pygame.K_t:
            self.page = self.PAGE_TITLES;    self.selected = 0; return

        # ── Cosmetics page ────────────────────────────────────────────────────
        if self.page == self.PAGE_COSMETICS:
            ITEMS_PER   = 4
            items       = list(COSMETICS)
            total_pages = max(1, math.ceil(len(items) / ITEMS_PER))
            if self.selected >= len(items) or self.selected < 0:
                self.selected = 0
            if key == pygame.K_UP:
                self.selected  = (self.selected - 1) % len(items)
                self.cosm_page = self.selected // ITEMS_PER; return
            if key == pygame.K_DOWN:
                self.selected  = (self.selected + 1) % len(items)
                self.cosm_page = self.selected // ITEMS_PER; return
            if key == pygame.K_LEFT:
                self.cosm_page = max(0, self.cosm_page - 1)
                self.selected  = self.cosm_page * ITEMS_PER; return
            if key == pygame.K_RIGHT:
                self.cosm_page = min(total_pages - 1, self.cosm_page + 1)
                self.selected  = self.cosm_page * ITEMS_PER; return
            cosm  = items[self.selected]
            owned = cosm["id"] in player.owned_cosmetics
            is_ach_reward = bool(cosm.get("achievement_unlock"))
            meets_kills = (TOKENS.seraphix_kills >= cosm.get("req_seraphix_kills", 0) and
                           TOKENS.nyxoth_kills   >= cosm.get("req_nyxoth_kills", 0)   and
                           TOKENS.vexara_kills   >= cosm.get("req_vexara_kills", 0)   and
                           TOKENS.malachar_kills >= cosm.get("req_malachar_kills", 0) and
                           TOKENS.gorvak_kills   >= cosm.get("req_gorvak_kills", 0))
            if key == pygame.K_b and not owned and not is_ach_reward and meets_kills and TOKENS.spend(cosm["cost"]):
                player.owned_cosmetics.add(cosm["id"])
                player.active_cosmetic = cosm["id"]
                TOKENS.unlock_cosmetic(cosm["id"]); TOKENS.equip_cosmetic(cosm["id"])
                floating_texts.append(FloatingText(player.x, player.y - 30,
                                                    f"Unlocked {cosm['name']}!", (255, 200, 60), 20))
                self._trigger_ach_check(floating_texts)
            elif key == pygame.K_e and owned:
                player.active_cosmetic = cosm["id"]
                TOKENS.equip_cosmetic(cosm["id"])
                floating_texts.append(FloatingText(player.x, player.y - 30,
                                                    f"Equipped {cosm['name']}!", CYAN, 20))
            return

        # ── Titles page ───────────────────────────────────────────────────────
        if self.page == self.PAGE_TITLES:
            items       = TITLES
            ITEMS_PER   = 6
            total_pages = max(1, math.ceil(len(items) / ITEMS_PER))
            if self.selected >= len(items) or self.selected < 0:
                self.selected = 0
            if key == pygame.K_UP:
                self.selected   = (self.selected - 1) % len(items)
                self.title_page = self.selected // ITEMS_PER; return
            if key == pygame.K_DOWN:
                self.selected   = (self.selected + 1) % len(items)
                self.title_page = self.selected // ITEMS_PER; return
            if key == pygame.K_LEFT:
                self.title_page = max(0, self.title_page - 1)
                self.selected   = self.title_page * ITEMS_PER; return
            if key == pygame.K_RIGHT:
                self.title_page = min(total_pages - 1, self.title_page + 1)
                self.selected   = self.title_page * ITEMS_PER; return
            title = items[self.selected]
            owned = title["id"] in player.owned_titles
            if key == pygame.K_b and not owned and title["cost"] == 0:
                player.owned_titles.add(title["id"])
                TOKENS.unlock_title(title["id"])
                floating_texts.append(FloatingText(player.x, player.y - 30,
                                                    f"Unlocked: {title['name']}!", (180, 120, 255), 20))
            elif key == pygame.K_b and not owned and TOKENS.spend(title["cost"]):
                player.owned_titles.add(title["id"])
                TOKENS.unlock_title(title["id"])
                floating_texts.append(FloatingText(player.x, player.y - 30,
                                                    f"Unlocked: {title['name']}!", (180, 120, 255), 20))
            elif key == pygame.K_e and owned:
                player.active_title = title["id"]
                TOKENS.equip_title(title["id"])
                lbl = "(No Title)" if title["id"] == "none" else title["name"]
                floating_texts.append(FloatingText(player.x, player.y - 30,
                                                    f"Title: {lbl}", (180, 120, 255), 20))
            return

        # ── Weapons page ──────────────────────────────────────────────────────
        ITEMS_PER   = 7
        all_weapons = [(i, w, False) for i, w in enumerate(WEAPONS)]
        all_weapons += [(1000 + i, w, True) for i, w in enumerate(SPECIAL_WEAPONS)]
        total_pages = max(1, math.ceil(len(all_weapons) / ITEMS_PER))
        all_idxs = [widx for widx, _, _ in all_weapons]
        if self.selected not in all_idxs:
            self.selected = 0
        cur_pos = all_idxs.index(self.selected)
        if key == pygame.K_UP:
            cur_pos = (cur_pos - 1) % len(all_weapons)
            self.selected  = all_idxs[cur_pos]; self.weap_page = cur_pos // ITEMS_PER; return
        if key == pygame.K_DOWN:
            cur_pos = (cur_pos + 1) % len(all_weapons)
            self.selected  = all_idxs[cur_pos]; self.weap_page = cur_pos // ITEMS_PER; return
        if key == pygame.K_LEFT:
            self.weap_page = max(0, self.weap_page - 1)
            self.selected  = all_idxs[self.weap_page * ITEMS_PER]; return
        if key == pygame.K_RIGHT:
            self.weap_page = min(total_pages - 1, self.weap_page + 1)
            self.selected  = all_idxs[self.weap_page * ITEMS_PER]; return
        if key == pygame.K_h:
            self._do_heal(player, floating_texts); return
        widx, w, is_special = all_weapons[cur_pos]
        owned      = widx in player.owned_weapons
        equipped   = widx == player.weapon_idx
        meets_lvl  = player.level >= w["req_lvl"]
        can_afford = player.gold >= w["cost"]
        cwc2       = getattr(player, "corruption_waves_cleared", 0)
        unlocked   = cwc2 >= w.get("unlock_value", 0) if is_special else True
        if key == pygame.K_b and not owned and meets_lvl and unlocked and can_afford:
            player.gold -= w["cost"]; player.owned_weapons.append(widx); player.weapon_idx = widx
            floating_texts.append(FloatingText(player.x, player.y - 30, f"Bought {w['name']}!",
                                               (255, 80, 220) if is_special else YELLOW, 20))
        elif key == pygame.K_e and owned and not equipped and meets_lvl:
            player.weapon_idx = widx
            floating_texts.append(FloatingText(player.x, player.y - 30, f"Equipped {w['name']}!",
                                               (180, 80, 255) if is_special else CYAN, 20))

    def handle_click(self, pos, player, floating_texts):
        """Call this when a mouse click occurs while the shop is open."""
        if not self.open:
            return
        pw, ph = 700, 680
        px = SW // 2 - pw // 2; py = SH // 2 - ph // 2
        tab_w3 = pw // 3
        # Tab header clicks
        if py <= pos[1] <= py + 48:
            if px <= pos[0] < px + tab_w3:
                self.page = self.PAGE_WEAPONS;   self.selected = 0
            elif px + tab_w3 <= pos[0] < px + tab_w3 * 2:
                self.page = self.PAGE_COSMETICS; self.selected = 0
            elif px + tab_w3 * 2 <= pos[0] <= px + pw:
                self.page = self.PAGE_TITLES;    self.selected = 0
            return

        if self.page == self.PAGE_WEAPONS:
            if hasattr(self, "_heal_rect") and self._heal_rect.collidepoint(pos):
                self._do_heal(player, floating_texts); return
            if getattr(self, "_weap_prev_rect", None) and self._weap_prev_rect.collidepoint(pos):
                self.weap_page = max(0, self.weap_page - 1)
                all_idxs = list(range(len(WEAPONS))) + [1000 + i for i in range(len(SPECIAL_WEAPONS))]
                self.selected = all_idxs[self.weap_page * 7]; return
            if getattr(self, "_weap_next_rect", None) and self._weap_next_rect.collidepoint(pos):
                self.weap_page += 1
                all_idxs = list(range(len(WEAPONS))) + [1000 + i for i in range(len(SPECIAL_WEAPONS))]
                self.selected = all_idxs[min(self.weap_page * 7, len(all_idxs) - 1)]; return
            for slot, (rect, widx, w, is_special, owned, equipped) in getattr(self, "_weap_rects", {}).items():
                if rect.collidepoint(pos):
                    self.selected = widx
                    meets_lvl  = player.level >= w["req_lvl"]
                    can_afford = player.gold >= w["cost"]
                    cwc        = getattr(player, "corruption_waves_cleared", 0)
                    unlocked   = cwc >= w.get("unlock_value", 0) if is_special else True
                    if not owned and meets_lvl and unlocked and can_afford:
                        player.gold -= w["cost"]; player.owned_weapons.append(widx); player.weapon_idx = widx
                        floating_texts.append(FloatingText(player.x, player.y - 30, f"Bought {w['name']}!",
                                                           (255, 80, 220) if is_special else YELLOW, 20))
                    elif owned and not equipped and meets_lvl:
                        player.weapon_idx = widx
                        floating_texts.append(FloatingText(player.x, player.y - 30, f"Equipped {w['name']}!",
                                                           (180, 80, 255) if is_special else CYAN, 20))
                    break

        elif self.page == self.PAGE_COSMETICS:
            if getattr(self, "_cosm_prev_rect", None) and self._cosm_prev_rect.collidepoint(pos):
                self.cosm_page = max(0, self.cosm_page - 1); self.selected = self.cosm_page * 4; return
            if getattr(self, "_cosm_next_rect", None) and self._cosm_next_rect.collidepoint(pos):
                self.cosm_page += 1; self.selected = self.cosm_page * 4; return
            for slot, (rect, cosm_id, owned, equipped) in getattr(self, "_cosm_rects", {}).items():
                if rect.collidepoint(pos):
                    self.selected = self.cosm_page * 4 + slot
                    cosm = next((c for c in COSMETICS if c["id"] == cosm_id), None)
                    is_ach_reward = bool(cosm and cosm.get("achievement_unlock"))
                    meets_kills = (cosm and
                                   TOKENS.seraphix_kills >= cosm.get("req_seraphix_kills", 0) and
                                   TOKENS.nyxoth_kills   >= cosm.get("req_nyxoth_kills", 0)   and
                                   TOKENS.vexara_kills   >= cosm.get("req_vexara_kills", 0)   and
                                   TOKENS.malachar_kills >= cosm.get("req_malachar_kills", 0) and
                                   TOKENS.gorvak_kills   >= cosm.get("req_gorvak_kills", 0))
                    if cosm and not owned and not is_ach_reward and meets_kills and TOKENS.spend(cosm["cost"]):
                        player.owned_cosmetics.add(cosm_id); player.active_cosmetic = cosm_id
                        TOKENS.unlock_cosmetic(cosm_id); TOKENS.equip_cosmetic(cosm_id)
                        floating_texts.append(FloatingText(player.x, player.y - 30,
                                                           f"Unlocked {cosm['name']}!", (255, 200, 60), 20))
                        self._trigger_ach_check(floating_texts)
                    elif owned and not equipped:
                        player.active_cosmetic = cosm_id; TOKENS.equip_cosmetic(cosm_id)
                        floating_texts.append(FloatingText(player.x, player.y - 30,
                                                           f"Equipped {cosm['name']}!", CYAN, 20))
                    break

        elif self.page == self.PAGE_TITLES:
            if getattr(self, "_title_prev_rect", None) and self._title_prev_rect.collidepoint(pos):
                self.title_page = max(0, self.title_page - 1); self.selected = self.title_page * 6; return
            if getattr(self, "_title_next_rect", None) and self._title_next_rect.collidepoint(pos):
                self.title_page += 1; self.selected = self.title_page * 6; return
            for slot, (rect, title_id) in getattr(self, "_title_rects", {}).items():
                if rect.collidepoint(pos):
                    self.selected = self.title_page * 6 + slot
                    title = next((t for t in TITLES if t["id"] == title_id), None)
                    if not title:
                        break
                    owned = title_id in player.owned_titles
                    if not owned:
                        can_buy = (title["cost"] == 0) or TOKENS.spend(title["cost"])
                        if can_buy:
                            player.owned_titles.add(title_id); TOKENS.unlock_title(title_id)
                            floating_texts.append(FloatingText(player.x, player.y - 30,
                                                               f"Unlocked: {title['name']}!", (180, 120, 255), 20))
                    else:
                        player.active_title = title_id; TOKENS.equip_title(title_id)
                        lbl = "(No Title)" if title_id == "none" else title["name"]
                        floating_texts.append(FloatingText(player.x, player.y - 30,
                                                           f"Title: {lbl}", (180, 120, 255), 20))
                    break

    def _trigger_ach_check(self, floating_texts):
        """Fire achievement checks from the shop context using the live game if available."""
        import __main__
        game = getattr(__main__, "_live_game", None)
        if game is None:
            return
        new_ids = PROFILE.check_achievements(game)
        game._queue_achievement_toasts(new_ids)

    def _do_heal(self, player, floating_texts):
        if player.gold >= 250 and player.hp < player.max_hp:
            player.gold -= 250
            healed = min(10, player.max_hp - player.hp)
            player.hp  += healed
            floating_texts.append(
                FloatingText(player.x, player.y - 40, f"+{healed} HP", (80, 220, 80), 20))

# ── Perk Card Screen ──────────────────────────────────────────────────────────

class PerkScreen:
    CARD_W = 240
    CARD_H = 360
    GAP    = 40

    # Per-card hover lift animation state: target and current float offset
    _hover_offsets = [0.0, 0.0, 0.0]   # current y-lift (pixels, positive = up)
    _hover_glows   = [0.0, 0.0, 0.0]   # current glow intensity 0.0-1.0
    _entry_t       = 0.0                # 0→1, card entry animation progress

    # Unique icon symbol per perk (matches ALL_PERKS order)
    _ICONS = ["⚔", "🛡", "⚡", "❤", "💪", "💰", "🎯", "🔥", "💨"]

    def __init__(self, player, fonts):
        self.player  = player
        self.fonts   = fonts
        self.active  = False
        self.cards   = []     # list of 3 perk dicts
        self.hovered = -1
        self.chosen  = False
        self._hover_offsets = [0.0, 0.0, 0.0]
        self._hover_glows   = [0.0, 0.0, 0.0]
        self._entry_t       = 0.0
        self._tick          = 0   # frame counter for shimmer / pulse

    def offer(self):
        """Pick 3 distinct random perks and show the screen."""
        pool = ALL_PERKS[:]
        random.shuffle(pool)
        self.cards          = pool[:3]
        self.active         = True
        self.chosen         = False
        self._entry_t       = 0.0
        self._hover_offsets = [0.0, 0.0, 0.0]
        self._hover_glows   = [0.0, 0.0, 0.0]
        self._tick          = 0

    def handle_event(self, event):
        if not self.active:
            return
        if event.type == pygame.MOUSEMOTION:
            self.hovered = self._card_at(*event.pos)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            idx = self._card_at(*event.pos)
            if idx >= 0:
                self._pick(idx)
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_1, pygame.K_KP1): self._pick(0)
            if event.key in (pygame.K_2, pygame.K_KP2): self._pick(1)
            if event.key in (pygame.K_3, pygame.K_KP3): self._pick(2)

    def _pick(self, idx):
        if 0 <= idx < len(self.cards):
            perk = self.cards[idx]
            self.player.apply_perk(perk["key"], perk["bonus"])
            self.active = False
            self.chosen = True

    def _card_x(self, idx):
        total_w = self.CARD_W * 3 + self.GAP * 2
        start_x = SW // 2 - total_w // 2
        return start_x + idx * (self.CARD_W + self.GAP)

    def _card_at(self, mx, my):
        base_cy = SH // 2 - self.CARD_H // 2
        for i in range(3):
            cx  = self._card_x(i)
            cy  = base_cy - int(self._hover_offsets[i])
            if cx <= mx <= cx + self.CARD_W and cy <= my <= cy + self.CARD_H:
                return i
        return -1

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _draw_rounded_gradient(surf, rect, col_top, col_bot, radius=16):
        """Fill a rounded rect with a vertical gradient using horizontal scanlines."""
        x, y, w, h = rect
        for row in range(h):
            t   = row / max(h - 1, 1)
            cr  = int(col_top[0] + (col_bot[0] - col_top[0]) * t)
            cg  = int(col_top[1] + (col_bot[1] - col_top[1]) * t)
            cb  = int(col_top[2] + (col_bot[2] - col_top[2]) * t)
            # Clip scanline to rounded corners
            if row < radius:
                inset = radius - int(math.sqrt(max(0, radius*radius - (radius - row)**2)))
            elif row >= h - radius:
                rr = h - row - 1
                inset = radius - int(math.sqrt(max(0, radius*radius - (radius - rr)**2)))
            else:
                inset = 0
            pygame.draw.line(surf, (cr, cg, cb), (x + inset, y + row), (x + w - inset - 1, y + row))

    @staticmethod
    def _draw_outer_glow(surf, cx, cy, w, h, col, intensity, radius=20):
        """Multi-layer translucent rect halo around a card."""
        layers = 5
        for k in range(layers, 0, -1):
            expand = k * (radius // layers)
            alpha  = int(intensity * 55 * (k / layers))
            s = pygame.Surface((w + expand * 2, h + expand * 2), pygame.SRCALPHA)
            pygame.draw.rect(s, (*col, alpha),
                             (0, 0, w + expand * 2, h + expand * 2),
                             border_radius=18 + expand)
            surf.blit(s, (cx - expand, cy - expand))

    @staticmethod
    def _draw_inner_shadow(surf, cx, cy, w, h, col, radius=16):
        """Subtle inner shadow at card top to give depth."""
        s = pygame.Surface((w, 18), pygame.SRCALPHA)
        for row in range(18):
            alpha = int(80 * (1 - row / 18))
            pygame.draw.line(s, (0, 0, 0, alpha), (0, row), (w, row))
        surf.blit(s, (cx, cy + radius))

    @staticmethod
    def _draw_sparkle(surf, cx, cy, col, tick, seed):
        """Draw 4 tiny rotating sparkle crosses around the icon area."""
        rng = random.Random(seed)
        for _ in range(4):
            angle  = tick * 0.04 + rng.uniform(0, math.pi * 2)
            dist   = rng.uniform(46, 58)
            sx     = cx + int(math.cos(angle) * dist)
            sy     = cy + int(math.sin(angle) * dist)
            size   = rng.randint(2, 4)
            alpha  = int(180 + 70 * math.sin(tick * 0.07 + rng.uniform(0, 3)))
            for dx, dy in [(0, -size), (0, size), (-size, 0), (size, 0)]:
                s = pygame.Surface((2, 2), pygame.SRCALPHA)
                s.fill((*col, min(255, alpha)))
                surf.blit(s, (sx + dx, sy + dy))

    # ── main draw ─────────────────────────────────────────────────────────────

    def draw(self, surf):
        if not self.active:
            return

        # ── Low quality: use the original flat-card design (no gradients/animations) ──
        if GAME_SETTINGS.low:
            overlay = pygame.Surface((SW, SH), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 190))
            surf.blit(overlay, (0, 0))

            CW = 220; CH = 280; GAP = 30
            total_w = CW * 3 + GAP * 2
            start_x = SW // 2 - total_w // 2
            cy = SH // 2 - CH // 2

            title = self.fonts["huge"].render("CHOOSE A PERK", True, YELLOW)
            surf.blit(title, (SW // 2 - title.get_width() // 2, cy - 64))
            sub = self.fonts["small"].render("Click a card  or press  1 / 2 / 3", True, GRAY)
            surf.blit(sub, (SW // 2 - sub.get_width() // 2, cy - 28))

            for i, perk in enumerate(self.cards):
                cx      = start_x + i * (CW + GAP)
                hovered = (i == self.hovered)
                col     = perk["color"]

                bg = lerp_color(PANEL, col, 0.12 if hovered else 0.04)
                pygame.draw.rect(surf, bg, (cx, cy, CW, CH), border_radius=14)
                border_col = col if hovered else lerp_color(GRAY, col, 0.4)
                pygame.draw.rect(surf, border_col, (cx, cy, CW, CH),
                                 3 if hovered else 1, border_radius=14)

                if hovered:
                    glow_s = pygame.Surface((CW + 20, CH + 20), pygame.SRCALPHA)
                    pygame.draw.rect(glow_s, (*col, 40), (0, 0, CW + 20, CH + 20), border_radius=18)
                    surf.blit(glow_s, (cx - 10, cy - 10))

                icon_cy = cy + 72
                pygame.draw.circle(surf, lerp_color(DARK, col, 0.3), (cx + CW // 2, icon_cy), 44)
                pygame.draw.circle(surf, col, (cx + CW // 2, icon_cy), 44, 2)
                icon_ltr = self.fonts["huge"].render(perk["label"][0], True, col)
                surf.blit(icon_ltr, (cx + CW // 2 - icon_ltr.get_width() // 2,
                                     icon_cy - icon_ltr.get_height() // 2))

                name = self.fonts["large"].render(perk["label"], True, WHITE)
                surf.blit(name, (cx + CW // 2 - name.get_width() // 2, cy + 132))

                desc = self.fonts["small"].render(perk["desc"], True, col)
                surf.blit(desc, (cx + CW // 2 - desc.get_width() // 2, cy + 166))

                stacks     = int(self.player.perk(perk["key"]) / perk["bonus"] + 0.5)
                future_val = self.player.perk(perk["key"]) + perk["bonus"]
                if perk["key"] == "hp_regen":
                    future_str = f"Total: +{future_val:.1f} HP/hit"
                else:
                    future_str = f"Total: +{int(future_val * 100)}%"
                if stacks > 0:
                    st_txt = self.fonts["tiny"].render(f"Owned: {stacks}x  |  {future_str}", True, lerp_color(GRAY, col, 0.5))
                else:
                    st_txt = self.fonts["tiny"].render(future_str, True, lerp_color(GRAY, col, 0.5))
                surf.blit(st_txt, (cx + CW // 2 - st_txt.get_width() // 2, cy + 194))

                key_hint = self.fonts["med"].render(f"[{i+1}]", True, GRAY if not hovered else YELLOW)
                surf.blit(key_hint, (cx + CW // 2 - key_hint.get_width() // 2, cy + CH - 36))
            return

        self._tick += 1

        # ── Animate entry (cards drop in from above over ~20 frames) ──────────
        self._entry_t = min(1.0, self._entry_t + 0.055)
        # Ease-out cubic
        et = 1.0 - (1.0 - self._entry_t) ** 3

        # ── Animate hover lift / glow ─────────────────────────────────────────
        LIFT_TARGET = 16.0
        for i in range(3):
            want_lift = LIFT_TARGET if i == self.hovered else 0.0
            want_glow = 1.0         if i == self.hovered else 0.0
            self._hover_offsets[i] += (want_lift - self._hover_offsets[i]) * 0.18
            self._hover_glows[i]   += (want_glow - self._hover_glows[i])   * 0.18

        # ── Dimmed background overlay ─────────────────────────────────────────
        overlay = pygame.Surface((SW, SH), pygame.SRCALPHA)
        overlay.fill((0, 0, 10, 210))
        surf.blit(overlay, (0, 0))

        # ── Subtle radial vignette ────────────────────────────────────────────
        vign = pygame.Surface((SW, SH), pygame.SRCALPHA)
        cx_v, cy_v = SW // 2, SH // 2
        for radius in range(420, 0, -30):
            alpha = max(0, int(60 * (1 - radius / 420)))
            pygame.draw.circle(vign, (0, 0, 0, alpha), (cx_v, cy_v), radius)
        surf.blit(vign, (0, 0))

        # ── Title section ────────────────────────────────────────────────────
        title_y    = SH // 2 - self.CARD_H // 2 - 80
        pulse      = 0.5 + 0.5 * math.sin(self._tick * 0.05)
        title_col  = lerp_color(YELLOW, WHITE, pulse * 0.3)

        # Title glow halo
        glow_surf = pygame.Surface((500, 70), pygame.SRCALPHA)
        pygame.draw.ellipse(glow_surf, (*YELLOW, 18), (0, 0, 500, 70))
        surf.blit(glow_surf, (SW // 2 - 250, title_y - 10))

        title = self.fonts["huge"].render("✦  CHOOSE A PERK  ✦", True, title_col)
        surf.blit(title, (SW // 2 - title.get_width() // 2, title_y))

        sub = self.fonts["small"].render("Click a card  ·  or press  1 / 2 / 3", True, (130, 130, 150))
        surf.blit(sub, (SW // 2 - sub.get_width() // 2, title_y + 46))

        # ── Horizontal rule under title ───────────────────────────────────────
        rule_y = title_y + 66
        pygame.draw.line(surf, (60, 60, 80), (SW // 2 - 260, rule_y), (SW // 2 + 260, rule_y), 1)

        # ── Cards ─────────────────────────────────────────────────────────────
        base_cy = SH // 2 - self.CARD_H // 2

        for i, perk in enumerate(self.cards):
            col      = perk["color"]
            hovered  = (i == self.hovered)
            glow_int = self._hover_glows[i]

            # Entry animation: cards start 80px above final position
            entry_offset = int((1.0 - et) * (80 + i * 20))
            lift_offset  = int(self._hover_offsets[i])
            cx  = self._card_x(i)
            cy  = base_cy - lift_offset - entry_offset

            # Entry alpha fade-in (stagger by card index)
            entry_alpha_t = min(1.0, max(0.0, self._entry_t * 3.0 - i * 0.5))
            card_alpha    = int(255 * entry_alpha_t)
            if card_alpha <= 0:
                continue

            # ── Outer glow halo ───────────────────────────────────────────────
            if glow_int > 0.02:
                self._draw_outer_glow(surf, cx, cy, self.CARD_W, self.CARD_H,
                                      col, glow_int * entry_alpha_t)

            # ── Card body: dark gradient background ───────────────────────────
            card_surf = pygame.Surface((self.CARD_W, self.CARD_H), pygame.SRCALPHA)
            col_top = lerp_color((22, 22, 36), col, 0.08 + glow_int * 0.06)
            col_bot = lerp_color((12, 12, 20), col, 0.03 + glow_int * 0.03)
            self._draw_rounded_gradient(card_surf,
                                        (0, 0, self.CARD_W, self.CARD_H),
                                        col_top, col_bot, radius=16)
            card_surf.set_alpha(card_alpha)
            surf.blit(card_surf, (cx, cy))

            # ── Coloured header band ──────────────────────────────────────────
            band_h  = 96
            band_s  = pygame.Surface((self.CARD_W, band_h), pygame.SRCALPHA)
            band_top = lerp_color(col, WHITE, 0.12)
            band_bot = lerp_color(col, (10, 10, 18), 0.85)
            self._draw_rounded_gradient(band_s,
                                        (0, 0, self.CARD_W, band_h),
                                        band_top, band_bot, radius=16)
            # Clip bottom of band to straight edge
            pygame.draw.rect(band_s, (0, 0, 0, 0), (0, band_h - 4, self.CARD_W, 4))
            band_s.set_alpha(card_alpha)
            surf.blit(band_s, (cx, cy))

            # ── Inner shadow below header band ────────────────────────────────
            self._draw_inner_shadow(surf, cx, cy + band_h, self.CARD_W, 18, col)

            # ── Card border ───────────────────────────────────────────────────
            border_col   = lerp_color(lerp_color(GRAY, col, 0.5), WHITE, glow_int * 0.4)
            border_width = 2 if hovered else 1
            border_surf  = pygame.Surface((self.CARD_W, self.CARD_H), pygame.SRCALPHA)
            pygame.draw.rect(border_surf, (*border_col, card_alpha),
                             (0, 0, self.CARD_W, self.CARD_H),
                             border_width, border_radius=16)
            surf.blit(border_surf, (cx, cy))

            # ── Top-edge shine line ───────────────────────────────────────────
            shine_alpha = int((80 + glow_int * 100) * entry_alpha_t)
            shine_surf  = pygame.Surface((self.CARD_W - 40, 2), pygame.SRCALPHA)
            for px in range(self.CARD_W - 40):
                t  = px / (self.CARD_W - 40)
                fa = int(shine_alpha * math.sin(t * math.pi))
                pygame.draw.line(shine_surf, (255, 255, 255, fa), (px, 0), (px, 1))
            surf.blit(shine_surf, (cx + 20, cy + 2))

            # ── Icon badge ────────────────────────────────────────────────────
            icon_cx = cx + self.CARD_W // 2
            icon_cy = cy + 52

            # Outer ring shadow
            shadow_s = pygame.Surface((100, 100), pygame.SRCALPHA)
            pygame.draw.circle(shadow_s, (0, 0, 0, 80), (52, 54), 40)
            surf.blit(shadow_s, (icon_cx - 52, icon_cy - 52))

            # Outer ring
            ring_col = lerp_color(col, WHITE, 0.25 + glow_int * 0.25)
            pygame.draw.circle(surf, lerp_color(DARK, col, 0.5), (icon_cx, icon_cy), 38)
            pygame.draw.circle(surf, ring_col, (icon_cx, icon_cy), 38, 2)

            # Inner fill
            inner_col = lerp_color((20, 20, 30), col, 0.30 + glow_int * 0.15)
            pygame.draw.circle(surf, inner_col, (icon_cx, icon_cy), 34)

            # Sparkles on hover
            if glow_int > 0.1:
                self._draw_sparkle(surf, icon_cx, icon_cy, col, self._tick, seed=i * 999)

            # Icon letter (large, centred, bright)
            icon_bright = lerp_color(col, WHITE, 0.5 + glow_int * 0.3)
            icon_surf   = self.fonts["huge"].render(perk["label"][0], True, icon_bright)
            surf.blit(icon_surf, (icon_cx - icon_surf.get_width() // 2,
                                  icon_cy - icon_surf.get_height() // 2))

            # ── Divider ───────────────────────────────────────────────────────
            div_y = cy + band_h + 14
            div_s = pygame.Surface((self.CARD_W - 32, 1), pygame.SRCALPHA)
            for px in range(self.CARD_W - 32):
                t  = px / (self.CARD_W - 32)
                fa = int(60 * math.sin(t * math.pi))
                pygame.draw.line(div_s, (*col, fa), (px, 0), (px, 0))
            surf.blit(div_s, (cx + 16, div_y))

            # ── Perk name ─────────────────────────────────────────────────────
            name_col  = lerp_color(WHITE, col, 0.15 + glow_int * 0.1)
            name_surf = self.fonts["large"].render(perk["label"], True, name_col)
            surf.blit(name_surf, (icon_cx - name_surf.get_width() // 2, cy + 110))

            # ── Description ───────────────────────────────────────────────────
            desc_col  = lerp_color((160, 160, 175), col, 0.45)
            desc_surf = self.fonts["small"].render(perk["desc"], True, desc_col)
            surf.blit(desc_surf, (icon_cx - desc_surf.get_width() // 2, cy + 144))

            # ── Stack pips row ────────────────────────────────────────────────
            stacks    = int(self.player.perk(perk["key"]) / perk["bonus"] + 0.5)
            max_pips  = 5
            pip_r     = 5
            pip_gap   = 14
            pip_total = max_pips * (pip_r * 2) + (max_pips - 1) * (pip_gap - pip_r * 2)
            pip_sx    = icon_cx - pip_total // 2
            pip_y     = cy + 180

            for p in range(max_pips):
                px = pip_sx + p * pip_gap
                filled = p < stacks
                if filled:
                    pygame.draw.circle(surf, col, (px, pip_y), pip_r)
                    pygame.draw.circle(surf, lerp_color(col, WHITE, 0.5), (px, pip_y), pip_r, 1)
                else:
                    pygame.draw.circle(surf, (40, 40, 58), (px, pip_y), pip_r)
                    pygame.draw.circle(surf, (65, 65, 85), (px, pip_y), pip_r, 1)

            # Stack label
            if stacks > 0:
                st_label = self.fonts["tiny"].render(f"Owned: {stacks}×", True,
                                                     lerp_color((100, 100, 115), col, 0.6))
                surf.blit(st_label, (icon_cx - st_label.get_width() // 2, pip_y + 10))

            # ── Future total preview ──────────────────────────────────────────
            future_val = self.player.perk(perk["key"]) + perk["bonus"]
            if perk["key"] == "hp_regen":
                future_str = f"+{future_val:.1f} HP/hit after pick"
            else:
                future_str = f"+{int(future_val * 100)}%  after pick"
            fv_col  = lerp_color((120, 120, 135), col, 0.55)
            fv_surf = self.fonts["tiny"].render(future_str, True, fv_col)
            surf.blit(fv_surf, (icon_cx - fv_surf.get_width() // 2, cy + 208))

            # ── Horizontal rule before footer ─────────────────────────────────
            rule2_y = cy + self.CARD_H - 52
            rule2_s = pygame.Surface((self.CARD_W - 24, 1), pygame.SRCALPHA)
            for px in range(self.CARD_W - 24):
                t  = px / (self.CARD_W - 24)
                fa = int(40 * math.sin(t * math.pi))
                pygame.draw.line(rule2_s, (180, 180, 200, fa), (px, 0), (px, 0))
            surf.blit(rule2_s, (cx + 12, rule2_y))

            # ── Key badge footer ──────────────────────────────────────────────
            badge_w, badge_h = 52, 26
            badge_x = icon_cx - badge_w // 2
            badge_y = cy + self.CARD_H - 38
            badge_bg  = lerp_color((30, 30, 45), col, 0.25 + glow_int * 0.2)
            badge_bdr = lerp_color((80, 80, 100), col, 0.6 + glow_int * 0.3)
            pygame.draw.rect(surf, badge_bg,  (badge_x, badge_y, badge_w, badge_h), border_radius=8)
            pygame.draw.rect(surf, badge_bdr, (badge_x, badge_y, badge_w, badge_h), 1, border_radius=8)
            key_col  = lerp_color(GRAY, YELLOW, glow_int)
            key_surf = self.fonts["med"].render(f" {i+1} ", True, key_col)
            surf.blit(key_surf, (icon_cx - key_surf.get_width() // 2,
                                 badge_y + badge_h // 2 - key_surf.get_height() // 2))

# ── Display scaling helper ────────────────────────────────────────────────────

def _scaled_flip(render_surf):
    """
    Present the current frame.  Because apply_display_mode always uses
    pygame.SCALED, the display surface is already the 1280×720 logical
    canvas — pygame handles the physical upscale and mouse-coordinate
    remapping automatically.  We just flip.

    render_surf is accepted for API compatibility but is unused: callers
    draw directly to the display surface (returned by apply_display_mode /
    pygame.display.get_surface()), so no intermediate blit is required.
    """
    pygame.display.flip()


# ── Username entry screen ─────────────────────────────────────────────────────

def is_first_run():
    """Returns True and marks as seen if this is the first ever play press."""
    if not os.path.isfile(FIRST_RUN_FILE):
        try:
            with open(FIRST_RUN_FILE, "w", encoding="utf-8") as f:
                json.dump({"seen_tutorial": True}, f)
        except Exception:
            pass
        return True
    return False


def save_checkpoint(game, slot):
    """Save the player's full state to a numbered save slot (1-5)."""
    p = game.player
    data = {
        "slot":                       slot,
        "username":                   PROFILE.username or p.username,
        "wave":                       game.wave,
        "boss_killed":                game.boss_killed,
        "level":                      p.level,
        "xp":                         p.xp,
        "xp_to_next":                 p.xp_to_next,
        "max_hp":                     p.max_hp,
        "hp":                         p.hp,
        "gold":                       p.gold,
        "weapon_idx":                 p.weapon_idx,
        "owned_weapons":              p.owned_weapons,
        "perks":                      p.perks,
        "kill_count":                 p.kill_count,
        "corruption_waves_cleared":   p.corruption_waves_cleared,
        "hardcore": game.hardcore,
        # World drops — saved so nothing is lost on reload
        "gold_coins": [
            {"x": gc.x, "y": gc.y, "amount": gc.amount}
            for gc in game.gold_coins if gc.alive
        ],
        "hp_orbs": [
            {"x": orb.x, "y": orb.y, "amount": orb.amount}
            for orb in game.hp_orbs if orb.alive
        ],
    }
    try:
        with open(_slot_path(slot), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"[Checkpoint] Slot {slot} saved — wave {game.wave}")
    except Exception as e:
        print(f"[Checkpoint] Could not save slot {slot}: {e}")


def load_checkpoint(slot):
    """Return checkpoint dict for slot (1-5), or None if missing/corrupt."""
    try:
        with open(_slot_path(slot), "r", encoding="utf-8") as f:
            data = json.load(f)
        required = ("username", "wave", "level", "hp", "max_hp", "perks")
        if all(k in data for k in required):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return None


def load_all_slots():
    """Return a list of 5 entries: checkpoint dict or None for each slot 1-5."""
    return [load_checkpoint(s) for s in range(1, NUM_SAVE_SLOTS + 1)]


def delete_checkpoint(slot):
    """Delete the save file for a slot."""
    try:
        path = _slot_path(slot)
        if os.path.isfile(path):
            os.remove(path)
    except Exception as e:
        print(f"[Checkpoint] Could not delete slot {slot}: {e}")


def profile_creation_screen(screen, clock, fonts):
    """First-launch screen to create the global player profile.
    Modifies PROFILE in place and saves it. Returns when a username is confirmed."""
    cursor_blink = 0
    username     = ""
    error_msg    = ""
    error_timer  = 0
    avatar_rect  = pygame.Rect(SW // 2 - 48, SH // 2 - 180, 96, 96)

    def _pick_image():
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.wm_attributes("-topmost", True)
            path = filedialog.askopenfilename(
                title="Choose profile picture",
                filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.gif"),
                           ("All files", "*.*")],
            )
            root.destroy()
            return path or ""
        except Exception:
            return ""

    while True:
        clock.tick(FPS)
        cursor_blink += 1
        mx, my = pygame.mouse.get_pos()

        # Build confirm button rect here so click handler can reference it
        ib = pygame.Rect(SW // 2 - 160, avatar_rect.bottom + 52, 320, 48)
        cb = pygame.Rect(SW // 2 - 120, ib.bottom + 20, 240, 50)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_BACKSPACE:
                    username = username[:-1]
                elif event.key == pygame.K_RETURN:
                    if username.strip():
                        PROFILE.username = username.strip()
                        PROFILE.save()
                        return
                    else:
                        error_msg   = "Please enter a username"
                        error_timer = 120
                elif len(username) < 20 and event.unicode.isprintable():
                    username += event.unicode
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if avatar_rect.collidepoint(event.pos):
                    path = _pick_image()
                    if path:
                        PROFILE.image_path         = path
                        PROFILE._avatar_path_cache = None
                elif cb.collidepoint(event.pos):
                    if username.strip():
                        PROFILE.username = username.strip()
                        PROFILE.save()
                        return
                    else:
                        error_msg   = "Please enter a username"
                        error_timer = 120

        if error_timer > 0:
            error_timer -= 1

        # ── Draw ─────────────────────────────────────────────────────────────
        screen.fill(DARK)
        for gx in range(0, SW, 64):
            pygame.draw.line(screen, (22, 22, 36), (gx, 0), (gx, SH))
        for gy in range(0, SH, 64):
            pygame.draw.line(screen, (22, 22, 36), (0, gy), (SW, gy))

        # Title
        t = fonts["huge"].render("Create Your Profile", True, (140, 80, 255))
        screen.blit(t, (SW // 2 - t.get_width() // 2, 80))
        sub = fonts["small"].render(
            "This profile is global — separate from your in-game username.", True, GRAY)
        screen.blit(sub, (SW // 2 - sub.get_width() // 2, 140))

        # Avatar box
        av_surf = PROFILE.get_avatar()
        hov_av  = avatar_rect.collidepoint(mx, my)
        if av_surf:
            screen.blit(av_surf, avatar_rect.topleft)
        else:
            pygame.draw.rect(screen, PANEL, avatar_rect, border_radius=8)
            cam1 = fonts["tiny"].render("Click to",   True, GRAY)
            cam2 = fonts["tiny"].render("add photo",  True, GRAY)
            screen.blit(cam1, (avatar_rect.centerx - cam1.get_width() // 2, avatar_rect.centery - 12))
            screen.blit(cam2, (avatar_rect.centerx - cam2.get_width() // 2, avatar_rect.centery + 4))
        rim_col = (140, 80, 255) if hov_av else (80, 60, 140)
        pygame.draw.rect(screen, rim_col, avatar_rect, 2, border_radius=8)
        if hov_av:
            hint = fonts["tiny"].render("Change photo", True, (140, 80, 255))
            screen.blit(hint, (avatar_rect.centerx - hint.get_width() // 2, avatar_rect.bottom + 4))

        # Username input
        ul = fonts["small"].render("Choose a profile username:", True, GRAY)
        screen.blit(ul, (SW // 2 - ul.get_width() // 2, avatar_rect.bottom + 24))
        pygame.draw.rect(screen, PANEL, ib, border_radius=10)
        pygame.draw.rect(screen, CYAN,  ib, 2, border_radius=10)
        disp = username + ("|" if cursor_blink % 60 < 30 else "")
        screen.blit(fonts["large"].render(disp, True, WHITE), (ib.x + 12, ib.y + 10))

        # Confirm button
        ready   = bool(username.strip())
        btn_col = (140, 80, 255) if ready else GRAY
        pygame.draw.rect(screen, lerp_color(PANEL, btn_col, 0.3 if ready else 0.08), cb, border_radius=10)
        pygame.draw.rect(screen, btn_col, cb, 2 if ready else 1, border_radius=10)
        cl = fonts["large"].render("Create Profile", True, btn_col)
        screen.blit(cl, (cb.centerx - cl.get_width() // 2, cb.centery - cl.get_height() // 2))

        # Error
        if error_timer > 0:
            em = fonts["small"].render(error_msg, True, RED)
            screen.blit(em, (SW // 2 - em.get_width() // 2, cb.bottom + 12))

        pygame.display.flip()


def username_screen(screen, clock, fonts):
    """Main menu screen. Returns (username_str, checkpoint_or_None, save_slot, hardcore_bool)."""
    # mode: "main" | "difficulty" | "slot_new" | "slot_load"
    mode          = "main"
    selected_slot = None
    difficulty    = "normal"
    cursor_blink  = 0
    show_rename   = False
    rename_buf    = ""
    show_settings = False
    show_credits  = False
    show_tutorial = False
    show_help     = False
    tutorial_page = 0
    credits_page  = 0
    slider_drag   = False
    show_extras       = False
    show_quit_confirm = False
    show_lb           = False
    show_achievements = False
    show_inventory    = False
    case_anim = {
        "phase":      "idle",
        "strip":      [],
        "offset_x":   0.0,
        "target_x":   0.0,
        "frame":       0,       # current frame of animation
        "total_frames": 0,      # total frames for the scroll
        "result":     None,
        "win_idx":    0,
        "glow":       0,
    }
    ach_tab           = 0   # 0 = normal, 1 = hardcore
    ach_scroll        = 0   # pixel scroll offset for the achievement grid
    lb_page           = 0   # 0 = normal, 1 = hardcore
    show_patchnotes = False
    lb            = Leaderboard(hardcore=False)
    lb_hc         = Leaderboard(hardcore=True)
    slots         = load_all_slots()   # list of 5: checkpoint dict or None
    tip_idx = random.randrange(len(MENU_TIPS))

    MUSIC.play("menu")

    # Background: video (high quality) or image slideshow (low quality)
    # Use a list so the quality toggle handler can swap the value out
    menu_video      = [MenuVideo() if not GAME_SETTINGS.low else None]
    # Slideshow state — loaded lazily when low quality is first needed
    slideshow_surfs = []
    slide_idx    = 0
    slide_timer  = 0
    SLIDE_HOLD   = FPS * 4   # 4 seconds per image
    FADE_FRAMES  = FPS // 2  # 30 frames = 0.5s crossfade
    slide_fade   = 0         # 0 = not fading; 1..FADE_FRAMES = mid-crossfade
    slide_next   = 0         # index of the incoming image during crossfade

    def _load_slides():
        surfs = []
        for idx in range(5):
            path = asset(f"slide_{idx}.png")
            if os.path.isfile(path):
                try:
                    img = pygame.image.load(path).convert()
                    img = pygame.transform.smoothscale(img, (SW, SH))
                    surfs.append(img)
                except pygame.error:
                    pass
        return surfs

    # Load now if starting in low quality
    if GAME_SETTINGS.low:
        slideshow_surfs = _load_slides()

    # Flame particles for the title effect (used on low quality)
    flame_particles = []
    flame_timer     = 0

    # High-quality procedural fire simulation (cellular automaton style)
    # Wrapped in a dict so the draw loop can reassign without nonlocal.
    _fire = {
        "buf":  None,   # 2-D list of heat values [col][row], 0.0–1.0
        "w":    0,
        "h":    0,
        "tick": 0,
    }

    # Settings panel geometry (defined here so hit-tests work before first draw)
    SP_W, SP_H = 420, 470   # height covers sliders + quality + health bar + fullscreen
    SP_X = SW // 2 - SP_W // 2
    SP_Y = SH // 2 - SP_H // 2
    SLIDER_X    = SP_X + 80
    SLIDER_W    = SP_W - 160
    SLIDER_Y    = SP_Y + 110    # music slider Y
    SLIDER2_Y   = SP_Y + 195    # sfx slider Y
    QUALITY_Y   = SP_Y + 268    # quality toggle Y
    HEALTHBAR_Y = SP_Y + 328    # player health bar toggle Y
    FSCRN_Y     = SP_Y + 398    # fullscreen toggle Y

    def vol_to_x(vol):
        return int(SLIDER_X + vol * SLIDER_W)

    def x_to_vol(px):
        return max(0.0, min(1.0, (px - SLIDER_X) / SLIDER_W))

    # Pre-define button rects so they exist before the first draw pass
    lb_rect       = pygame.Rect(0, 0, 1, 1)
    pn_rect       = pygame.Rect(0, 0, 1, 1)
    help_rect     = pygame.Rect(0, 0, 1, 1)
    ach_rect      = pygame.Rect(0, 0, 1, 1)
    pen_rect      = pygame.Rect(0, 0, 1, 1)   # rename profile name button
    # Main menu buttons
    btn_new_game  = pygame.Rect(0, 0, 1, 1)
    btn_load_game = pygame.Rect(0, 0, 1, 1)
    btn_settings  = pygame.Rect(0, 0, 1, 1)
    btn_credits   = pygame.Rect(0, 0, 1, 1)
    btn_extras    = pygame.Rect(0, 0, 1, 1)
    btn_inventory = pygame.Rect(0, 0, 1, 1)
    inv_open_rect = pygame.Rect(0, 0, 1, 1)   # "Open Case" button inside inventory
    play_rect     = None
    update_rect   = pygame.Rect(0, 0, 1, 1)
    close_rect    = pygame.Rect(SW - 48, 8, 36, 36)

    while True:
        clock.tick(FPS)
        cursor_blink += 1
        mx_now, my_now = pygame.mouse.get_pos()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if event.type == pygame.USEREVENT + 1:
                MUSIC.on_track_end()
            if show_settings:
                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_RETURN):
                        show_settings = False
                        slider_drag   = False
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    px, py = event.pos
                    if (SLIDER_X - 10 <= px <= SLIDER_X + SLIDER_W + 10 and
                            SLIDER_Y - 14 <= py <= SLIDER_Y + 14):
                        slider_drag = "music"
                        MUSIC.set_volume(x_to_vol(px))
                    elif (SLIDER_X - 10 <= px <= SLIDER_X + SLIDER_W + 10 and
                            SLIDER2_Y - 14 <= py <= SLIDER2_Y + 14):
                        slider_drag = "sfx"
                        SOUNDS.set_volume(x_to_vol(px))
                    elif (SP_X + 20 <= px <= SP_X + SP_W // 2 - 5 and
                            QUALITY_Y <= py <= QUALITY_Y + 36):
                        if GAME_SETTINGS.quality != "low":
                            GAME_SETTINGS.quality = "low"
                            GAME_SETTINGS.save()
                            if menu_video[0]:
                                menu_video[0].release()
                                menu_video[0] = None
                            if not slideshow_surfs:
                                slideshow_surfs.extend(_load_slides())
                            slide_idx = 0; slide_timer = 0; slide_fade = 0
                    elif (SP_X + SP_W // 2 + 5 <= px <= SP_X + SP_W - 20 and
                            QUALITY_Y <= py <= QUALITY_Y + 36):
                        if GAME_SETTINGS.quality != "high":
                            GAME_SETTINGS.quality = "high"
                            GAME_SETTINGS.save()
                            if menu_video[0] is None:
                                menu_video[0] = MenuVideo()
                            slideshow_surfs.clear()
                    elif (SP_X + 20 <= px <= SP_X + SP_W - 20 and
                            HEALTHBAR_Y <= py <= HEALTHBAR_Y + 36):
                        GAME_SETTINGS.player_health_bar = not GAME_SETTINGS.player_health_bar
                        GAME_SETTINGS.save()
                    elif FSCRN_Y <= py <= FSCRN_Y + 36:
                        if SP_X + 20 <= px <= SP_X + SP_W - 20:
                            GAME_SETTINGS.fullscreen = not GAME_SETTINGS.fullscreen
                            GAME_SETTINGS.save()
                            apply_display_mode(None)
                            screen = pygame.display.get_surface()
                    elif not (SP_X <= px <= SP_X + SP_W and SP_Y <= py <= SP_Y + SP_H):
                        show_settings = False
                if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    slider_drag = False
                    GAME_SETTINGS.save()   # persist volume on release
                if event.type == pygame.MOUSEMOTION and slider_drag:
                    if slider_drag == "music":
                        MUSIC.set_volume(x_to_vol(event.pos[0]))
                    else:
                        SOUNDS.set_volume(x_to_vol(event.pos[0]))
                continue   # don't process other events while settings is open

            # ── Leaderboard overlay ────────────────────────────────────────
            if show_lb:
                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_LEFT, pygame.K_a):
                        lb_page = 0
                    elif event.key in (pygame.K_RIGHT, pygame.K_d):
                        lb_page = 1
                    else:
                        show_lb = False
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    px_lb, py_lb = event.pos
                    lw2, lh2 = 700, 500
                    lx2 = SW // 2 - lw2 // 2; ly2 = SH // 2 - lh2 // 2
                    btn_prev = pygame.Rect(lx2 + 16,          ly2 + lh2 - 48, 140, 34)
                    btn_next = pygame.Rect(lx2 + lw2 - 156,   ly2 + lh2 - 48, 140, 34)
                    if btn_prev.collidepoint(px_lb, py_lb):
                        lb_page = 0
                    elif btn_next.collidepoint(px_lb, py_lb):
                        lb_page = 1
                    elif not (lx2 <= px_lb <= lx2 + lw2 and ly2 <= py_lb <= ly2 + lh2):
                        show_lb = False
                continue

            # ── Achievements overlay ───────────────────────────────────────
            if show_achievements:
                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_LEFT, pygame.K_a):
                        ach_tab    = 0
                        ach_scroll = 0
                    elif event.key in (pygame.K_RIGHT, pygame.K_d):
                        ach_tab    = 1
                        ach_scroll = 0
                    elif event.key == pygame.K_UP:
                        ach_scroll = max(0, ach_scroll - 60)
                    elif event.key == pygame.K_DOWN:
                        ach_scroll += 60   # clamped in draw below
                    else:
                        show_achievements = False
                if event.type == pygame.MOUSEWHEEL:
                    ach_scroll = max(0, ach_scroll - event.y * 40)
                    # upper bound clamped in draw
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    AW, AH = 980, 580
                    AX = SW // 2 - AW // 2; AY = SH // 2 - AH // 2
                    tab0 = pygame.Rect(AX + 16,       AY + 12, 148, 34)
                    tab1 = pygame.Rect(AX + 16 + 160, AY + 12, 148, 34)
                    if tab0.collidepoint(event.pos):
                        ach_tab    = 0
                        ach_scroll = 0
                    elif tab1.collidepoint(event.pos):
                        ach_tab    = 1
                        ach_scroll = 0
                    elif not (AX <= event.pos[0] <= AX + AW and AY <= event.pos[1] <= AY + AH):
                        show_achievements = False
                continue

            # ── Patch notes overlay ────────────────────────────────────────
            if show_patchnotes:
                if event.type == pygame.KEYDOWN:
                    show_patchnotes = False
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    show_patchnotes = False
                continue

            # ── Tutorial overlay ───────────────────────────────────────────
            if show_tutorial:
                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_RIGHT, pygame.K_d) and tutorial_page == 0:
                        tutorial_page = 1
                    elif event.key in (pygame.K_LEFT, pygame.K_a) and tutorial_page == 1:
                        tutorial_page = 0
                    elif event.key in (pygame.K_RETURN, pygame.K_ESCAPE):
                        if tutorial_page == 1:
                            if show_help:
                                show_tutorial = False
                                show_help     = False
                            else:
                                if menu_video[0]: menu_video[0].release()
                                return PROFILE.username, None, selected_slot, (difficulty == "hardcore")
                        elif event.key == pygame.K_ESCAPE:
                            show_tutorial = False
                            show_help     = False
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mx_t, my_t = event.pos
                    TW, TH = 720, 480
                    TX = SW // 2 - TW // 2; TY = SH // 2 - TH // 2
                    next_btn  = pygame.Rect(TX + TW - 180, TY + TH - 56, 160, 40)
                    prev_btn  = pygame.Rect(TX + 20,        TY + TH - 56, 160, 40)
                    if tutorial_page == 0 and next_btn.collidepoint(mx_t, my_t):
                        tutorial_page = 1
                    elif tutorial_page == 1 and prev_btn.collidepoint(mx_t, my_t):
                        tutorial_page = 0
                    elif tutorial_page == 1 and next_btn.collidepoint(mx_t, my_t):
                        if show_help:
                            show_tutorial = False
                            show_help     = False
                        else:
                            if menu_video[0]: menu_video[0].release()
                            return PROFILE.username, None, selected_slot, (difficulty == "hardcore")
                continue

            # ── Credits overlay ────────────────────────────────────────────
            if show_credits:
                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_LEFT, pygame.K_a):
                        credits_page = max(0, credits_page - 1)
                    elif event.key in (pygame.K_RIGHT, pygame.K_d):
                        credits_page += 1   # clamped in draw
                    else:
                        show_credits = False
                        credits_page = 0
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    show_credits = False
                    credits_page = 0
                continue

            # ── Quit confirm dialog ────────────────────────────────────────
            if show_quit_confirm:
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        show_quit_confirm = False
                    elif event.key in (pygame.K_RETURN, pygame.K_y):
                        if menu_video[0]: menu_video[0].release()
                        pygame.quit(); sys.exit()
                    elif event.key == pygame.K_n:
                        show_quit_confirm = False
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    px_q, py_q = event.pos
                    QW, QH = 320, 140
                    QX = SW // 2 - QW // 2; QY = SH // 2 - QH // 2
                    btn_yes = pygame.Rect(QX + 24,          QY + 76, 120, 40)
                    btn_no  = pygame.Rect(QX + QW - 144,    QY + 76, 120, 40)
                    if btn_yes.collidepoint(px_q, py_q):
                        if menu_video[0]: menu_video[0].release()
                        pygame.quit(); sys.exit()
                    elif btn_no.collidepoint(px_q, py_q) or \
                         not (QX <= px_q <= QX + QW and QY <= py_q <= QY + QH):
                        show_quit_confirm = False
                continue

            # ── Inventory / case opening overlay ──────────────────────────
            if show_inventory:
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    if case_anim["phase"] == "idle":
                        show_inventory = False
                    else:
                        case_anim["phase"] = "idle"   # cancel animation
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    px_i, py_i = event.pos
                    IW, IH = 860, 560
                    IX = SW // 2 - IW // 2; IY = SH // 2 - IH // 2
                    if case_anim["phase"] == "idle":
                        # "Open Case" button click
                        if inv_open_rect.collidepoint(px_i, py_i):
                            if TOKENS.cases > 0 and TOKENS.spend(5):
                                TOKENS.spend_case()
                                result_entry = roll_case()
                                CARD_W_STRIP = 140; CARD_GAP_STRIP = 8
                                STRIP_STEP_I = CARD_W_STRIP + CARD_GAP_STRIP
                                win_idx = 44
                                strip = [random.choice(CASE_POOL) for _ in range(win_idx)]
                                strip.append(result_entry)
                                strip += [random.choice(CASE_POOL) for _ in range(8)]
                                # Target: win_idx card centred in the IW-40 wide strip view
                                target_x = win_idx * STRIP_STEP_I - ((860 - 40) // 2 - CARD_W_STRIP // 2)
                                ANIM_FRAMES = 120  # 2 seconds at 60fps
                                case_anim.update({
                                    "phase":        "spinning",
                                    "strip":        strip,
                                    "offset_x":     0.0,
                                    "target_x":     float(target_x),
                                    "frame":        0,
                                    "total_frames": ANIM_FRAMES,
                                    "result":       result_entry,
                                    "win_idx":      win_idx,
                                    "glow":         0,
                                })
                        # Close if clicking outside the panel
                        elif not (IX <= px_i <= IX + IW and IY <= py_i <= IY + IH):
                            show_inventory = False
                    elif case_anim["phase"] == "landed":
                        # Click anywhere to dismiss result and go back to idle
                        case_anim["phase"] = "idle"
                        # Unlock the cosmetic they won
                        won_id = case_anim["result"]["cosm_id"]
                        TOKENS.unlock_cosmetic(won_id)
                continue
            if event.type == pygame.KEYDOWN:
                if show_rename:
                    if event.key == pygame.K_ESCAPE:
                        show_rename = False; rename_buf = ""
                    elif event.key == pygame.K_RETURN and rename_buf.strip():
                        PROFILE.username = rename_buf.strip(); PROFILE.save()
                        show_rename = False; rename_buf = ""
                    elif event.key == pygame.K_BACKSPACE:
                        rename_buf = rename_buf[:-1]
                    elif len(rename_buf) < 20 and event.unicode.isprintable():
                        rename_buf += event.unicode
                elif mode == "difficulty":
                    if event.key == pygame.K_ESCAPE:
                        mode = "main"
                elif mode in ("slot_new", "slot_load"):
                    if event.key == pygame.K_ESCAPE:
                        mode = "difficulty" if mode == "slot_new" else "main"
                        selected_slot = None
                elif mode == "main":
                    if event.key == pygame.K_ESCAPE:
                        show_quit_confirm = True
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                px, py = event.pos
                # ── Rename overlay consumes clicks ─────────────────────────
                if show_rename:
                    RW, RH = 360, 130
                    RX = SW // 2 - RW // 2; RY = SH // 2 - RH // 2
                    rib = pygame.Rect(RX + 16, RY + 44, RW - 32, 40)
                    rcb = pygame.Rect(RX + 16, RY + 92, RW - 32, 30)
                    if rcb.collidepoint(px, py) and rename_buf.strip():
                        PROFILE.username = rename_buf.strip(); PROFILE.save()
                        show_rename = False; rename_buf = ""
                    elif not (RX <= px <= RX + RW and RY <= py <= RY + RH):
                        show_rename = False; rename_buf = ""
                    continue
                if close_rect.collidepoint(px, py):
                    if menu_video[0]: menu_video[0].release()
                    pygame.quit(); sys.exit()
                if update_rect.collidepoint(px, py) and _update_info.get("url"):
                    webbrowser.open(_update_info["url"])
                # ── Pen icon (rename) in profile widget ───────────────────
                elif pen_rect.collidepoint(px, py):
                    show_rename = True
                    rename_buf  = PROFILE.username
                # ── Main menu buttons — only active when no sub-panel open ─
                elif mode == "main":
                    if btn_new_game.collidepoint(px, py):
                        difficulty = "normal"
                        mode = "difficulty"
                        show_extras = False
                    elif btn_load_game.collidepoint(px, py):
                        mode = "slot_load"
                        slots = load_all_slots()
                        show_extras = False
                    elif btn_settings.collidepoint(px, py):
                        show_settings = True
                        show_extras = False
                    elif btn_credits.collidepoint(px, py):
                        show_credits  = True
                        credits_page  = 0
                        show_extras = False
                    elif btn_inventory.collidepoint(px, py):
                        show_inventory = True
                        case_anim["phase"] = "idle"
                        show_extras = False
                    elif btn_extras.collidepoint(px, py):
                        show_extras = not show_extras
                    elif show_extras:
                        if lb_rect.collidepoint(px, py):
                            show_lb = True; show_extras = False
                        elif pn_rect.collidepoint(px, py):
                            show_patchnotes = True; show_extras = False
                        elif help_rect.collidepoint(px, py):
                            show_tutorial = True; show_help = True
                            tutorial_page = 0; show_extras = False
                        elif ach_rect.collidepoint(px, py):
                            show_achievements = True; show_extras = False
                        else:
                            show_extras = False
                # ── Difficulty picker ──────────────────────────────────────
                elif mode == "difficulty":
                    if diff_normal_rect.collidepoint(px, py):
                        difficulty = "normal"; mode = "slot_new"
                    elif diff_hard_rect.collidepoint(px, py):
                        difficulty = "hardcore"; mode = "slot_new"
                    elif not (DX <= px <= DX + DW and DY <= py <= DY + DH):
                        mode = "main"
                # ── Slot selection ─────────────────────────────────────────
                elif mode == "slot_new":
                    for si, sr in enumerate(slot_rects):
                        if sr.collidepoint(px, py):
                            slot_num = si + 1
                            if is_first_run():
                                show_tutorial = True; show_help = False
                                tutorial_page = 0; selected_slot = slot_num
                            else:
                                if menu_video[0]: menu_video[0].release()
                                return PROFILE.username, None, slot_num, (difficulty == "hardcore")
                elif mode == "slot_load":
                    for si, sr in enumerate(slot_rects):
                        if sr.collidepoint(px, py):
                            slot_num = si + 1
                            cp = slots[si]
                            if cp:
                                if menu_video[0]: menu_video[0].release()
                                hc = cp.get("hardcore", False)
                                return cp["username"], cp, slot_num, hc
                    SW2 = 480; SX2 = SW // 2 - SW2 // 2
                    SY2 = btn_load_game.bottom + 10
                    SH2 = NUM_SAVE_SLOTS * 64 + 56
                    if not (SX2 <= px <= SX2 + SW2 and SY2 <= py <= SY2 + SH2):
                        mode = "main"

        # ── Draw ─────────────────────────────────────────────────────────────
        # Background
        if GAME_SETTINGS.low:
            # Slideshow with crossfade — only advance when actually in low quality
            if slideshow_surfs:
                if slide_fade > 0:
                    # Mid-crossfade: draw current image then blend next on top
                    screen.blit(slideshow_surfs[slide_idx], (0, 0))
                    t_fade = slide_fade / FADE_FRAMES          # 0→1
                    fade_alpha = max(0, min(255, int(255 * t_fade)))
                    next_surf = slideshow_surfs[slide_next].copy()
                    next_surf.set_alpha(fade_alpha)
                    screen.blit(next_surf, (0, 0))
                    slide_fade += 1
                    if slide_fade > FADE_FRAMES:
                        # Crossfade complete — commit to next image
                        slide_idx  = slide_next
                        slide_fade = 0
                        slide_timer = 0
                else:
                    screen.blit(slideshow_surfs[slide_idx], (0, 0))
                    slide_timer += 1
                    if slide_timer >= SLIDE_HOLD:
                        # Start crossfade to next image
                        slide_next = (slide_idx + 1) % len(slideshow_surfs)
                        slide_fade = 1
                # Dark overlay so UI stays readable
                ov_sl = pygame.Surface((SW, SH), pygame.SRCALPHA)
                ov_sl.fill((0, 0, 0, 120))
                screen.blit(ov_sl, (0, 0))
            else:
                # No slides found — fallback to static grid
                screen.fill(DARK)
                tile = 64
                for gx in range(SW // tile + 1):
                    for gy in range(SH // tile + 1):
                        pygame.draw.rect(screen, (26, 26, 40),
                                         (gx * tile, gy * tile, tile - 1, tile - 1))
        else:
            vid_frame = menu_video[0].next_frame(SW, SH) if menu_video[0] else None
            if vid_frame:
                screen.blit(vid_frame, (0, 0))
                vov = pygame.Surface((SW, SH), pygame.SRCALPHA)
                vov.fill((0, 0, 0, 140))
                screen.blit(vov, (0, 0))
            else:
                screen.fill(DARK)
                tile = 64
                for gx in range(SW // tile + 1):
                    for gy in range(SH // tile + 1):
                        pygame.draw.rect(screen, (26, 26, 40),
                                         (gx * tile, gy * tile, tile - 1, tile - 1))

        # ── Title with animated flame ─────────────────────────────────────────
        TITLE_TEXT  = "DUNGEON CRAWLER"
        TITLE_COL   = (180, 60, 220)   # purple
        title       = fonts["huge"].render(TITLE_TEXT, True, TITLE_COL)
        title_x     = SW // 2 - title.get_width() // 2
        title_y     = 120
        title_bot   = title_y + title.get_height()

        if not GAME_SETTINGS.low:
            # ── High quality: cellular-automaton fire simulation ──────────────
            _CELL = 6   # pixels per cell — larger = faster + chunkier flames
            fw_cells = (title.get_width() + 3) // _CELL
            fh_cells = (title.get_height() * 2) // _CELL   # 2× text height tall

            # Initialise or resize buffer
            if _fire["buf"] is None or _fire["w"] != fw_cells or _fire["h"] != fh_cells:
                _fire["w"]   = fw_cells
                _fire["h"]   = fh_cells
                _fire["buf"] = [[0.0] * fh_cells for _ in range(fw_cells)]

            _fire["tick"] += 1

            # Step simulation every other frame
            if _fire["tick"] % 2 == 0:
                buf = _fire["buf"]
                fw  = fw_cells
                fh  = fh_cells

                # Seed bottom row with full heat
                for x in range(fw):
                    buf[x][fh - 1] = random.uniform(0.85, 1.0)
                    buf[x][fh - 2] = random.uniform(0.65, 1.0)

                new_buf = [[0.0] * fh for _ in range(fw)]
                for x in range(fw):
                    for y in range(fh - 1):
                        xl = max(0, x - 1)
                        xr = min(fw - 1, x + 1)
                        heat = (
                            buf[xl][y + 1] * 0.22 +
                            buf[x ][y + 1] * 0.56 +
                            buf[xr][y + 1] * 0.22
                        )
                        # Cool more aggressively so flames die before reaching the top
                        cool = 0.06 + ((fh - 1 - y) / fh) * 0.06
                        new_buf[x][y] = max(0.0, heat - cool)
                    new_buf[x][fh - 1] = buf[x][fh - 1]
                _fire["buf"] = new_buf

            # Render — skip cells below a visible threshold, strong alpha falloff
            buf = _fire["buf"]
            fw  = _fire["w"]
            fh  = _fire["h"]
            fire_surf = pygame.Surface((fw * _CELL, fh * _CELL), pygame.SRCALPHA)
            for x in range(fw):
                for y in range(fh):
                    h = buf[x][y]
                    if h < 0.08:
                        continue   # invisible — skip entirely
                    # Colour: bright yellow-white core → orange → dark red tip
                    if h > 0.75:
                        col = lerp_color((255, 160, 20), (255, 240, 120), (h - 0.75) / 0.25)
                    elif h > 0.45:
                        col = lerp_color((220, 60, 0),   (255, 160, 20),  (h - 0.45) / 0.30)
                    else:
                        col = lerp_color((120, 20, 0),   (220, 60, 0),    (h - 0.08) / 0.37)
                    # Alpha: quadratic falloff — low heat = nearly invisible
                    alpha = min(255, int((h ** 1.6) * 255))
                    pygame.draw.rect(fire_surf, (*col, alpha),
                                     (x * _CELL, y * _CELL, _CELL, _CELL))

            # Blit so the fire's bottom aligns with the bottom of the title text
            fire_x = title_x - -1
            fire_y = title_bot - fh * _CELL - 11
            screen.blit(fire_surf, (fire_x, fire_y))

        else:
            # ── Low quality: procedural flame particles ───────────────────────
            flame_timer += 1
            if flame_timer % 2 == 0:   # spawn every 2 frames for density
                for _ in range(4):
                    fx = title_x + random.randint(0, title.get_width())
                    fy = title_y + random.randint(-4, 8)
                    flame_particles.append([
                        float(fx), float(fy),
                        random.uniform(-0.4, 0.4),   # vx
                        random.uniform(-2.2, -0.8),  # vy  (upward)
                        random.randint(20, 40),       # life
                        random.randint(20, 40),       # max_life
                        random.randint(4, 9),         # size
                    ])

            # Update + draw flame particles (behind title text)
            next_fp = []
            for fp in flame_particles:
                fp[0] += fp[2]; fp[1] += fp[3]
                fp[4] -= 1
                if fp[4] > 0:
                    next_fp.append(fp)
                    t    = fp[4] / fp[5]           # 1→0 as particle ages
                    size = max(1, int(fp[6] * t))
                    # Colour: bright yellow core → orange → red tip as life fades
                    if t > 0.6:
                        col = lerp_color((255, 220, 60), (255, 100, 10), 1 - (t - 0.6) / 0.4)
                    elif t > 0.25:
                        col = lerp_color((255, 100, 10), (180, 20, 80), 1 - (t - 0.25) / 0.35)
                    else:
                        col = lerp_color((180, 20, 80), (60, 0, 60), 1 - t / 0.25)
                    alpha = max(0, min(255, int(200 * t)))
                    if alpha > 0:
                        r2 = max(0, min(255, int(col[0])))
                        g2 = max(0, min(255, int(col[1])))
                        b2 = max(0, min(255, int(col[2])))
                        fs = pygame.Surface((size * 2 + 2, size * 2 + 2), pygame.SRCALPHA)
                        pygame.draw.circle(fs, (r2, g2, b2, alpha), (size + 1, size + 1), size)
                        screen.blit(fs, (int(fp[0]) - size - 1, int(fp[1]) - size - 1))
            flame_particles = next_fp

        # Draw title on top of the flame
        glow_s = fonts["huge"].render(TITLE_TEXT, True, (100, 20, 140))
        screen.blit(glow_s, (title_x - 1, title_y + 1))
        screen.blit(title, (title_x, title_y))

        # ── Main menu buttons — single vertical column ────────────────────────
        MBW  = 260   # button width
        MBH  = 52    # button height
        MGAP = 12    # gap between buttons
        MX   = SW // 2 - MBW // 2
        MY   = title_bot + 28

        has_saves   = any(s is not None for s in slots)
        load_col    = (80, 220, 140) if has_saves else (70, 80, 70)

        rows = [
            ("btn_new_game",  "New Game",        GREEN,            True),
            ("btn_load_game", "Load Game",        load_col,         True),
            ("btn_settings",  "Settings",         (140, 180, 255),  True),
            ("btn_credits",   "Credits",           (200, 160, 255),  True),
            ("btn_inventory", "Inventory",         (255, 160, 60),   True),
            ("btn_extras",    "Extras  ▾",        (255, 200, 80),   True),
        ]

        btn_new_game  = pygame.Rect(MX, MY,                       MBW, MBH)
        btn_load_game = pygame.Rect(MX, MY + (MBH + MGAP),        MBW, MBH)
        btn_settings  = pygame.Rect(MX, MY + (MBH + MGAP) * 2,   MBW, MBH)
        btn_credits   = pygame.Rect(MX, MY + (MBH + MGAP) * 3,   MBW, MBH)
        btn_inventory = pygame.Rect(MX, MY + (MBH + MGAP) * 4,   MBW, MBH)
        btn_extras    = pygame.Rect(MX, MY + (MBH + MGAP) * 5,   MBW, MBH)

        for rect, (_, label, col, active) in zip(
                [btn_new_game, btn_load_game, btn_settings, btn_credits, btn_inventory, btn_extras], rows):
            bg = lerp_color(PANEL, col, 0.25 if active else 0.08)
            pygame.draw.rect(screen, bg, rect, border_radius=10)
            pygame.draw.rect(screen, col if active else GRAY, rect, 2, border_radius=10)
            lbl = fonts["large"].render(label, True, col if active else GRAY)
            screen.blit(lbl, (rect.centerx - lbl.get_width() // 2,
                               rect.centery - lbl.get_height() // 2))
            # Case count badge on Inventory button
            if label == "Inventory" and TOKENS.cases > 0:
                badge_r = 11
                bx_b = rect.right - badge_r - 4
                by_b = rect.top   - badge_r + 4
                pygame.draw.circle(screen, RED,   (bx_b, by_b), badge_r)
                pygame.draw.circle(screen, WHITE, (bx_b, by_b), badge_r, 1)
                bc_s = fonts["tiny"].render(str(TOKENS.cases), True, WHITE)
                screen.blit(bc_s, (bx_b - bc_s.get_width() // 2, by_b - bc_s.get_height() // 2))

        # ── Sub-panel placeholders (no new_game sub-panel — profile name used directly) ─
        play_rect  = None
        slot_rects = [pygame.Rect(0, 0, 1, 1)] * NUM_SAVE_SLOTS
        diff_normal_rect = pygame.Rect(0, 0, 1, 1)
        diff_hard_rect   = pygame.Rect(0, 0, 1, 1)
        DX = DY = DW = DH = 0

        # ── Difficulty picker ─────────────────────────────────────────────────
        if mode == "difficulty":
            DBW = 360; DBH = 56; DGAP = 12
            DW  = DBW + 32
            DH  = 40 + DBH * 2 + DGAP + 16
            DX  = SW // 2 - DW // 2
            DY  = btn_new_game.bottom + 10
            dp_s = pygame.Surface((DW, DH), pygame.SRCALPHA)
            pygame.draw.rect(dp_s, (18, 22, 38, 235), (0, 0, DW, DH), border_radius=12)
            screen.blit(dp_s, (DX, DY))
            pygame.draw.rect(screen, GREEN, (DX, DY, DW, DH), 2, border_radius=12)
            dt = fonts["med"].render("Choose difficulty:", True, GRAY)
            screen.blit(dt, (DX + DW // 2 - dt.get_width() // 2, DY + 10))

            # Normal mode button
            diff_normal_rect = pygame.Rect(DX + 16, DY + 40, DBW, DBH)
            norm_hov = diff_normal_rect.collidepoint(mx_now, my_now)
            norm_col = (80, 180, 255)
            pygame.draw.rect(screen, lerp_color(PANEL, norm_col, 0.35 if norm_hov else 0.18),
                             diff_normal_rect, border_radius=10)
            pygame.draw.rect(screen, norm_col, diff_normal_rect, 2 if norm_hov else 1, border_radius=10)
            nl = fonts["large"].render("Normal Mode", True, norm_col)
            screen.blit(nl, (diff_normal_rect.centerx - nl.get_width() // 2,
                              diff_normal_rect.centery - nl.get_height() // 2))

            # Hardcore mode button
            diff_hard_rect = pygame.Rect(DX + 16, DY + 40 + DBH + DGAP, DBW, DBH)
            hard_hov = diff_hard_rect.collidepoint(mx_now, my_now)
            hard_col = (255, 80, 40)
            pygame.draw.rect(screen, lerp_color(PANEL, hard_col, 0.35 if hard_hov else 0.18),
                             diff_hard_rect, border_radius=10)
            pygame.draw.rect(screen, hard_col, diff_hard_rect, 2 if hard_hov else 1, border_radius=10)
            # Flaming skull decorations on each side (procedural, animated)
            hl = fonts["large"].render("Hardcore Mode", True, hard_col)
            skull_size = 18
            skull_w    = skull_size * 2 + 4
            gap        = 10
            total_w    = skull_w + gap + hl.get_width() + gap + skull_w
            start_x    = diff_hard_rect.centerx - total_w // 2
            cy_h       = diff_hard_rect.centery
            draw_flaming_skull(screen, start_x + skull_size,
                               cy_h, cursor_blink, size=skull_size)
            screen.blit(hl, (start_x + skull_w + gap,
                              cy_h - hl.get_height() // 2))
            draw_flaming_skull(screen, start_x + skull_w + gap + hl.get_width() + gap + skull_size,
                               cy_h, cursor_blink + 15, size=skull_size)

            esc_h = fonts["tiny"].render("ESC to go back", True, GRAY)
            screen.blit(esc_h, (DX + DW // 2 - esc_h.get_width() // 2, DY + DH - 18))

        # ── Slot selection panel (new game or load game) ──────────────────────
        if mode in ("slot_new", "slot_load"):
            SW2 = 480; SH2 = NUM_SAVE_SLOTS * 64 + 56
            SX2 = SW // 2 - SW2 // 2
            if mode == "slot_new":
                SY2 = btn_new_game.bottom + 10
                panel_col = (255, 80, 40) if difficulty == "hardcore" else GREEN
                diff_label = "  [HARDCORE]" if difficulty == "hardcore" else ""
                pname = PROFILE.username or "Player"
                title_str = f"Choose slot — \"{pname}\"{diff_label}"
            else:
                SY2 = btn_load_game.bottom + 10
                panel_col = (80, 220, 140)
                title_str = "Select a save to load"

            sp = pygame.Surface((SW2, SH2), pygame.SRCALPHA)
            pygame.draw.rect(sp, (14, 18, 30, 240), (0, 0, SW2, SH2), border_radius=14)
            screen.blit(sp, (SX2, SY2))
            pygame.draw.rect(screen, panel_col, (SX2, SY2, SW2, SH2), 2, border_radius=14)
            th = fonts["med"].render(title_str, True, panel_col)
            screen.blit(th, (SX2 + SW2 // 2 - th.get_width() // 2, SY2 + 12))

            slot_rects = []
            for si in range(NUM_SAVE_SLOTS):
                slot_num = si + 1
                sr = pygame.Rect(SX2 + 16, SY2 + 44 + si * 64, SW2 - 32, 52)
                slot_rects.append(sr)
                cp = slots[si]

                if mode == "slot_load" and cp is None:
                    # Empty slot in load mode — unclickable
                    pygame.draw.rect(screen, (25, 28, 40), sr, border_radius=8)
                    pygame.draw.rect(screen, (50, 50, 65), sr, 1, border_radius=8)
                    empty_lbl = fonts["small"].render(f"Slot {slot_num}  —  Empty", True, (60, 60, 75))
                    screen.blit(empty_lbl, (sr.x + 14, sr.centery - empty_lbl.get_height() // 2))
                else:
                    hovered   = sr.collidepoint(mx_now, my_now)
                    is_hc     = cp.get("hardcore", False) if cp else False
                    if cp:
                        if is_hc and mode == "slot_load":
                            rim_col = (220, 60, 40)
                            bg_col  = lerp_color(PANEL, (220, 60, 40), 0.30 if hovered else 0.18)
                        else:
                            rim_col = panel_col
                            bg_col  = lerp_color(PANEL, panel_col, 0.30 if hovered else 0.15)
                    else:
                        bg_col  = lerp_color(PANEL, (80, 80, 100), 0.25 if hovered else 0.10)
                        rim_col = (120, 120, 150)
                    pygame.draw.rect(screen, bg_col, sr, border_radius=8)
                    pygame.draw.rect(screen, rim_col, sr, 1 if not hovered else 2, border_radius=8)

                    if cp:
                        hc_tag  = "  [HARDCORE]" if is_hc else ""
                        name_col = (255, 160, 120) if (is_hc and mode == "slot_load") else WHITE
                        name_s  = fonts["med"].render(
                            f"Slot {slot_num}  —  {cp['username']}{hc_tag}", True, name_col)
                        info_s  = fonts["tiny"].render(
                            f"Wave {cp['wave']}  •  Level {cp['level']}  •  "
                            f"{cp.get('boss_killed', 0)} bosses killed", True, GRAY)
                        screen.blit(name_s, (sr.x + 14, sr.y + 7))
                        screen.blit(info_s, (sr.x + 14, sr.y + 30))
                    else:
                        empty_s = fonts["med"].render(f"Slot {slot_num}  —  Empty", True, (130, 140, 160))
                        screen.blit(empty_s, (sr.x + 14, sr.centery - empty_s.get_height() // 2))

            esc_hint = fonts["tiny"].render("ESC to go back", True, GRAY)
            screen.blit(esc_hint, (SX2 + SW2 // 2 - esc_hint.get_width() // 2, SY2 + SH2 - 18))

        # ── Extras sub-panel dropdown ─────────────────────────────────────────
        EX_W = MBW; EX_BH = 40; EX_GAP = 6
        EX_items = [
            ("Leaderboard",  YELLOW,            "lb"),
            ("Achievements", (180, 120, 255),   "ach"),
            ("Patch Notes",  (100, 220, 160),   "pn"),
            ("Help",         (255, 180, 80),     "help"),
        ]
        EX_H  = EX_BH * len(EX_items) + EX_GAP * (len(EX_items) - 1) + 16
        EX_X  = btn_extras.x
        EX_Y  = btn_extras.bottom + 6
        ex_rects = []
        for i in range(len(EX_items)):
            ex_rects.append(pygame.Rect(EX_X, EX_Y + 8 + i * (EX_BH + EX_GAP), EX_W, EX_BH))
        lb_rect   = ex_rects[0]
        ach_rect  = ex_rects[1]
        pn_rect   = ex_rects[2]
        help_rect = ex_rects[3]

        if show_extras:
            ep = pygame.Surface((EX_W, EX_H), pygame.SRCALPHA)
            pygame.draw.rect(ep, (20, 20, 36, 230), (0, 0, EX_W, EX_H), border_radius=10)
            screen.blit(ep, (EX_X, EX_Y))
            pygame.draw.rect(screen, (255, 200, 80), (EX_X, EX_Y, EX_W, EX_H), 1, border_radius=10)
            for i, (elabel, ecol, _) in enumerate(EX_items):
                er = ex_rects[i]
                hov = er.collidepoint(mx_now, my_now)
                pygame.draw.rect(screen, lerp_color(PANEL, ecol, 0.25 if hov else 0.12), er, border_radius=7)
                pygame.draw.rect(screen, ecol, er, 1 if not hov else 2, border_radius=7)
                el = fonts["small"].render(elabel, True, ecol)
                screen.blit(el, (er.centerx - el.get_width() // 2,
                                  er.centery - el.get_height() // 2))

        # ── Profile widget — top-left ─────────────────────────────────────────
        PW_X = 10; PW_Y = 44
        av      = PROFILE.get_avatar()
        AV_SIZE = 64
        av_box  = pygame.Rect(PW_X, PW_Y, AV_SIZE, AV_SIZE)
        pygame.draw.rect(screen, PANEL, av_box, border_radius=8)
        pygame.draw.rect(screen, (140, 80, 255), av_box, 1, border_radius=8)
        if av:
            screen.blit(av, av_box.topleft)
            pygame.draw.rect(screen, (140, 80, 255), av_box, 1, border_radius=8)
        else:
            init   = PROFILE.username[:1].upper() if PROFILE.username else "?"
            init_s = fonts["large"].render(init, True, (140, 80, 255))
            screen.blit(init_s, (av_box.centerx - init_s.get_width() // 2,
                                  av_box.centery - init_s.get_height() // 2))

        tx       = PW_X + AV_SIZE + 8
        name_s   = fonts["med"].render(PROFILE.username or "No Profile", True, WHITE)
        lvl_s    = fonts["small"].render(f"Account Lvl  {PROFILE.account_level}", True, (180, 140, 255))
        xp_in_lvl = PROFILE.account_xp % PROFILE.XP_PER_LEVEL
        screen.blit(name_s, (tx, PW_Y + 4))
        screen.blit(lvl_s,  (tx, PW_Y + 26))

        # Pen / rename icon — small pencil drawn to the right of the name
        pen_x = tx + name_s.get_width() + 6
        pen_y = PW_Y + 4
        pen_rect = pygame.Rect(pen_x, pen_y, 16, 16)
        pen_hov  = pen_rect.collidepoint(mx_now, my_now)
        pc = (200, 160, 255) if pen_hov else (100, 80, 140)
        # Pencil body diagonal
        pygame.draw.line(screen, pc, (pen_x + 2, pen_y + 13), (pen_x + 13, pen_y + 2), 2)
        # Tip
        pygame.draw.line(screen, pc, (pen_x + 1, pen_y + 14), (pen_x + 3, pen_y + 14), 1)
        pygame.draw.line(screen, pc, (pen_x + 1, pen_y + 13), (pen_x + 1, pen_y + 15), 1)
        # Eraser end
        pygame.draw.rect(screen, pc, (pen_x + 11, pen_y + 1, 4, 3))

        xp_bar_w = 100
        draw_bar(screen, tx, PW_Y + 46, xp_bar_w, 6,
                 xp_in_lvl, PROFILE.XP_PER_LEVEL, (140, 80, 255))
        xp_s = fonts["tiny"].render(f"{xp_in_lvl}/{PROFILE.XP_PER_LEVEL} XP", True, GRAY)
        screen.blit(xp_s, (tx + xp_bar_w + 4, PW_Y + 42))

        # ── Rename overlay ────────────────────────────────────────────────────
        if show_rename:
            ov_r = pygame.Surface((SW, SH), pygame.SRCALPHA)
            ov_r.fill((0, 0, 0, 180))
            screen.blit(ov_r, (0, 0))
            RW, RH = 360, 130
            RX = SW // 2 - RW // 2; RY = SH // 2 - RH // 2
            pygame.draw.rect(screen, (18, 16, 30), (RX, RY, RW, RH), border_radius=12)
            pygame.draw.rect(screen, (140, 80, 255), (RX, RY, RW, RH), 2, border_radius=12)
            rt = fonts["med"].render("Rename Profile", True, (180, 140, 255))
            screen.blit(rt, (RX + RW // 2 - rt.get_width() // 2, RY + 10))
            rib = pygame.Rect(RX + 16, RY + 44, RW - 32, 38)
            pygame.draw.rect(screen, PANEL, rib, border_radius=8)
            pygame.draw.rect(screen, CYAN,  rib, 2, border_radius=8)
            disp_r = rename_buf + ("|" if cursor_blink % 60 < 30 else "")
            screen.blit(fonts["large"].render(disp_r, True, WHITE), (rib.x + 10, rib.y + 6))
            rcb = pygame.Rect(RX + 16, RY + 92, RW - 32, 30)
            rc_col = GREEN if rename_buf.strip() else GRAY
            pygame.draw.rect(screen, lerp_color(PANEL, rc_col, 0.3), rcb, border_radius=8)
            pygame.draw.rect(screen, rc_col, rcb, 1, border_radius=8)
            rc_lbl = fonts["small"].render("Confirm", True, rc_col)
            screen.blit(rc_lbl, (rcb.centerx - rc_lbl.get_width() // 2,
                                  rcb.centery - rc_lbl.get_height() // 2))

        # ── Inventory overlay ─────────────────────────────────────────────────
        if show_inventory:
            ov_inv = pygame.Surface((SW, SH), pygame.SRCALPHA)
            ov_inv.fill((0, 0, 0, 210))
            screen.blit(ov_inv, (0, 0))

            IW, IH = 860, 560
            IX = SW // 2 - IW // 2; IY = SH // 2 - IH // 2
            INV_COL = (255, 160, 60)
            pygame.draw.rect(screen, (14, 12, 24), (IX, IY, IW, IH), border_radius=16)
            pygame.draw.rect(screen, INV_COL,      (IX, IY, IW, IH), 2, border_radius=16)

            # Title
            ititle = fonts["large"].render("Inventory", True, INV_COL)
            screen.blit(ititle, (IX + IW // 2 - ititle.get_width() // 2, IY + 14))

            if case_anim["phase"] == "idle":
                # ── Idle view ─────────────────────────────────────────────────
                # Case icon (drawn box)
                cx_case = IX + IW // 2
                cy_case = IY + 200
                case_size = 80
                pygame.draw.rect(screen, (60, 42, 18),
                                 (cx_case - case_size, cy_case - case_size // 2,
                                  case_size * 2, case_size), border_radius=8)
                pygame.draw.rect(screen, INV_COL,
                                 (cx_case - case_size, cy_case - case_size // 2,
                                  case_size * 2, case_size), 3, border_radius=8)
                # Clasp
                pygame.draw.rect(screen, INV_COL,
                                 (cx_case - 14, cy_case - case_size // 2 - 10, 28, 12),
                                 border_radius=4)
                pygame.draw.rect(screen, (40, 28, 8),
                                 (cx_case - 8, cy_case - case_size // 2 - 8, 16, 8),
                                 border_radius=3)
                # Case count
                cc_s = fonts["huge"].render(str(TOKENS.cases), True, WHITE)
                screen.blit(cc_s, (cx_case - cc_s.get_width() // 2, cy_case - case_size // 2 + 12))
                cl_s = fonts["small"].render(f"case{'s' if TOKENS.cases != 1 else ''} in inventory", True, GRAY)
                screen.blit(cl_s, (IX + IW // 2 - cl_s.get_width() // 2, cy_case + case_size // 2 + 10))

                # Key cost note
                key_s = fonts["small"].render("Key cost: 5 tokens per open", True, (255, 200, 60))
                screen.blit(key_s, (IX + IW // 2 - key_s.get_width() // 2, cy_case + case_size // 2 + 36))

                # Open button
                can_open = TOKENS.cases > 0 and TOKENS.total >= 5
                ob_w, ob_h = 260, 52
                inv_open_rect = pygame.Rect(IX + IW // 2 - ob_w // 2, IY + IH - 110, ob_w, ob_h)
                ob_col = INV_COL if can_open else GRAY
                pygame.draw.rect(screen, lerp_color(PANEL, ob_col, 0.3 if can_open else 0.1),
                                 inv_open_rect, border_radius=10)
                pygame.draw.rect(screen, ob_col, inv_open_rect, 2, border_radius=10)
                ob_lbl = fonts["large"].render("Open Case  (5 tokens)", True, ob_col)
                screen.blit(ob_lbl, (inv_open_rect.centerx - ob_lbl.get_width() // 2,
                                     inv_open_rect.centery - ob_lbl.get_height() // 2))

                hint_inv = fonts["tiny"].render("ESC to close", True, GRAY)
                screen.blit(hint_inv, (IX + IW // 2 - hint_inv.get_width() // 2, IY + IH - 24))

            else:
                # ── Animation / result view ───────────────────────────────────
                CARD_W_S = 140; CARD_H_S = 180; CARD_GAP = 8
                STRIP_STEP = CARD_W_S + CARD_GAP

                # Advance animation each frame
                if case_anim["phase"] == "spinning":
                    f  = case_anim["frame"]
                    tf = case_anim["total_frames"]
                    case_anim["frame"] = f + 1
                    if f >= tf:
                        case_anim["offset_x"] = case_anim["target_x"]
                        case_anim["phase"]    = "landed"
                        case_anim["glow"]     = 0
                        TOKENS.unlock_cosmetic(case_anim["result"]["cosm_id"])
                    else:
                        # Cubic ease-out: fast start, smooth deceleration
                        t_norm = f / tf          # 0 → 1
                        ease   = 1 - (1 - t_norm) ** 3
                        case_anim["offset_x"] = ease * case_anim["target_x"]

                if case_anim["phase"] == "landed":
                    case_anim["glow"] = (case_anim["glow"] + 1) % 60

                # Draw strip inside a clipping region
                strip_y    = IY + 140
                strip_clip = pygame.Rect(IX + 20, strip_y, IW - 40, CARD_H_S)
                pygame.draw.rect(screen, (10, 8, 20), strip_clip, border_radius=6)

                strip_surf = pygame.Surface((IW - 40, CARD_H_S), pygame.SRCALPHA)
                off = int(case_anim["offset_x"])
                for ci, entry in enumerate(case_anim["strip"]):
                    card_x = ci * STRIP_STEP - off
                    if card_x + CARD_W_S < 0 or card_x > IW - 40:
                        continue
                    rarity_name, rarity_col = entry["rarity"]
                    # Card background
                    pygame.draw.rect(strip_surf, lerp_color((20, 16, 30), rarity_col, 0.25),
                                     (card_x, 4, CARD_W_S, CARD_H_S - 8), border_radius=8)
                    pygame.draw.rect(strip_surf, rarity_col,
                                     (card_x, 4, CARD_W_S, CARD_H_S - 8), 2, border_radius=8)
                    # Cosmetic preview circle
                    cosm = next((c for c in COSMETICS + CASE_COSMETICS if c["id"] == entry["cosm_id"]), None)
                    if cosm:
                        _draw_cosmetic_preview(strip_surf, cosm["pattern"], cosm["preview"],
                                               card_x + CARD_W_S // 2, CARD_H_S // 2 - 14, 30)
                        cn = fonts["tiny"].render(cosm["name"], True, WHITE)
                        strip_surf.blit(cn, (card_x + CARD_W_S // 2 - cn.get_width() // 2,
                                             CARD_H_S - 36))
                    rar_s = fonts["tiny"].render(rarity_name, True, rarity_col)
                    strip_surf.blit(rar_s, (card_x + CARD_W_S // 2 - rar_s.get_width() // 2,
                                            CARD_H_S - 20))

                screen.blit(strip_surf, (IX + 20, strip_y))

                # Centre indicator line
                line_x = IX + IW // 2
                pygame.draw.rect(screen, (255, 255, 255),
                                 (line_x - 2, strip_y - 6, 4, CARD_H_S + 12), border_radius=2)

                # Result display when landed
                if case_anim["phase"] == "landed":
                    won  = case_anim["result"]
                    cosm = next((c for c in COSMETICS + CASE_COSMETICS if c["id"] == won["cosm_id"]), None)
                    rarity_name, rarity_col = won["rarity"]
                    glow_pulse = abs(math.sin(case_anim["glow"] * 0.1)) * 0.5 + 0.5
                    glow_col   = tuple(int(c * glow_pulse) for c in rarity_col)

                    res_y = strip_y + CARD_H_S + 18
                    won_s = fonts["large"].render(
                        f"You won: {cosm['name'] if cosm else won['cosm_id']}!", True, rarity_col)
                    pygame.draw.rect(screen, glow_col,
                                     (IX + IW // 2 - won_s.get_width() // 2 - 12, res_y - 4,
                                      won_s.get_width() + 24, won_s.get_height() + 8), border_radius=8)
                    screen.blit(won_s, (IX + IW // 2 - won_s.get_width() // 2, res_y))
                    rar_res = fonts["med"].render(rarity_name, True, rarity_col)
                    screen.blit(rar_res, (IX + IW // 2 - rar_res.get_width() // 2, res_y + 36))
                    click_hint = fonts["small"].render("Click anywhere to continue", True, GRAY)
                    screen.blit(click_hint, (IX + IW // 2 - click_hint.get_width() // 2, IY + IH - 30))
                else:
                    spin_hint = fonts["small"].render("Opening...", True, GRAY)
                    screen.blit(spin_hint, (IX + IW // 2 - spin_hint.get_width() // 2, IY + IH - 30))

        # Rotating tip
        tip_s = fonts["small"].render(f"Tip: {MENU_TIPS[tip_idx]}", True, YELLOW)
        screen.blit(tip_s, (SW // 2 - tip_s.get_width() // 2, SH - 44))

        # ── Achievements overlay ──────────────────────────────────────────────
        if show_achievements:
            ov_a = pygame.Surface((SW, SH), pygame.SRCALPHA)
            ov_a.fill((0, 0, 0, 210))
            screen.blit(ov_a, (0, 0))

            AW, AH = 980, 580
            AX = SW // 2 - AW // 2; AY = SH // 2 - AH // 2
            ach_col = (255, 80, 40) if ach_tab == 1 else (180, 120, 255)
            pygame.draw.rect(screen, (18, 16, 30), (AX, AY, AW, AH), border_radius=16)
            pygame.draw.rect(screen, ach_col, (AX, AY, AW, AH), 2, border_radius=16)

            # Tab buttons
            tab_labels = ["Normal", "Hardcore"]
            tab_cols   = [(180, 120, 255), (255, 80, 40)]
            for ti in range(2):
                tr = pygame.Rect(AX + 16 + ti * 160, AY + 12, 148, 34)
                is_active = (ti == ach_tab)
                tc = tab_cols[ti]
                pygame.draw.rect(screen, lerp_color(PANEL, tc, 0.4 if is_active else 0.1), tr, border_radius=8)
                pygame.draw.rect(screen, tc, tr, 2 if is_active else 1, border_radius=8)
                tl = fonts["med"].render(tab_labels[ti], True, tc)
                screen.blit(tl, (tr.centerx - tl.get_width() // 2, tr.centery - tl.get_height() // 2))
                if is_active:
                    pygame.draw.rect(screen, tc, (tr.x, tr.bottom + 2, tr.width, 3), border_radius=2)

            # Achievement count summary
            NORMAL_CATS = {"bosses", "levels", "waves", "kills", "cosmetics", "weapons", "gold", "meta"}
            HC_CATS     = {"hardcore"}
            visible_achs = [a for a in ACHIEVEMENTS
                            if a["cat"] in (HC_CATS if ach_tab == 1 else NORMAL_CATS)]
            tab_ids        = [a["id"] for a in visible_achs]
            unlocked_count = sum(1 for aid in tab_ids if aid in PROFILE.unlocked)
            prog_s = fonts["small"].render(f"{unlocked_count} / {len(tab_ids)} unlocked", True, GRAY)
            screen.blit(prog_s, (AX + AW - prog_s.get_width() - 28, AY + 20))

            # Grid layout constants — taller cards to fit all content without overflow
            CARD_W    = 282; CARD_H   = 90
            COLS      = 3;   CGAP_X   = 12; CGAP_Y = 10
            SCROLL_W  = 8
            GRID_X    = AX + 16
            GRID_Y    = AY + 58
            GRID_W    = AW - 32 - SCROLL_W - 6
            GRID_MAX_H = AH - 58 - 36

            total_rows   = math.ceil(len(visible_achs) / COLS)
            total_grid_h = total_rows * (CARD_H + CGAP_Y)

            # Clamp scroll
            max_scroll   = max(0, total_grid_h - GRID_MAX_H)
            ach_scroll   = max(0, min(ach_scroll, max_scroll))

            # Helper: truncate text to fit max_w pixels, appending '...'
            def _fit(font, text, max_w):
                if font.size(text)[0] <= max_w:
                    return text
                while text and font.size(text + "...")[0] > max_w:
                    text = text[:-1]
                return text + "..."

            clip_surf = pygame.Surface((GRID_W, GRID_MAX_H), pygame.SRCALPHA)
            clip_surf.fill((0, 0, 0, 0))

            for idx, ach in enumerate(visible_achs):
                col_i = idx % COLS
                row_i = idx // COLS
                cx    = col_i * (CARD_W + CGAP_X)
                cy    = row_i * (CARD_H + CGAP_Y) - ach_scroll

                if cy + CARD_H < 0 or cy > GRID_MAX_H:
                    continue

                is_done  = ach["id"] in PROFILE.unlocked
                card_col = ach_col if is_done else (60, 60, 75)
                bg_col   = lerp_color((18, 16, 30), card_col, 0.25 if is_done else 0.06)
                pygame.draw.rect(clip_surf, bg_col,   (cx, cy, CARD_W, CARD_H), border_radius=8)
                pygame.draw.rect(clip_surf, card_col, (cx, cy, CARD_W, CARD_H),
                                 1 if not is_done else 2, border_radius=8)

                # Usable inner width (10px left pad, 32px right reserved for checkmark/tokens)
                INNER_W = CARD_W - 42

                # Row 1 — achievement name
                name_col  = WHITE if is_done else (90, 90, 100)
                name_text = _fit(fonts["small"], ach["name"], INNER_W)
                nm = fonts["small"].render(name_text, True, name_col)
                clip_surf.blit(nm, (cx + 10, cy + 7))

                # Row 2 — description (always shown, tiny font)
                desc_col  = (160, 160, 170) if is_done else (75, 75, 85)
                desc_text = _fit(fonts["tiny"], ach["desc"], INNER_W + 20)
                ds = fonts["tiny"].render(desc_text, True, desc_col)
                clip_surf.blit(ds, (cx + 10, cy + 27))

                # Row 3 — progress bar (only when trackable and not yet done)
                prog = PROFILE.get_progress(ach["id"])
                if prog and not is_done:
                    cur, mx2   = prog
                    bar_x      = cx + 10
                    bar_y      = cy + 44
                    bar_w      = CARD_W - 20
                    bar_h      = 6
                    pygame.draw.rect(clip_surf, (40, 40, 55), (bar_x, bar_y, bar_w, bar_h), border_radius=3)
                    fill_w = int(bar_w * min(cur, mx2) / mx2) if mx2 else 0
                    if fill_w > 0:
                        pygame.draw.rect(clip_surf, card_col, (bar_x, bar_y, fill_w, bar_h), border_radius=3)
                    # Progress text: "cur / max" centred below bar
                    prog_text = f"{cur:,} / {mx2:,}"
                    pt_s = fonts["tiny"].render(prog_text, True, (120, 120, 140))
                    clip_surf.blit(pt_s, (cx + 10, bar_y + 9))
                else:
                    # When done or no progress, just leave the space (description fills it)
                    pass

                # Row 4 — token reward (bottom-left) + checkmark (bottom-right)
                tok_col  = (255, 200, 60) if is_done else (80, 70, 40)
                tok_text = f"+{ach['tokens']} token{'s' if ach['tokens'] != 1 else ''}"
                tok_s    = fonts["tiny"].render(tok_text, True, tok_col)
                clip_surf.blit(tok_s, (cx + 10, cy + CARD_H - 17))

                if is_done:
                    ck_x = cx + CARD_W - 22
                    ck_y = cy + CARD_H - 16
                    pygame.draw.line(clip_surf, card_col, (ck_x,      ck_y + 6),  (ck_x + 5,  ck_y + 12), 2)
                    pygame.draw.line(clip_surf, card_col, (ck_x + 5,  ck_y + 12), (ck_x + 13, ck_y),      2)

            screen.blit(clip_surf, (GRID_X, GRID_Y))

            # ── Scrollbar ─────────────────────────────────────────────────────
            if total_grid_h > GRID_MAX_H:
                sb_x    = AX + AW - SCROLL_W - 14
                sb_y    = GRID_Y
                sb_h    = GRID_MAX_H
                # Track
                pygame.draw.rect(screen, (35, 30, 50), (sb_x, sb_y, SCROLL_W, sb_h), border_radius=4)
                # Thumb — proportional size, clamped to track
                thumb_h = max(30, int(sb_h * GRID_MAX_H / total_grid_h))
                thumb_y = sb_y + int((sb_h - thumb_h) * ach_scroll / max_scroll) if max_scroll else sb_y
                pygame.draw.rect(screen, ach_col, (sb_x, thumb_y, SCROLL_W, thumb_h), border_radius=4)

            # Fade top and bottom edges of grid for a soft clipping look
            for edge_y, direction in [(GRID_Y, 1), (GRID_Y + GRID_MAX_H - 20, -1)]:
                for i in range(20):
                    fade_a = int(210 * (1 - i / 20)) if direction == 1 else int(210 * (i / 20))
                    fade_s = pygame.Surface((GRID_W, 1), pygame.SRCALPHA)
                    fade_s.fill((18, 16, 30, fade_a))
                    screen.blit(fade_s, (GRID_X, edge_y + i * direction))

            # Navigation hint
            hint_a = fonts["tiny"].render(
                "◄/► or A/D — switch tabs   |   scroll or Up/Down — scroll list   |   click outside — close",
                True, GRAY)
            screen.blit(hint_a, (AX + AW // 2 - hint_a.get_width() // 2, AY + AH - 22))

        # ── Leaderboard overlay ───────────────────────────────────────────────
        if show_lb:
            ov = pygame.Surface((SW, SH), pygame.SRCALPHA)
            ov.fill((0, 0, 0, 200))
            screen.blit(ov, (0, 0))
            lw2, lh2 = 700, 500
            lx2 = SW // 2 - lw2 // 2; ly2 = SH // 2 - lh2 // 2
            border_col = (255, 100, 40) if lb_page == 1 else YELLOW
            pygame.draw.rect(screen, PANEL,      (lx2, ly2, lw2, lh2), border_radius=14)
            pygame.draw.rect(screen, border_col, (lx2, ly2, lw2, lh2), 2, border_radius=14)

            # Draw the correct leaderboard
            active_lb = lb_hc if lb_page == 1 else lb
            active_lb.draw(screen, fonts, lx2 + 16, ly2 + 16, lw2 - 32, t=cursor_blink)

            # Page dots
            for pi in range(2):
                dot_col = border_col if pi == lb_page else (60, 60, 80)
                pygame.draw.circle(screen, dot_col,
                                   (SW // 2 - 10 + pi * 20, ly2 + lh2 - 58), 5)

            # Prev / Next buttons
            btn_prev = pygame.Rect(lx2 + 16,        ly2 + lh2 - 48, 140, 34)
            btn_next = pygame.Rect(lx2 + lw2 - 156, ly2 + lh2 - 48, 140, 34)

            if lb_page == 1:
                pygame.draw.rect(screen, lerp_color(PANEL, YELLOW, 0.2), btn_prev, border_radius=8)
                pygame.draw.rect(screen, YELLOW, btn_prev, 1, border_radius=8)
                pl = fonts["small"].render("◄ Normal", True, YELLOW)
                screen.blit(pl, (btn_prev.centerx - pl.get_width() // 2,
                                  btn_prev.centery - pl.get_height() // 2))
            else:
                pygame.draw.rect(screen, lerp_color(PANEL, (255, 100, 40), 0.2), btn_next, border_radius=8)
                pygame.draw.rect(screen, (255, 100, 40), btn_next, 1, border_radius=8)
                nl = fonts["small"].render("Hardcore ►", True, (255, 100, 40))
                screen.blit(nl, (btn_next.centerx - nl.get_width() // 2,
                                  btn_next.centery - nl.get_height() // 2))

            close_hint = fonts["tiny"].render(
                "◄/► or A/D to switch pages  •  click outside or any other key to close", True, GRAY)
            screen.blit(close_hint, (SW // 2 - close_hint.get_width() // 2, ly2 + lh2 - 18))

        # ── Patch notes overlay ───────────────────────────────────────────────
        if show_patchnotes:
            ov3 = pygame.Surface((SW, SH), pygame.SRCALPHA)
            ov3.fill((0, 0, 0, 200))
            screen.blit(ov3, (0, 0))
            PNW, PNH = 860, 520
            PNX = SW // 2 - PNW // 2; PNY = SH // 2 - PNH // 2
            PN_COL = (100, 220, 160)
            pygame.draw.rect(screen, PANEL,  (PNX, PNY, PNW, PNH), border_radius=14)
            pygame.draw.rect(screen, PN_COL, (PNX, PNY, PNW, PNH), 2, border_radius=14)

            pn_title = fonts["large"].render("Patch Notes", True, PN_COL)
            screen.blit(pn_title, (PNX + PNW // 2 - pn_title.get_width() // 2, PNY + 14))
            pygame.draw.line(screen, (40, 80, 60),
                             (PNX + 20, PNY + 50), (PNX + PNW - 20, PNY + 50), 1)

            # Category colours
            CAT_COLS = {
                "added":   (80, 220, 120),
                "changed": (80, 180, 255),
                "fixed":   (255, 200, 60),
                "removed": (220, 80, 80),
            }

            col_w   = (PNW - 60) // 2   # each patch takes half the panel width
            y_start = PNY + 62

            for pi, patch in enumerate(PATCH_NOTES):
                cx = PNX + 20 + pi * (col_w + 20)
                cy = y_start

                # Version header
                ver_s  = fonts["med"].render(patch["version"], True, PN_COL)
                date_s = fonts["tiny"].render(patch["date"], True, GRAY)
                screen.blit(ver_s,  (cx, cy))
                screen.blit(date_s, (cx, cy + ver_s.get_height() + 2))
                cy += ver_s.get_height() + date_s.get_height() + 10
                pygame.draw.line(screen, (40, 80, 60),
                                 (cx, cy), (cx + col_w, cy), 1)
                cy += 8

                for cat, text in patch["changes"]:
                    col_c = CAT_COLS.get(cat, GRAY)
                    tag   = fonts["tiny"].render(f"[{cat.upper()}]", True, col_c)
                    # Word-wrap text to fit column
                    words  = text.split()
                    line   = ""
                    lines  = []
                    for word in words:
                        test = line + (" " if line else "") + word
                        if fonts["tiny"].size(test)[0] > col_w - tag.get_width() - 10:
                            lines.append(line)
                            line = word
                        else:
                            line = test
                    if line:
                        lines.append(line)
                    for li, ln in enumerate(lines):
                        row_y = cy + li * 16
                        if row_y + 16 > PNY + PNH - 40:
                            break
                        if li == 0:
                            screen.blit(tag, (cx, row_y))
                            txt = fonts["tiny"].render(ln, True, WHITE)
                            screen.blit(txt, (cx + tag.get_width() + 6, row_y))
                        else:
                            txt = fonts["tiny"].render(ln, True, WHITE)
                            screen.blit(txt, (cx + tag.get_width() + 6, row_y))
                    cy += len(lines) * 16 + 4

            close_pn = fonts["small"].render("Press any key or click to close", True, GRAY)
            screen.blit(close_pn, (SW // 2 - close_pn.get_width() // 2, PNY + PNH - 28))

        # ── Credits overlay ───────────────────────────────────────────────────
        if show_credits:
            CR_COL   = (200, 160, 255)
            CRW      = 640
            MIN_H    = 420
            MAX_H    = SH - 80
            BODY_PAD = 20   # horizontal padding inside panel
            ROW_SEC  = fonts["med"].get_linesize() + 6
            ROW_NAME = fonts["small"].get_linesize() + 4
            ROW_GAP  = 18   # gap after each section
            FOOTER_H = 52   # space for navigation hint at bottom
            HEADER_H = 58   # title + divider

            # Build flat list of (type, text) rows from CREDITS
            all_rows = []
            for section, names in CREDITS.items():
                all_rows.append(("section", section))
                for name in names:
                    all_rows.append(("name", name))
                all_rows.append(("gap", ""))

            # Measure how many rows fit per page
            usable_h = MAX_H - HEADER_H - FOOTER_H
            rows_per_page = []
            h_used = 0
            page_rows = []
            for row in all_rows:
                rh = ROW_SEC if row[0] == "section" else (ROW_NAME if row[0] == "name" else ROW_GAP)
                if h_used + rh > usable_h and page_rows:
                    rows_per_page.append(page_rows)
                    page_rows = [row]
                    h_used    = rh
                else:
                    page_rows.append(row)
                    h_used += rh
            if page_rows:
                rows_per_page.append(page_rows)

            total_pages = max(1, len(rows_per_page))
            credits_page = max(0, min(credits_page, total_pages - 1))

            # Compute actual content height for this page
            page = rows_per_page[credits_page] if rows_per_page else []
            content_h = sum(
                ROW_SEC if r[0] == "section" else (ROW_NAME if r[0] == "name" else ROW_GAP)
                for r in page)
            CRH = max(MIN_H, HEADER_H + content_h + FOOTER_H + BODY_PAD)
            CRH = min(CRH, MAX_H)
            CRX = SW // 2 - CRW // 2
            CRY = SH // 2 - CRH // 2

            ov4 = pygame.Surface((SW, SH), pygame.SRCALPHA)
            ov4.fill((0, 0, 0, 210))
            screen.blit(ov4, (0, 0))
            pygame.draw.rect(screen, PANEL,  (CRX, CRY, CRW, CRH), border_radius=14)
            pygame.draw.rect(screen, CR_COL, (CRX, CRY, CRW, CRH), 2, border_radius=14)

            cr_title = fonts["large"].render("Credits", True, CR_COL)
            screen.blit(cr_title, (CRX + CRW // 2 - cr_title.get_width() // 2, CRY + 14))
            pygame.draw.line(screen, (80, 60, 120),
                             (CRX + 20, CRY + 50), (CRX + CRW - 20, CRY + 50), 1)

            # Draw rows for current page
            cy2 = CRY + HEADER_H
            for rtype, rtext in page:
                if rtype == "section":
                    sec_s = fonts["med"].render(rtext, True, CR_COL)
                    screen.blit(sec_s, (CRX + CRW // 2 - sec_s.get_width() // 2, cy2))
                    cy2 += ROW_SEC
                elif rtype == "name":
                    nm_s = fonts["small"].render(rtext, True, WHITE)
                    screen.blit(nm_s, (CRX + CRW // 2 - nm_s.get_width() // 2, cy2))
                    cy2 += ROW_NAME
                else:
                    pygame.draw.line(screen, (50, 40, 70),
                                     (CRX + 40, cy2 + ROW_GAP // 2),
                                     (CRX + CRW - 40, cy2 + ROW_GAP // 2), 1)
                    cy2 += ROW_GAP

            # Footer: page nav + close hint
            footer_y = CRY + CRH - FOOTER_H + 8
            if total_pages > 1:
                prev_col = CR_COL if credits_page > 0 else GRAY
                next_col = CR_COL if credits_page < total_pages - 1 else GRAY
                prev_s = fonts["small"].render("< Prev", True, prev_col)
                next_s = fonts["small"].render("Next >", True, next_col)
                page_s = fonts["small"].render(f"{credits_page + 1} / {total_pages}", True, WHITE)
                screen.blit(prev_s, (CRX + 20, footer_y))
                screen.blit(page_s, (CRX + CRW // 2 - page_s.get_width() // 2, footer_y))
                screen.blit(next_s, (CRX + CRW - next_s.get_width() - 20, footer_y))
            hint_y = footer_y + fonts["small"].get_linesize() + 4
            close_cr = fonts["small"].render(
                "Arrow keys to page  |  Any other key or click to close", True, GRAY)
            screen.blit(close_cr, (CRX + CRW // 2 - close_cr.get_width() // 2, hint_y))
        if show_settings:
            ov2 = pygame.Surface((SW, SH), pygame.SRCALPHA)
            ov2.fill((0, 0, 0, 180))
            screen.blit(ov2, (0, 0))

            pygame.draw.rect(screen, PANEL,           (SP_X, SP_Y, SP_W, SP_H), border_radius=14)
            pygame.draw.rect(screen, (140, 180, 255), (SP_X, SP_Y, SP_W, SP_H), 2, border_radius=14)

            stitle = fonts["large"].render("Settings", True, (140, 180, 255))
            screen.blit(stitle, (SP_X + SP_W // 2 - stitle.get_width() // 2, SP_Y + 18))
            pygame.draw.line(screen, (60, 60, 90), (SP_X + 20, SP_Y + 55), (SP_X + SP_W - 20, SP_Y + 55), 1)

            def _draw_slider(label, vol, sy):
                lbl = fonts["med"].render(label, True, WHITE)
                screen.blit(lbl, (SP_X + 20, sy - 32))
                pct = fonts["med"].render(f"{int(vol * 100)}%", True, CYAN)
                screen.blit(pct, (SP_X + SP_W - 20 - pct.get_width(), sy - 32))
                pygame.draw.rect(screen, (50, 50, 70), (SLIDER_X, sy - 4, SLIDER_W, 8), border_radius=4)
                fw = int(vol * SLIDER_W)
                if fw > 0:
                    pygame.draw.rect(screen, CYAN, (SLIDER_X, sy - 4, fw, 8), border_radius=4)
                kx = int(SLIDER_X + vol * SLIDER_W)
                pygame.draw.circle(screen, WHITE, (kx, sy), 12)
                pygame.draw.circle(screen, CYAN,  (kx, sy), 10)
                pygame.draw.circle(screen, WHITE, (kx, sy), 10, 2)
                for t in (0.0, 0.25, 0.5, 0.75, 1.0):
                    tx = int(SLIDER_X + t * SLIDER_W)
                    pygame.draw.line(screen, (80, 80, 100), (tx, sy + 14), (tx, sy + 20), 1)

            _draw_slider("Music Volume",  MUSIC.volume,  SLIDER_Y)
            pygame.draw.line(screen, (45, 45, 65),
                             (SP_X + 20, SLIDER_Y + 28), (SP_X + SP_W - 20, SLIDER_Y + 28), 1)
            _draw_slider("Sound Effects", SOUNDS.volume, SLIDER2_Y)
            pygame.draw.line(screen, (45, 45, 65),
                             (SP_X + 20, SLIDER2_Y + 28), (SP_X + SP_W - 20, SLIDER2_Y + 28), 1)

            # Quality toggle
            qlbl = fonts["med"].render("Quality", True, WHITE)
            screen.blit(qlbl, (SP_X + 20, QUALITY_Y + 8))
            btn_w2 = (SP_W - 60) // 2
            for qi, (qlabel, qval) in enumerate([("Low", "low"), ("High", "high")]):
                qx   = SP_X + SP_W // 2 - btn_w2 + qi * (btn_w2 + 10)
                active_q = GAME_SETTINGS.quality == qval
                qcol = (100, 220, 100) if qval == "high" else (220, 160, 60)
                qbg  = lerp_color(PANEL, qcol, 0.3 if active_q else 0.05)
                pygame.draw.rect(screen, qbg,  (qx, QUALITY_Y, btn_w2, 36), border_radius=8)
                pygame.draw.rect(screen, qcol if active_q else GRAY,
                                 (qx, QUALITY_Y, btn_w2, 36), 2 if active_q else 1, border_radius=8)
                qt = fonts["med"].render(qlabel, True, qcol if active_q else GRAY)
                screen.blit(qt, (qx + btn_w2 // 2 - qt.get_width() // 2,
                                 QUALITY_Y + 18 - qt.get_height() // 2))

            close_h = fonts["small"].render("Click outside or press ESC / ENTER to close", True, GRAY)
            screen.blit(close_h, (SP_X + SP_W // 2 - close_h.get_width() // 2, SP_Y + SP_H - 28))

            # Player health bar toggle
            pygame.draw.line(screen, (45, 45, 65),
                             (SP_X + 20, HEALTHBAR_Y - 10), (SP_X + SP_W - 20, HEALTHBAR_Y - 10), 1)
            hb_col  = (80, 220, 140)
            hb_on   = GAME_SETTINGS.player_health_bar
            hb_lbl  = fonts["med"].render("Player Health Bar", True, WHITE)
            screen.blit(hb_lbl, (SP_X + 20, HEALTHBAR_Y + 8))
            # Toggle pill
            pill_x = SP_X + SP_W - 80; pill_y = HEALTHBAR_Y + 4
            pill_w = 56; pill_h = 28
            pill_bg = lerp_color(PANEL, hb_col, 0.35 if hb_on else 0.05)
            pygame.draw.rect(screen, pill_bg, (pill_x, pill_y, pill_w, pill_h), border_radius=14)
            pygame.draw.rect(screen, hb_col if hb_on else GRAY,
                             (pill_x, pill_y, pill_w, pill_h), 2, border_radius=14)
            knob_x = pill_x + pill_w - 16 if hb_on else pill_x + 12
            pygame.draw.circle(screen, hb_col if hb_on else GRAY, (knob_x, pill_y + pill_h // 2), 10)
            on_off = fonts["tiny"].render("ON" if hb_on else "OFF", True, hb_col if hb_on else GRAY)
            screen.blit(on_off, (pill_x + pill_w // 2 - on_off.get_width() // 2,
                                  pill_y + pill_h // 2 - on_off.get_height() // 2))

            # ── Fullscreen toggle ─────────────────────────────────────────────
            pygame.draw.line(screen, (45, 45, 65),
                             (SP_X + 20, FSCRN_Y - 10), (SP_X + SP_W - 20, FSCRN_Y - 10), 1)
            fs_col = (200, 160, 255)
            fs_on  = GAME_SETTINGS.fullscreen
            fs_lbl = fonts["med"].render("Fullscreen", True, WHITE)
            screen.blit(fs_lbl, (SP_X + 20, FSCRN_Y + 8))
            fs_pill_x = SP_X + SP_W - 80; fs_pill_y = FSCRN_Y + 4
            fs_pill_bg = lerp_color(PANEL, fs_col, 0.35 if fs_on else 0.05)
            pygame.draw.rect(screen, fs_pill_bg,
                             (fs_pill_x, fs_pill_y, pill_w, pill_h), border_radius=14)
            pygame.draw.rect(screen, fs_col if fs_on else GRAY,
                             (fs_pill_x, fs_pill_y, pill_w, pill_h), 2, border_radius=14)
            fs_knob_x = fs_pill_x + pill_w - 16 if fs_on else fs_pill_x + 12
            pygame.draw.circle(screen, fs_col if fs_on else GRAY,
                               (fs_knob_x, fs_pill_y + pill_h // 2), 10)
            fs_oo = fonts["tiny"].render("ON" if fs_on else "OFF", True, fs_col if fs_on else GRAY)
            screen.blit(fs_oo, (fs_pill_x + pill_w // 2 - fs_oo.get_width() // 2,
                                fs_pill_y + pill_h // 2 - fs_oo.get_height() // 2))

        # Version label bottom-centre of menu screen
        ver_menu = fonts["tiny"].render(GAME_VERSION, True, (55, 55, 70))
        screen.blit(ver_menu, (SW // 2 - ver_menu.get_width() // 2, SH - 18))

        # ── Tutorial overlay ──────────────────────────────────────────────────
        if show_tutorial:
            TW, TH = 720, 480
            TX = SW // 2 - TW // 2; TY = SH // 2 - TH // 2

            tov = pygame.Surface((SW, SH), pygame.SRCALPHA)
            tov.fill((0, 0, 0, 200))
            screen.blit(tov, (0, 0))

            pygame.draw.rect(screen, PANEL, (TX, TY, TW, TH), border_radius=14)
            pygame.draw.rect(screen, CYAN,  (TX, TY, TW, TH), 2, border_radius=14)

            ttitle = fonts["large"].render("How to Play", True, CYAN)
            screen.blit(ttitle, (TX + TW // 2 - ttitle.get_width() // 2, TY + 16))
            pygame.draw.line(screen, (50, 50, 80), (TX + 16, TY + 50), (TX + TW - 16, TY + 50), 1)

            # Page dots
            for pi in range(2):
                dot_col = CYAN if pi == tutorial_page else (60, 60, 80)
                pygame.draw.circle(screen, dot_col,
                                   (TX + TW // 2 - 10 + pi * 20, TY + 60), 5)

            if tutorial_page == 0:
                pg_title = fonts["med"].render("Controls", True, YELLOW)
                screen.blit(pg_title, (TX + TW // 2 - pg_title.get_width() // 2, TY + 72))

                controls = [
                    ("Movement",    "WASD  /  Arrow Keys"),
                    ("Shoot",       "Hold Left Mouse Button  —  aim with cursor"),
                    ("Dash",        "SPACE  —  2 charges, ~7s cooldown each"),
                    ("Switch Weapon","Mouse Wheel  /  number keys in shop"),
                    ("Open Shop",   "TAB  —  buy weapons & heal here"),
                    ("Pause",       "P  or  ESC"),
                ]
                for ri, (label, value) in enumerate(controls):
                    ry = TY + 110 + ri * 46
                    pygame.draw.rect(screen, (35, 35, 52),
                                     (TX + 20, ry, TW - 40, 38), border_radius=6)
                    pygame.draw.rect(screen, (55, 55, 80),
                                     (TX + 20, ry, TW - 40, 38), 1, border_radius=6)
                    lbl_s = fonts["small"].render(label, True, (160, 200, 255))
                    val_s = fonts["small"].render(value, True, WHITE)
                    screen.blit(lbl_s, (TX + 34, ry + 10))
                    screen.blit(val_s, (TX + 200, ry + 10))
            else:
                pg_title = fonts["med"].render("Game Mechanics", True, YELLOW)
                screen.blit(pg_title, (TX + TW // 2 - pg_title.get_width() // 2, TY + 72))

                mechanics = [
                    ("Waves",        "Survive endless enemy waves. Every 10th wave is a Boss wave."),
                    ("Gold & Shop",  "Enemies drop gold — open shop with TAB to buy weapons & heal."),
                    ("HP Orbs",      "Enemies occasionally drop green HP orbs. Uncollected drops are auto-collected after 10s."),
                    ("Perks",        "Every 5 waves pick 1 of 3 perk cards: Damage, Defense, Speed, Lifesteal, Range, and more."),
                    ("Corruption",   "Special waves spawn elite enemies — harder, but worth more gold and XP."),
                    ("Bosses",       "Defeat bosses to earn Tokens. Spend tokens on cosmetics in the shop."),
                ]
                for ri, (label, value) in enumerate(mechanics):
                    ry = TY + 110 + ri * 46
                    pygame.draw.rect(screen, (35, 35, 52),
                                     (TX + 20, ry, TW - 40, 38), border_radius=6)
                    pygame.draw.rect(screen, (55, 55, 80),
                                     (TX + 20, ry, TW - 40, 38), 1, border_radius=6)
                    lbl_s = fonts["small"].render(label, True, (160, 200, 255))
                    val_s = fonts["tiny"].render(value, True, WHITE)
                    screen.blit(lbl_s, (TX + 34, ry + 10))
                    screen.blit(val_s, (TX + 175, ry + 12))

            next_btn = pygame.Rect(TX + TW - 180, TY + TH - 56, 160, 40)
            prev_btn = pygame.Rect(TX + 20,        TY + TH - 56, 160, 40)

            if tutorial_page == 1:
                pygame.draw.rect(screen, lerp_color(PANEL, (80, 200, 80), 0.25),
                                 prev_btn, border_radius=8)
                pygame.draw.rect(screen, (80, 150, 80), prev_btn, 1, border_radius=8)
                pb_lbl = fonts["med"].render("◄ Back", True, (150, 220, 150))
                screen.blit(pb_lbl, (prev_btn.centerx - pb_lbl.get_width() // 2,
                                     prev_btn.centery - pb_lbl.get_height() // 2))

            if tutorial_page == 0:
                btn_label = "Next ►"
                btn_col   = CYAN
            elif show_help:
                btn_label = "Close X"
                btn_col   = (255, 180, 80)
            else:
                btn_label = "Play!  ►"
                btn_col   = GREEN
            pygame.draw.rect(screen, lerp_color(PANEL, btn_col, 0.25),
                             next_btn, border_radius=8)
            pygame.draw.rect(screen, btn_col, next_btn, 2, border_radius=8)
            nb_lbl = fonts["med"].render(btn_label, True, btn_col)
            screen.blit(nb_lbl, (next_btn.centerx - nb_lbl.get_width() // 2,
                                  next_btn.centery - nb_lbl.get_height() // 2))

            hint_t = fonts["tiny"].render(
                "◄/► or A/D to navigate  |  ESC to close", True, GRAY)
            screen.blit(hint_t, (TX + TW // 2 - hint_t.get_width() // 2, TY + TH - 16))

        # ── Top strip — only shown when an update is waiting ────────────────────
        STRIP_H = 34
        update_available = (
            _update_info.get("version") and
            _update_info["version"] != GAME_VERSION
        )

        if update_available:
            pygame.draw.rect(screen, (150, 112, 0), (0, 0, SW, STRIP_H))
            pygame.draw.rect(screen, (220, 170, 0), (0, STRIP_H - 1, SW, 1))

            pre_tag   = "  (pre-release)" if _update_info.get("prerelease") else ""
            ver_txt   = f"Update available:  v{_update_info['version']}{pre_tag}"
            notes_txt = (f"  —  {_update_info['notes']}" if _update_info.get("notes") else "")
            info_surf = fonts["small"].render(ver_txt + notes_txt, True, (255, 240, 180))
            screen.blit(info_surf, (12, STRIP_H // 2 - info_surf.get_height() // 2))

            # Download button — sits left of the X close button
            btn_label = fonts["small"].render("Download", True, (20, 14, 0))
            btn_w     = btn_label.get_width() + 24
            btn_h     = STRIP_H - 8
            btn_x     = SW - btn_w - 52
            btn_y     = 4
            update_rect = pygame.Rect(btn_x, btn_y, btn_w, btn_h)
            mx_u, my_u  = pygame.mouse.get_pos()
            btn_bg      = (255, 220, 60) if update_rect.collidepoint(mx_u, my_u) else (200, 162, 20)
            pygame.draw.rect(screen, btn_bg,         update_rect, border_radius=5)
            pygame.draw.rect(screen, (255, 230, 80), update_rect, 1, border_radius=5)
            screen.blit(btn_label, (btn_x + 12,
                                    STRIP_H // 2 - btn_label.get_height() // 2))

            # X close button
            x_hovered = close_rect.collidepoint(*pygame.mouse.get_pos())
            x_bg  = (180, 30, 30) if x_hovered else (100, 20, 20)
            x_brd = (255, 80, 80) if x_hovered else (160, 40, 40)
            pygame.draw.rect(screen, x_bg,  close_rect, border_radius=6)
            pygame.draw.rect(screen, x_brd, close_rect, 1, border_radius=6)
            cx2 = close_rect.centerx; cy2 = close_rect.centery; arm = 8
            pygame.draw.line(screen, (255, 120, 120), (cx2 - arm, cy2 - arm), (cx2 + arm, cy2 + arm), 2)
            pygame.draw.line(screen, (255, 120, 120), (cx2 + arm, cy2 - arm), (cx2 - arm, cy2 + arm), 2)
        else:
            update_rect = pygame.Rect(0, 0, 1, 1)   # never hit

        # ── Quit confirm dialog ───────────────────────────────────────────────
        if show_quit_confirm:
            QW, QH = 320, 140
            QX = SW // 2 - QW // 2; QY = SH // 2 - QH // 2
            # Dim background
            dim = pygame.Surface((SW, SH), pygame.SRCALPHA)
            dim.fill((0, 0, 0, 160))
            screen.blit(dim, (0, 0))
            # Panel
            pygame.draw.rect(screen, (28, 24, 40), (QX, QY, QW, QH), border_radius=12)
            pygame.draw.rect(screen, (180, 60, 60), (QX, QY, QW, QH), 2, border_radius=12)
            # Title
            q_title = fonts["med"].render("Exit game?", True, (255, 220, 220))
            screen.blit(q_title, (QX + QW // 2 - q_title.get_width() // 2, QY + 20))
            hint_q  = fonts["tiny"].render("ESC to cancel", True, (120, 110, 130))
            screen.blit(hint_q, (QX + QW // 2 - hint_q.get_width() // 2, QY + 48))
            # Buttons
            mx_q, my_q = pygame.mouse.get_pos()
            btn_yes = pygame.Rect(QX + 24,       QY + 76, 120, 40)
            btn_no  = pygame.Rect(QX + QW - 144, QY + 76, 120, 40)
            yes_hov = btn_yes.collidepoint(mx_q, my_q)
            no_hov  = btn_no.collidepoint(mx_q, my_q)
            pygame.draw.rect(screen, (180, 40, 40) if yes_hov else (110, 25, 25),
                             btn_yes, border_radius=8)
            pygame.draw.rect(screen, (255, 80, 80), btn_yes, 1, border_radius=8)
            pygame.draw.rect(screen, (40, 140, 60) if no_hov else (25, 80, 40),
                             btn_no,  border_radius=8)
            pygame.draw.rect(screen, (80, 220, 100), btn_no, 1, border_radius=8)
            yes_lbl = fonts["med"].render("Yes", True, (255, 180, 180))
            no_lbl  = fonts["med"].render("No",  True, (160, 255, 180))
            screen.blit(yes_lbl, (btn_yes.centerx - yes_lbl.get_width() // 2,
                                   btn_yes.centery - yes_lbl.get_height() // 2))
            screen.blit(no_lbl,  (btn_no.centerx  - no_lbl.get_width()  // 2,
                                   btn_no.centery  - no_lbl.get_height() // 2))

        _scaled_flip(screen)

# ── Main Game ─────────────────────────────────────────────────────────────────

class Game:
    def __init__(self, username="Player", render_surf=None, window=None,
                 apply_display_fn=None, checkpoint=None, save_slot=None, hardcore=False):
        # Use the shared render surface passed from the entry point (fixed 1280×720).
        # If called without one (e.g. during testing) fall back to the display surface.
        if render_surf is not None:
            self.screen = render_surf
        else:
            self.screen = pygame.display.set_mode((SW, SH))
        self._window          = window
        self._apply_display   = apply_display_fn
        self._overlay = pygame.Surface((SW, SH), pygame.SRCALPHA)
        pygame.display.set_caption("Dungeon Crawler 45.0b10")
        self.clock    = pygame.time.Clock()
        self.world_w  = 3000; self.world_h = 3000
        self.username = username
        self.save_slot = save_slot
        self.hardcore  = hardcore
        self.fonts    = {
            "large": _make_font(28, bold=True),
            "med":   _make_font(20, bold=True),
            "small": _make_font(15),
            "tiny":  _make_font(13),
            "huge":  _make_font(48, bold=True),
        }
        self.leaderboard    = Leaderboard(hardcore=False)
        self.leaderboard_hc = Leaderboard(hardcore=True)
        self.lb_rank        = None
        self.reset()
        if checkpoint:
            self._apply_checkpoint(checkpoint)
        MUSIC.play("battle")

    def _apply_checkpoint(self, cp):
        """Restore full player + game state from a checkpoint dict."""
        p = self.player
        p.username  = cp.get("username", p.username)
        saved_wave  = cp.get("wave", 1)
        self.boss_killed = cp.get("boss_killed", 0)
        p.level     = cp.get("level", 1)
        p.xp        = cp.get("xp", 0)
        p.xp_to_next = cp.get("xp_to_next", 100)
        p.max_hp    = cp.get("max_hp", 100)
        p.hp        = max(1, cp.get("hp", 100))
        p.gold      = cp.get("gold", 0)
        p.kill_count = cp.get("kill_count", 0)
        p.corruption_waves_cleared = cp.get("corruption_waves_cleared", 0)
        # Weapons
        owned = cp.get("owned_weapons", [0])
        p.owned_weapons = [int(w) for w in owned]
        widx = cp.get("weapon_idx", 0)
        p.weapon_idx = widx if widx in p.owned_weapons else p.owned_weapons[0]
        # Perks
        p.perks = {str(k): float(v) for k, v in cp.get("perks", {}).items()}
        # Restore hardcore flag
        self.hardcore = cp.get("hardcore", False)
        # Sync watermarks to the restored values so achievement checks only count
        # kills and gold earned *after* this load point, not the whole run history.
        self._ach_kills_credited = p.kill_count
        self._ach_gold_credited  = p.gold
        # Wave state — set wave to (saved_wave) so the break's wave += 1 lands on saved_wave + 1.
        # The player resumes from the wave AFTER the one they saved on.
        self.wave              = saved_wave
        self.wave_active       = False
        self.wave_enemy_count  = 0
        self.boss_wave         = False
        self.elite_wave        = False
        self.spawn_timer       = 0
        self.spawn_interval    = max(40, 90 - saved_wave * 3)
        self.wave_enemy_target = self._wave_size(saved_wave + 1)
        self.break_timer       = WAVE_BREAK_SECS * FPS
        self.enemies           = []
        # Restore any uncollected world drops
        self.gold_coins = [
            GoldCoin(gc["x"], gc["y"], gc["amount"])
            for gc in cp.get("gold_coins", [])
        ]
        # Freeze restored coins in place (no initial scatter velocity)
        for gc in self.gold_coins:
            gc.vx = 0.0; gc.vy = 0.0
        self.hp_orbs = [
            HpOrb(orb["x"], orb["y"], orb["amount"])
            for orb in cp.get("hp_orbs", [])
        ]
        for orb in self.hp_orbs:
            orb.vx = 0.0; orb.vy = 0.0
        print(f"[Checkpoint] Loaded: resuming after wave {saved_wave}, level {p.level}, "
              f"{len(p.perks)} perks")

    def reset(self):
        self.player         = Player(self.world_w // 2, self.world_h // 2, self.username)
        self.enemies        = []
        self.projectiles    = []
        self.particles      = []
        self.floating_texts = []
        self.gold_coins     = []
        self.fire_orbs      = []
        self.hp_orbs        = []
        self.shop           = Shop()
        self.perk_screen    = PerkScreen(self.player, self.fonts)
        self.pending_save   = False   # True when a save is deferred until perk screen closes
        self.ach_toasts     = []      # list of dicts: {name, tokens, cat, timer, max_timer}
        self.game_over      = False
        self.paused         = False
        self.lb_rank        = None
        self.pause_settings  = False
        self.pause_slider_drag = False
        self.pause_dev       = False
        self.pause_dev_input = ""    # password entry buffer
        self.pause_dev_prompt = False  # password prompt visible
        self.dev_boss_expand  = False
        self.dev_perk_expand  = False
        self.dev_ach_expand   = False
        self.dev_ach_scroll   = 0   # pixel scroll for achievement flyout
        # Boss state
        self.boss           = None
        self.boss_clone     = None   # Vexara phase-2 clone
        self.boss_intro     = None   # BossIntro cinematic, None when inactive
        self.enrage_anim    = None   # BossEnrageAnim cinematic, None when inactive
        self.boss_wave      = False   # True while boss is alive this wave
        self.boss_pool      = list(range(len(BOSS_TYPES)))
        random.shuffle(self.boss_pool)
        self.boss_pool_idx  = 0
        self.boss_killed    = 0
        self._bosses_killed_names = set()   # tracks which named bosses died this run
        # Watermarks — track what has already been credited to PROFILE so we only
        # ever add the *delta* each time check_achievements is called, not the total.
        self._ach_kills_credited = 0
        self._ach_gold_credited  = 0
        # Wave system
        self.wave              = 1
        self.wave_active       = True
        self.wave_enemy_count  = 0
        self.wave_enemy_target = self._wave_size(1)
        self.spawn_timer       = 0
        self.spawn_interval    = 90
        self.break_timer       = 0
        self.elite_wave             = False   # True when current wave is a corruption wave
        self.corruption_flash_timer = 0       # frames of screen flash on corruption wave start
        self.corruption_zaps        = []      # list of active zig-zag lines: [pts, life, max_life]
        self.corruption_zap_cd      = 0       # countdown until next zap spawns
        # Vexara arena visuals
        self.vex_zaps               = []      # intense zap lines during Vexara boss wave
        self.vex_zap_cd             = 0
        self.vex_runes              = []      # slow drifting hex rune shapes [wx, wy, angle, spin, life, max_life]
        self.vex_rune_cd            = 0
        self.vex_cracks             = []      # pre-generated world-space corruption cracks [[pts,...],...]
        self.vex_wisps              = []      # drifting void wisps [wx, wy, vx, vy, life, max_life, size]
        self.vex_wisp_cd            = 0
        # Nyxoth arena visuals
        self.nyx_stars              = []      # static star field [sx, sy, radius, brightness]
        self.nyx_nebulas            = []      # drifting nebula blobs [wx, wy, r, col, alpha]
        self.nyx_bombs              = []      # active NyxFireBomb objects
        # Malachar arena visuals
        self.mal_lava_flows         = []      # world-space lava rivers [[pts], width, phase]
        self.mal_embers             = []      # floating ember particles [wx,wy,vx,vy,life,max_life]
        self.mal_ember_cd           = 0
        # Seraphix arena visuals
        self.sera_pillars           = []      # world-space pillar positions [wx, wy, height, glow_phase]
        self.sera_motes             = []      # drifting divine light particles [wx,wy,vx,vy,life,max_life,size]
        self.sera_mote_cd           = 0
        # Gorvak dungeon arena visuals
        self.gorv_lanterns          = []      # [wx, wy, flicker_phase, radius]
        self.gorv_chains            = []      # [[(wx,wy),...], thickness]
        self.death_anim_timer       = 0       # > 0 while death animation plays

        # ── Arena floor decorations (world-space, pre-generated once) ────────
        rng = random.Random(42)   # fixed seed so decorations are stable across waves
        # Worn scuff circles — faint rings on the floor
        self.arena_scuffs = [
            (rng.randint(80, self.world_w - 80),
             rng.randint(80, self.world_h - 80),
             rng.randint(18, 52),
             rng.uniform(0.15, 0.40))   # radius, opacity scale
            for _ in range(120)
        ]
        # Stone crack lines — short jagged marks
        self.arena_cracks = []
        for _ in range(80):
            cx = rng.randint(60, self.world_w - 60)
            cy = rng.randint(60, self.world_h - 60)
            ang = rng.uniform(0, math.pi)
            hlen = rng.randint(12, 50)
            jag = rng.uniform(-0.5, 0.5)
            mid = (cx + math.cos(ang + jag) * hlen * 0.5,
                   cy + math.sin(ang + jag) * hlen * 0.5)
            self.arena_cracks.append((
                (cx, cy),
                mid,
                (cx + math.cos(ang) * hlen, cy + math.sin(ang) * hlen),
                rng.uniform(0.12, 0.30)
            ))
        # Decorative mosaic tiles — slightly lighter square patches
        self.arena_mosaic = [
            (rng.randint(0, self.world_w // 128) * 128,
             rng.randint(0, self.world_h // 128) * 128,
             rng.choice([True, False]))
            for _ in range(60)
        ]
        self.death_particles        = []      # list of [x, y, vx, vy, life, max_life, col]
        # Spawn first handful
        for _ in range(min(4, self.wave_enemy_target)):
            self.spawn_enemy()
            self.wave_enemy_count += 1

    def _wave_size(self, wave):
        return 6 + wave * 3

    def _spawn_boss(self):
        idx  = self.boss_pool[self.boss_pool_idx % len(self.boss_pool)]
        self.boss_pool_idx += 1
        # Spawn off-screen from player
        ang  = random.uniform(0, math.pi * 2)
        bx   = self.player.x + math.cos(ang) * 600
        by   = self.player.y + math.sin(ang) * 600
        bx   = max(80, min(self.world_w - 80, bx))
        by   = max(80, min(self.world_h - 80, by))
        self.boss = Boss(bx, by, idx, self.player.level)
        bt   = BOSS_TYPES[idx]
        MUSIC.play_boss(bt["name"])
        self.boss_intro = BossIntro(self.boss, self.fonts)
        self.floating_texts.append(
            FloatingText(self.player.x, self.player.y - 100,
                         f"BOSS: {bt['name']}!", (255, 60, 60), 26))
        # Generate Nyxoth space arena
        if bt["pattern"] == "homing":
            self.nyx_bombs = []
            # Stars: random screen-space positions (regenerated each frame relative to cam)
            self.nyx_stars = [
                (random.randint(0, SW), random.randint(0, SH),
                 random.randint(1, 3), random.uniform(0.4, 1.0))
                for _ in range(180)
            ]
            # Nebulas: world-space blobs that stay fixed in the arena
            neb_cols = [
                (60, 0, 120), (0, 20, 80), (80, 0, 60),
                (20, 0, 100), (40, 10, 70), (0, 30, 90),
            ]
            self.nyx_nebulas = [
                (random.randint(200, self.world_w - 200),
                 random.randint(200, self.world_h - 200),
                 random.randint(80, 200),
                 random.choice(neb_cols),
                 random.randint(30, 70))
                for _ in range(28)
            ]

        # Generate Malachar lava arena
        if bt["pattern"] == "charge":
            self.mal_embers   = []
            self.mal_ember_cd = 0
            self.mal_lava_flows = []
            for _ in range(32):
                fx  = random.randint(0, self.world_w)
                fy  = random.randint(0, self.world_h)
                pts = [(fx, fy)]
                ang = random.uniform(0, math.pi * 2)
                sl  = random.randint(180, 380)
                for _ in range(random.randint(12, 22)):
                    ang += random.uniform(-0.4, 0.4)
                    fx = max(0, min(self.world_w, fx + math.cos(ang) * sl))
                    fy = max(0, min(self.world_h, fy + math.sin(ang) * sl))
                    pts.append((fx, fy))
                self.mal_lava_flows.append(
                    [pts, random.randint(10, 28), random.uniform(0, math.pi * 2)])

        # Generate Seraphix Greek arena pillars
        if bt["pattern"] == "orbit":
            self.sera_motes   = []
            self.sera_mote_cd = 0
            cx = self.world_w // 2
            cy = self.world_h // 2
            self.sera_pillars = []
            for ring_r, count in [(700, 12), (420, 8)]:
                for i in range(count):
                    ang = (math.pi * 2 / count) * i + (0.1 if ring_r < 500 else 0)
                    px2 = cx + math.cos(ang) * ring_r
                    py2 = cy + math.sin(ang) * ring_r
                    self.sera_pillars.append(
                        (px2, py2, random.randint(54, 72), random.uniform(0, math.pi * 2))
                    )

        # Generate Gorvak dungeon arena
        if bt["pattern"] == "burst":
            self.gorv_lanterns = []
            self.gorv_chains   = []
            # Lanterns scattered across the world
            for _ in range(30):
                self.gorv_lanterns.append((
                    random.randint(80, self.world_w - 80),
                    random.randint(80, self.world_h - 80),
                    random.uniform(0, math.pi * 2),   # flicker phase
                    random.randint(60, 120),           # light radius
                ))
            # Hanging chain clusters — short drooping segments
            for _ in range(24):
                cx2 = random.randint(60, self.world_w - 60)
                cy2 = random.randint(60, self.world_h - 60)
                pts = [(cx2, cy2)]
                ang  = random.uniform(math.pi * 0.3, math.pi * 0.7)   # drooping downward
                segs = random.randint(3, 6)
                for _ in range(segs):
                    cx2 += math.cos(ang + random.uniform(-0.3, 0.3)) * random.randint(12, 22)
                    cy2 += math.sin(ang + random.uniform(-0.1, 0.1)) * random.randint(10, 18)
                    pts.append((cx2, cy2))
                self.gorv_chains.append([pts, random.randint(2, 4)])

        # Generate Vexara corrupted arena — cracks radiating from the centre
        if bt["pattern"] == "spiral":
            self.vex_cracks = []
            ccx = self.world_w // 2
            ccy = self.world_h // 2
            for _ in range(20):
                ang = random.uniform(0, math.pi * 2)
                pts = [(float(ccx), float(ccy))]
                cx2, cy2 = float(ccx), float(ccy)
                length = random.randint(180, 580)
                segs   = random.randint(5, 12)
                seg_l  = length / segs
                for _ in range(segs):
                    ang  += random.uniform(-0.55, 0.55)
                    cx2   = max(20, min(self.world_w - 20, cx2 + math.cos(ang) * seg_l))
                    cy2   = max(20, min(self.world_h - 20, cy2 + math.sin(ang) * seg_l))
                    pts.append((cx2, cy2))
                self.vex_cracks.append(pts)

    def _on_boss_killed(self):
        b = self.boss
        self.boss_killed += 1
        self._bosses_killed_names.add(b.name)   # track for achievements
        TOKENS.earn(1)   # +1 persistent token per boss kill
        # 25% chance to drop a case on boss kill
        if random.random() < 0.25:
            TOKENS.add_case()
            self.floating_texts.append(
                FloatingText(b.x, b.y - 150, "Case dropped! Check Inventory.", (255, 200, 40), 20))
        if b.name == "Seraphix the Fallen":
            TOKENS.record_seraphix_kill()
        if b.name == "Nyxoth the Abyssal":
            TOKENS.record_nyxoth_kill()
        if b.name == "Vexara the Hex-Weaver":
            TOKENS.record_vexara_kill()
        if b.name == "Malachar the Undying":
            TOKENS.record_malachar_kill()
        if b.name == "Gorvak Ironhide":
            TOKENS.record_gorvak_kill()
        self.floating_texts.append(
            FloatingText(b.x, b.y - 130, "+1 Token!", (255, 200, 60), 22))
        self.wave_active  = False
        self.boss_wave    = False
        self.boss_clone   = None
        self.break_timer  = WAVE_BREAK_SECS * FPS
        self.nyx_bombs    = []
        self.nyx_stars    = []
        self.nyx_nebulas  = []
        self.mal_lava_flows = []
        self.mal_embers   = []
        self.sera_pillars = []
        self.sera_motes   = []
        self.gorv_lanterns = []
        self.gorv_chains   = []
        self.vex_cracks    = []
        self.vex_wisps     = []
        # Clear all lingering enemy projectiles so the player can't be hit after the kill
        self.projectiles = [p for p in self.projectiles if p.owner == "player"]
        self.fire_orbs   = []
        self.hp_orbs     = []
        MUSIC.play("battle")
        SOUNDS.play("boss_death")
        # Big gold + XP reward
        for _ in range(12):
            self.gold_coins.append(GoldCoin(b.x, b.y, b.gold_drop // 12))
        # Boss HP orbs — scatter 3–5 orbs worth 20 HP each (not in hardcore)
        if not self.hardcore:
            for _ in range(random.randint(3, 5)):
                ox = random.uniform(-60, 60)
                oy = random.uniform(-60, 60)
                self.hp_orbs.append(HpOrb(b.x + ox, b.y + oy, 20))
        self.floating_texts.append(
            FloatingText(b.x, b.y - 60, f"BOSS SLAIN! +{b.gold_drop}g", YELLOW, 26))
        self.floating_texts.append(
            FloatingText(b.x, b.y - 100, f"Wave {self.wave} cleared!", GREEN, 24))
        for _ in range(40):
            self.particles.append(Particle(b.x, b.y, b.color))
            self.particles.append(Particle(b.x, b.y, YELLOW))
        if self.player.gain_xp(b.xp_drop):
            self.floating_texts.append(
                FloatingText(self.player.x, self.player.y - 60,
                             f"LEVEL UP!  {self.player.level}", CYAN, 22))
        # Check achievements after boss kill
        new_achs = PROFILE.check_achievements(self)
        self._queue_achievement_toasts(new_achs)
        # Always offer a perk after a boss
        self.perk_screen.offer()
        # Defer save until after the perk is picked (or immediately if no slot assigned)
        self.pending_save = True

    def _skip_wave(self):
        """Dev tool: instantly end the current wave/boss and jump to the break."""
        # Clear all living enemies and their projectiles
        for e in self.enemies:
            e.alive = False
        self.enemies = []
        self.fire_orbs = []

        # Kill boss intro and boss if present
        if self.boss_intro and self.boss_intro.active:
            self.boss_intro.done = True
            self.boss_intro = None
        if self.boss and self.boss.alive:
            self.boss.alive = False
            self.boss_killed += 1
            self.boss.minions = []

        # Clear all enemy projectiles
        self.projectiles = [p for p in self.projectiles if p.owner == "player"]

        # End the wave and start a break
        # Credit corruption wave if one was active when skipped
        if self.elite_wave:
            self.player.corruption_waves_cleared += 1
            self.floating_texts.append(
                FloatingText(self.player.x, self.player.y - 110,
                             f"Corruption Wave cleared!  ({self.player.corruption_waves_cleared} total)",
                             (220, 80, 255), 20))
        self.wave_active            = False
        self.boss_wave              = False
        self.boss                   = None
        self.boss_clone             = None
        self.elite_wave             = False
        self.corruption_flash_timer = 0
        self.break_timer            = WAVE_BREAK_SECS * FPS
        self.wave_enemy_count  = self.wave_enemy_target   # mark as fully spawned

        MUSIC.play("battle")
        self.floating_texts.append(
            FloatingText(self.player.x, self.player.y - 80,
                         f"[DEV] Wave {self.wave} skipped!", (255, 160, 40), 22))

    def _skip_to_boss(self, btype_idx):
        """Dev tool: skip to a break that leads directly into a specific boss wave."""
        self._skip_wave()
        # The break timer will fire and do self.wave += 1, then check wave % 10 == 0.
        # So we set self.wave to one below the next multiple of 10 from current wave.
        next_boss_wave = (self.wave // 10 + 1) * 10
        self.wave = next_boss_wave - 1   # +1 happens at break end → hits the multiple
        # Front-load the desired boss in the pool
        self.boss_pool     = [btype_idx] + [j for j in range(len(BOSS_TYPES)) if j != btype_idx]
        self.boss_pool_idx = 0
        bt = BOSS_TYPES[btype_idx]
        self.floating_texts.append(
            FloatingText(self.player.x, self.player.y - 110,
                         f"[DEV] Next boss: {bt['name']}!", (255, 120, 200), 22))

    def spawn_enemy(self):
        p       = self.player
        lvl     = p.level
        weights = [30, max(0, 20 + lvl * 2), max(0, 10 + lvl * 3),
                   max(0, 5 + lvl * 2),       max(0, min(20, (lvl - 8) * 2))]
        total   = sum(weights) or 1
        weights = [w / total for w in weights]
        r = random.random(); cum = 0; etype = 0
        for i, w in enumerate(weights):
            cum += w
            if r <= cum:
                etype = i; break
        for _ in range(20):
            angle = random.uniform(0, math.pi * 2)
            dist  = random.uniform(400, 800)
            ex = p.x + math.cos(angle) * dist
            ey = p.y + math.sin(angle) * dist
            if 50 < ex < self.world_w - 50 and 50 < ey < self.world_h - 50:
                self.enemies.append(Enemy(ex, ey, etype, max(1, lvl), is_elite=self.elite_wave))
                return
        self.enemies.append(Enemy(
            random.randint(100, self.world_w - 100),
            random.randint(100, self.world_h - 100),
            etype, max(1, lvl), is_elite=self.elite_wave))

    def get_camera(self):
        cx = max(0, min(self.world_w - SW, int(self.player.x - SW // 2)))
        cy = max(0, min(self.world_h - SH, int(self.player.y - SH // 2)))
        return (cx, cy)

    def draw_world(self, cam):
        is_vexara  = (self.boss_wave and self.boss and
                      getattr(self.boss, 'pattern', None) == "spiral")
        is_nyxoth  = (self.boss_wave and self.boss and
                      getattr(self.boss, 'pattern', None) == "homing")
        is_malachar = (self.boss_wave and self.boss and
                       getattr(self.boss, 'pattern', None) == "charge")
        is_seraphix = (self.boss_wave and self.boss and
                       getattr(self.boss, 'pattern', None) == "orbit")
        is_gorvak  = (self.boss_wave and self.boss and
                      getattr(self.boss, 'pattern', None) == "burst")
        is_normal  = not (is_vexara or is_nyxoth or is_malachar or is_seraphix or is_gorvak or self.elite_wave)

        # ── Base fill ─────────────────────────────────────────────────────────
        if self.elite_wave:
            self.screen.fill((28, 16, 42))
        elif is_vexara:
            self.screen.fill((20, 10, 36))
        elif is_nyxoth:
            self.screen.fill((0, 0, 0))
        elif is_malachar:
            self.screen.fill((18, 6, 2))
        elif is_seraphix:
            self.screen.fill((195, 185, 158))
        elif is_gorvak:
            self.screen.fill((38, 34, 30))    # dark warm grey dungeon stone
        else:
            self.screen.fill((18, 20, 28))   # deep slate base

        # ── Tile grid (all modes except Malachar) ────────────────────────────
        if not is_malachar:
            tile = 64
            ox = cam[0] % tile; oy = cam[1] % tile
            if self.elite_wave:
                tile_col = (30, 18, 48)
            elif is_vexara:
                tile_col = (24, 12, 44)
            elif is_nyxoth:
                tile_col = (4, 4, 10)
            elif is_seraphix:
                # Warm marble slabs — ivory / cream checkerboard
                for gx in range(-1, SW // tile + 2):
                    for gy in range(-1, SH // tile + 2):
                        world_gx = (gx + (cam[0] // tile)) % 2
                        world_gy = (gy + (cam[1] // tile)) % 2
                        if (world_gx + world_gy) % 2 == 0:
                            tc = (200, 190, 162)
                        else:
                            tc = (188, 178, 150)
                        pygame.draw.rect(self.screen, tc,
                                         (gx * tile - ox, gy * tile - oy, tile - 1, tile - 1))
                # Golden grout lines
                grout_col = (170, 148, 96)
                for gx in range(-1, SW // tile + 2):
                    pygame.draw.line(self.screen, grout_col,
                                     (gx * tile - ox, 0), (gx * tile - ox, SH), 1)
                for gy in range(-1, SH // tile + 2):
                    pygame.draw.line(self.screen, grout_col,
                                     (0, gy * tile - oy), (SW, gy * tile - oy), 1)
            elif is_gorvak:
                # Rough dungeon stone — 80px tiles with 4-shade variation
                stone_tile = 80
                sox = cam[0] % stone_tile; soy = cam[1] % stone_tile
                for gx in range(-1, SW // stone_tile + 2):
                    for gy in range(-1, SH // stone_tile + 2):
                        world_gx = (gx + (cam[0] // stone_tile)) % 3
                        world_gy = (gy + (cam[1] // stone_tile)) % 3
                        shade = (world_gx * 3 + world_gy) % 4
                        tc = [(42, 38, 33), (50, 44, 38), (36, 32, 28), (46, 40, 35)][shade]
                        pygame.draw.rect(self.screen, tc,
                                         (gx * stone_tile - sox, gy * stone_tile - soy,
                                          stone_tile - 2, stone_tile - 2))
            else:
                # Two-tone stone slabs — alternating slightly lighter squares
                for gx in range(-1, SW // tile + 2):
                    for gy in range(-1, SH // tile + 2):
                        world_gx = (gx + (cam[0] // tile)) % 2
                        world_gy = (gy + (cam[1] // tile)) % 2
                        if (world_gx + world_gy) % 2 == 0:
                            tc = (24, 27, 36)
                        else:
                            tc = (27, 30, 40)
                        pygame.draw.rect(self.screen, tc,
                                         (gx * tile - ox, gy * tile - oy, tile - 1, tile - 1))

            if not is_normal and not is_seraphix and not is_gorvak and not is_vexara:
                # Elite / boss-specific tiles (solid colour — Nyxoth only now)
                if self.elite_wave: tc2 = (30, 18, 48)
                else:               tc2 = (4, 4, 10)
                for gx in range(-1, SW // tile + 2):
                    for gy in range(-1, SH // tile + 2):
                        pygame.draw.rect(self.screen, tc2,
                                         (gx * tile - ox, gy * tile - oy, tile - 1, tile - 1))

        # ── Vexara: corrupted hex floor + glowing cracks + summoning circle ──────
        if is_vexara:
            t_vex = pygame.time.get_ticks()
            acx   = int(self.world_w // 2 - cam[0])   # arena centre screen-x
            acy   = int(self.world_h // 2 - cam[1])   # arena centre screen-y

            if GAME_SETTINGS.low:
                # Low quality: skip hex polygon grid entirely; draw static cracks
                # and a minimal summoning circle — no per-frame trig per tile
                for crack_pts in self.vex_cracks:
                    s_pts = [(int(wx - cam[0]), int(wy - cam[1])) for wx, wy in crack_pts]
                    if not any(-60 < sx < SW + 60 and -60 < sy < SH + 60 for sx, sy in s_pts):
                        continue
                    if len(s_pts) >= 2:
                        pygame.draw.lines(self.screen, (80, 0, 140), False, s_pts, 1)
                if -360 < acx < SW + 360 and -360 < acy < SH + 360:
                    for rr in (150, 210, 270, 340):
                        pygame.draw.circle(self.screen, (55, 0, 100), (acx, acy), rr, 1)
                    pygame.draw.circle(self.screen, (48, 0, 88),  (acx, acy), 90, 2)
                    pygame.draw.circle(self.screen, (12, 0, 24),  (acx, acy), 14)
            else:
                # ── Hex tile grid (flat-top hexagons) ─────────────────────────
                hex_s  = 44
                col_w  = int(hex_s * 1.5)              # 66 px centre-to-centre horizontally
                row_h  = int(hex_s * 0.8660 * 2)       # ≈76 px centre-to-centre vertically
                half_r = row_h // 2

                gc_start = cam[0] // col_w - 1
                gc_end   = (cam[0] + SW) // col_w + 2
                gr_start = cam[1] // row_h - 1
                gr_end   = (cam[1] + SH) // row_h + 2

                for gc in range(gc_start, gc_end):
                    for gr in range(gr_start, gr_end):
                        hcx = gc * col_w - cam[0]
                        hcy = gr * row_h + (half_r if gc % 2 else 0) - cam[1]
                        if -hex_s * 2 < hcx < SW + hex_s * 2 and -hex_s * 2 < hcy < SH + hex_s * 2:
                            shade    = (gc * 7 + gr * 13) % 6
                            glow_hex = (gc * 11 + gr * 17) % 22 == 0
                            crack_hex= (gc * 5  + gr * 19) % 15 == 0
                            if glow_hex:
                                tc = (42, 8, 78)
                            elif crack_hex:
                                tc = (30, 6, 56)
                            elif shade < 2:
                                tc = (18, 7, 32)
                            elif shade < 4:
                                tc = (22, 10, 40)
                            else:
                                tc = (26, 13, 48)
                            pts = [(hcx + int(math.cos(math.pi / 3 * i) * hex_s),
                                    hcy + int(math.sin(math.pi / 3 * i) * hex_s))
                                   for i in range(6)]
                            pygame.draw.polygon(self.screen, tc, pts)
                            edge_col = (68, 18, 118) if glow_hex else (34, 14, 60)
                            pygame.draw.polygon(self.screen, edge_col, pts, 1)
                            if crack_hex:
                                ca = (gc * 3 + gr * 7) % 6
                                cx1h = hcx + int(math.cos(math.pi / 3 * ca) * (hex_s - 8))
                                cy1h = hcy + int(math.sin(math.pi / 3 * ca) * (hex_s - 8))
                                cx2h = hcx + int(math.cos(math.pi / 3 * ((ca + 3) % 6)) * (hex_s - 8))
                                cy2h = hcy + int(math.sin(math.pi / 3 * ((ca + 3) % 6)) * (hex_s - 8))
                                pygame.draw.line(self.screen, (55, 10, 95),
                                                 (cx1h, cy1h), (cx2h, cy2h), 1)

                # ── Pre-generated corruption cracks ───────────────────────────
                for crack_pts in self.vex_cracks:
                    s_pts = [(int(wx - cam[0]), int(wy - cam[1])) for wx, wy in crack_pts]
                    if not any(-60 < sx < SW + 60 and -60 < sy < SH + 60 for sx, sy in s_pts):
                        continue
                    if len(s_pts) >= 2:
                        glow_t   = math.sin(t_vex * 0.0018 + crack_pts[0][0] * 0.001) * 0.5 + 0.5
                        c_glow   = (max(0, min(255, int(80 + glow_t * 80))),
                                    0,
                                    max(0, min(255, int(130 + glow_t * 90))))
                        c_dim    = (max(0, min(255, int(25 + glow_t * 18))),
                                    0,
                                    max(0, min(255, int(45 + glow_t * 30))))
                        pygame.draw.lines(self.screen, c_dim,  False, s_pts, 3)
                        pygame.draw.lines(self.screen, c_glow, False, s_pts, 1)

                # ── Central summoning / ritual circle ──────────────────────────
                if -360 < acx < SW + 360 and -360 < acy < SH + 360:
                    ring_t = t_vex * 0.0010

                    for ri, rr in enumerate([340, 270, 210, 150]):
                        rp    = math.sin(ring_t + ri * 0.9) * 0.5 + 0.5
                        r_col = (max(0, min(255, int(55 + rp * 65))),
                                 0,
                                 max(0, min(255, int(90 + rp * 90))))
                        pygame.draw.circle(self.screen, r_col, (acx, acy), rr,
                                           2 if ri % 2 == 0 else 1)

                    for tri in range(2):
                        spin = ring_t * (0.25 if tri == 0 else -0.18)
                        tri_pts = []
                        for i in range(3):
                            a = spin + (math.pi * 2 / 3) * i + (math.pi / 6 if tri else 0)
                            tri_pts.append((acx + int(math.cos(a) * 290),
                                            acy + int(math.sin(a) * 290)))
                        star_p = math.sin(ring_t * 1.5 + tri) * 0.5 + 0.5
                        star_col = (max(0, min(255, int(60 + star_p * 60))),
                                    0,
                                    max(0, min(255, int(100 + star_p * 80))))
                        pygame.draw.polygon(self.screen, star_col, tri_pts, 1)

                    spoke_spin = ring_t * 0.18
                    for si in range(12):
                        sang    = (math.pi * 2 / 12) * si + spoke_spin
                        sp      = math.sin(ring_t * 2.2 + si * 0.55) * 0.5 + 0.5
                        sc      = (max(0, min(255, int(40 + sp * 40))),
                                   0,
                                   max(0, min(255, int(65 + sp * 65))))
                        ex2 = acx + int(math.cos(sang) * 340)
                        ey2 = acy + int(math.sin(sang) * 340)
                        pygame.draw.line(self.screen, sc, (acx, acy), (ex2, ey2), 1)

                    pygame.draw.circle(self.screen, (48, 0, 88),  (acx, acy), 90, 3)
                    pygame.draw.circle(self.screen, (65, 0, 115), (acx, acy), 58, 2)
                    void_p   = math.sin(ring_t * 2.8) * 0.5 + 0.5
                    void_col = (max(0, min(255, int(90 + void_p * 70))),
                                0,
                                max(0, min(255, int(145 + void_p * 90))))
                    pygame.draw.circle(self.screen, void_col, (acx, acy), 26, 2)
                    pygame.draw.circle(self.screen, (12, 0, 24), (acx, acy), 14)

        # ── Normal arena floor details ────────────────────────────────────────
        if is_normal:
            # Mosaic accent tiles — slightly raised-looking patches
            for mx, my, accent in self.arena_mosaic:
                sx2 = int(mx - cam[0]); sy2 = int(my - cam[1])
                if -130 < sx2 < SW + 130 and -130 < sy2 < SH + 130:
                    mc = (30, 34, 46) if accent else (22, 25, 34)
                    pygame.draw.rect(self.screen, mc, (sx2, sy2, 127, 127))

            # Worn scuff rings — faint teal-grey circles on the stone
            for wx, wy, wr, opac in self.arena_scuffs:
                sx2 = int(wx - cam[0]); sy2 = int(wy - cam[1])
                if -wr - 4 < sx2 < SW + wr + 4 and -wr - 4 < sy2 < SH + wr + 4:
                    # Blend colour toward slightly lighter
                    sc = (max(0, min(255, int(38 * opac + 18))),
                          max(0, min(255, int(44 * opac + 20))),
                          max(0, min(255, int(58 * opac + 28))))
                    pygame.draw.circle(self.screen, sc, (sx2, sy2), wr, 1)

            # Stone cracks — dark jagged line pairs
            for p0, p1, p2, opac in self.arena_cracks:
                s0 = (int(p0[0] - cam[0]), int(p0[1] - cam[1]))
                s1 = (int(p1[0] - cam[0]), int(p1[1] - cam[1]))
                s2 = (int(p2[0] - cam[0]), int(p2[1] - cam[1]))
                if not (-60 < s0[0] < SW + 60 and -60 < s0[1] < SH + 60):
                    continue
                cc = (max(0, min(255, int(14 + opac * 20))),
                      max(0, min(255, int(15 + opac * 22))),
                      max(0, min(255, int(20 + opac * 28))))
                pygame.draw.line(self.screen, cc, s0, s1, 1)
                pygame.draw.line(self.screen, cc, s1, s2, 1)

        # ── Seraphix: Greek arena floor + pillars ─────────────────────────────
        if is_seraphix:
            t_sera = pygame.time.get_ticks()
            acx = int(self.world_w // 2 - cam[0])
            acy = int(self.world_h // 2 - cam[1])

            # Concentric floor mosaic rings radiating from arena centre
            ring_cols = [(170, 148, 96), (185, 165, 118), (160, 138, 84)]
            for ri, rr in enumerate([80, 160, 260, 380, 520, 680]):
                rc = ring_cols[ri % len(ring_cols)]
                pygame.draw.circle(self.screen, rc, (acx, acy), rr, 2)

            # Radial spoke lines from centre (like amphitheatre floor divisions)
            for si in range(12):
                sang = (math.pi * 2 / 12) * si
                ex2 = acx + int(math.cos(sang) * 680)
                ey2 = acy + int(math.sin(sang) * 680)
                pygame.draw.line(self.screen, (175, 152, 100), (acx, acy), (ex2, ey2), 1)

            # Central decorative medallion
            pygame.draw.circle(self.screen, (175, 155, 108), (acx, acy), 40, 3)
            pygame.draw.circle(self.screen, (185, 168, 122), (acx, acy), 22, 2)
            pygame.draw.circle(self.screen, (195, 178, 135), (acx, acy), 8)

            # Greek meander key pattern along world boundary (screen-clipped)
            bx2 = int(-cam[0]); by2 = int(-cam[1])
            bw2 = self.world_w; bh2 = self.world_h
            meander_col = (165, 140, 88)
            step = 32
            for mx2 in range(bx2, bx2 + bw2, step):
                if -step < mx2 < SW + step:
                    # top edge key notch
                    pygame.draw.rect(self.screen, meander_col,
                                     (mx2, by2 + 8, step // 2, 4))
                    pygame.draw.rect(self.screen, meander_col,
                                     (mx2, by2 + 8, 4, 12))
                    # bottom edge
                    pygame.draw.rect(self.screen, meander_col,
                                     (mx2, by2 + bh2 - 24, step // 2, 4))
                    pygame.draw.rect(self.screen, meander_col,
                                     (mx2, by2 + bh2 - 24, 4, 12))
            for my2 in range(by2, by2 + bh2, step):
                if -step < my2 < SH + step:
                    pygame.draw.rect(self.screen, meander_col,
                                     (bx2 + 8, my2, 4, step // 2))
                    pygame.draw.rect(self.screen, meander_col,
                                     (bx2 + 8, my2, 12, 4))
                    pygame.draw.rect(self.screen, meander_col,
                                     (bx2 + bw2 - 24, my2, 4, step // 2))
                    pygame.draw.rect(self.screen, meander_col,
                                     (bx2 + bw2 - 24, my2, 12, 4))

            # Draw pillars (world-space)
            for pwx, pwy, pheight, gphase in self.sera_pillars:
                psx = int(pwx - cam[0]); psy = int(pwy - cam[1])
                if -80 < psx < SW + 80 and -80 < psy < SH + 80:
                    pw2 = 18   # pillar width
                    # Shadow beneath pillar
                    pygame.draw.ellipse(self.screen, (165, 148, 116),
                                        (psx - pw2, psy + pheight // 2 - 4, pw2 * 2, 12))
                    # Base plinth
                    pygame.draw.rect(self.screen, (210, 200, 170),
                                     (psx - pw2 - 3, psy + pheight // 2 - 6,
                                      pw2 * 2 + 6, 8), border_radius=2)
                    # Shaft — fluted column (3 vertical grooves)
                    shaft_top = psy - pheight // 2
                    shaft_bot = psy + pheight // 2 - 6
                    shaft_h   = shaft_bot - shaft_top
                    pygame.draw.rect(self.screen, (215, 205, 175),
                                     (psx - pw2, shaft_top, pw2 * 2, shaft_h))
                    # Flute grooves
                    for fi in range(1, 4):
                        fx2 = psx - pw2 + (pw2 * 2 // 4) * fi
                        pygame.draw.line(self.screen, (190, 178, 148),
                                         (fx2, shaft_top + 4), (fx2, shaft_bot - 4), 1)
                    # Highlights on shaft edges
                    pygame.draw.line(self.screen, (230, 222, 196),
                                     (psx - pw2, shaft_top), (psx - pw2, shaft_bot), 1)
                    # Capital (top block)
                    pygame.draw.rect(self.screen, (220, 210, 180),
                                     (psx - pw2 - 4, shaft_top - 8,
                                      pw2 * 2 + 8, 10), border_radius=2)
                    pygame.draw.rect(self.screen, (210, 198, 165),
                                     (psx - pw2 - 2, shaft_top - 4,
                                      pw2 * 2 + 4, 6))
                    # Divine glow pulse around pillar top
                    glow_p = math.sin(t_sera * 0.002 + gphase) * 0.5 + 0.5
                    gc_r = max(0, min(255, int(240 + glow_p * 15)))
                    gc_g = max(0, min(255, int(210 + glow_p * 20)))
                    gc_b = max(0, min(255, int(100 + glow_p * 60)))
                    pygame.draw.circle(self.screen, (gc_r, gc_g, gc_b),
                                       (psx, shaft_top - 4),
                                       max(1, int(4 + glow_p * 4)), 1)

        # ── Nyxoth: nebulas ───────────────────────────────────────────────────
        if is_nyxoth:
            for wx, wy, r, col, alpha in self.nyx_nebulas:
                nsx = int(wx - cam[0]); nsy = int(wy - cam[1])
                if -r < nsx < SW + r and -r < nsy < SH + r:
                    if GAME_SETTINGS.low:
                        # Single solid dim circle — no Surface allocation
                        lc = lerp_color(col, (0, 0, 0), 0.65)
                        pygame.draw.circle(self.screen, lc, (nsx, nsy), max(1, r // 2))
                    else:
                        for layer in range(3):
                            lr = max(1, int(r * (1 - layer * 0.28)))
                            la = max(0, alpha - layer * 18)
                            lc = lerp_color(col, (0, 0, 0), layer * 0.3)
                            ns = pygame.Surface((lr * 2 + 4, lr * 2 + 4), pygame.SRCALPHA)
                            pygame.draw.circle(ns, (*lc, la), (lr + 2, lr + 2), lr)
                            self.screen.blit(ns, (nsx - lr - 2, nsy - lr - 2))

        # ── Malachar: lava rivers ─────────────────────────────────────────────
        if is_malachar:
            t_lava = pygame.time.get_ticks()
            for flow_pts, flow_w, phase in self.mal_lava_flows:
                screen_pts = [(int(wx - cam[0]), int(wy - cam[1])) for wx, wy in flow_pts]
                if not any(-200 < sx2 < SW + 200 and -200 < sy2 < SH + 200
                           for sx2, sy2 in screen_pts):
                    continue
                if len(screen_pts) < 2:
                    continue
                if GAME_SETTINGS.low:
                    # Single pass — no glow pulse, just a static lava colour
                    pygame.draw.lines(self.screen, (200, 60, 0), False, screen_pts, max(2, flow_w))
                else:
                    glow = math.sin(t_lava * 0.003 + phase) * 0.5 + 0.5
                    lava_outer = (max(0, min(255, int(60 + 40 * glow))),
                                  max(0, min(255, int(20 + 10 * glow))), 0)
                    lava_core  = (max(0, min(255, int(200 + 55 * glow))),
                                  max(0, min(255, int(80 + 80 * glow))),
                                  max(0, min(255, int(20 * glow))))
                    pygame.draw.lines(self.screen, lava_outer, False, screen_pts, flow_w + 4)
                    pygame.draw.lines(self.screen, lava_core,  False, screen_pts, max(2, flow_w - 2))

        # ── Gorvak: dungeon chains and lanterns ───────────────────────────────
        if is_gorvak:
            t_gorv = pygame.time.get_ticks()

            # ── Lantern light pools FIRST (drawn under chains) ────────────────
            for lwx, lwy, lphase, lrad in self.gorv_lanterns:
                lsx = int(lwx - cam[0]); lsy = int(lwy - cam[1])
                if -lrad - 20 < lsx < SW + lrad + 20 and -lrad - 20 < lsy < SH + lrad + 20:
                    if GAME_SETTINGS.low:
                        # Single dim ambient circle — no flicker, no cage detail
                        pygame.draw.circle(self.screen, (55, 44, 30), (lsx, lsy), lrad // 3)
                        pygame.draw.circle(self.screen, (160, 100, 20), (lsx, lsy - 7), 3)
                    else:
                        # Very slow natural torch flicker — two slow sines
                        flicker = (math.sin(t_gorv * 0.0018 + lphase) * 0.28 +
                                   math.sin(t_gorv * 0.0047 + lphase * 2.3) * 0.12 + 0.62)
                        flicker = max(0.3, min(1.0, flicker))
                        cur_r   = int(lrad * flicker)

                        # Filled light pool — 5 concentric filled circles fading outward
                        for gi in range(5):
                            t_layer = 1 - gi / 5.0
                            gr      = max(1, int(cur_r * (1 - gi * 0.18)))
                            lc_r = max(0, min(255, int(42 + t_layer * (flicker * 90))))
                            lc_g = max(0, min(255, int(36 + t_layer * (flicker * 45))))
                            lc_b = max(0, min(255, int(26 + t_layer * (flicker * 8))))
                            pygame.draw.circle(self.screen, (lc_r, lc_g, lc_b), (lsx, lsy), gr)

                        lw2 = 7
                        pygame.draw.line(self.screen, (90, 80, 66),
                                         (lsx, lsy - lw2 * 3), (lsx, lsy - lw2), 2)
                        pygame.draw.rect(self.screen, (55, 48, 38),
                                         (lsx - lw2, lsy - lw2 * 2, lw2 * 2, lw2 * 2 + 2),
                                         border_radius=2)
                        pygame.draw.rect(self.screen, (110, 96, 76),
                                         (lsx - lw2, lsy - lw2 * 2, lw2 * 2, lw2 * 2 + 2),
                                         1, border_radius=2)
                        for bxi in range(-1, 2):
                            pygame.draw.line(self.screen, (90, 78, 62),
                                             (lsx + bxi * lw2 // 2, lsy - lw2 * 2),
                                             (lsx + bxi * lw2 // 2, lsy), 1)
                        fc_r = max(0, min(255, int(255 * flicker)))
                        fc_g = max(0, min(255, int(140 + flicker * 60)))
                        pygame.draw.circle(self.screen, (fc_r, fc_g, 20),
                                           (lsx, lsy - lw2), max(2, int(4 * flicker)))
                        pygame.draw.circle(self.screen, (255, 230, 160),
                                           (lsx, lsy - lw2), max(1, int(2 * flicker)))

            # ── Hanging chains (drawn over the light pools) ───────────────────
            for chain_pts, chain_w in self.gorv_chains:
                spts = [(int(wx - cam[0]), int(wy - cam[1])) for wx, wy in chain_pts]
                if not any(-40 < sx2 < SW + 40 and -40 < sy2 < SH + 40 for sx2, sy2 in spts):
                    continue
                if len(spts) >= 2:
                    if GAME_SETTINGS.low:
                        # Single mid-tone pass — no shadow, no highlight, no per-joint circles
                        pygame.draw.lines(self.screen, (110, 96, 78), False, spts, chain_w)
                    else:
                        pygame.draw.lines(self.screen, (30, 26, 22), False, spts, chain_w + 3)
                        pygame.draw.lines(self.screen, (110, 96, 78), False, spts, chain_w + 1)
                        pygame.draw.lines(self.screen, (150, 134, 112), False, spts, max(1, chain_w - 1))
                        for sx2, sy2 in spts:
                            pygame.draw.circle(self.screen, (130, 116, 96), (sx2, sy2), chain_w + 1)
                            pygame.draw.circle(self.screen, (170, 152, 128), (sx2, sy2), max(1, chain_w - 1))

        # ── World boundary ────────────────────────────────────────────────────
        bx = -cam[0]; by = -cam[1]
        if is_seraphix:
            pygame.draw.rect(self.screen, (210, 195, 155), (bx, by, self.world_w, self.world_h), 12)
            pygame.draw.rect(self.screen, (190, 165, 90),  (bx, by, self.world_w, self.world_h), 5)
            pygame.draw.rect(self.screen, (230, 210, 150), (bx, by, self.world_w, self.world_h), 2)
        elif is_gorvak:
            # Heavy iron-bound dungeon wall
            pygame.draw.rect(self.screen, (30, 24, 18), (bx, by, self.world_w, self.world_h), 14)
            pygame.draw.rect(self.screen, (55, 46, 36), (bx, by, self.world_w, self.world_h), 5)
            pygame.draw.rect(self.screen, (80, 68, 52), (bx, by, self.world_w, self.world_h), 2)
        elif is_vexara:
            # Corrupted void wall — deep purple layers bleeding inward
            pygame.draw.rect(self.screen, (8,  0,  16),  (bx, by, self.world_w, self.world_h), 20)
            pygame.draw.rect(self.screen, (25, 4,  50),  (bx, by, self.world_w, self.world_h), 10)
            pygame.draw.rect(self.screen, (50, 8,  90),  (bx, by, self.world_w, self.world_h), 5)
            pygame.draw.rect(self.screen, (80, 16, 140), (bx, by, self.world_w, self.world_h), 2)
        else:
            wall_col  = (50, 55, 75) if is_normal else (60, 20, 80) if (is_vexara or self.elite_wave) else (60, 20, 20) if is_malachar else (20, 20, 50)
            wall_col2 = (30, 34, 48) if is_normal else (40, 10, 60) if (is_vexara or self.elite_wave) else (40, 10, 10) if is_malachar else (10, 10, 30)
            pygame.draw.rect(self.screen, wall_col,  (bx, by, self.world_w, self.world_h), 8)
            pygame.draw.rect(self.screen, wall_col2, (bx, by, self.world_w, self.world_h), 3)

    def _queue_achievement_toasts(self, new_ach_ids):
        """Add newly-unlocked achievements to the toast queue and play the SFX once."""
        if not new_ach_ids:
            return
        SOUNDS.play("achievement")
        TOAST_DURATION = FPS * 4   # 4 seconds on screen
        for aid in new_ach_ids:
            ach = next(a for a in ACHIEVEMENTS if a["id"] == aid)
            self.ach_toasts.append({
                "name":      ach["name"],
                "tokens":    ach["tokens"],
                "cat":       ach["cat"],
                "timer":     TOAST_DURATION,
                "max_timer": TOAST_DURATION,
            })

    def draw_achievement_toasts(self):
        """Draw stacked achievement unlock toasts in the bottom-right corner."""
        if not self.ach_toasts:
            return

        TOAST_W   = 320
        TOAST_H   = 62
        TOAST_GAP = 8
        SLIDE_FRAMES = 18   # frames for slide-in animation
        FADE_FRAMES  = 30   # frames for fade-out at end
        MARGIN_R  = 14
        MARGIN_B  = 14
        ICON_SIZE = 10      # token dot icon

        # Advance timers — tick each toast down, remove expired
        still_alive = []
        for toast in self.ach_toasts:
            toast["timer"] -= 1
            if toast["timer"] > 0:
                still_alive.append(toast)
        self.ach_toasts = still_alive

        # Draw from bottom up (newest at bottom, oldest at top)
        for i, toast in enumerate(reversed(self.ach_toasts)):
            slot_y = SH - MARGIN_B - TOAST_H - i * (TOAST_H + TOAST_GAP)
            if slot_y < 0:
                break   # too many toasts, skip offscreen ones

            t      = toast["timer"]
            t_max  = toast["max_timer"]
            cat    = toast["cat"]

            # Slide in from the right
            slide_prog = min(1.0, (t_max - t) / SLIDE_FRAMES)
            slide_off  = int((1.0 - slide_prog) * (TOAST_W + MARGIN_R))

            # Fade out near end
            if t <= FADE_FRAMES:
                alpha = int(255 * (t / FADE_FRAMES))
            else:
                alpha = 255

            tx = SW - MARGIN_R - TOAST_W + slide_off   # negative = off-screen right
            ty = slot_y

            # Accent colour by category
            CAT_COLS = {
                "bosses":    (255, 100, 60),
                "levels":    (80, 200, 255),
                "waves":     (80, 220, 140),
                "kills":     (220, 80, 80),
                "cosmetics": (200, 140, 255),
                "weapons":   (255, 200, 60),
                "hardcore":  (255, 60, 40),
                "meta":      (255, 215, 0),
            }
            accent = CAT_COLS.get(cat, (180, 180, 255))

            # Background panel
            panel = pygame.Surface((TOAST_W, TOAST_H), pygame.SRCALPHA)
            bg_col = (18, 14, 28, min(alpha, 220))
            pygame.draw.rect(panel, bg_col, (0, 0, TOAST_W, TOAST_H), border_radius=10)
            # Accent left bar
            bar_a = min(alpha, 255)
            pygame.draw.rect(panel, (*accent, bar_a), (0, 0, 4, TOAST_H), border_radius=3)
            # Border
            pygame.draw.rect(panel, (*accent, bar_a), (0, 0, TOAST_W, TOAST_H), 1, border_radius=10)
            self.screen.blit(panel, (tx, ty))

            # "Achievement Unlocked!" header
            header_surf = self.fonts["tiny"].render("Achievement Unlocked!", True,
                                                     tuple(min(255, c) for c in accent))
            header_surf.set_alpha(alpha)
            self.screen.blit(header_surf, (tx + 12, ty + 7))

            # Achievement name
            name_surf = self.fonts["small"].render(toast["name"], True, WHITE)
            name_surf.set_alpha(alpha)
            self.screen.blit(name_surf, (tx + 12, ty + 24))

            # Token reward — drawn right-aligned
            tok_text = f"+{toast['tokens']}"
            tok_surf = self.fonts["small"].render(tok_text, True, (255, 200, 60))
            tok_surf.set_alpha(alpha)
            tok_x = tx + TOAST_W - tok_surf.get_width() - ICON_SIZE * 2 - 10
            self.screen.blit(tok_surf, (tok_x, ty + TOAST_H // 2 - tok_surf.get_height() // 2))
            # Coin icon next to token count
            draw_token_coin(self.screen,
                            tx + TOAST_W - ICON_SIZE - 8,
                            ty + TOAST_H // 2,
                            ICON_SIZE)

    def draw_hud(self):
        p = self.player

        # Calculate panel height: base 178 (includes dash bar rows) + 18px per active perk
        active_perks = [(k, v) for k, v in p.perks.items() if v > 0]
        panel_h = 178 + (len(active_perks) * 17 + 10 if active_perks else 0)

        # Left panel
        pygame.draw.rect(self.screen, PANEL, (0, 0, 250, panel_h), border_radius=8)
        pygame.draw.rect(self.screen, CYAN,  (0, 0, 250, panel_h), 1, border_radius=8)
        self.screen.blit(self.fonts["large"].render(f"Lvl {p.level}", True, YELLOW), (12, 8))
        self.screen.blit(self.fonts["small"].render(f"Kills: {p.kill_count}", True, WHITE), (120, 14))
        if self.hardcore:
            # Red outline around HP bar
            pygame.draw.rect(self.screen, (200, 30, 30), (8, 40, 234, 20), 2, border_radius=4)
        draw_bar(self.screen, 10, 42, 230, 16, p.hp, p.max_hp, (50, 220, 80))
        self.screen.blit(self.fonts["small"].render(f"HP {p.hp}/{p.max_hp}", True, WHITE), (12, 44))
        # Hardcore indicator: flaming skulls on each side of the HP bar
        if self.hardcore:
            _ht = pygame.time.get_ticks() // 16
            skull_cy = 42 + 8   # vertically centred on the HP bar
            draw_flaming_skull(self.screen, 4,   skull_cy, _ht,      size=7)
            draw_flaming_skull(self.screen, 238, skull_cy, _ht + 15, size=7)
        if p.level >= p.LEVEL_CAP:
            draw_bar(self.screen, 10, 66, 230, 12, 1, 1, BLUE)
            self.screen.blit(self.fonts["tiny"].render("XP  MAX LEVEL", True, (180, 180, 255)), (12, 68))
        else:
            draw_bar(self.screen, 10, 66, 230, 12, p.xp, p.xp_to_next, BLUE)
            self.screen.blit(self.fonts["tiny"].render(f"XP {p.xp}/{p.xp_to_next}", True, (180, 180, 255)), (12, 68))
        # Gold and tokens on same line, weapon name below
        self.screen.blit(self.fonts["med"].render(f"Gold: {p.gold}", True, YELLOW), (12, 86))
        # Token coin icon + count, right-aligned on same line as gold
        tok_hud = self.fonts["small"].render(str(TOKENS.total), True, (255, 200, 60))
        num_x   = 238 - tok_hud.get_width()
        draw_token_coin(self.screen, num_x - 12, 94, 8)
        self.screen.blit(tok_hud, (num_x, 89))
        self.screen.blit(self.fonts["small"].render(f"  {p.weapon['name']}", True, p.weapon["color"]), (12, 108))

        # Dash charge indicators
        DASH_COL     = (80, 220, 255)
        DASH_RDY_COL = (40, 160, 200)
        max_ch = p._dash_max_charges()
        dash_cd_val = p._dash_cd()
        bar_w = 230 // max_ch - 4
        for i in range(max_ch):
            bx = 10 + i * (bar_w + 4)
            charge_ready = (i < len(p.dash_cds) and p.dash_cds[i] <= 0)
            if charge_ready:
                pygame.draw.rect(self.screen, DASH_COL, (bx, 146, bar_w, 10), border_radius=3)
            else:
                cd_val = p.dash_cds[i] if i < len(p.dash_cds) else dash_cd_val
                fill = max(0, int(bar_w * (1 - cd_val / dash_cd_val)))
                pygame.draw.rect(self.screen, (30, 50, 60), (bx, 146, bar_w, 10), border_radius=3)
                if fill > 0:
                    pygame.draw.rect(self.screen, DASH_RDY_COL, (bx, 146, fill, 10), border_radius=3)
                pygame.draw.rect(self.screen, (50, 100, 120), (bx, 146, bar_w, 10), 1, border_radius=3)
        dash_lbl = self.fonts["tiny"].render("DASH [SPACE]", True, DASH_COL)
        self.screen.blit(dash_lbl, (10, 160))

        # Active perks section
        if active_perks:
            pygame.draw.line(self.screen, (60, 60, 80), (8, 176), (242, 176), 1)
            for i, (key, val) in enumerate(active_perks):
                pdef   = next((pd for pd in ALL_PERKS if pd["key"] == key), None)
                if not pdef:
                    continue
                stacks = int(val / pdef["bonus"] + 0.5)
                label  = pdef["label"]
                col    = pdef["color"]
                # Format value correctly: regen is raw HP, dash is stacks, others are percentages
                if key == "hp_regen":
                    val_str = f"+{val:.1f} HP/hit"
                elif key == "dash":
                    val_str = f"x{stacks} upgraded"
                else:
                    val_str = f"+{int(val * 100)}%"
                pygame.draw.circle(self.screen, col, (18, 185 + i * 17), 4)
                txt = self.fonts["tiny"].render(f"{label}  {val_str}  (x{stacks})", True, col)
                self.screen.blit(txt, (28, 178 + i * 17))

        # Minimap
        mm = 120; mm_x = SW - mm - 10; mm_y = 10
        pygame.draw.rect(self.screen, (20, 20, 35), (mm_x, mm_y, mm, mm))
        pygame.draw.rect(self.screen, GRAY,         (mm_x, mm_y, mm, mm), 1)
        pygame.draw.circle(self.screen, CYAN,
                           (mm_x + int(p.x / self.world_w * mm),
                            mm_y + int(p.y / self.world_h * mm)), 3)
        for e in self.enemies:
            if e.alive:
                pygame.draw.circle(self.screen, RED,
                                   (mm_x + int(e.x / self.world_w * mm),
                                    mm_y + int(e.y / self.world_h * mm)), 2)
        for gc in self.gold_coins:
            pygame.draw.circle(self.screen, YELLOW,
                               (mm_x + int(gc.x / self.world_w * mm),
                                mm_y + int(gc.y / self.world_h * mm)), 1)
        for orb in self.hp_orbs:
            pygame.draw.circle(self.screen, (60, 220, 90),
                               (mm_x + int(orb.x / self.world_w * mm),
                                mm_y + int(orb.y / self.world_h * mm)), 2)
        if self.boss and self.boss.alive:
            pygame.draw.circle(self.screen, (255, 60, 60),
                               (mm_x + int(self.boss.x / self.world_w * mm),
                                mm_y + int(self.boss.y / self.world_h * mm)), 5)
            pygame.draw.circle(self.screen, WHITE,
                               (mm_x + int(self.boss.x / self.world_w * mm),
                                mm_y + int(self.boss.y / self.world_h * mm)), 5, 1)
        self.screen.blit(self.fonts["tiny"].render("MAP", True, GRAY),
                         (mm_x + mm // 2 - 10, mm_y + mm + 2))

        # Wave status (top centre)
        if self.wave_active:
            if self.boss_wave and self.boss and self.boss.alive:
                wave_txt = self.fonts["med"].render(
                    f"BOSS WAVE {self.wave}  —  {self.boss.name}", True, (255, 60, 60))
            else:
                alive = sum(1 for e in self.enemies if e.alive)
                wave_txt = self.fonts["med"].render(f"Wave {self.wave}  |  Enemies: {alive}", True, RED)
            self.screen.blit(wave_txt, (SW // 2 - wave_txt.get_width() // 2, 8))
            if not self.boss_wave:
                waves_to_perk = 5 - (self.wave % 5)
                if waves_to_perk == 5: waves_to_perk = 0
                waves_to_boss = 10 - (self.wave % 10)
                if waves_to_boss == 10: waves_to_boss = 0
                hints = []
                if waves_to_perk > 0:
                    hints.append((f"Perk in {waves_to_perk} wave{'s' if waves_to_perk!=1 else ''}", PURPLE))
                if waves_to_boss > 0:
                    hints.append((f"BOSS in {waves_to_boss} wave{'s' if waves_to_boss!=1 else ''}", (200,60,60)))
                for hi, (htxt, hcol) in enumerate(hints):
                    hs = self.fonts["tiny"].render(htxt, True, hcol)
                    self.screen.blit(hs, (SW // 2 - hs.get_width() // 2, 32 + hi * 16))
        else:
            secs = max(0, math.ceil(self.break_timer / FPS))
            wave_txt = self.fonts["med"].render(
                f"Wave {self.wave} complete!  Next wave in {secs}s", True, GREEN)
            self.screen.blit(wave_txt, (SW // 2 - wave_txt.get_width() // 2, 8))
            if self.perk_screen.active:
                ph = self.fonts["small"].render("PERK AVAILABLE - choose a card!", True, YELLOW)
                self.screen.blit(ph, (SW // 2 - ph.get_width() // 2, 34))

        # Boss HP bar — cinematic bar at bottom of screen
        if self.boss and self.boss.alive:
            bw = 600; bh = 22
            bx = SW // 2 - bw // 2; by = SH - 62
            pygame.draw.rect(self.screen, (20, 10, 10),
                             (bx - 10, by - 16, bw + 20, bh + 30), border_radius=6)
            pygame.draw.rect(self.screen, (80, 20, 20),
                             (bx - 10, by - 16, bw + 20, bh + 30), 1, border_radius=6)
            bar_col = (220, 40, 40) if self.boss.enraged else (180, 60, 200)
            draw_bar(self.screen, bx, by, bw, bh, self.boss.hp, self.boss.max_hp, bar_col, bg=(40,10,10))
            enrage_tag = "  [ENRAGED]" if self.boss.enraged else ""
            blabel = self.fonts["small"].render(
                f"{self.boss.name}{enrage_tag}  {self.boss.hp} / {self.boss.max_hp}", True, WHITE)
            self.screen.blit(blabel, (SW // 2 - blabel.get_width() // 2, by - 14))

        if self.boss_clone and self.boss_clone.alive:
            cw = 400; ch = 14
            cx = SW // 2 - cw // 2; cy = SH - 90
            t_now = pygame.time.get_ticks()
            pulse = math.sin(t_now * 0.004) * 0.5 + 0.5
            clone_bar_col = lerp_color((180, 0, 255), (255, 80, 200), pulse)
            pygame.draw.rect(self.screen, (15, 5, 25), (cx - 6, cy - 12, cw + 12, ch + 22), border_radius=5)
            pygame.draw.rect(self.screen, clone_bar_col, (cx - 6, cy - 12, cw + 12, ch + 22), 1, border_radius=5)
            draw_bar(self.screen, cx, cy, cw, ch, self.boss_clone.hp, self.boss_clone.max_hp,
                     clone_bar_col, bg=(30, 5, 40))
            clabel = self.fonts["tiny"].render(
                f"Vexara (Clone)  {self.boss_clone.hp} / {self.boss_clone.max_hp}", True, (220, 160, 255))
            self.screen.blit(clabel, (SW // 2 - clabel.get_width() // 2, cy - 10))

        # Controls hint
        hint = self.fonts["tiny"].render("[TAB] Shop  |  WASD Move  |  LMB Shoot  |  [SPACE] Dash  |  [P] Pause", True, GRAY)
        self.screen.blit(hint, (SW // 2 - hint.get_width() // 2, SH - 22))

    def draw_game_over(self):
        overlay = pygame.Surface((SW, SH), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 210))
        self.screen.blit(overlay, (0, 0))

        # Title
        go = self.fonts["huge"].render("YOU DIED", True, RED)
        self.screen.blit(go, (SW // 2 - go.get_width() // 2, 28))

        # Rank banner (if made leaderboard)
        if self.lb_rank is not None:
            rank_col = YELLOW if self.lb_rank == 1 else (CYAN if self.lb_rank <= 3 else GREEN)
            rank_msg = f"NEW #{self.lb_rank} ON LEADERBOARD!"
            rm = self.fonts["large"].render(rank_msg, True, rank_col)
            self.screen.blit(rm, (SW // 2 - rm.get_width() // 2, 84))

        # ── Left panel: this run's stats ──────────────────────────────────────
        panel_x = 60; panel_y = 130; panel_w = 460; panel_h = 472
        pygame.draw.rect(self.screen, PANEL, (panel_x, panel_y, panel_w, panel_h), border_radius=12)
        pygame.draw.rect(self.screen, CYAN, (panel_x, panel_y, panel_w, panel_h), 1, border_radius=12)

        run_title = self.fonts["large"].render(f"{self.player.username}'s Run", True, CYAN)
        self.screen.blit(run_title, (panel_x + panel_w // 2 - run_title.get_width() // 2, panel_y + 14))
        pygame.draw.line(self.screen, (60, 60, 90),
                         (panel_x + 16, panel_y + 48), (panel_x + panel_w - 16, panel_y + 48), 1)

        stats = [
            ("Wave reached",        str(self.wave),              YELLOW),
            ("Level reached",       str(self.player.level),      CYAN),
            ("Enemies killed",      str(self.player.kill_count), WHITE),
            ("Bosses slain",        str(self.boss_killed),       (255, 100, 100)),
            ("Corruption waves",    str(self.player.corruption_waves_cleared), (220, 80, 255)),
            ("Gold collected",      str(self.player.gold),       YELLOW),
            ("Perks picked",        str(sum(int(v / next(p["bonus"] for p in ALL_PERKS
                                   if p["key"] == k) + 0.5) for k, v in self.player.perks.items())),
             PURPLE),
        ]
        for i, (label, value, col) in enumerate(stats):
            row_y = panel_y + 62 + i * 52
            lbl = self.fonts["med"].render(label, True, GRAY)
            val = self.fonts["large"].render(value, True, col)
            self.screen.blit(lbl, (panel_x + 24, row_y))
            self.screen.blit(val, (panel_x + panel_w - val.get_width() - 24, row_y))
            if i < len(stats) - 1:
                pygame.draw.line(self.screen, (45, 45, 62),
                                 (panel_x + 16, row_y + 38), (panel_x + panel_w - 16, row_y + 38), 1)

        # ── Right panel: leaderboard ──────────────────────────────────────────
        lb_x = 560; lb_y = 130; lb_w = 660; lb_h = 420
        pygame.draw.rect(self.screen, PANEL, (lb_x, lb_y, lb_w, lb_h), border_radius=12)
        pygame.draw.rect(self.screen, YELLOW, (lb_x, lb_y, lb_w, lb_h), 1, border_radius=12)

        active_lb = self.leaderboard_hc if self.hardcore else self.leaderboard
        active_lb.draw(self.screen, self.fonts, lb_x + 12, lb_y + 14,
                       lb_w - 24, highlight_name=self.player.username,
                       t=pygame.time.get_ticks() // 16)

        # Restart hint at bottom centre
        restart = self.fonts["large"].render("Press R to return to menu", True, YELLOW)
        self.screen.blit(restart, (SW // 2 - restart.get_width() // 2, SH - 48))

    # ── main loop ─────────────────────────────────────────────────────────────

    def run(self):
        import __main__
        __main__._live_game = self   # lets Profile.unlock sync cosmetics immediately
        while True:
            self.clock.tick(FPS)
            mx, my = pygame.mouse.get_pos()
            keys   = pygame.key.get_pressed()

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit(); sys.exit()
                if event.type == pygame.USEREVENT + 1:
                    MUSIC.on_track_end()
                if self.perk_screen.active:
                    self.perk_screen.handle_event(event)
                    continue
                # Settings button click on pause overlay
                if self.paused and not self.pause_settings and not self.pause_dev and not self.pause_dev_prompt:
                    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        # Settings button
                        sbx = SW // 2 - 258; sby = SH // 2 + 62
                        if sbx <= event.pos[0] <= sbx + 160 and sby <= event.pos[1] <= sby + 40:
                            self.pause_settings = True
                            continue
                        # Dev Tools button — show password prompt
                        dbx = SW // 2 - 80; dby = SH // 2 + 62
                        if dbx <= event.pos[0] <= dbx + 160 and dby <= event.pos[1] <= dby + 40:
                            self.pause_dev_prompt = True
                            self.pause_dev_input  = ""
                            continue
                        # Exit to Menu button
                        ex2 = SW // 2 + 98; ey2 = SH // 2 + 62
                        if ex2 <= event.pos[0] <= ex2 + 160 and ey2 <= event.pos[1] <= ey2 + 40:
                            MUSIC.play("menu")
                            return "menu"

                # Password prompt event handling
                if self.paused and self.pause_dev_prompt:
                    if event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_RETURN:
                            if self.pause_dev_input == "1234":
                                self.pause_dev        = True
                                self.pause_dev_prompt = False
                                self.pause_dev_input  = ""
                            else:
                                self.pause_dev_input = ""   # wrong — clear and try again
                        elif event.key in (pygame.K_ESCAPE, pygame.K_p):
                            self.pause_dev_prompt = False
                            self.pause_dev_input  = ""
                        elif event.key == pygame.K_BACKSPACE:
                            self.pause_dev_input = self.pause_dev_input[:-1]
                        elif len(self.pause_dev_input) < 8 and event.unicode.isprintable():
                            self.pause_dev_input += event.unicode
                    continue

                if self.paused and self.pause_dev:
                    if event.type == pygame.MOUSEWHEEL and self.dev_ach_expand:
                        self.dev_ach_scroll = max(0, self.dev_ach_scroll - event.y * 30)
                    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        px2, py2 = event.pos
                        DPX = SW // 2 - 170; DPY = SH // 2 - 110
                        DPW = 340;           DPH = 424   # 6 buttons
                        ROW_H = 56
                        gold_rect  = pygame.Rect(DPX + 20, DPY + 70 + ROW_H * 0, DPW - 40, 44)
                        lvl_rect   = pygame.Rect(DPX + 20, DPY + 70 + ROW_H * 1, DPW - 40, 44)
                        skip_rect  = pygame.Rect(DPX + 20, DPY + 70 + ROW_H * 2, DPW - 40, 44)
                        boss_rect  = pygame.Rect(DPX + 20, DPY + 70 + ROW_H * 3, DPW - 40, 44)
                        perk_rect  = pygame.Rect(DPX + 20, DPY + 70 + ROW_H * 4, DPW - 40, 44)
                        ach_rect2  = pygame.Rect(DPX + 20, DPY + 70 + ROW_H * 5, DPW - 40, 44)
                        # Boss flyout — right
                        FLY_W = 220; FLY_X = DPX + DPW + 8; FLY_Y = DPY
                        FLY_H = 20 + len(BOSS_TYPES) * 34 + 8
                        # Perk flyout — left
                        PKW = 220; PKX = DPX - PKW - 8; PKY = DPY
                        PKH = 20 + len(ALL_PERKS) * 34 + 8
                        # Achievement flyout — right (wider, scrollable)
                        ACH_W = 340; ACH_X = DPX + DPW + 8; ACH_Y = DPY
                        ACH_VISIBLE_H = 440; ACH_ROW = 32
                        ACH_H = ACH_VISIBLE_H
                        if gold_rect.collidepoint(px2, py2):
                            self.player.gold += 100; continue
                        if lvl_rect.collidepoint(px2, py2):
                            self.player.level += 1
                            self.player.max_hp = int((100 + self.player.level * 15) *
                                                     (1 + self.player.perk("max_hp_pct")))
                            self.player.hp = min(self.player.hp, self.player.max_hp)
                            self.player.xp_to_next = self.player.xp_for_level(self.player.level)
                            self.player.xp = 0; continue
                        if skip_rect.collidepoint(px2, py2):
                            self._skip_wave(); self.pause_dev = False; self.paused = False
                            self.dev_boss_expand = self.dev_perk_expand = self.dev_ach_expand = False
                            continue
                        if boss_rect.collidepoint(px2, py2):
                            self.dev_boss_expand = not self.dev_boss_expand
                            self.dev_perk_expand = self.dev_ach_expand = False; continue
                        if perk_rect.collidepoint(px2, py2):
                            self.dev_perk_expand = not self.dev_perk_expand
                            self.dev_boss_expand = self.dev_ach_expand = False; continue
                        if ach_rect2.collidepoint(px2, py2):
                            self.dev_ach_expand  = not self.dev_ach_expand
                            self.dev_boss_expand = self.dev_perk_expand = False
                            self.dev_ach_scroll  = 0; continue
                        # Boss flyout buttons
                        if self.dev_boss_expand:
                            for bi, bt in enumerate(BOSS_TYPES):
                                boss_btn = pygame.Rect(FLY_X + 8, FLY_Y + 20 + bi * 34, FLY_W - 16, 28)
                                if boss_btn.collidepoint(px2, py2):
                                    self._skip_to_boss(bi)
                                    self.dev_boss_expand = False
                                    self.pause_dev = False; self.paused = False; continue
                        # Perk flyout buttons
                        if self.dev_perk_expand:
                            for pi, pd in enumerate(ALL_PERKS):
                                perk_btn = pygame.Rect(PKX + 8, PKY + 20 + pi * 34, PKW - 16, 28)
                                if perk_btn.collidepoint(px2, py2):
                                    self.player.perks[pd["key"]] = (
                                        self.player.perks.get(pd["key"], 0) + pd["bonus"])
                                    self.floating_texts.append(
                                        FloatingText(self.player.x, self.player.y - 60,
                                                     f"[DEV] +{pd['label']}!", pd["color"], 20)); continue
                        # Achievement flyout buttons
                        if self.dev_ach_expand:
                            if ACH_X <= px2 <= ACH_X + ACH_W and ACH_Y <= py2 <= ACH_Y + ACH_H:
                                row_y_base = ACH_Y + 24
                                for ai, ach in enumerate(ACHIEVEMENTS):
                                    ry = row_y_base + ai * ACH_ROW - self.dev_ach_scroll
                                    if ry + ACH_ROW < ACH_Y or ry > ACH_Y + ACH_H:
                                        continue
                                    ab = pygame.Rect(ACH_X + 6, ry, ACH_W - 12, ACH_ROW - 4)
                                    if ab.collidepoint(px2, py2):
                                        if PROFILE.unlock(ach["id"]):
                                            self._queue_achievement_toasts([ach["id"]])
                                            self.floating_texts.append(
                                                FloatingText(self.player.x, self.player.y - 80,
                                                             f"[DEV] Achievement granted!", (255, 200, 60), 20))
                                        continue
                        # Close if clicked outside all panels
                        in_main = DPX <= px2 <= DPX + DPW and DPY <= py2 <= DPY + DPH
                        in_boss = self.dev_boss_expand and FLY_X <= px2 <= FLY_X + FLY_W and FLY_Y <= py2 <= FLY_Y + FLY_H
                        in_perk = self.dev_perk_expand and PKX <= px2 <= PKX + PKW and PKY <= py2 <= PKY + PKH
                        in_ach  = self.dev_ach_expand  and ACH_X <= px2 <= ACH_X + ACH_W and ACH_Y <= py2 <= ACH_Y + ACH_H
                        if not (in_main or in_boss or in_perk or in_ach):
                            self.pause_dev = self.dev_boss_expand = self.dev_perk_expand = self.dev_ach_expand = False
                    if event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_p):
                        self.pause_dev = self.dev_boss_expand = self.dev_perk_expand = self.dev_ach_expand = False
                    continue
                # Settings slider drag while paused
                if self.paused and self.pause_settings:
                    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        px2, py2 = event.pos
                        PSX = SW // 2 - 210; PSY = SH // 2 - 190
                        PSW = 420;           PSH = 390
                        SLX = PSX + 80;      SLW = PSW - 160
                        SLY  = PSY + 100
                        SLY2 = PSY + 182
                        PQY  = PSY + 242
                        PHY  = PSY + 298
                        PFY  = PSY + 354
                        pbw2 = (PSW - 60) // 2
                        if SLX - 10 <= px2 <= SLX + SLW + 10 and SLY - 14 <= py2 <= SLY + 14:
                            self.pause_slider_drag = "music"
                            MUSIC.set_volume(max(0.0, min(1.0, (px2 - SLX) / SLW)))
                        elif SLX - 10 <= px2 <= SLX + SLW + 10 and SLY2 - 14 <= py2 <= SLY2 + 14:
                            self.pause_slider_drag = "sfx"
                            SOUNDS.set_volume(max(0.0, min(1.0, (px2 - SLX) / SLW)))
                        elif (PSX + PSW // 2 - pbw2 <= px2 <= PSX + PSW // 2 and
                              PQY <= py2 <= PQY + 36):
                            GAME_SETTINGS.quality = "low"
                            GAME_SETTINGS.save()
                        elif (PSX + PSW // 2 + 10 <= px2 <= PSX + PSW // 2 + 10 + pbw2 and
                              PQY <= py2 <= PQY + 36):
                            GAME_SETTINGS.quality = "high"
                            GAME_SETTINGS.save()
                        elif (PSX + 20 <= px2 <= PSX + PSW - 20 and
                              PHY <= py2 <= PHY + 36):
                            GAME_SETTINGS.player_health_bar = not GAME_SETTINGS.player_health_bar
                            GAME_SETTINGS.save()
                        elif PFY <= py2 <= PFY + 36 and PSX + 20 <= px2 <= PSX + PSW - 20:
                            GAME_SETTINGS.fullscreen = not GAME_SETTINGS.fullscreen
                            GAME_SETTINGS.save()
                            if self._apply_display:
                                self._window = self._apply_display(self._window)
                                self.screen  = pygame.display.get_surface()
                        elif not (PSX <= px2 <= PSX + PSW and PSY <= py2 <= PSY + PSH):
                            self.pause_settings = False
                    if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                        self.pause_slider_drag = False
                        GAME_SETTINGS.save()   # persist volume on release
                    if event.type == pygame.MOUSEMOTION and self.pause_slider_drag:
                        PSX = SW // 2 - 210; PSW = 420; SLX = PSX + 80; SLW = PSW - 160
                        vol = max(0.0, min(1.0, (event.pos[0] - SLX) / SLW))
                        if self.pause_slider_drag == "music":
                            MUSIC.set_volume(vol)
                        else:
                            SOUNDS.set_volume(vol)
                    if event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_p):
                        self.pause_settings = False
                    continue   # don't fall through to normal event handling
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        # ESC pauses/unpauses rather than quitting
                        if not self.game_over and not self.perk_screen.active:
                            self.paused = not self.paused
                            if not self.paused:
                                self.pause_settings = False
                            continue
                    if self.game_over:
                        if event.key == pygame.K_r:
                            MUSIC.play("menu")
                            return "menu"   # signal caller to go back to username screen
                        continue
                    if event.key == pygame.K_p and not self.perk_screen.active:
                        self.paused = not self.paused
                        if not self.paused:
                            self.pause_settings = False
                    if self.paused:
                        continue
                    if event.key == pygame.K_SPACE and not self.shop.open and not self.perk_screen.active:
                        # Dash in movement direction (or toward mouse if standing still)
                        keys_now = pygame.key.get_pressed()
                        ddx = ddy = 0
                        if keys_now[pygame.K_w] or keys_now[pygame.K_UP]:    ddy -= 1
                        if keys_now[pygame.K_s] or keys_now[pygame.K_DOWN]:  ddy += 1
                        if keys_now[pygame.K_a] or keys_now[pygame.K_LEFT]:  ddx -= 1
                        if keys_now[pygame.K_d] or keys_now[pygame.K_RIGHT]: ddx += 1
                        if ddx == 0 and ddy == 0:   # no movement key — dash toward mouse
                            cam_now = self.get_camera()
                            mx_w = pygame.mouse.get_pos()[0] + cam_now[0]
                            my_w = pygame.mouse.get_pos()[1] + cam_now[1]
                            ddx = mx_w - self.player.x
                            ddy = my_w - self.player.y
                        self.player.try_dash(ddx, ddy)
                        for _ in range(5):
                            self.particles.append(Particle(self.player.x, self.player.y, (80, 220, 255)))
                    if event.key == pygame.K_TAB:
                        self.shop.toggle()
                        if self.shop.open:
                            MUSIC.pause_resume()   # snapshot position of whatever is playing
                            MUSIC.play("shop")
                        else:
                            MUSIC.unpause_resume() # resume battle or boss track mid-bar
                    if self.shop.open:
                        self.shop.handle_key(event.key, self.player, self.floating_texts)
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.shop.open:
                    self.shop.handle_click(event.pos, self.player, self.floating_texts)

            cam = self.get_camera()

            # ── Death animation ────────────────────────────────────────────────
            if self.death_anim_timer > 0:
                self.death_anim_timer -= 1
                self.draw_world(cam)
                # Draw world contents frozen (no updates)
                for gc in self.gold_coins: gc.draw(self.screen, cam)
                for orb in self.fire_orbs: orb.draw(self.screen, cam)
                for orb in self.hp_orbs:  orb.draw(self.screen, cam)
                for e in self.enemies: e.draw(self.screen, cam)
                for proj in self.projectiles: proj.draw(self.screen, cam)
                if self.boss and self.boss.alive:
                    self.boss.draw(self.screen, cam)
                    for m in self.boss.minions: m.draw(self.screen, cam)
                if self.boss_clone and self.boss_clone.alive:
                    self.boss_clone.draw(self.screen, cam)
                # Don't draw the player body — they're exploding
                # Update + draw death particles
                next_dp = []
                for dp in self.death_particles:
                    dp[0] += dp[2]; dp[1] += dp[3]
                    dp[2] *= 0.94; dp[3] *= 0.94   # slight drag
                    dp[4] -= 1
                    if dp[4] > 0:
                        next_dp.append(dp)
                        t    = dp[4] / dp[5]
                        size = max(1, int(6 * t))
                        alpha = max(0, min(255, int(255 * t)))
                        sx = int(dp[0] - cam[0]); sy = int(dp[1] - cam[1])
                        col = dp[6]
                        # Draw as a short streak
                        tail_x = sx - int(dp[2] * 3); tail_y = sy - int(dp[3] * 3)
                        tail_c = lerp_color(col, BLACK, 0.6)
                        pygame.draw.line(self.screen, tail_c, (tail_x, tail_y), (sx, sy),
                                         max(1, size - 1))
                        pygame.draw.circle(self.screen, col, (sx, sy), size)
                self.death_particles = next_dp
                # Dark vignette that grows as timer runs out
                t_norm = 1 - self.death_anim_timer / 120
                vign_alpha = int(180 * t_norm)
                if vign_alpha > 0:
                    vign = pygame.Surface((SW, SH), pygame.SRCALPHA)
                    vign.fill((0, 0, 0, vign_alpha))
                    self.screen.blit(vign, (0, 0))
                self.draw_hud()
                self.draw_achievement_toasts()
                _scaled_flip(self.screen)
                if self.death_anim_timer == 0:
                    self.game_over = True
                    active_lb = self.leaderboard_hc if self.hardcore else self.leaderboard
                    self.lb_rank = active_lb.submit(
                        self.player.username, self.wave, self.player.level,
                        self.player.kill_count, self.boss_killed)
                    # Hardcore: wipe the save slot on death
                    if self.hardcore and self.save_slot:
                        delete_checkpoint(self.save_slot)
                        print(f"[Hardcore] Slot {self.save_slot} deleted on death.")
                continue

            if self.game_over:
                self.draw_world(cam)
                self.draw_game_over()
                _scaled_flip(self.screen)
                continue

            # ── Cosmetic achievement checks run every frame (even while paused/shop open) ─
            # This ensures buying a cosmetic in the shop immediately fires the achievement.
            oc = TOKENS.owned_cosmetics
            cosm_ach_map = [("wings","cosm_wings"),("blackhole","cosm_blackhole"),
                            ("hexweaver","cosm_hexweaver"),("lavalord","cosm_lavalord"),
                            ("ironhide","cosm_ironhide")]
            all_cosm_ids = {c["id"] for c in COSMETICS}
            _cosm_new = []
            for cosm_id, aid in cosm_ach_map:
                if cosm_id in oc and aid not in PROFILE.unlocked:
                    if PROFILE.unlock(aid):
                        _cosm_new.append(aid)
            if all_cosm_ids <= oc and "cosm_all" not in PROFILE.unlocked:
                if PROFILE.unlock("cosm_all"):
                    _cosm_new.append("cosm_all")
            if _cosm_new:
                self._queue_achievement_toasts(_cosm_new)

            if not self.shop.open and not self.perk_screen.active and not self.paused:
                # ── Flush deferred save (triggered after wave clear / boss kill) ─
                if self.pending_save and self.save_slot:
                    save_checkpoint(self, self.save_slot)
                    self.pending_save = False

                self.player._enemies_ref = self.enemies
                self.player._boss_ref    = [b for b in [self.boss, self.boss_clone] if b and b.alive]
                self.player.update(keys, mx, my, cam, self.projectiles,
                                   (self.world_w, self.world_h))

                # ── Wave logic ────────────────────────────────────────────────
                alive_count = sum(1 for e in self.enemies if e.alive)
                boss_alive  = self.boss is not None and self.boss.alive
                clone_alive = self.boss_clone is not None and self.boss_clone.alive

                if self.wave_active:
                    # Boss wave: only one entity — the boss (and optional Vexara clone)
                    if self.boss_wave:
                        if not boss_alive and not clone_alive and not (self.boss_intro and self.boss_intro.active):
                            # Boss (and clone if present) are both dead
                            self._on_boss_killed()
                    else:
                        self.spawn_timer += 1
                        if (self.spawn_timer >= self.spawn_interval
                                and self.wave_enemy_count < self.wave_enemy_target):
                            self.spawn_timer = 0
                            self.spawn_enemy()
                            self.wave_enemy_count += 1
                        if self.wave_enemy_count >= self.wave_enemy_target and alive_count == 0:
                            self.wave_active = False
                            self.break_timer = WAVE_BREAK_SECS * FPS
                            # Clear lingering enemy projectiles so the player can't be hit after the wave
                            self.projectiles = [p for p in self.projectiles if p.owner == "player"]
                            self.fire_orbs   = []
                            if self.elite_wave:
                                self.player.corruption_waves_cleared += 1
                                MUSIC.play("battle")
                                self.floating_texts.append(
                                    FloatingText(self.player.x, self.player.y - 80,
                                                 f"Corruption Wave cleared!  ({self.player.corruption_waves_cleared} total)",
                                                 (220, 80, 255), 22))
                            else:
                                self.floating_texts.append(
                                    FloatingText(self.player.x, self.player.y - 80,
                                                 f"Wave {self.wave} cleared!", GREEN, 24))
                            self.elite_wave = False
                            if self.wave % 5 == 0 and self.wave % 10 != 0:
                                self.perk_screen.offer()
                            # 2% chance to drop a case on wave clear
                            if random.random() < 0.02:
                                TOKENS.add_case()
                                self.floating_texts.append(
                                    FloatingText(self.player.x, self.player.y - 110,
                                                 "Case dropped! Check Inventory.", (255, 200, 40), 20))
                            # Check level/kill/cosmetic achievements at every wave clear
                            new_achs = PROFILE.check_achievements(self)
                            self._queue_achievement_toasts(new_achs)
                            # Defer save until after perk screen (if open) or immediately
                            self.pending_save = True
                else:
                    self.break_timer -= 1
                    # ── Second save: last frame of break — shop purchases & drops captured ─
                    if self.break_timer == 1 and self.save_slot:
                        save_checkpoint(self, self.save_slot)
                    if self.break_timer <= 0:
                        self.wave             += 1
                        self.wave_active       = True
                        self.wave_enemy_count  = 0
                        self.boss_wave         = (self.wave % 10 == 0)
                        if self.boss_wave:
                            self._spawn_boss()
                        else:
                            # Roll elite wave chance based on player level
                            chance = elite_wave_chance(self.player.level)
                            self.elite_wave = (random.random() < chance)
                            self.wave_enemy_target = self._wave_size(self.wave)
                            self.spawn_timer       = 0
                            self.spawn_interval    = max(40, 90 - self.wave * 3)
                            MUSIC.play("battle")
                            if self.elite_wave:
                                self.corruption_flash_timer = 90   # 1.5s flash effect
                                MUSIC.play("corruption_wave")
                                self.floating_texts.append(
                                    FloatingText(self.player.x, self.player.y - 120,
                                                 "CORRUPTION WAVE!", (180, 0, 220), 34))
                                self.floating_texts.append(
                                    FloatingText(self.player.x, self.player.y - 78,
                                                 f"Wave {self.wave} — Corrupted enemies incoming!", (200, 80, 255), 20))
                            else:
                                self.elite_wave = False
                                self.floating_texts.append(
                                    FloatingText(self.player.x, self.player.y - 80,
                                                 f"Wave {self.wave} incoming!", RED, 24))

                # ── Enemy update ───────────────────────────────────────────────
                new_splinters = []
                for e in self.enemies:
                    drop_orb_before = e.drop_orb
                    e.update(self.player, self.projectiles, (self.world_w, self.world_h))
                    # Dragon dropped a fire orb this tick
                    if e.drop_orb and not drop_orb_before:
                        orb_count = 3 if (e.elite and e.behaviour == "dragon") else 1
                        for _ in range(orb_count):
                            ox = random.uniform(-20, 20) if orb_count > 1 else 0
                            oy = random.uniform(-20, 20) if orb_count > 1 else 0
                            self.fire_orbs.append(FireOrb(e.x + ox, e.y + oy, max(1, e.dmg // 3)))
                        e.drop_orb = False

                # ── Boss update ────────────────────────────────────────────────
                if self.boss and self.boss.alive and not (self.boss_intro and self.boss_intro.active) \
                        and not (self.enrage_anim and self.enrage_anim.active):
                    self.boss.update(self.player, self.projectiles, (self.world_w, self.world_h))
                    # Pending enrage — start the cinematic instead of announcing directly
                    if getattr(self.boss, '_pending_enrage', False):
                        self.boss._pending_enrage = False
                        self.enrage_anim = BossEnrageAnim(self.boss, self.fonts)
                    # Vexara split trigger
                    if getattr(self.boss, '_trigger_vex_split', False):
                        self.boss._trigger_vex_split = False
                        # Spawn clone offset to the side
                        offset_ang = random.uniform(0, math.pi * 2)
                        cx = self.boss.x + math.cos(offset_ang) * 160
                        cy = self.boss.y + math.sin(offset_ang) * 160
                        cx = max(80, min(self.world_w - 80, cx))
                        cy = max(80, min(self.world_h - 80, cy))
                        # Find Vexara's btype_idx
                        vex_idx = next(i for i, b in enumerate(BOSS_TYPES)
                                       if b["name"] == "Vexara the Hex-Weaver")
                        clone = Boss(cx, cy, vex_idx, self.player.level)
                        clone.is_vex_clone   = True
                        clone.vex_split_done = True
                        clone.enraged        = True    # clone always in enraged mode
                        clone.hp             = self.boss.hp   # same HP as primary
                        clone.max_hp         = self.boss.max_hp
                        clone.spiral_ang     = self.boss.spiral_ang + math.pi  # offset angle
                        clone.vex_pulse_t    = 30   # offset pulse phase
                        self.boss_clone      = clone
                        self.boss.vex_clone  = clone
                        self.floating_texts.append(
                            FloatingText(self.boss.x, self.boss.y - 80,
                                         "Vexara SPLITS!", (255, 80, 220), 26))

                # ── Vexara clone update ────────────────────────────────────────
                if self.boss_clone and self.boss_clone.alive:
                    self.boss_clone.update(self.player, self.projectiles,
                                           (self.world_w, self.world_h))
                elif self.boss_clone and not self.boss_clone.alive:
                    # Clone died — explosion particles
                    for _ in range(30):
                        self.particles.append(Particle(self.boss_clone.x, self.boss_clone.y,
                                                        (200, 0, 255)))
                        self.particles.append(Particle(self.boss_clone.x, self.boss_clone.y,
                                                        (255, 80, 200)))
                    self.floating_texts.append(
                        FloatingText(self.boss_clone.x, self.boss_clone.y - 60,
                                     "Clone destroyed!", (255, 80, 220), 20))
                    self.boss_clone = None

                # ── Orbiting void orbs hit detection ───────────────────────────
                for orb in self.player.void_orbs:
                    for e in self.enemies:
                        if not e.alive: continue
                        if math.hypot(orb.x - e.x, orb.y - e.y) < orb.size + e.size:
                            e.take_damage(orb.dmg)
                            self.floating_texts.append(FloatingText(e.x, e.y - 20, f"-{orb.dmg}", RED))
                            for _ in range(4):
                                self.particles.append(Particle(orb.x, orb.y, orb.col))
                            if not e.alive:
                                self.player.kill_count += 1
                                SOUNDS.play("enemy_death", volume_scale=0.8)
                                n_coins = random.randint(1, 3)
                                per_coin = max(1, e.gold_drop // n_coins)
                                for _ in range(n_coins):
                                    self.gold_coins.append(GoldCoin(e.x, e.y, per_coin))
                                if self.player.gain_xp(e.xp_drop):
                                    SOUNDS.play("level_up")
                                    self.floating_texts.append(
                                        FloatingText(self.player.x, self.player.y - 60,
                                                     f"LEVEL UP!  {self.player.level}", CYAN, 22))
                                for _ in range(10):
                                    self.particles.append(Particle(e.x, e.y, e.color))
                                if not self.hardcore and random.random() < 0.10 and len(self.hp_orbs) < 5:
                                    hp_amt = 10 if e.elite else 5
                                    self.hp_orbs.append(HpOrb(e.x, e.y, hp_amt))

                # ── Projectiles ────────────────────────────────────────────────
                for proj in self.projectiles[:]:
                    proj.update()
                    if not proj.alive:
                        self.projectiles.remove(proj); continue

                    if proj.owner == "player":
                        # Boss is invulnerable during spawn cinematic or enrage animation
                        if self.boss and self.boss.alive:
                            if (self.boss_intro and self.boss_intro.active) or \
                               (self.enrage_anim and self.enrage_anim.active):
                                pass  # cannot hit boss during either cinematic
                            elif math.hypot(proj.x - self.boss.x, proj.y - self.boss.y) < proj.size + self.boss.size:
                                self.boss.take_damage(proj.dmg)
                                self.player.lifesteal_acc += self.player.perk("hp_regen"); heal = int(self.player.lifesteal_acc); self.player.lifesteal_acc -= heal; self.player.hp = min(self.player.max_hp, self.player.hp + heal)
                                self.floating_texts.append(FloatingText(self.boss.x, self.boss.y - 30, f"-{proj.dmg}", (255, 100, 100)))
                                for _ in range(5):
                                    self.particles.append(Particle(proj.x, proj.y, self.boss.proj_col))
                                proj.alive = False
                                if getattr(self.boss, '_trigger_mal_spin', False):
                                    self.boss._trigger_mal_spin = False
                                    SOUNDS.play("orc_spin", volume_scale=1.0)
                                    self.floating_texts.append(
                                        FloatingText(self.boss.x, self.boss.y - 60,
                                                     "INFERNO SPIN!", (255, 140, 20), 22))
                                continue
                        # Hit Vexara clone — checked independently of main boss alive state
                        if self.boss_clone and self.boss_clone.alive:
                            if math.hypot(proj.x - self.boss_clone.x, proj.y - self.boss_clone.y) < proj.size + self.boss_clone.size:
                                self.boss_clone.take_damage(proj.dmg)
                                self.player.lifesteal_acc += self.player.perk("hp_regen"); heal = int(self.player.lifesteal_acc); self.player.lifesteal_acc -= heal; self.player.hp = min(self.player.max_hp, self.player.hp + heal)
                                self.floating_texts.append(
                                    FloatingText(self.boss_clone.x, self.boss_clone.y - 30,
                                                 f"-{proj.dmg}", (255, 100, 100)))
                                for _ in range(5):
                                    self.particles.append(Particle(proj.x, proj.y, self.boss_clone.proj_col))
                                proj.alive = False
                                continue
                        # Hit Gorvak minions
                        if self.boss and self.boss.alive:
                            for m in self.boss.minions:
                                if m.alive and math.hypot(proj.x - m.x, proj.y - m.y) < proj.size + m.SIZE:
                                    m.take_damage(proj.dmg)
                                    self.player.lifesteal_acc += self.player.perk("hp_regen"); heal = int(self.player.lifesteal_acc); self.player.lifesteal_acc -= heal; self.player.hp = min(self.player.max_hp, self.player.hp + heal)
                                    self.floating_texts.append(FloatingText(m.x, m.y - 18, f"-{proj.dmg}", (120, 220, 120)))
                                    for _ in range(5):
                                        self.particles.append(Particle(proj.x, proj.y, (120, 220, 120)))
                                    proj.alive = False
                                    if not m.alive:
                                        for _ in range(8):
                                            self.particles.append(Particle(m.x, m.y, (120, 220, 120)))
                                    break
                        is_pierce = isinstance(proj, PierceProjectile)
                        for e in self.enemies:
                            if not e.alive: continue
                            if is_pierce and hasattr(proj, 'hit_ids') and e.etype in proj.hit_ids: continue
                            if math.hypot(proj.x - e.x, proj.y - e.y) < proj.size + e.size:
                                e.take_damage(proj.dmg)
                                self.player.lifesteal_acc += self.player.perk("hp_regen"); heal = int(self.player.lifesteal_acc); self.player.lifesteal_acc -= heal; self.player.hp = min(self.player.max_hp, self.player.hp + heal)
                                self.floating_texts.append(FloatingText(e.x, e.y - 20, f"-{proj.dmg}", RED))
                                for _ in range(6):
                                    self.particles.append(Particle(proj.x, proj.y, proj.col))
                                if is_pierce:
                                    proj.hit_ids.add(e.etype)   # mark as hit, don't remove proj
                                else:
                                    proj.alive = False
                                if not e.alive:
                                    self.player.kill_count += 1
                                    SOUNDS.play("enemy_death", volume_scale=0.8)
                                    n_coins  = random.randint(1, 3)
                                    per_coin = max(1, e.gold_drop // n_coins)
                                    for _ in range(n_coins):
                                        self.gold_coins.append(GoldCoin(e.x, e.y, per_coin))
                                    if self.player.gain_xp(e.xp_drop):
                                        SOUNDS.play("level_up")
                                        self.floating_texts.append(
                                            FloatingText(self.player.x, self.player.y - 60,
                                                         f"LEVEL UP!  {self.player.level}", CYAN, 22))
                                    for _ in range(12):
                                        self.particles.append(Particle(e.x, e.y, e.color))
                                    # ── HP orb drop (10% chance, max 5 orbs on screen) ──
                                    if not self.hardcore and random.random() < 0.10 and len(self.hp_orbs) < 5:
                                        hp_amt = 10 if e.elite else 5
                                        self.hp_orbs.append(HpOrb(e.x, e.y, hp_amt))
                                    # ── On-death specials ──────────────────────
                                    if e.behaviour == "bounce" and not e.is_splinter:
                                        for _ in range(2):
                                            ox = random.uniform(-30, 30)
                                            oy = random.uniform(-30, 30)
                                            new_splinters.append(
                                                Enemy(e.x + ox, e.y + oy,
                                                      0, self.player.level,
                                                      is_splinter=True))
                                if not is_pierce:
                                    break

                    elif proj.owner == "enemy":
                        if math.hypot(proj.x - self.player.x, proj.y - self.player.y) < proj.size + self.player.size:
                            if self.player.take_damage(proj.dmg):
                                SOUNDS.play("player_hit", volume_scale=0.9)
                                self.floating_texts.append(FloatingText(self.player.x, self.player.y - 30, f"-{proj.dmg}", RED))
                                for _ in range(8):
                                    self.particles.append(Particle(self.player.x, self.player.y, PURPLE))
                            proj.alive = False

                self.enemies = [e for e in self.enemies if e.alive] + new_splinters

                # ── Fire orbs ──────────────────────────────────────────────────
                self.fire_orbs = [orb for orb in self.fire_orbs if orb.update(self.player)]

                # ── Nyxoth fire bombs ──────────────────────────────────────────
                # Drain pending bombs queued by boss update
                if self.boss and self.boss.pattern == "homing":
                    pending = getattr(self.boss, 'fire_orbs_pending', [])
                    if pending:
                        self.nyx_bombs.extend(pending)
                        self.boss.fire_orbs_pending = []
                self.nyx_bombs = [b for b in self.nyx_bombs if b.update(self.player)]

                # ── Gold coins ─────────────────────────────────────────────────
                for gc in self.gold_coins[:]:
                    gc.update(self.player)
                    if not gc.alive:
                        self.floating_texts.append(
                            FloatingText(self.player.x, self.player.y - 40, f"+{gc.amount}g", YELLOW))
                        self.gold_coins.remove(gc)

                # ── HP orbs ────────────────────────────────────────────────────
                for orb in self.hp_orbs[:]:
                    orb.update(self.player)
                    if not orb.alive:
                        self.floating_texts.append(
                            FloatingText(self.player.x, self.player.y - 40,
                                         f"+{orb.amount} HP", GREEN))
                        self.hp_orbs.remove(orb)

                # ── Boss intro tick ───────────────────────────────────────────
                if self.boss_intro and self.boss_intro.active:
                    self.boss_intro.update()
                    if not self.boss_intro.active:
                        self.boss_intro = None   # cinematic done, boss goes live

                # ── Boss enrage animation tick ────────────────────────────────
                if self.enrage_anim and self.enrage_anim.active:
                    self.enrage_anim.update()
                    if not self.enrage_anim.active:
                        # Anim done — now actually set enraged and announce
                        if self.boss and self.boss.alive:
                            self.boss.enraged = True
                            self.floating_texts.append(
                                FloatingText(self.boss.x, self.boss.y - 80,
                                             f"{self.boss.name} ENRAGES!", (255, 80, 80), 24))
                        self.enrage_anim = None
                for p in self.particles[:]:
                    p.update()
                    if p.life <= 0: self.particles.remove(p)
                for ft in self.floating_texts[:]:
                    ft.update()
                    if ft.life <= 0: self.floating_texts.remove(ft)

                if self.player.hp <= 0 and self.death_anim_timer == 0 and not self.game_over:
                    self.death_anim_timer = 120   # 2 seconds at 60fps
                    SOUNDS.play("player_hit", volume_scale=1.0)
                    # Spawn outward explosion particles — bolts in all directions
                    px_c = self.player.x; py_c = self.player.y
                    cols = [CYAN, WHITE, (80, 200, 255), (200, 80, 255), YELLOW]
                    for i in range(40):
                        ang  = math.pi * 2 / 40 * i + random.uniform(-0.1, 0.1)
                        spd  = random.uniform(2.5, 7.0)
                        life = random.randint(50, 115)
                        self.death_particles.append([
                            px_c, py_c,
                            math.cos(ang) * spd,
                            math.sin(ang) * spd,
                            life, life,
                            random.choice(cols),
                        ])

            # ── Draw ──────────────────────────────────────────────────────────
            self.draw_world(cam)

            # Nyxoth: star field overlay (screen-space, doesn't scroll)
            if (self.boss_wave and self.boss and
                    getattr(self.boss, 'pattern', None) == "homing"):
                t_twink = pygame.time.get_ticks()
                for i, (ssx, ssy, sr, sbright) in enumerate(self.nyx_stars):
                    twinkle  = sbright * (0.6 + 0.4 * math.sin(t_twink * 0.003 + i * 1.7))
                    star_col = lerp_color((100, 80, 160), (255, 255, 255), twinkle)
                    pygame.draw.circle(self.screen, star_col, (ssx, ssy), sr)
            for gc in self.gold_coins:
                gc.draw(self.screen, cam)
            for orb in self.fire_orbs:
                orb.draw(self.screen, cam)
            for orb in self.hp_orbs:
                orb.draw(self.screen, cam)
            for bomb in self.nyx_bombs:
                bomb.draw(self.screen, cam)
            for e in self.enemies:
                e.draw(self.screen, cam)
            if self.boss and self.boss.alive and not (self.boss_intro and self.boss_intro.active) \
                    and not (self.enrage_anim and self.enrage_anim.active):
                self.boss.draw(self.screen, cam)
                for m in self.boss.minions:
                    m.draw(self.screen, cam)
            if self.boss_clone and self.boss_clone.alive:
                self.boss_clone.draw(self.screen, cam)
            for proj in self.projectiles:
                proj.draw(self.screen, cam)
            for p in self.particles:
                p.draw(self.screen, cam)
            self.player.draw(self.screen, cam, self.fonts["small"], self.fonts["tiny"])
            for ft in self.floating_texts:
                ft.draw(self.screen, cam, self.fonts["small"])

            if self.boss_intro and self.boss_intro.active:
                # Cinematic draws darkness + boss materialisation + HUD on top
                self.boss_intro.draw(self.screen, cam, self.draw_hud)
            elif self.enrage_anim and self.enrage_anim.active:
                # Enrage cinematic — darkness + shockwaves + card + HUD on top
                self.enrage_anim.draw(self.screen, cam, self.draw_hud)
            else:
                # ── Gorvak dungeon overlay (torchlight flicker, under HUD) ────
                is_gorvak_boss = (self.boss_wave and self.boss and
                                  getattr(self.boss, 'pattern', None) == "burst")
                if is_gorvak_boss:
                    t_now = pygame.time.get_ticks()
                    # Torchlight flicker — warm amber tint that subtly breathes
                    flicker = (math.sin(t_now * 0.011) * 0.35 +
                               math.sin(t_now * 0.029) * 0.15 + 0.5)
                    flicker = max(0.0, min(1.0, flicker))
                    tint_r  = max(0, min(255, int(40 + flicker * 25)))
                    tint_g  = max(0, min(255, int(18 + flicker * 10)))
                    self._overlay.fill((tint_r, tint_g, 0, 35))
                    self.screen.blit(self._overlay, (0, 0))

                # ── Seraphix arena overlay (divine light, under HUD) ──────────
                is_seraphix_boss = (self.boss_wave and self.boss and
                                    getattr(self.boss, 'pattern', None) == "orbit")
                if is_seraphix_boss:
                    t_now = pygame.time.get_ticks()

                    # Warm golden tint — subtle, pulsing gently
                    pulse_s = math.sin(t_now * 0.0015) * 0.5 + 0.5
                    tint_r  = max(0, min(255, int(60 + pulse_s * 30)))
                    tint_g  = max(0, min(255, int(40 + pulse_s * 20)))
                    self._overlay.fill((tint_r, tint_g, 0, 28))
                    self.screen.blit(self._overlay, (0, 0))

                    if not GAME_SETTINGS.low:
                        # Diagonal divine light shafts (screen-space, slow drift)
                        shaft_alpha = max(0, min(255, int(18 + pulse_s * 12)))
                        self._overlay.fill((0, 0, 0, 0))
                        for si in range(5):
                            shaft_x = int((SW * si / 4 + t_now * 0.008 * (1 + si * 0.3)) % (SW + 200)) - 100
                            shaft_w = 30 + si * 12
                            pts_shaft = [
                                (shaft_x,           -20),
                                (shaft_x + shaft_w, -20),
                                (shaft_x + shaft_w + 80, SH + 20),
                                (shaft_x + 80,           SH + 20),
                            ]
                            sa = max(0, min(255, shaft_alpha - si * 2))
                            if sa > 2:
                                pygame.draw.polygon(self._overlay,
                                                    (255, 230, 140, sa), pts_shaft)
                        self.screen.blit(self._overlay, (0, 0))

                    # Spawn floating divine motes (LQ: much lower rate, capped list)
                    self.sera_mote_cd -= 1
                    lq_mote_limit = 8 if GAME_SETTINGS.low else 9999
                    if self.sera_mote_cd <= 0 and len(self.sera_motes) < lq_mote_limit:
                        self.sera_mote_cd = random.randint(18, 40) if GAME_SETTINGS.low else random.randint(3, 9)
                        ex = cam[0] + random.randint(0, SW)
                        ey = cam[1] + random.randint(0, SH)
                        self.sera_motes.append([
                            float(ex), float(ey),
                            random.uniform(-0.4, 0.4),   # vx drift
                            random.uniform(-1.4, -0.3),  # vy upward
                            random.randint(40, 100),
                            random.randint(40, 100),
                            random.randint(2, 5),
                        ])

                    # Update + draw motes — direct solid circles
                    surviving_sm = []
                    for sm in self.sera_motes:
                        sm[0] += sm[2]; sm[1] += sm[3]
                        sm[4] -= 1
                        if sm[4] > 0:
                            surviving_sm.append(sm)
                            st  = sm[4] / sm[5]
                            smx = int(sm[0] - cam[0]); smy = int(sm[1] - cam[1])
                            if -8 < smx < SW + 8 and -8 < smy < SH + 8:
                                sr  = max(1, int(sm[6] * st))
                                # Colour: bright gold → pale yellow → white
                                scr = max(0, min(255, int(255)))
                                scg = max(0, min(255, int(200 + st * 40)))
                                scb = max(0, min(255, int(80 + st * 140)))
                                pygame.draw.circle(self.screen, (scr, scg, scb), (smx, smy), sr)
                    self.sera_motes = surviving_sm

                # ── Malachar arena overlay (world-space, under HUD) ───────────
                is_malachar_boss = (self.boss_wave and self.boss and
                                    getattr(self.boss, 'pattern', None) == "charge")
                if is_malachar_boss:
                    t_now   = pygame.time.get_ticks()
                    pulse_m = math.sin(t_now * 0.002) * 0.5 + 0.5
                    tint_r  = max(0, min(255, int(50 + pulse_m * 40)))
                    tint_g  = max(0, min(255, int(10 + pulse_m * 15)))
                    self._overlay.fill((tint_r, tint_g, 0, 65))
                    self.screen.blit(self._overlay, (0, 0))

                    # Spawn rising embers (LQ: slower spawn, hard cap on list)
                    self.mal_ember_cd -= 1
                    lq_ember_limit = 12 if GAME_SETTINGS.low else 9999
                    if self.mal_ember_cd <= 0 and len(self.mal_embers) < lq_ember_limit:
                        self.mal_ember_cd = random.randint(22, 45) if GAME_SETTINGS.low else random.randint(6, 16)
                        ex = cam[0] + random.randint(0, SW)
                        ey = cam[1] + random.randint(0, SH)
                        self.mal_embers.append([
                            float(ex), float(ey),
                            random.uniform(-0.5, 0.5),
                            random.uniform(-1.8, -0.6),
                            random.randint(30, 70),
                            random.randint(30, 70),
                        ])

                    # Update + draw embers — draw directly, no per-ember surface alloc
                    surviving_em = []
                    for em in self.mal_embers:
                        em[0] += em[2]; em[1] += em[3]
                        em[4] -= 1
                        if em[4] > 0:
                            surviving_em.append(em)
                            et  = em[4] / em[5]
                            ea  = max(0, min(255, int(200 * et)))
                            if ea < 8:
                                continue
                            esx = int(em[0] - cam[0]); esy = int(em[1] - cam[1])
                            if -4 < esx < SW + 4 and -4 < esy < SH + 4:
                                er  = max(1, int(3 * et))
                                ecr = max(0, min(255, int(255 - (1 - et) * 55)))
                                ecg = max(0, min(255, int(80 + et * 140)))
                                pygame.draw.circle(self.screen, (ecr, ecg, 0), (esx, esy), er)
                    self.mal_embers = surviving_em

                # ── Vexara boss arena overlay (world-space, under HUD) ─────────
                is_vexara_boss = (self.boss_wave and self.boss and
                                  getattr(self.boss, 'pattern', None) == "spiral")
                if is_vexara_boss:
                    t_now = pygame.time.get_ticks()
                    pulse_t = math.sin(t_now * 0.0025) * 0.5 + 0.5
                    tint_r  = max(0, min(255, int(lerp_color((50, 0, 80), (80, 0, 60), pulse_t)[0])))
                    tint_b  = max(0, min(255, int(lerp_color((50, 0, 80), (80, 0, 60), pulse_t)[2])))
                    self._overlay.fill((tint_r, 0, tint_b, 90))
                    self.screen.blit(self._overlay, (0, 0))

                    # Extra pressure flag: clone is alive — double bullet volume
                    clone_alive = self.boss_clone is not None and self.boss_clone.alive

                    if not GAME_SETTINGS.low:
                        # ── Intense zap lines ─────────────────────────────────
                        self.vex_zap_cd -= 1
                        if self.vex_zap_cd <= 0:
                            self.vex_zap_cd = random.randint(6, 20)
                            wx0 = cam[0] + random.randint(10, SW - 10)
                            wy0 = cam[1] + random.randint(10, SH - 10)
                            pts = [(wx0, wy0)]
                            zx, zy = wx0, wy0
                            segs   = random.randint(6, 14)
                            length = random.randint(120, 380)
                            base_a = random.uniform(0, math.pi * 2)
                            seg_l  = length / segs
                            for _ in range(segs):
                                base_a += random.uniform(-1.1, 1.1)
                                zx += math.cos(base_a) * seg_l
                                zy += math.sin(base_a) * seg_l
                                pts.append((zx, zy))
                            life = random.randint(10, 22)
                            self.vex_zaps.append([pts, life, life])

                        # Draw all zaps onto a single shared overlay then blit once
                        if self.vex_zaps:
                            self._overlay.fill((0, 0, 0, 0))
                            surviving_vz = []
                            for zap in self.vex_zaps:
                                zpts, zlife, zmax = zap
                                zlife -= 1
                                if zlife > 0:
                                    surviving_vz.append([zpts, zlife, zmax])
                                    fade  = zlife / zmax
                                    alpha = int(255 * fade)
                                    screen_pts = [(int(px - cam[0]), int(py - cam[1])) for px, py in zpts]
                                    if len(screen_pts) >= 2:
                                        pygame.draw.lines(self._overlay, (180, 0, 255, max(0, int(alpha * 0.5))),
                                                          False, screen_pts, 5)
                                        core_col = lerp_color((255, 80, 255), (255, 255, 255), fade)
                                        pygame.draw.lines(self._overlay, (*core_col, alpha),
                                                          False, screen_pts, 2)
                                        pygame.draw.lines(self._overlay, (255, 255, 255, max(0, int(alpha * 0.7))),
                                                          False, screen_pts, 1)
                            self.screen.blit(self._overlay, (0, 0))
                            self.vex_zaps = surviving_vz
                        else:
                            self.vex_zaps = [z for z in self.vex_zaps if z[1] > 0]

                        # ── Drifting hex runes ────────────────────────────────
                        self.vex_rune_cd -= 1
                        if self.vex_rune_cd <= 0:
                            self.vex_rune_cd = random.randint(40, 90)
                            rwx = cam[0] + random.randint(40, SW - 40)
                            rwy = cam[1] + random.randint(40, SH - 40)
                            spin = random.uniform(-0.012, 0.012)
                            life = random.randint(120, 220)
                            self.vex_runes.append([float(rwx), float(rwy),
                                                   random.uniform(0, math.pi * 2),
                                                   spin, life, life])

                        # Draw all runes onto shared overlay then blit once
                        if self.vex_runes:
                            self._overlay.fill((0, 0, 0, 0))
                            surviving_r = []
                            for rune in self.vex_runes:
                                rwx, rwy, rang, spin, rlife, rmax = rune
                                rlife -= 1
                                rune[4] = rlife
                                rune[2] += spin
                                if rlife > 0:
                                    surviving_r.append(rune)
                                    fade  = min(1.0, rlife / rmax * 2) if rlife > rmax * 0.5 else (rlife / (rmax * 0.5))
                                    alpha = int(70 * fade)
                                    if alpha > 2:
                                        rsx = int(rwx - cam[0]); rsy = int(rwy - cam[1])
                                        if -80 < rsx < SW + 80 and -80 < rsy < SH + 80:
                                            r_rad = int(30 + math.sin(rlife * 0.05) * 8)
                                            hex_pts = [
                                                (rsx + int(math.cos(rang + math.pi / 3 * i) * r_rad),
                                                 rsy + int(math.sin(rang + math.pi / 3 * i) * r_rad))
                                                for i in range(6)
                                            ]
                                            inner_pts = [
                                                (rsx + int(math.cos(rang + math.pi / 3 * i) * (r_rad // 2)),
                                                 rsy + int(math.sin(rang + math.pi / 3 * i) * (r_rad // 2)))
                                                for i in range(6)
                                            ]
                                            rune_col = lerp_color((200, 0, 255), (255, 80, 200), pulse_t)
                                            pygame.draw.polygon(self._overlay, (*rune_col, alpha), hex_pts, 1)
                                            pygame.draw.polygon(self._overlay, (*rune_col, alpha // 2), inner_pts, 1)
                                            for i in range(3):
                                                pygame.draw.line(self._overlay, (*rune_col, alpha // 3),
                                                                 hex_pts[i], hex_pts[i + 3], 1)
                            self.screen.blit(self._overlay, (0, 0))
                            self.vex_runes = surviving_r
                        else:
                            self.vex_runes = [r for r in self.vex_runes if r[4] > 0]
                    else:
                        # LQ: drain existing zap/rune lists without drawing them
                        self.vex_zaps  = [z for z in self.vex_zaps  if z[1] > 1]
                        self.vex_runes = [r for r in self.vex_runes  if r[4] > 1]

                    # ── Void wisps — drifting upward corruption motes ─────────
                    # LQ: slow spawn rate + hard cap; halve cap again when clone is alive
                    wisp_cap = (6 if clone_alive else 10) if GAME_SETTINGS.low else 9999
                    self.vex_wisp_cd -= 1
                    if self.vex_wisp_cd <= 0 and len(self.vex_wisps) < wisp_cap:
                        self.vex_wisp_cd = random.randint(20, 50) if GAME_SETTINGS.low else random.randint(4, 12)
                        spawn_mode = random.randint(0, 2)
                        if spawn_mode == 0 and self.vex_cracks:
                            crack = random.choice(self.vex_cracks)
                            pt    = random.choice(crack)
                            wx2, wy2 = pt[0], pt[1]
                        elif spawn_mode == 1:
                            ang = random.uniform(0, math.pi * 2)
                            r2  = random.randint(0, 300)
                            wx2 = self.world_w // 2 + math.cos(ang) * r2
                            wy2 = self.world_h // 2 + math.sin(ang) * r2
                        else:
                            wx2 = cam[0] + random.randint(0, SW)
                            wy2 = cam[1] + random.randint(0, SH)
                        self.vex_wisps.append([
                            float(wx2), float(wy2),
                            random.uniform(-0.35, 0.35),
                            random.uniform(-1.2, -0.3),
                            random.randint(50, 120),
                            random.randint(50, 120),
                            random.randint(2, 5),
                        ])

                    # Update and draw wisps — two-tone void colour fading out
                    surviving_w = []
                    for w in self.vex_wisps:
                        w[0] += w[2]; w[1] += w[3]
                        w[4] -= 1
                        if w[4] > 0:
                            surviving_w.append(w)
                            wt  = w[4] / w[5]
                            wsx = int(w[0] - cam[0])
                            wsy = int(w[1] - cam[1])
                            if -10 < wsx < SW + 10 and -10 < wsy < SH + 10:
                                wr  = max(1, int(w[6] * wt))
                                if GAME_SETTINGS.low:
                                    # Single colour draw on LQ
                                    pygame.draw.circle(self.screen, (140, 0, 200), (wsx, wsy), wr)
                                else:
                                    wc_inner = (max(0, min(255, int(200 + wt * 55))),
                                                max(0, min(255, int(wt * 80))),
                                                max(0, min(255, int(220 + wt * 35))))
                                    wc_outer = (max(0, min(255, int(80 + wt * 60))),
                                                0,
                                                max(0, min(255, int(120 + wt * 80))))
                                    if wr > 1:
                                        pygame.draw.circle(self.screen, wc_outer, (wsx, wsy), wr)
                                    pygame.draw.circle(self.screen, wc_inner, (wsx, wsy), max(1, wr - 1))
                    self.vex_wisps = surviving_w

                    # ── Summoning circle overlay glow (over all game objects) ──
                    acx_ov = int(self.world_w // 2 - cam[0])
                    acy_ov = int(self.world_h // 2 - cam[1])
                    if -500 < acx_ov < SW + 500 and -500 < acy_ov < SH + 500:
                        ring_t_ov = t_now * 0.0010
                        for ri, base_r in enumerate([148, 100, 55]):
                            rp     = math.sin(ring_t_ov * 1.4 + ri * 1.1) * 0.5 + 0.5
                            cr     = max(1, int(base_r + rp * 14) - ri * 2)
                            bright = max(0, int(55 + rp * 55) - ri * 14)
                            ov_col = (max(0, min(255, bright // 2)),
                                      0,
                                      max(0, min(255, bright)))
                            if bright > 6 and cr > 0:
                                pygame.draw.circle(self.screen, ov_col,
                                                   (acx_ov, acy_ov), cr,
                                                   2 if ri < 2 else 1)

                # ── Corruption wave ambient overlay (world-space, under HUD) ──
                if self.elite_wave:
                    self._overlay.fill((40, 0, 60, 55))
                    self.screen.blit(self._overlay, (0, 0))

                    self.corruption_zap_cd -= 1
                    if self.corruption_zap_cd <= 0:
                        self.corruption_zap_cd = random.randint(18, 55)
                        wx0 = cam[0] + random.randint(20, SW - 20)
                        wy0 = cam[1] + random.randint(20, SH - 20)
                        pts = [(wx0, wy0)]
                        zx, zy = wx0, wy0
                        segs   = random.randint(5, 11)
                        length = random.randint(80, 260)
                        base_a = random.uniform(0, math.pi * 2)
                        seg_l  = length / segs
                        for _ in range(segs):
                            base_a += random.uniform(-0.9, 0.9)
                            zx += math.cos(base_a) * seg_l
                            zy += math.sin(base_a) * seg_l
                            pts.append((zx, zy))
                        life = random.randint(14, 28)
                        self.corruption_zaps.append([pts, life, life])

                    if self.corruption_zaps:
                        self._overlay.fill((0, 0, 0, 0))
                        surviving = []
                        for zap in self.corruption_zaps:
                            zpts, zlife, zmax = zap
                            zlife -= 1
                            if zlife > 0:
                                surviving.append([zpts, zlife, zmax])
                                fade  = zlife / zmax
                                width = max(1, int(3 * fade))
                                alpha = int(220 * fade)
                                screen_pts = [(int(px - cam[0]), int(py - cam[1])) for px, py in zpts]
                                if len(screen_pts) >= 2:
                                    pygame.draw.lines(self._overlay, (200, 80, 255, alpha),
                                                      False, screen_pts, width + 1)
                                    pygame.draw.lines(self._overlay, (240, 180, 255, min(255, int(alpha * 0.6))),
                                                      False, screen_pts, max(1, width - 1))
                        self.screen.blit(self._overlay, (0, 0))
                        self.corruption_zaps = surviving
                    else:
                        self.corruption_zaps = []

                self.draw_hud()

            self.shop.draw(self.screen, self.player, self.fonts)
            self.perk_screen.draw(self.screen)
            if self.paused:
                overlay = pygame.Surface((SW, SH), pygame.SRCALPHA)
                overlay.fill((0, 0, 0, 160))
                self.screen.blit(overlay, (0, 0))

                pt = self.fonts["huge"].render("PAUSED", True, WHITE)
                self.screen.blit(pt, (SW // 2 - pt.get_width() // 2, SH // 2 - 80))

                ps = self.fonts["med"].render("Press P or ESC to resume", True, GRAY)
                self.screen.blit(ps, (SW // 2 - ps.get_width() // 2, SH // 2 - 20))

                # Three buttons: Settings | Dev Tools | Exit to Menu
                sb_rect   = pygame.Rect(SW // 2 - 258, SH // 2 + 62, 160, 40)
                db_rect   = pygame.Rect(SW // 2 - 80,  SH // 2 + 62, 160, 40)
                exit_rect = pygame.Rect(SW // 2 + 98,  SH // 2 + 62, 160, 40)

                for rect, label, col in [
                    (sb_rect,   "Settings",       (140, 180, 255)),
                    (db_rect,   "Dev Tools",       (255, 160, 40)),
                    (exit_rect, "Exit to Menu",    (220, 60, 60)),
                ]:
                    pygame.draw.rect(self.screen, lerp_color(PANEL, col, 0.2), rect, border_radius=8)
                    pygame.draw.rect(self.screen, col, rect, 1, border_radius=8)
                    btn_lbl = self.fonts["small"].render(label, True, col)
                    self.screen.blit(btn_lbl, (rect.centerx - btn_lbl.get_width() // 2,
                                               rect.centery - btn_lbl.get_height() // 2))

                # Settings panel (if open)
                if self.pause_settings:
                    PSX = SW // 2 - 210; PSY = SH // 2 - 190
                    PSW = 420;           PSH = 390
                    SLX = PSX + 80;      SLW = PSW - 160
                    SLY  = PSY + 100     # music slider
                    SLY2 = PSY + 182     # sfx slider
                    PQY  = PSY + 242     # quality toggle row
                    PHY  = PSY + 298     # player health bar toggle row
                    PFY  = PSY + 354     # fullscreen toggle row

                    pygame.draw.rect(self.screen, PANEL,
                                     (PSX, PSY, PSW, PSH), border_radius=14)
                    pygame.draw.rect(self.screen, (140, 180, 255),
                                     (PSX, PSY, PSW, PSH), 2, border_radius=14)

                    stitle = self.fonts["large"].render("Settings", True, (140, 180, 255))
                    self.screen.blit(stitle,
                                     (PSX + PSW // 2 - stitle.get_width() // 2, PSY + 14))
                    pygame.draw.line(self.screen, (60, 60, 90),
                                     (PSX + 20, PSY + 50), (PSX + PSW - 20, PSY + 50), 1)

                    def _ps(label, vol, sy):
                        lbl = self.fonts["med"].render(label, True, WHITE)
                        self.screen.blit(lbl, (PSX + 20, sy - 34))
                        pct = self.fonts["med"].render(f"{int(vol * 100)}%", True, CYAN)
                        self.screen.blit(pct, (PSX + PSW - 20 - pct.get_width(), sy - 34))
                        pygame.draw.rect(self.screen, (50, 50, 70),
                                         (SLX, sy - 4, SLW, 8), border_radius=4)
                        fw = int(vol * SLW)
                        if fw > 0:
                            pygame.draw.rect(self.screen, CYAN, (SLX, sy - 4, fw, 8), border_radius=4)
                        kx = int(SLX + vol * SLW)
                        pygame.draw.circle(self.screen, WHITE, (kx, sy), 12)
                        pygame.draw.circle(self.screen, CYAN,  (kx, sy), 10)
                        pygame.draw.circle(self.screen, WHITE, (kx, sy), 10, 2)
                        for t in (0.0, 0.25, 0.5, 0.75, 1.0):
                            tx2 = int(SLX + t * SLW)
                            pygame.draw.line(self.screen, (80, 80, 100),
                                             (tx2, sy + 14), (tx2, sy + 20), 1)

                    _ps("Music Volume",  MUSIC.volume,  SLY)
                    pygame.draw.line(self.screen, (45, 45, 65),
                                     (PSX + 20, SLY + 28), (PSX + PSW - 20, SLY + 28), 1)
                    _ps("Sound Effects", SOUNDS.volume, SLY2)
                    pygame.draw.line(self.screen, (45, 45, 65),
                                     (PSX + 20, SLY2 + 28), (PSX + PSW - 20, SLY2 + 28), 1)

                    # Quality toggle
                    qlbl = self.fonts["med"].render("Quality", True, WHITE)
                    self.screen.blit(qlbl, (PSX + 20, PQY + 8))
                    pbw2 = (PSW - 60) // 2
                    for qi, (qlabel, qval) in enumerate([("Low", "low"), ("High", "high")]):
                        qx   = PSX + PSW // 2 - pbw2 + qi * (pbw2 + 10)
                        aq   = GAME_SETTINGS.quality == qval
                        qcol = (100, 220, 100) if qval == "high" else (220, 160, 60)
                        qbg  = lerp_color(PANEL, qcol, 0.3 if aq else 0.05)
                        pygame.draw.rect(self.screen, qbg,
                                         (qx, PQY, pbw2, 36), border_radius=8)
                        pygame.draw.rect(self.screen, qcol if aq else GRAY,
                                         (qx, PQY, pbw2, 36), 2 if aq else 1, border_radius=8)
                        qt = self.fonts["med"].render(qlabel, True, qcol if aq else GRAY)
                        self.screen.blit(qt, (qx + pbw2 // 2 - qt.get_width() // 2,
                                              PQY + 18 - qt.get_height() // 2))

                    # Player health bar toggle
                    pygame.draw.line(self.screen, (45, 45, 65),
                                     (PSX + 20, PHY - 10), (PSX + PSW - 20, PHY - 10), 1)
                    hb_col = (80, 220, 140)
                    hb_on  = GAME_SETTINGS.player_health_bar
                    hb_lbl = self.fonts["med"].render("Player Health Bar", True, WHITE)
                    self.screen.blit(hb_lbl, (PSX + 20, PHY + 8))
                    pill_x = PSX + PSW - 80; pill_y = PHY + 4
                    pill_w = 56; pill_h = 28
                    pill_bg = lerp_color(PANEL, hb_col, 0.35 if hb_on else 0.05)
                    pygame.draw.rect(self.screen, pill_bg,
                                     (pill_x, pill_y, pill_w, pill_h), border_radius=14)
                    pygame.draw.rect(self.screen, hb_col if hb_on else GRAY,
                                     (pill_x, pill_y, pill_w, pill_h), 2, border_radius=14)
                    knob_x = pill_x + pill_w - 16 if hb_on else pill_x + 12
                    pygame.draw.circle(self.screen, hb_col if hb_on else GRAY,
                                       (knob_x, pill_y + pill_h // 2), 10)
                    on_off = self.fonts["tiny"].render("ON" if hb_on else "OFF", True, hb_col if hb_on else GRAY)
                    self.screen.blit(on_off, (pill_x + pill_w // 2 - on_off.get_width() // 2,
                                              pill_y + pill_h // 2 - on_off.get_height() // 2))

                    # Fullscreen toggle
                    pygame.draw.line(self.screen, (45, 45, 65),
                                     (PSX + 20, PFY - 10), (PSX + PSW - 20, PFY - 10), 1)
                    fs_col_p = (200, 160, 255)
                    fs_on_p  = GAME_SETTINGS.fullscreen
                    fs_lbl_p = self.fonts["med"].render("Fullscreen", True, WHITE)
                    self.screen.blit(fs_lbl_p, (PSX + 20, PFY + 8))
                    fp_x = PSX + PSW - 80; fp_y = PFY + 4
                    fp_bg = lerp_color(PANEL, fs_col_p, 0.35 if fs_on_p else 0.05)
                    pygame.draw.rect(self.screen, fp_bg,
                                     (fp_x, fp_y, pill_w, pill_h), border_radius=14)
                    pygame.draw.rect(self.screen, fs_col_p if fs_on_p else GRAY,
                                     (fp_x, fp_y, pill_w, pill_h), 2, border_radius=14)
                    fk_x = fp_x + pill_w - 16 if fs_on_p else fp_x + 12
                    pygame.draw.circle(self.screen, fs_col_p if fs_on_p else GRAY,
                                       (fk_x, fp_y + pill_h // 2), 10)
                    fs_oo_p = self.fonts["tiny"].render("ON" if fs_on_p else "OFF", True,
                                                        fs_col_p if fs_on_p else GRAY)
                    self.screen.blit(fs_oo_p, (fp_x + pill_w // 2 - fs_oo_p.get_width() // 2,
                                               fp_y + pill_h // 2 - fs_oo_p.get_height() // 2))

                    close_h = self.fonts["tiny"].render(
                        "Click outside or press P / ESC to close", True, GRAY)
                    self.screen.blit(close_h,
                                     (PSX + PSW // 2 - close_h.get_width() // 2,
                                      PSY + PSH - 22))

                # Password prompt
                if self.pause_dev_prompt:
                    PPW, PPH = 340, 160
                    PPX = SW // 2 - PPW // 2; PPY = SH // 2 - PPH // 2
                    pygame.draw.rect(self.screen, PANEL,           (PPX, PPY, PPW, PPH), border_radius=14)
                    pygame.draw.rect(self.screen, (255, 160, 40),  (PPX, PPY, PPW, PPH), 2, border_radius=14)
                    pt = self.fonts["large"].render("Dev Tools", True, (255, 160, 40))
                    self.screen.blit(pt, (PPX + PPW // 2 - pt.get_width() // 2, PPY + 14))
                    ps = self.fonts["small"].render("Enter password:", True, GRAY)
                    self.screen.blit(ps, (PPX + PPW // 2 - ps.get_width() // 2, PPY + 52))
                    # Password input box (mask input as dots)
                    pw_display = "●" * len(self.pause_dev_input)
                    bx2 = PPX + 30; by2 = PPY + 76
                    pygame.draw.rect(self.screen, (35, 35, 52), (bx2, by2, PPW - 60, 38), border_radius=8)
                    pygame.draw.rect(self.screen, (255, 160, 40), (bx2, by2, PPW - 60, 38), 1, border_radius=8)
                    pw_surf = self.fonts["large"].render(pw_display, True, WHITE)
                    self.screen.blit(pw_surf, (bx2 + 12, by2 + 8))
                    hint_p = self.fonts["tiny"].render("ENTER to confirm  |  ESC to cancel", True, GRAY)
                    self.screen.blit(hint_p, (PPX + PPW // 2 - hint_p.get_width() // 2, PPY + PPH - 24))

                if self.pause_dev:
                    DPX = SW // 2 - 170; DPY = SH // 2 - 110
                    DPW = 340;           DPH = 424
                    DEV_COL = (255, 160, 40)
                    ROW_H   = 56

                    pygame.draw.rect(self.screen, PANEL,   (DPX, DPY, DPW, DPH), border_radius=14)
                    pygame.draw.rect(self.screen, DEV_COL, (DPX, DPY, DPW, DPH), 2, border_radius=14)
                    dtitle = self.fonts["large"].render("Dev Tools", True, DEV_COL)
                    self.screen.blit(dtitle, (DPX + DPW // 2 - dtitle.get_width() // 2, DPY + 14))
                    pygame.draw.line(self.screen, (70, 55, 30), (DPX + 16, DPY + 50), (DPX + DPW - 16, DPY + 50), 1)

                    if self.wave_active and self.boss_wave:
                        wave_hint = f"Boss wave {self.wave}"
                    elif self.wave_active and self.elite_wave:
                        wave_hint = f"CORRUPTION Wave {self.wave}  —  {sum(1 for e in self.enemies if e.alive)} alive"
                    elif self.wave_active:
                        wave_hint = f"Wave {self.wave}  —  {sum(1 for e in self.enemies if e.alive)} alive"
                    else:
                        wave_hint = f"Wave {self.wave} — break"
                    wh = self.fonts["tiny"].render(wave_hint, True, GRAY)
                    self.screen.blit(wh, (DPX + DPW // 2 - wh.get_width() // 2, DPY + 53))

                    skip_col  = (255, 100, 100)
                    boss_col  = (255, 120, 200)
                    perk_col  = (100, 220, 140)
                    ach_col2  = (180, 120, 255)
                    next_wave = self.wave + 1 if not self.wave_active else self.wave
                    boss_label = "▸ Skip to Boss ◂" if self.dev_boss_expand else "▸ Skip to Boss Wave"
                    perk_label = "◂ Give Perk ▸"    if self.dev_perk_expand else "▸ Give Perk"
                    ach_label  = "Achievements ◂"   if self.dev_ach_expand  else "▸ Grant Achievement"
                    for row, (label, val_str, col) in enumerate([
                        ("+100 Gold",   f"Current: {self.player.gold}g",     YELLOW),
                        ("+1 Level",    f"Current: Lvl {self.player.level}", CYAN),
                        ("Skip Wave",   f">> Wave {next_wave}",              skip_col),
                        (boss_label,    "→ right panel",                     boss_col),
                        (perk_label,    "← left panel",                      perk_col),
                        (ach_label,     "→ right panel",                     ach_col2),
                    ]):
                        btn = pygame.Rect(DPX + 20, DPY + 70 + row * ROW_H, DPW - 40, 44)
                        pygame.draw.rect(self.screen, lerp_color(PANEL, col, 0.22), btn, border_radius=8)
                        pygame.draw.rect(self.screen, col, btn, 1, border_radius=8)
                        bl = self.fonts["med"].render(label, True, col)
                        self.screen.blit(bl, (btn.x + 14, btn.centery - bl.get_height() // 2))
                        vl = self.fonts["small"].render(val_str, True, GRAY)
                        self.screen.blit(vl, (btn.right - vl.get_width() - 14,
                                              btn.centery - vl.get_height() // 2))

                    # ── Boss flyout — RIGHT ──────────────────────────────────
                    if self.dev_boss_expand:
                        FLY_W = 220; FLY_X = DPX + DPW + 8; FLY_Y = DPY
                        FLY_H = 20 + len(BOSS_TYPES) * 34 + 8
                        pygame.draw.rect(self.screen, PANEL,    (FLY_X, FLY_Y, FLY_W, FLY_H), border_radius=10)
                        pygame.draw.rect(self.screen, boss_col, (FLY_X, FLY_Y, FLY_W, FLY_H), 1, border_radius=10)
                        fh = self.fonts["tiny"].render("Select Boss", True, boss_col)
                        self.screen.blit(fh, (FLY_X + FLY_W // 2 - fh.get_width() // 2, FLY_Y + 4))
                        boss_colors = [(220,60,60),(200,80,255),(80,180,80),(255,220,60),(80,80,220)]
                        for bi, bt in enumerate(BOSS_TYPES):
                            bcol = boss_colors[bi % len(boss_colors)]
                            bb   = pygame.Rect(FLY_X + 8, FLY_Y + 20 + bi * 34, FLY_W - 16, 28)
                            pygame.draw.rect(self.screen, lerp_color(PANEL, bcol, 0.18), bb, border_radius=6)
                            pygame.draw.rect(self.screen, bcol, bb, 1, border_radius=6)
                            bn = self.fonts["small"].render(bt["name"], True, bcol)
                            self.screen.blit(bn, (bb.x + 8, bb.centery - bn.get_height() // 2))

                    # ── Perk flyout — LEFT ───────────────────────────────────
                    if self.dev_perk_expand:
                        PKW = 220; PKX = DPX - PKW - 8; PKY = DPY
                        PKH = 20 + len(ALL_PERKS) * 34 + 8
                        pygame.draw.rect(self.screen, PANEL,    (PKX, PKY, PKW, PKH), border_radius=10)
                        pygame.draw.rect(self.screen, perk_col, (PKX, PKY, PKW, PKH), 1, border_radius=10)
                        ph = self.fonts["tiny"].render("Give Perk (stackable)", True, perk_col)
                        self.screen.blit(ph, (PKX + PKW // 2 - ph.get_width() // 2, PKY + 4))
                        for pi, pd in enumerate(ALL_PERKS):
                            pcol = pd["color"]
                            pb   = pygame.Rect(PKX + 8, PKY + 20 + pi * 34, PKW - 16, 28)
                            pygame.draw.rect(self.screen, lerp_color(PANEL, pcol, 0.18), pb, border_radius=6)
                            pygame.draw.rect(self.screen, pcol, pb, 1, border_radius=6)
                            pn = self.fonts["small"].render(pd["label"], True, pcol)
                            self.screen.blit(pn, (pb.x + 8, pb.centery - pn.get_height() // 2))
                            cur_val = self.player.perks.get(pd["key"], 0)
                            if cur_val > 0:
                                sv = self.fonts["tiny"].render(f"×{round(cur_val/pd['bonus'])}", True, GRAY)
                                self.screen.blit(sv, (pb.right - sv.get_width() - 8,
                                                       pb.centery - sv.get_height() // 2))

                    # ── Achievement flyout — RIGHT (scrollable) ──────────────
                    if self.dev_ach_expand:
                        ACH_W = 340; ACH_X = DPX + DPW + 8; ACH_Y = DPY
                        ACH_VISIBLE_H = 440; ACH_ROW = 32
                        pygame.draw.rect(self.screen, PANEL,    (ACH_X, ACH_Y, ACH_W, ACH_VISIBLE_H), border_radius=10)
                        pygame.draw.rect(self.screen, ach_col2, (ACH_X, ACH_Y, ACH_W, ACH_VISIBLE_H), 1, border_radius=10)
                        ah = self.fonts["tiny"].render("Click to grant achievement", True, ach_col2)
                        self.screen.blit(ah, (ACH_X + ACH_W // 2 - ah.get_width() // 2, ACH_Y + 6))
                        # Clamp scroll
                        max_ach_scroll = max(0, len(ACHIEVEMENTS) * ACH_ROW - (ACH_VISIBLE_H - 24))
                        self.dev_ach_scroll = max(0, min(self.dev_ach_scroll, max_ach_scroll))
                        # Clipping surface
                        ach_clip = pygame.Surface((ACH_W, ACH_VISIBLE_H - 24), pygame.SRCALPHA)
                        ach_clip.fill((0, 0, 0, 0))
                        for ai, ach in enumerate(ACHIEVEMENTS):
                            ry  = ai * ACH_ROW - self.dev_ach_scroll
                            if ry + ACH_ROW < 0 or ry > ACH_VISIBLE_H - 24:
                                continue
                            done = ach["id"] in PROFILE.unlocked
                            ac   = (80, 200, 80) if done else ach_col2
                            ab   = pygame.Rect(6, ry, ACH_W - 12, ACH_ROW - 4)
                            pygame.draw.rect(ach_clip, lerp_color(PANEL, ac, 0.3 if done else 0.12), ab, border_radius=5)
                            pygame.draw.rect(ach_clip, ac, ab, 1, border_radius=5)
                            an = self.fonts["tiny"].render(ach["name"], True, WHITE if done else (180, 180, 200))
                            ach_clip.blit(an, (ab.x + 6, ab.centery - an.get_height() // 2))
                            if done:
                                pygame.draw.line(ach_clip, (80,200,80), (ab.right-22, ry+ACH_ROW//2-4),
                                                 (ab.right-16, ry+ACH_ROW//2+2), 2)
                                pygame.draw.line(ach_clip, (80,200,80), (ab.right-16, ry+ACH_ROW//2+2),
                                                 (ab.right-8,  ry+ACH_ROW//2-6), 2)
                        self.screen.blit(ach_clip, (ACH_X, ACH_Y + 24))

                    close_d = self.fonts["tiny"].render(
                        "Click outside or press P / ESC to close", True, GRAY)
                    self.screen.blit(close_d, (DPX + DPW // 2 - close_d.get_width() // 2, DPY + DPH - 22))


            # ── Corruption wave screen flash ───────────────────────────────────
            if self.corruption_flash_timer > 0:
                self.corruption_flash_timer -= 1
                t = self.corruption_flash_timer / 90.0   # 1.0 → 0.0
                # Pulsing: peaks at t=0.85 (fast rise) then fades to 0
                pulse = math.sin(t * math.pi)
                alpha = max(0, min(180, int(180 * pulse)))
                if alpha > 0:
                    cf = pygame.Surface((SW, SH), pygame.SRCALPHA)
                    cf.fill((120, 0, 180, alpha))
                    self.screen.blit(cf, (0, 0))
                # Banner — fades in then holds then fades
                if t > 0.15:
                    banner_alpha = max(0, min(255, int(255 * min(1.0, (t - 0.15) * 3))))
                    COR_COL = (220, 80, 255)
                    bw2 = 560; bh2 = 70
                    bx2 = SW // 2 - bw2 // 2; by2 = SH // 2 - bh2 // 2 - 40
                    bs = pygame.Surface((bw2, bh2), pygame.SRCALPHA)
                    bs.fill((30, 0, 50, min(220, banner_alpha)))
                    pygame.draw.rect(bs, (*COR_COL, banner_alpha), (0, 0, bw2, bh2), 3, border_radius=10)
                    self.screen.blit(bs, (bx2, by2))
                    ctitle = self.fonts["huge"].render("CORRUPTION WAVE", True, COR_COL)
                    ctitle.set_alpha(banner_alpha)
                    self.screen.blit(ctitle, (SW // 2 - ctitle.get_width() // 2, by2 + 14))

            self.draw_achievement_toasts()
            _scaled_flip(self.screen)

def apply_display_mode(current_window):
    """
    Create (or recreate) the pygame display window according to GAME_SETTINGS.
    Returns the new window Surface.  The game always renders to the fixed
    SW×SH (1280×720) virtual canvas.  pygame.SCALED handles physical scaling
    and remaps mouse coordinates automatically.
    """
    if GAME_SETTINGS.fullscreen:
        flags = pygame.FULLSCREEN | pygame.SCALED
    else:
        flags = pygame.SCALED
    try:
        win = pygame.display.set_mode((SW, SH), flags)
    except pygame.error:
        # Toggling fullscreen can fail on Windows — reinit display and retry
        pygame.display.quit()
        pygame.display.init()
        try:
            win = pygame.display.set_mode((SW, SH), flags)
        except pygame.error:
            # Final fallback: safe windowed mode
            GAME_SETTINGS.fullscreen = False
            GAME_SETTINGS.save()
            win = pygame.display.set_mode((SW, SH), pygame.SCALED)
    return win


if __name__ == "__main__":
    _window = apply_display_mode(None)
    pygame.display.set_caption("Dungeon Crawler 45.0b10")

    # Window icon
    _icon_path = asset("icon.png")
    if os.path.isfile(_icon_path):
        try:
            pygame.display.set_icon(pygame.image.load(_icon_path))
        except pygame.error as e:
            print(f"[Icon] Could not load icon.png: {e}")

    _clock  = pygame.time.Clock()
    _fonts  = {
        "large": _make_font(28, bold=True),
        "med":   _make_font(20, bold=True),
        "small": _make_font(15),
        "tiny":  _make_font(13),
        "huge":  _make_font(48, bold=True),
    }

    # Show profile creation screen on first launch (no profile yet)
    if not PROFILE.exists():
        _screen = pygame.display.get_surface()
        profile_creation_screen(_screen, _clock, _fonts)

    while True:
        _screen = pygame.display.get_surface()
        chosen_name, chosen_cp, chosen_slot, chosen_hardcore = username_screen(_screen, _clock, _fonts)

        _window = apply_display_mode(_window)
        _screen = pygame.display.get_surface()

        result = Game(username=chosen_name, render_surf=_screen,
                      window=_window, apply_display_fn=apply_display_mode,
                      checkpoint=chosen_cp, save_slot=chosen_slot,
                      hardcore=chosen_hardcore).run()
        _window = apply_display_mode(_window)
