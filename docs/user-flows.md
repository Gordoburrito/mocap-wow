# User flows: every screen, how you get in, how you get out

The Joy-Cons are strapped to PVC weapons, so there are no controller buttons. Every input is a
body pose, a body move (webcam), or a weapon motion (Joy-Con). The app is a window, a ⚔ item in
the menu bar, and an optional HUD panel over the game.

**Rule: nothing traps you.** Every pop-up has a button out *and* Escape; every page is in the
sidebar; the HUD page has "← Back to the app". `lab/ui_flows_check.py` walks all of this in a
real browser with real mouse and key presses and checks the app agrees (see the end).

## The map

```mermaid
flowchart LR
    subgraph Mac["Your Mac"]
        Launch(["Open Mocap<br/>Applications · Spotlight"]) --> Window
        Menu["⚔ menu bar"] -->|Show Mocap| Window
        Menu -->|HUD over the game| Overlay["HUD panel over WoW"]
        Overlay -->|✕, or ⚔ → HUD over the game| Menu
        Window -->|close window: keeps running| Menu
        Menu -->|Quit, ⌘Q| Quit(["Quit: keys released, profile saved"])
        Menu -->|Open the demo| Demo["Demo window<br/>sample data, simulated hardware"]
    end
    subgraph Window["App window: sidebar on every page"]
        Library <--> Engine
        Library <--> Metrics
        Library <--> HUD["Live HUD"]
        Library <-->|tile · breadcrumb| Gesture["A gesture's page"]
    end
```

Pop-ups open on top of a page and always close back to it:

```mermaid
flowchart LR
    Library -->|Capture a move · sidebar Capture · ⌥C · tap the shafts| Capture
    Gesture["Gesture page"] -->|Add takes · Re-record all| Capture
    Engine -->|Calibrate| Calibrate
    Engine -->|Record 6 s| Neutral["Neutral recording"]
    Metrics -->|Start the test| Test["Accuracy test"]
    Capture -->|Cancel · Escape · Save · timeout 40 s| Back((back where you were))
    Calibrate -->|Cancel · Escape · Done| Back
    Neutral -->|Cancel · Escape · finishes in 9 s| Back
    Test -->|Stop · Escape · finishes| Back
```

## Every screen

| Screen | Ways in | Ways out |
|---|---|---|
| App window | Open Mocap; ⚔ → Show Mocap; click the Dock icon | Close the window (keeps running in the menu bar); ⌘Q; ⚔ → Quit |
| Library | Sidebar; "← Back to the app" from the HUD; breadcrumb on a gesture page | Sidebar; a tile; Capture |
| Gesture page | A tile in the Library | "Library" breadcrumb; sidebar |
| Engine | Sidebar | Sidebar |
| Metrics | Sidebar; ⚔ → Accuracy test… | Sidebar |
| Live HUD (in the window) | Sidebar | "← Back to the app"; Escape; sidebar is not shown here |
| HUD panel over the game | ⚔ → HUD over the game; `--overlay` | Its ✕ button; ⚔ → HUD over the game again; ▴ collapses it |
| Capture (3-2-1 takes, live video + skeleton) | Capture a move; sidebar Capture; ⌥C; tap the shafts together; Add takes / Re-record all; + Pose; + Body move | Cancel; Escape; Save and go live; Redo (starts over); times out 40 s after your last kept take |
| Choosing a key (gesture page) | Click the key chip | Press a key; Escape; click anywhere else |
| Calibrate a weapon | Engine → a weapon → Calibrate | Cancel; Escape; Done on the summary |
| Neutral recording | Engine → Record 6 s | Cancel; Escape; finishes by itself |
| Accuracy test | Metrics → Start the test; ⚔ → Accuracy test… | Stop; Escape; finishes by itself |
| "Saved" message (HUD) | Saving a capture | ✕; Undo; fades after 12 s |
| Near-miss question (HUD) | A move almost fired | Yes, loosen it; No, ignore that move; Not now; fades after 20 s |
| "Wasn't me" (HUD) | Something fired | Press it (marks it wrong); fades after 15 s |
| Delete a gesture | Gesture page → Delete gesture | Click again to delete; wait 3 s and it backs off |
| Demo window | ⚔ → Open the demo; Library → Try it with sample data (empty Library) | Close it or ⌘Q (it's a separate app instance) |

Photos of your takes are files in the data folder; removing a take, clearing, deleting a gesture,
Undo, Cancel and Redo all delete the photos nothing uses any more (a copied profile keeps the ones
it shares).

## 1. First-time setup

```mermaid
flowchart LR
    A([Open Mocap]) --> B[Allow the camera<br/>macOS asks]
    B --> C[System Settings → Accessibility:<br/>turn on Mocap, reopen it]
    C --> D[Pair the Joy-Cons, strap them<br/>to your PVC weapons]
    D --> E{Top bar all green?<br/>Webcam · weapons · Keys OK}
    E -- no --> B
    E -- yes --> F[Engine: Calibrate each weapon<br/>Record Neutral]
    F --> G[In WoW: bind spells<br/>and macros to keys]
    G --> H([Capture your moves])
    A -. just looking .-> Demo([Try it with sample data])
```

## 2. Capture a move

Like recording software: you see yourself on the webcam with your skeleton on top, each take is
a countdown and a short recording, and each kept take's photo (its peak, skeleton on top) lands
in the take strip under the video. The gesture keeps those photos: its tile plays your most
typical take on hover and its page shows every take.

```mermaid
flowchart LR
    S([Capture: button · ⌥C · tap shafts]) --> C3[3 · 2 · 1, beeps]
    C3 --> R[GO: record 2.5 s<br/>red border, REC timer]
    R --> J{Caught a move?}
    J -- yes --> K1[✓ Take n of 5<br/>its photo joins the take strip] --> N{5 takes?}
    J -- no --> A1[Didn't catch it: again] --> C3
    N -- no --> C3
    N -- yes --> Key[Press the key, name it, Save]
    S -. Cancel · Escape, any time .-> Out([closed, nothing saved])
```

What it decides from the first take (or press "It's a pose" / "It's a body move"):

```mermaid
flowchart LR
    A([First take]) --> B{What did you do?}
    B -- swung a weapon --> M[Weapon motion<br/>5 takes, it buzzes each one]
    B -- held a pose --> P[Pose, 2 s]
    B -- moved with weapons still --> V[Body move, 5 takes<br/>judged on what moved]
    B -. or press .-> X[It's a pose / It's a body move]
    X --> P
    X --> V
    M --> K[Press the key you bound in WoW]
    P --> K
    V --> K
    K --> S([Save and go live]) --> U[Undo in the HUD if that was a mistake]
    A -. Cancel · Escape .-> Out([closed, nothing saved])
    K -. Redo .-> A
```

## 3. Playing

```mermaid
flowchart LR
    A([Arm: arms-up pose · ARMED · ⚔ menu]) --> C[Switch to WoW]
    C --> D[Swing, move, pose]
    D --> E{Recognized?}
    E -- no --> N[Nothing sent<br/>near miss → asks in the HUD]
    E -- yes --> F{Armed and<br/>WoW in front?}
    F -- no --> H[Held back, shown in Activity]
    F -- yes --> I[One key press]
    I --> W{Meant it?}
    W -- no --> X["Wasn't me: ignore that move"]
    W -- yes --> D
```

## 4. Camera + gyro during play

```mermaid
flowchart LR
    A([Weapon motion ends]) --> B{Gyro alone sure?}
    B -- yes --> K[Key now]
    B -- no --> C{Camera sees the<br/>weapon hand?}
    C -- yes --> D[Wait for frames covering the move<br/>~40 ms, decide on gyro + hand path]
    C -- no --> E{Needs the camera<br/>e.g. high vs low slash?}
    E -- yes --> F[Held back · shown in HUD]
    E -- no --> G[Decide on gyro]
    D --> K
    G --> K
```

## 5. Changing things as you go

```mermaid
flowchart LR
    A{What changed?} -- edited a macro in WoW --> B([Nothing to do: same key, new macro])
    A -- moved a spell to a new key --> C[Gesture page: click the key chip, press the new key]
    A -- it keeps missing you --> D[Gesture page: slide toward Loose, or Add takes]
    A -- it fires by accident --> E[Wasn't me · Strict · Judged on: Whole body]
    A -- new spec --> F[Profile menu: New / Copy current]
    A -- remounted a Joy-Con --> G[Engine: Calibrate that weapon]
```

## 6. Checking accuracy

```mermaid
flowchart LR
    A([Metrics → Start the test]) --> B[Get ready · Raptor Strike]
    B --> C[Now! Do it · no keys sent]
    C --> D{Recognized?}
    D -- right --> E[✓ and the delay]
    D -- another move --> F[✗ which one]
    D -- nothing in 4 s --> G[missed]
    E & F & G --> H{More?}
    H -- yes --> B
    H -- no --> I([Score, per-move table, confusions])
    A -. Stop · Escape .-> J([closed])
```

## How this was checked

`lab/ui_flows_check.py` drives the real UI in Chrome against the app with simulated hardware
(and against the demo, with its 18 sample gestures), pressing and releasing the mouse and keys
like a person, and checks the engine's state after each step: every page from the sidebar and
back; every gesture's tile to its page and back; the Library filters; changing "Judged on" and
back; capture opened three ways and left by Cancel, Escape, It's a pose, It's a body move; a body
move captured through the key step, Redo, Escape, then saved with a real key press; the key
step's Cancel; the HUD's ✕; a gesture's key chip and Escape; Delete backing off by itself;
Calibrate, Neutral and the accuracy test each left by their button and by Escape; every photo
on the tiles and gesture pages loads; the key step shows the takes' photos; and no JavaScript
errors anywhere.

Walking it found and fixed:
1. **Cancel didn't work** (nor any button, sometimes): the page redrew 20 times a second during
   capture and a press-and-release across a redraw was lost. Pages now redraw only when what
   they show changes, and buttons and links act on press-and-release even across a redraw.
2. The Live HUD page had no way back in the app window (no browser back button there).
3. A Neutral recording couldn't be cancelled; the near-miss question had no "Not now"; the
   "Saved" message and the HUD panel had no ✕.
4. Escape only worked in some pop-ups; it now closes any of them.
5. In the demo, a move still playing when you opened a capture landed in it.
