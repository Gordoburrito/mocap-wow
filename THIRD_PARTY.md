# Third-party software, models and data

Mocap's own code is MIT licensed (see LICENSE). The built app bundles the packages below.

| Component | Licence | Notes |
|---|---|---|
| Python | PSF | bundled interpreter |
| MediaPipe + Pose Landmarker (full) model | Apache-2.0 | `pose_landmarker_full.task`, downloaded from Google's model storage |
| OpenCV (opencv-contrib-python) | Apache-2.0 | its wheels include FFmpeg (LGPL-2.1+) and other libraries, dynamically linked; see the wheel's licence files |
| SDL2 (pysdl2-dll) | zlib | Joy-Con access |
| PySDL2 | public domain (CC0) | |
| NumPy | BSD-3-Clause | |
| aiohttp | Apache-2.0 | |
| PyObjC | MIT | |
| Matplotlib, sounddevice | PSF-based / MIT | imported by MediaPipe |
| PyInstaller bootloader | GPL-2.0 with the bootloader exception | used to build the app; the exception allows distributing bundled apps |

## Sample photos in the demo (redistributed)

`mocap/demo_photos/` holds one real photo per sample gesture in the demo, from Wikimedia
Commons, each under its own licence (public domain, CC0, CC BY or CC BY-SA; none of them is
covered by this project's MIT licence). They were downscaled to 720 px on the long side and
otherwise left as they are; the skeleton drawn over them in the app is computed at run time.
Regenerate them with `lab/fetch_demo_photos.py`; `credits.json` next to them has the same
details in machine-readable form.

| File | Photo | Author | Licence |
|---|---|---|---|
| `draw_aim.jpg` | [Archery at the 2017 Warrior Games 34896116493 31aee156df b.jpg](https://commons.wikimedia.org/wiki/File:Archery_at_the_2017_Warrior_Games_34896116493_31aee156df_b.jpg) | Department of Defense Warrior Games | [Public domain](https://commons.wikimedia.org/wiki/Commons:Public_domain) |
| `snap_shot.jpg` | [Zanshin Kimono Standform.jpg](https://commons.wikimedia.org/wiki/File:Zanshin_Kimono_Standform.jpg) | Mushinkyudo at de.wikipedia | [Public domain](https://commons.wikimedia.org/wiki/Commons:Public_domain) |
| `sky_shot.jpg` | [FlightArchery Jack.jpg](https://commons.wikimedia.org/wiki/File:FlightArchery_Jack.jpg) | Brevis13 | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) |
| `pull_string.jpg` | [Archer Doreen Roberts (14845626809).jpg](https://commons.wikimedia.org/wiki/File:Archer_Doreen_Roberts_(14845626809).jpg) | Archives Of Ontario from Toronto, Canada | [Public domain](https://commons.wikimedia.org/wiki/Commons:Public_domain) |
| `turn_bow.jpg` | [Archer japonais.jpg](https://commons.wikimedia.org/wiki/File:Archer_japonais.jpg) | Raimund von Stillfried-Rathenitz (1839-1911) | [Public domain](https://commons.wikimedia.org/wiki/Commons:Public_domain) |
| `slash_left.jpg` | [Entrenamiento de Iaido La Paz - Bolivia 7.jpg](https://commons.wikimedia.org/wiki/File:Entrenamiento_de_Iaido_La_Paz_-_Bolivia_7.jpg) | Das Morton | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0) |
| `slash_right.jpg` | [Entrenamiento de IAIDO, La Paz Bolivia, mayo 2026.png](https://commons.wikimedia.org/wiki/File:Entrenamiento_de_IAIDO,_La_Paz_Bolivia,_mayo_2026.png) | SilvEsth | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) |
| `low_slash.jpg` | [Entrenamiento de Iaido La Paz - Bolivia 4.jpg](https://commons.wikimedia.org/wiki/File:Entrenamiento_de_Iaido_La_Paz_-_Bolivia_4.jpg) | Das Morton | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0) |
| `thrust.jpg` | [Advanced lunge performed by an Epee fencer at Athenaikos Fencing Club.jpg](https://commons.wikimedia.org/wiki/File:Advanced_lunge_performed_by_an_Epee_fencer_at_Athenaikos_Fencing_Club.jpg) | George E. Koronaios | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) |
| `circle.jpg` | [Entrenamiento de Iaido La Paz - Bolivia 3.jpg](https://commons.wikimedia.org/wiki/File:Entrenamiento_de_Iaido_La_Paz_-_Bolivia_3.jpg) | Das Morton | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0) |
| `duck.jpg` | [Marine Corps officer candidate participate in physical training.jpg](https://commons.wikimedia.org/wiki/File:Marine_Corps_officer_candidate_participate_in_physical_training.jpg) | U.S. Marine Corps photo by Lance Cpl. Phuchung Nguyen | [Public domain](https://commons.wikimedia.org/wiki/Commons:Public_domain) |
| `kick.jpg` | [Taekwondo side kick.jpg](https://commons.wikimedia.org/wiki/File:Taekwondo_side_kick.jpg) | Cerebellum | [Public domain](https://commons.wikimedia.org/wiki/Commons:Public_domain) |
| `stomp.jpg` | [USMC-100518-M-4569K-033.jpg](https://commons.wikimedia.org/wiki/File:USMC-100518-M-4569K-033.jpg) | U.S. Marine Corps | [Public domain](https://commons.wikimedia.org/wiki/Commons:Public_domain) |
| `shove.jpg` | [Dimostrazione di Tui Shou.jpg](https://commons.wikimedia.org/wiki/File:Dimostrazione_di_Tui_Shou.jpg) | Aquila antica | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) |
| `arms_up.jpg` | [On The Top (69175155).jpeg](https://commons.wikimedia.org/wiki/File:On_The_Top_(69175155).jpeg) | Nikolay Loubet | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0) |
| `right_up.jpg` | [1985 Friendly Chinese man in Gelugor, Penang Island. Spielvogel Archiv.jpg](https://commons.wikimedia.org/wiki/File:1985_Friendly_Chinese_man_in_Gelugor,_Penang_Island._Spielvogel_Archiv.jpg) | Spielvogel | [CC0](https://creativecommons.org/publicdomain/zero/1.0/deed.en) |

## Data used for testing (not redistributed)

The labs download these at run time; nothing from them is in this repository except
aggregate numbers (accuracy percentages).

- **6DMG** (Chen, Ji, AlRegib; MMSys 2012), handheld gyro + accelerometer gestures. CC BY 4.0.
  https://zenodo.org/records/10471794
- **UTD-MHAD** (Chen, Jafari, Kehtarnavaz; ICIP 2015), wrist inertial sensor + Kinect + RGB
  video. No licence stated; cite the paper. https://personal.utdallas.edu/~kehtar/UTD-MHAD.html
- **Wikimedia Commons** photos and two jumping-jack videos, each under its own Creative Commons
  licence (recorded per file by `lab/fetch_poses.py` in `data/poses/manifest.json`).

"World of Warcraft" is a trademark of Blizzard Entertainment. "Joy-Con" is a trademark of
Nintendo. This project is not affiliated with either.
