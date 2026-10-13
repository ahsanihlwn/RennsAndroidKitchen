# Dialog, transition, dropdown and drag feedback corrections

Open With now has a native clipped scroll viewport, showing at most five
56-pixel option cards with the original 12-pixel gaps (328 pixels). On smaller
windows it shows fewer cards while retaining the header, remember checkbox
and actions outside the viewport. Bounds are rechecked after real Qt font and
word-wrap layout. The option surface uses cached Skia corner/edge assets;
Qt still paints every caption and icon.

Transparent card-dialog shells no longer inherit Main's opaque background.
The shared glass helper follows the inner layout card. Explicit morph hosts
retain ownership of their moving and settled bounds: a layout request cannot
expand Create Project's glass back into its 12-pixel animation margin.

Scrolling Settings previously stopped and deleted the profile animation model
while the page canvas still held its child widgets. The App stops the owning
canvas first. The canvas also cancels on model, view or target destruction,
restores its owned target effect, stops timers and emits completion once.
Queued paints after cancellation perform no drawing.

Fatal Crash and its nested log now center on the same owning app window,
including after final Qt layout and resize. The log is sized to that window.
My Projects has no hover animation, capture or hover timer; its press/release
animation remains.

FileTree drag previews contain native Qt icons/names, with correct DPR, instead
of captured table rows. Physical and mounted files share this preview. Qt MIME
data, paths and actions remain available during the drag. Destination feedback
repaints the opaque viewport, retargets during autoscroll, and clears on leave,
drop and cancelled drag. Invalid mounted destinations are not highlighted.

Dropdown closing now uses the same `ButtonCardMorph` and spring curve as Create
Project. The chosen caption has its own Qt drawing path and travels outside
the shrinking option viewport, so it cannot be clipped away. The real anchor
stays at opacity zero through the full 440 ms close, including spring settling;
there is no separate bounce after showing the button. A temporary caption
preview determines the destination layout while preferences remain unchanged.
Confirmed selections keep the destination caption through the selection
callback; a closing owner restores the preview without applying a late choice.
Disabling animations retains an immediate handoff.

The black texture backdrop tracks its parent Resize events and covers the
entire new client rectangle. Resizing also releases any stale fade underlay.

## Reproduction

The in-memory option stress fixture repeats the seven existing app choices;
it creates no projects and saves no preferences.

```powershell
python tools/benchmark_openwith_scroll.py --options 70 --artifacts docs/openwith_validation/many
python tools/benchmark_openwith_scroll.py --options 70 --parent-height 500 --artifacts docs/openwith_validation/small
```

The probe records native screenshots, window/viewport bounds and paint/GPU
counts. It does not measure presentation FPS. Native regression tests cover
70 options, maximum-five clipping, small windows, touchpad/mouse scrolling,
caption pixels, overshoot, interrupted/deleted transition models, centered
nested dialogs and drag feedback. Drag tests intercept the native OS loop and
perform no filesystem moves.

The protected `rennsandroidkitchen` backup was read only.

## Final verification

- Native suite: all 152 tests passed in 37.6 seconds.
- Portable suite: 152 tests completed, with 16 hardware checks skipped.
- Native Open With checks at 150% DPI: all 9 passed.
- Actual AppUI probe: entering Settings and scrolling during a live profile
  morph cancels its canvas without an exception; rapid page changes also
  produced no exception.
- Native close-morph captures at 5/20/45/80% show the chosen Qt caption while
  the actual anchor remains at opacity zero. Create Project glass stays within
  the card border, and nested fatal/log dialogs share the app window center.
- Native 70-option probes confirm the five-item cap, clipped scrolling, clear
  outer corners and parent bounds at both 760 and 500 pixel window heights.

## Dropdown press and language refresh follow-up

Dropdown options now use `PressAnimatedButton`, which paints its press transform
directly instead of installing a child `QGraphicsEffect` inside the card effect.
The old held-press popup capture contained zero glyph pixels. The regression
checks actual option pixels on hover, held press and release; it fails with the
old button and passes with the direct painter. Qt continues to rasterize fonts.

The language caption and width update synchronously before the page crossfade.
The dropdown subtree is excluded from both language binding refresh and the
snapshot overlay mask, keeping its selected caption live throughout the fade.
Actual AppUI checks switching English to Indonesia confirm the 80/94-pixel
button bounds remain outside the overlay after the page refresh.

- Native suite: all 154 tests passed in 38.3 seconds.
- Portable suite: 154 tests completed in 26.9 seconds; 16 hardware checks skipped.
- Native shared-dropdown suite at 150% DPI: all 8 tests passed.

## Dropdown close handoff follow-up

Closing without selecting no longer rewrites the caption. A selected caption
also stays selected during popup teardown, removing the previous
new/old/new sequence. The settled spring blends into the exact native button
frame, and the owner repaints the destination before the popup hides. Popup
bounds expand if a changed caption would put the destination outside them.
The source remains hidden during the animation. Cached Qt caption pixels share
the same font/icon drawer in the live button and the closing morph.

Language crossfades capture current Qt content rather than a stale monitor
frame. The exclusion mask is recalculated after translation layout changes.

- Native suite: all 156 tests passed in 38.0 seconds.
- Portable suite: 156 tests completed in 27.3 seconds; 16 hardware checks skipped.
- Native dropdown checks at 150% DPI: all 10 passed.
- Final popup/button pixels match exactly for Escape and selection, including
  a caption that makes the destination wider than the initial anchor.
- Actual AppUI language selection changes the caption once, never rolls it
  back, and excludes it from the page fade. A subsequent Escape preserves
  identical button pixels. No exceptions were recorded; preferences were mocked.
