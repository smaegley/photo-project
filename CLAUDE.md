# Family Slide Archive

Digitizing ~32 Airequipt magazines (**1140 slides**) of family 35mm slides (mid-1960s–mid-1970s) shot by Steve's father **Wendel**, each captioned from Wendel's handwritten index cards. Goal: a richly-tagged, browsable archive explorable by **people / place / date / event**, with map + timeline. Owner = **Steve** (steve@maegley.com), who appears in the photos.

## Status
- **Spec is NOT frozen.** Steve and Claude are building out the spec together *before* building any app. Do not start building until Steve says it's frozen.
- A prior planning agent produced the handoff package; its prose docs were **patched, not rewritten**, and contradict each other. Trust the **manifest**, verify everything else.

## Locations
- `/mnt/photos` — dedicated 200GB drive for the photos.
- `/mnt/photos/photo-project/` — handoff package from the prior agent (SPEC, manifest, scripts, QA, slide images).
- `/home/aiuser/projects/photo-project/` — working project folder (this dir).
- **Source of truth:** `/mnt/photos/photo-project/handoff/slide_manifest.csv` — 1140 rows, 32 mags, fully captioned, all `status=ok`. Columns: `seq, magazine, slide_in_mag, organized_file (Mag<N>_Slide<NN>.JPG), original_file, mag_subject, mag_date_span, card_caption, date_raw, people, place, validation, status, notes`.

## Key decisions
1. **No originals on this machine.** Archival masters stay on Steve's Mac. This build needs only **one final photo folder** named by **magazine + slide matching the manifest** (`Mag<N>_Slide<NN>`). The old `Originals/` concept and the entire `batch`/SD-reformat unique-key apparatus are dropped from the spec.
2. The spec will be **rewritten together** from Steve's description — not by reconciling the old text. Steve is concurrently organizing the photos with another agent.

## Working notes
- When a **bulk photo copy/transfer is in progress**, do NOT audit folder/image completeness until Steve confirms it's done — partial/empty folders are expected mid-copy.
- The package was authored on a Mac; macOS `._*` AppleDouble junk files are scattered through the folders (safe to delete once the copy finishes).
