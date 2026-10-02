# Existing Lightroom MCP / bridge projects: what we can use

Reviewed 2026-10-02 from shallow clones of each repository's default branch. None of them does what Match Look does: copy one photo's look onto others and solve each photo's white balance, exposure and tone so they match. All of them are general remote controls for Lightroom. So the question is what to borrow, not what to switch to.

## Summary

| Repo | What it is | License | Usable? |
|---|---|---|---|
| [jimjohnbeebe-jpg/LrC_Autonomous_Gateway](https://github.com/jimjohnbeebe-jpg/LrC_Autonomous_Gateway) | Lua plugin plus a Node/TypeScript MCP server for Claude Desktop. Edits in a loop: set sliders, look at a render, adjust. Can match exposure across a burst. Tested on Windows with LrC 15.5.1. | MIT | **Yes, for its findings and data.** It has written reports from tests in real Lightroom. |
| [4xiomdev/lightroom-classic-mcp](https://github.com/4xiomdev/lightroom-classic-mcp) | Python MCP server plus a plugin, macOS-only. Broad tool set: develop settings, snapshots, labels, export, AI masks. | MIT | **Yes, as a reference.** Its AI-mask code is the one to borrow later. |
| [Automaat/lightroom-mcp](https://github.com/Automaat/lightroom-mcp) | The most widely used. Node MCP server plus a plugin, macOS and Windows. Covers search, ratings, develop settings, presets, collections and export. Includes a skill for matching a reference style. | MIT | Reference only. It has no snapshot tool and no preview from inside Lightroom. |
| [par4987/lightroom-mcp](https://github.com/par4987/lightroom-mcp) | Fork of Automaat with many more tools: AI masks, previews, spots, white balance, tone curve, snapshots. | MIT | Reference only. |
| [kotvaer/lightroom-mcp](https://github.com/kotvaer/lightroom-mcp) | Fork of Automaat that adds safety tools: guarded virtual copies, recovery snapshots, rollback. | MIT | Reference only. |
| [Kmanley1/lightroom-cli](https://github.com/Kmanley1/lightroom-cli) | Python SDK plus a command line (`lr`, 107 commands) plus an MCP server. Edits through the Develop module controller. | MIT | Possible alternative bridge (it's Python), but it is much heavier than we need. |
| [saril009/lightroom-mcp](https://github.com/saril009/lightroom-mcp) | Node MCP server; the plugin polls a local HTTP server. | **No license file** | **No.** Without a license we can't copy its code. |
| [Symphon-y/lightroom-mcp](https://github.com/Symphon-y/lightroom-mcp) | Culling and organizing; develop editing is listed as a later phase. | **No license file** | **No.** It also lacks the develop tools we need. |

## Why we keep our own bridge
We need six operations: read the selection, apply settings, render, snapshot, set a label, and ping. Our plugin does exactly these, in about 300 lines of Lua, and is tested under Lua 5.1. Switching to one of the bigger servers would add:
- **A socket transport.** LrC-AVG's tests found Lightroom's sockets cycle about every 10 seconds when no client is connected, and need re-arming and rebinding.
- **A second runtime.** Most of these servers need Node installed.
- **Dozens of tools we don't use.**

Our bridge passes requests as files in a folder, which avoids the socket problems.

## Adopted in this repo (from LrC_Autonomous_Gateway's Lightroom 15.5.1 test reports)
| Finding (their report) | What we changed |
|---|---|
| Lightroom **silently ignores** a setting it doesn't accept, with no error (S5, P-12) | `apply_settings` now reads every write back and returns `not_taken`. The workflow shows these as warnings in the report. |
| Plugin state isn't reliably shared between menu scripts (P-15) | Bridge "running" status is a heartbeat file on disk. A stop file stops the loop from any script. Only one loop can run at a time. |
| `applyDevelopSettings(settings, historyName)` takes a History step name | Every write is named **"Match Look"** in the History panel. |
| Writing Temperature alone leaves white balance on "As Shot"; writing `WhiteBalance = "Custom"` with it works (WB report) | This was already our approach. Their test confirms it. |
| A preview requested right after a write can come back stale; exporting is reliable (P-01). Each export takes about 2.6 s (P-02). | We already export. Expect about 3 s per photo per pass. |
| `createDevelopSnapshot` works, and restoring a snapshot is exact over all 178 settings, including `Look` (P-05) | This was already our approach. Their test confirms it. |
| A profile is a pair: `CameraProfile` plus its `Look` table (P-07) | Both are copied as part of the look. |
| `getPhotoByLocalId` works (P-18) | Already used. Their test confirms it. |
| A real dump of all 178 develop-setting keys from LrC 15.5.1 | Fixed our key lists: lowercase `orientation`, `ChromaticAberrationR/B`, `LensBlur`, the old `Auto*` auto-tone switches, and the `Enable*` switches for per-photo tools are no longer copied. A test now classifies every real key (`tests/fixtures/lrc15_develop_keys.json`, MIT, credited). |

## Worth borrowing later
- **AI masks** (4xiomdev `MaskActions.lua`, par4987 `add_ai_mask`). `LrDevelopController.addToCurrentMask("aiSelection", "subject" | "sky" | "person" | ...)` creates AI masks. This corrects our plan, which assumed plugins can't create AI masks. It would allow:
  - a skin-tone guard: measure and fix the subject separately
  - a fix for mixed lighting: correct the sky and the subject separately

  The catch is that it works through the Develop module on the active photo only, one photo at a time.
- **Exposure-only burst sync** (LrC-AVG `engine/src/sync/exposure.ts`). It uses the same secant-search idea as our solver, and confirms the closed-loop approach works in real Lightroom.
- **Reference-style workflow notes** (Automaat `skills/raw-photo-lightroom-preset`). It separates technical correction from creative style and works one representative photo per lighting group. This matches our design and could guide grouping a mixed shoot by lighting.
