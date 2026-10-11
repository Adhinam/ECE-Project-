# Store video tracking review

The supplied screenshots identify one shopper at 19.5 seconds (ID 14), 24 seconds
(ID 16), and 35.2 seconds (ID 17). These are a reported identity fragmentation,
not three different visitors. Review all three timestamps before accepting a
tracker configuration. Also check that the white-hooded shopper does not inherit
this person's earlier ID.

`scripts/check_cap_checkpoints.py` compares saved tracks with five manually
reviewed boxes in the 1270 x 720 source video. It is a narrow regression check,
not a general accuracy benchmark. A missing detection fails the identity check;
reducing the number of IDs alone is not evidence of improved accuracy.

The original app run is `run_5e2ae82c9e7c`. A separate initial evaluation,
`run_407bf03750b5`, improves some distant detections at 960px but **fails** this
regression: the cap shopper has ID 15 then 19, and ID 15 transfers to the
white-hooded shopper. Its 34 IDs, 1 entry and 2 exits are candidate output, not
verified visitor counts. Keep the original run for comparison.

The accepted targeted comparison uses YOLO26 Small at 960px and weighted
appearance/motion association (appearance weight 0.55, hard rejection below
cosine similarity 0.1 on confident detections). It retains ID 7 at all three
cap-shopper checkpoints and ID 16 at both white-hooded-shopper checkpoints.
Box overlap with the manually reviewed cap boxes is 0.949, 0.988 and 0.971.
These results establish this specific fix, not complete-video identity accuracy.
This configuration is slower on CPU than Nano at 640px.

The completed 1452-frame inference run `run_c383793d6e48` reproduces all five
checkpoint matches. Inspection also found a separate weak-box handover at
20.1 seconds: the cream-coated shopper's ID briefly attached to an exiting
person. Crossing evidence now requires confidence >= 0.6, and event confidence
is no longer artificially raised to a floor. This prevents that false exit;
it can also miss genuine crossings supported only by weak detections.

`run_26903e7d5335` rebuilds the complete playable video, event records and
dashboard from those saved tracks using the corrected crossing rule. It shows
3 observed entries and 2 observed exits (at 19.8 and 68.6 seconds). This is
explicitly a saved-track event replay, not a second identity inference run.
Other identity fragmentation remains possible; the whole clip has not been
manually labelled. Verification: 211 automated tests, plus saved-run browser
playback and seeking checks. Earlier runs remain available for comparison.

The original doorway is on the extreme image border. Counting uses bounding-box
centres; a crossing cannot be observed if the centre is only visible on one side.
The editor warns about border lines and offers **Move doorway inward**, with undo.
Place the line across the real walking path with visible observations on both
sides and confirm the IN arrow. The initial evaluation moved the original line
150px inward for review; this is not automatic calibration or ground truth.

Crossing logic now uses a stable side and cumulative movement, excludes crossings
beyond the finite line endpoints, and resets evidence after long observation
gaps. These changes address slow walking and jitter without treating a lost
detection as an exit. Saved reports include calibration warnings and diagnostic
track and appearance logs.
