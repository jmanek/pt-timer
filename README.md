# pt-timer

A terminal-based interval timer with GO/REST sets.  Used to track my physical therapy isometrics.

## Installation

```bash
chmod +x timer.py
ln -sf $(pwd)/timer.py ~/.local/bin/pt-timer
```

## Usage

```bash
pt-timer                    # basic mode
pt-timer <first> <second>   # interval mode
pt-timer -h                 # full help
```

### Basic mode
Tracks elapsed time only. GO label always visible. Press space to save a set and start a REST period.

### Interval mode
```bash
pt-timer 5 2
```
Tracks two nested intervals within each GO set:
- `first` — seconds for the GO counter (c1) before switching to STOP (c2)
- `second` — seconds for the STOP counter (c2) before cycling back to GO (c1)

Reps are counted each time c1 completes a full cycle.

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `--countdown`, `-c` | Countdown seconds before each GO set | `3` |

## Controls

| Key | Action |
|-----|--------|
| `space` | Save current set, switch to REST (or back to GO) |
| `r` | Reset everything |
| `Ctrl+C` | Quit |

## How it works

Each session alternates between GO and REST sets:

1. **GO set** — timer runs, c1/c2 intervals cycle, reps are tracked
2. **REST set** — simple elapsed time, no interval tracking
3. A countdown plays before each GO set begins

Saved sets are displayed above the active timer. REST sets are not counted in the set number.
