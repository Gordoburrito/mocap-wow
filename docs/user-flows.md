# User flows

The Joy-Cons are strapped to PVC weapons, so there are no controller buttons. Every input is a
body pose (webcam) or a weapon motion (Joy-Con).

## 1. First-time setup

```mermaid
flowchart LR
    A([Run ./run.sh]) --> B[Allow Camera + Accessibility<br/>for Ghostty, restart]
    B --> C[Pair the Joy-Cons, strap them<br/>to your PVC weapons]
    C --> D{Top bar all green?<br/>Camera · weapons · Keys OK}
    D -- no --> B
    D -- yes --> E[Record Neutral<br/>6 s of standing normally]
    E --> F[In WoW: bind spells<br/>and macros to keys]
    F --> G([Ready to add gestures])
```

## 2. Add a gesture

```mermaid
flowchart LR
    A([+ Pose or + Motion]) --> B[Name it]
    B --> C[Click the key chip,<br/>press the key you bound in WoW]
    C --> D{Kind?}
    D -- Pose --> P[3-2-1 countdown,<br/>hold the pose 2 s]
    D -- Motion --> S[Do the motion 5 times,<br/>the weapon buzzes each one]
    P --> T{Fires when<br/>you mean it?}
    S --> T
    T -- misses you --> L[Looser slider<br/>or Add more]
    T -- fires by accident --> R[Stricter slider, record Neutral,<br/>or Record moves to ignore]
    L --> T
    R --> T
    T -- yes --> W[Test key: switch to WoW,<br/>key fires after 3 s]
    W --> Z([Done])
```

## 3. Playing

```mermaid
flowchart LR
    A([./run.sh]) --> B[Arm it: cross weapons overhead<br/>or click ARMED]
    B --> C[Switch to WoW]
    C --> D[Do a pose or a motion]
    D --> E{Recognized?}
    E -- no --> N[Nothing sent<br/>shown in Activity]
    E -- yes --> F{Armed and<br/>WoW in front?}
    F -- no --> H[Held back<br/>shown in Activity]
    F -- yes --> I[One key press sent]
    I --> J([WoW runs your spell or macro,<br/>the weapon buzzes])
    J --> D
```

## 4. Changing things as you go

```mermaid
flowchart LR
    A{What changed?} -- edited a macro in WoW --> B([Nothing to do here:<br/>same key, new macro])
    A -- moved a spell to a new key --> C[Click the gesture's key chip,<br/>press the new key]
    A -- new spec --> D[Start with another profile:<br/>./run.sh --profile marks.json]
    A -- want another gesture --> E[Flow 2]
```

## 5. Checking accuracy

```mermaid
flowchart LR
    A([Metrics → Start the test]) --> B[Prompt: Get ready · Slash left]
    B --> C[Now! Do the move<br/>no keys are sent]
    C --> D{Recognized?}
    D -- right --> E[✓ + delay]
    D -- another move --> F[✗ shows which]
    D -- nothing in 4 s --> G[missed]
    E --> H{More prompts?}
    F --> H
    G --> H
    H -- yes --> B
    H -- no --> I([Score, per-move table,<br/>what got mistaken for what])
    I --> J[Fix: re-record, loosen,<br/>or make two moves more different]
```

## 6. Camera + gyro during play

```mermaid
flowchart LR
    A([Weapon motion ends]) --> B{Gyro alone sure?}
    B -- yes --> K[Key now]
    B -- no --> C{Camera sees the<br/>weapon hand?}
    C -- yes --> D[Wait for frames covering the move<br/>~40 ms, then decide on gyro + hand path]
    C -- no --> E{Needs the camera<br/>e.g. high vs low slash?}
    E -- yes --> F[Held back · shown in HUD]
    E -- no --> G[Decide on gyro]
    D --> K
    G --> K
    K --> L[Confident match + camera agreed:<br/>update mount correction]
```

## Not built yet

Turning the camera stays on the mouse or the A / D keys (no bow-tilt steering).

```mermaid
flowchart LR
    V[Voice: say a word → one key] ~~~ TW[Two-weapon moves:<br/>both weapons as one gesture] ~~~ EP[ESP32 props]
```
