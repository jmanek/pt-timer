#!/usr/bin/env python3
import re
import time
import argparse
import sys
import termios
import tty
import select
import threading

# ANSI helpers
RESET   = "\033[0m"
DIM     = "\033[2m"
BOLD    = "\033[1m"
GREEN   = "\033[32m"
RED     = "\033[31m"
YELLOW  = "\033[33m"
MAGENTA = "\033[35m"
WHITE   = "\033[37m"
CYAN    = "\033[36m"

BOX_W = 38  # inner visible width of the timer box

ANSI_RE = re.compile(r'\033\[[^m]*m')


def visible_len(s: str) -> int:
    return len(ANSI_RE.sub('', s))


def box(content: str) -> list[str]:
    pad = max(0, BOX_W - visible_len(content))
    return [
        f" {WHITE}┌{'─' * (BOX_W + 2)}┐{RESET}",
        f" {WHITE}│{RESET} {content}{' ' * pad} {WHITE}│{RESET}",
        f" {WHITE}└{'─' * (BOX_W + 2)}┘{RESET}",
    ]


def fmt_go_timer(c1, c2, total_s, ms, c1_count, active):
    c1_color  = f"{BOLD}{GREEN}" if active == "c1" else DIM
    c2_color  = f"{BOLD}{RED}"   if active == "c2" else DIM
    time_str  = f"{YELLOW}{total_s}.{ms:03d}{RESET}"
    c1_str    = f"{c1_color}{c1}.{ms:03d}{RESET}" if active == "c1" else f"{c1_color}{c1}{RESET}"
    c2_str    = f"{c2_color}{c2}.{ms:03d}{RESET}" if active == "c2" else f"{c2_color}{c2}{RESET}"
    count_str = f"{MAGENTA}{c1_count}{RESET}"
    state_str = f"{BOLD}{GREEN}GO{RESET}" if active == "c1" else f"{BOLD}{RED}STOP{RESET}"
    return f"{c1_str}, {c2_str}, {time_str}  {DIM}reps: {RESET}{count_str}  {state_str}"


def fmt_basic_timer(total_s, ms):
    time_str = f"{YELLOW}{total_s}.{ms:03d}{RESET}"
    return f"{time_str}  {BOLD}{GREEN}GO{RESET}"


def fmt_rest_timer(total_s, ms):
    time_str = f"{YELLOW}{total_s}.{ms:03d}{RESET}"
    return f"{time_str}  {BOLD}{RED}REST{RESET}"


def fmt_paused_timer(total_s, ms):
    time_str = f"{YELLOW}{total_s}.{ms:03d}{RESET}"
    return f"{time_str}  {BOLD}{CYAN}PAUSED{RESET}"


def fmt_lap(lap, set_num, basic):
    if lap[0] == "go":
        _, lc1, lc2, lt, lms, lcount = lap
        reps = "" if basic else f"  reps: {lcount}"
        return f" {DIM}Set {set_num}:  {lt}.{lms:03d}{reps}{RESET}"
    else:
        _, lt, lms = lap
        return f" {DIM}Rest:  {lt}.{lms:03d}{RESET}"


# ---------------------------------------------------------------------------
# Voice listener — returns a thread function, or None if unavailable
# ---------------------------------------------------------------------------
VOICE_KEYWORDS = {
    # save current set → same as space
    "set":      "space",
    "rest":     "space",
    "done":     "space",
    "next":     "space",
    "stop":     "space",
    # end rest, start new set → same as space (works in both phases)
    "start":    "space",
    "go":       "space",
    "resume":   "pause",
    "continue": "pause",
    # toggle pause
    "pause":   "pause",
    "freeze":  "pause",
    # reset
    "reset":   "reset",
    "restart": "reset",
}


def make_voice_thread(space_pressed, pause_event, reset_pressed, stop_event):
    try:
        import vosk
        import pyaudio
        import json
    except ImportError:
        print("  [voice] vosk not installed — run: pip install vosk pyaudio")
        return None

    vosk.SetLogLevel(-1)

    try:
        model = vosk.Model(lang="en-us")
    except Exception as e:
        print(f"  [voice] Failed to load Vosk model: {e}")
        return None

    grammar = json.dumps(list(VOICE_KEYWORDS.keys()))

    def listen():
        try:
            pa = pyaudio.PyAudio()
            stream = pa.open(format=pyaudio.paInt16, channels=1, rate=16000,
                             input=True, frames_per_buffer=4096)
        except Exception as e:
            print(f"  [voice] Microphone unavailable: {e}")
            return

        import json as _json
        rec = vosk.KaldiRecognizer(model, 16000, grammar)

        try:
            while not stop_event.is_set():
                data = stream.read(4096, exception_on_overflow=False)
                if rec.AcceptWaveform(data):
                    text = _json.loads(rec.Result()).get("text", "")
                    words = set(text.lower().split())
                    for kw, action in VOICE_KEYWORDS.items():
                        if kw in words:
                            if action == "space":
                                space_pressed.set()
                            elif action == "pause":
                                pause_event.set()
                            elif action == "reset":
                                reset_pressed.set()
                            break
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()

    return listen


# ---------------------------------------------------------------------------
# Main timer
# ---------------------------------------------------------------------------
def run_timer(first_interval, second_interval, countdown_secs: int = 3,
              voice: bool = False) -> None:
    basic = first_interval is None
    laps = []
    phase = "go"   # "go" or "rest"
    c1, c2, total = 0, 0, 0
    ms = 0
    active = "c1"
    c1_count = 0
    prev_line_count = 0
    countdown_val = None

    # pause state
    paused = False
    paused_at = 0.0
    total_pause_duration = 0.0

    space_pressed = threading.Event()
    reset_pressed = threading.Event()
    pause_event   = threading.Event()
    stop_event    = threading.Event()

    def read_keys():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while not stop_event.is_set():
                r, _, _ = select.select([sys.stdin], [], [], 0.05)
                if r:
                    ch = sys.stdin.read(1)
                    if ch == ' ':
                        space_pressed.set()
                    elif ch == 'r':
                        reset_pressed.set()
                    elif ch == 'p':
                        pause_event.set()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    key_thread = threading.Thread(target=read_keys, daemon=True)
    key_thread.start()

    if voice:
        voice_fn = make_voice_thread(space_pressed, pause_event, reset_pressed, stop_event)
        if voice_fn:
            voice_thread = threading.Thread(target=voice_fn, daemon=True)
            voice_thread.start()

    sys.stdout.write("\033[?25l")  # hide cursor

    def draw():
        nonlocal prev_line_count
        lines = []
        set_num = 0
        for lap in laps:
            if lap[0] == "go":
                set_num += 1
            lines.append(fmt_lap(lap, set_num, basic))
        if countdown_val is not None:
            lines += box(f"{BOLD}{YELLOW}Starting in {countdown_val}...{RESET}")
        elif paused:
            lines += box(fmt_paused_timer(total, ms))
        elif phase == "go":
            if basic:
                lines += box(fmt_basic_timer(total, ms))
            else:
                lines += box(fmt_go_timer(c1, c2, total, ms, c1_count, active))
        else:
            lines += box(fmt_rest_timer(total, ms))

        if prev_line_count > 0:
            sys.stdout.write(f"\033[{prev_line_count}A")
        for line in lines:
            sys.stdout.write(f"\r\033[2K{line}\n")
        sys.stdout.flush()
        prev_line_count = len(lines)

    def do_reset():
        nonlocal c1, c2, total, ms, active, c1_count, phase, prev_line_count
        nonlocal paused, paused_at, total_pause_duration
        if prev_line_count > 0:
            sys.stdout.write(f"\033[{prev_line_count}A\033[J")
        laps.clear()
        c1, c2, total, ms = 0, 0, 0, 0
        active = "c1"
        c1_count = 0
        phase = "go"
        prev_line_count = 0
        paused = False
        paused_at = 0.0
        total_pause_duration = 0.0

    def do_countdown() -> bool:
        """Countdown before GO phase. Returns False if reset triggered."""
        nonlocal countdown_val
        for n in range(countdown_secs, 0, -1):
            countdown_val = n
            t_end = time.monotonic() + 1.0
            while time.monotonic() < t_end:
                time.sleep(0.033)
                draw()
                if reset_pressed.is_set():
                    countdown_val = None
                    return False
        countdown_val = None
        space_pressed.clear()
        return True

    draw()
    if not do_countdown():
        pass

    start = time.monotonic()
    last_second = 0

    try:
        while True:
            time.sleep(0.033)

            # --- reset ---
            if reset_pressed.is_set():
                reset_pressed.clear()
                space_pressed.clear()
                pause_event.clear()
                do_reset()
                draw()
                if not do_countdown():
                    continue
                start = time.monotonic()
                last_second = 0
                continue

            # --- pause toggle ---
            if pause_event.is_set():
                pause_event.clear()
                if not paused:
                    paused = True
                    paused_at = time.monotonic()
                else:
                    total_pause_duration += time.monotonic() - paused_at
                    paused = False
                draw()
                continue

            if paused:
                draw()
                continue

            elapsed = time.monotonic() - start - total_pause_duration
            current_second = int(elapsed)
            ms = int((elapsed % 1) * 1000)

            if phase == "go":
                if basic:
                    total = current_second
                else:
                    while last_second < current_second:
                        last_second += 1
                        total = last_second
                        if active == "c1":
                            c1 += 1
                            if c1 >= first_interval:
                                c1 = 0
                                c2 = 0
                                active = "c2"
                                c1_count += 1
                        else:
                            c2 += 1
                            if c2 >= second_interval:
                                c2 = 0
                                c1 = 0
                                active = "c1"
            else:
                total = current_second

            if space_pressed.is_set():
                space_pressed.clear()
                if phase == "go":
                    laps.append(("go", c1, c2, total, ms, c1_count))
                    c1, c2, total, ms = 0, 0, 0, 0
                    active = "c1"
                    c1_count = 0
                    phase = "rest"
                    total_pause_duration = 0.0
                    draw()
                    # no countdown for rest — starts immediately
                else:
                    laps.append(("rest", total, ms))
                    c1, c2, total, ms = 0, 0, 0, 0
                    active = "c1"
                    c1_count = 0
                    phase = "go"
                    total_pause_duration = 0.0
                    draw()
                    if not do_countdown():
                        continue
                start = time.monotonic()
                last_second = 0
                continue

            draw()

    except KeyboardInterrupt:
        stop_event.set()
    finally:
        sys.stdout.write("\033[?25h\n")
        sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pt-timer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="A terminal-based interval timer with GO/REST sets.",
        epilog="""
MODES
  Basic mode       pt-timer
    Tracks elapsed time only. GO label always visible.
    Use spacebar (or voice) to save a set and start a REST period.

  Interval mode    pt-timer <first> <second>
    Tracks two nested intervals within each GO set.
    first  — seconds for the GO counter (c1) before switching to STOP (c2)
    second — seconds for the STOP counter (c2) before cycling back to GO (c1)
    Reps are counted each time c1 completes a full cycle.

HOW THE TIMER WORKS
  Each session alternates between GO and REST sets:
    GO set   — timer runs with full display, ends when you press space (or say "set"/"rest"/"done")
    REST set — simple elapsed time, ends when you press space (or say "start"/"go"/"resume")
               then a countdown before the next GO set begins

  In interval mode the GO set display shows:
    c1.ms, c2.ms, total.ms   reps: N   GO/STOP
    c1 counts up during GO phase, c2 counts up during STOP phase,
    then they repeat. Reps increments each time c1 completes.

CONTROLS
  space        save current set, switch to REST (or back to GO with countdown)
  p            pause / resume timer
  r            reset everything and restart from the beginning
  Ctrl+C       quit

VOICE COMMANDS  (on by default, disable with --no-voice)
  "set" / "rest" / "done" / "next" / "stop"     save set (same as space)
  "start" / "go"                                 end rest / start set (same as space)
  "resume" / "continue"                          unpause
  "pause" / "freeze"                    pause or resume
  "reset" / "restart"                   reset everything
"""
    )
    parser.add_argument("first", type=int, nargs="?", default=None,
                        help="Seconds for the GO (c1) interval")
    parser.add_argument("second", type=int, nargs="?", default=None,
                        help="Seconds for the STOP (c2) interval")
    parser.add_argument("--countdown", "-c", type=int, default=3, metavar="N",
                        help="Countdown seconds before each GO set (default: 3)")
    parser.add_argument("--no-voice", action="store_true",
                        help="Disable voice command recognition")
    args = parser.parse_args()

    if args.first is not None:
        print(f"first={args.first}  second={args.second}  "
              f"(space=set, p=pause, r=reset, Ctrl+C to stop)\n")
    else:
        print("basic mode  (space=set, p=pause, r=reset, Ctrl+C to stop)\n")
    run_timer(args.first, args.second, args.countdown, voice=not args.no_voice)


if __name__ == "__main__":
    main()
