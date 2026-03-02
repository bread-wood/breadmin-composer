# Spec: Minesweeper Terminal Game

## Overview

A terminal-based implementation of the classic Minesweeper game that runs entirely in the
command line. Players reveal cells on a grid, flag suspected mines, and win by uncovering
all safe cells without triggering a mine. Board dimensions and mine count are configurable
at launch, making it suitable as a pipeline validation target that exercises multiple
parallel implementation modules.

---

## Success Criteria

- [ ] Running `minesweeper` (or `python -m minesweeper`) starts an interactive game session
  in the terminal and exits 0 on clean quit
- [ ] The board renders with correct dimensions: a 9x9 board displays 9 rows and 9 columns
  of cells; a 16x16 board displays 16 rows and 16 columns
- [ ] Mine count is configurable; `--mines 10` places exactly 10 mines on the board
- [ ] Board width, height, and mine count are configurable via CLI flags
  (e.g., `--width 9 --height 9 --mines 10`)
- [ ] Cells have three visible states: unrevealed, revealed (showing adjacent mine count or
  blank if 0), and flagged
- [ ] Revealing a cell with 0 adjacent mines automatically reveals all connected cells
  with 0 adjacent mines (flood fill)
- [ ] Revealing a mine ends the game immediately, displays the full board, and prints
  "Game over" to stdout
- [ ] Revealing the last non-mine cell wins the game, displays the full board, and prints
  "You win" to stdout
- [ ] The first reveal never hits a mine (mine placement deferred until after first move)
- [ ] Flagging and unflagging a cell is supported without revealing it
- [ ] The player can navigate the board (move cursor or specify coordinates) and perform
  reveal and flag actions using keyboard input

---

## Scope

**Included:**

- Single-player terminal game loop with interactive keyboard input
- Configurable board: width, height, mine count (specified as CLI flags)
- Board rendering to the terminal: cell states (unrevealed, revealed with digit, flagged,
  mine on loss)
- Standard Minesweeper rules: flood-fill reveal on zero-neighbor cells, first-move safety
  guarantee
- Win detection: all non-mine cells revealed
- Loss detection: mine cell revealed
- Mine counter (total mines minus flagged cells) displayed during play
- Move counter or elapsed time displayed during play (at least one)
- Clean exit on win or loss (returns to normal terminal state)

---

## Constraints

- Must run on macOS and Linux without installation beyond Python standard library plus at
  most one third-party terminal library (e.g., `curses` is standard; `blessed` or `rich`
  are acceptable if needed)
- No external game framework — no pygame, no Tkinter
- Board state must be fully testable in isolation without a live terminal (i.e., game logic
  must not depend directly on terminal I/O)
- Minimum supported board: 2x2 with 1 mine; maximum reasonable board: 30x30 with up to
  (width * height - 1) mines
- Invalid configuration (mines >= cells, zero dimensions) must print an error to stderr
  and exit non-zero without starting the game

---

## Key Unknowns

1. Which terminal rendering approach should be used — Python `curses` (standard library,
   lower-level) vs. a third-party library such as `blessed` or `rich` — and does the
   chosen approach support the required cell navigation and keyboard input on both macOS
   and Linux without significant platform-specific workarounds? The answer determines the
   renderer module's dependency and API surface.

2. What is the right data structure for board state (e.g., a 2D list of cell objects vs.
   parallel flat arrays vs. a dict keyed by coordinate) given the need for both
   flood-fill traversal and O(1) cell lookup by coordinate? The answer determines the
   board module's core type and the interface between the board, game logic, and renderer
   modules.

---

## Non-Goals

- Graphical user interface (GUI) of any kind — no pygame, no Tkinter, no web browser
- Network play or multiplayer
- Leaderboards, persistent high scores, or score tracking across sessions
- Save and load game state
- Replay or undo functionality
- Custom themes or color schemes beyond basic terminal colors
- Automated solver or hint system
- Mobile or non-terminal platforms
