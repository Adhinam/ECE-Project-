# Camera zone editor

Double-click `start-zone-app.cmd` in the project folder. It starts the local app
and opens http://127.0.0.1:8765 in your browser. Keep its terminal open while using
the app. Videos and results stay on this computer; no cloud service is used.

The standalone `index.html` still works for drawing layouts, but model processing
requires the local app. Existing layouts can be loaded into either version.

If setting up on a new computer, install the project environment dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.zone-app.txt
```

1. Choose a video and pause on a clear frame using the timeline.
2. Enter the Store ID. Choose **Section / zone**, give it a name such as `dairy`,
   and click **Start drawing**. Click the corners and **Finish shape**.
3. For **Entry / exit doorway**, click two line endpoints, then click inside the
   store. The IN arrow must point inward. One line counts crossings both ways.
4. For a billing queue, draw tightly around the path where shoppers wait. Leave
   out the checkout counter and nearby aisle. Shoppers count when their detected
   box centre stays inside for about half a second. The dashboard shows current
   and peak depth, people seen, queue visits and time in the marked area. Add
   staff areas if needed. Select any saved shape
   and drag its vertices to adjust. Undo also restores deletions and adjustments.
5. Use **Save layout JSON…** to choose a file location (supported browsers), or
   **Save & process video** to save the layout and annotated video together.
   **Open an existing layout JSON** only loads a file you have already saved.

## Process and inspect results

Choose an output folder, relative to the project folder or an absolute path
inside the workspace (`D:\4TH YEAR PROJECT PPT` on this computer). The default is
`sample_video/output/app_runs`. Each run gets a new folder so results are preserved.

Click **Save & process video**. The app copies the chosen source video and writes
the layout, runs the configured YOLO26 + Deep OC-SORT appearance tracker on CPU,
and updates the entry/exit and zone-time dashboard. Only one model job runs at a
time. The completed H.264 MP4 plays on the same page and can be downloaded.
The annotated output is silent; the original source copy retains its original audio.

Results include `annotated.mp4`, `layout.json`, `report.json`, `crossings.csv`,
`zone_visits.csv`, `zone_totals.csv`, `queue_visits.csv`, `queue_totals.csv`,
and pipeline JSONL under `events/`.
Use **Open a saved run** to reload earlier results after restarting the app.
Model loading and inference may take longer than the video itself on CPU.
The current default uses YOLO26 Small at 960px with appearance-weighted matching,
which favors accuracy over CPU speed. See `docs/tracking-review.md` for the
verified shopper checkpoints and remaining validation limits.

If a doorway is too close to the image edge, select it and use **Move doorway
inward** or drag its endpoints. People must be visible on both sides of the
line for a crossing to be observed. The dashboard also shows calibration warnings.

IDs in tables match the numerical IDs drawn on the video. The separate session
visitor ID is retained in the entry/exit CSV when available. Tracking identities
are not a certified count of unique customers. People already visible when the
clip begins do not generate a fabricated entrance. Zone visits are accumulated
once per visit, including visits shorter than the dwell alert threshold; periodic
dwell events are not added repeatedly. An unfinished visit is labelled partial
at the end of the video. Missing detections do not count as a store exit.

Coordinates are exported in original video pixels, regardless of display size.
The editor uses decoded visible-frame dimensions where the browser exposes them;
the processing app also remaps layouts to OpenCV's confirmed frame dimensions.
This handles sample-aspect-ratio differences between browser display and raw frames.
Self-intersecting polygons and incompatible video dimensions are rejected.
The entry arrow follows the existing pipeline's `_inward_normal` convention
(the internal `bottom` value points upward; the UI uses the arrow to avoid ambiguity).

Save the downloaded file under `configs/`, then from the repository directory in
PowerShell, point the existing full pipeline at it:

```powershell
$env:STORE_LAYOUT_PATH = 'configs/store_layout_my_store.json'
$env:PIPELINE_VIDEO_SOURCE = 'sample_video/Resized video.mp4'
$env:PIPELINE_STORE_ID = 'my-store'  # Must match the editor Store ID
$env:PIPELINE_CAMERA_ID = 'cam-entrance'
.\.venv\Scripts\python.exe -m pipeline.main run
```

The command above is an alternative to the app's **Save & process video** button.
Use a separate layout per fixed camera position. Same resolution does not guarantee
the same view. Camera movement or zoom requires recalibration. Browser-supported
video codecs are required; convert unsupported CCTV formats to H.264 MP4 first.

The current pipeline checks bounding-box centres for zones and crossings. Draw
boundaries with that in mind; footpoint-based floor zoning can be a later improvement.
Staff areas use the project's current zone heuristic and do not verify staff identity.

Drawing changes stay in memory until saved or processed. Save before closing the page.

Processed results persist in the run folder. Selecting or starting a run saves its
ID in the page URL and remembers it in this browser. Refreshing restores that run;
unsaved drawing edits still need to be exported separately.

CSV buttons download without replacing the dashboard. Only files that exist for
the selected completed run are offered. Older runs may lack billing exports;
their existing results remain available. Downloads include all records, independent
of table filters. CSV durations and timestamps are seconds from the recording;
the dashboard formats durations as minutes and seconds. Queue summary counts cover
all IDs, while the ID filter applies to individual visit records. Combined queue
time is the sum of visit durations, including overlapping visits and partial visits
at the end of the recording; it is not confirmed checkout service time.
