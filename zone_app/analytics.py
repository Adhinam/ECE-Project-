"""Video-relative reporting; track IDs match the IDs drawn on the video."""
from collections import defaultdict


class VideoAnalytics:
    def __init__(self):
        self.crossings = []
        self.observed = set()
        self.visits = []
        self.frames = 0
        self.seconds = 0.0

    def update(self, tracks, events, zones, fps):
        self.frames += 1
        self.seconds = (tracks.frame_index + 1) / fps
        self.observed.update(t.track_id for t in tracks.tracks)
        for event in events:
            kind = event.event_type.value.lower()
            if kind in ('entry', 'exit', 'reentry'):
                self.crossings.append({
                    'type': 'entry' if kind == 'reentry' else kind,
                    'id': event.track_id,
                    'visitor_id': event.payload.get('visitor_id'),
                    'seconds': tracks.frame_index / fps,
                    'doorway': event.payload.get('line_id') or event.payload.get('entry_line_id', ''),
                })
        self.visits = []
        for identity, history in zones.histories.items():
            for visit in history.visits:
                end = visit.exited_frame
                # Last processed frame + one frame interval for an unfinished visit.
                seconds = visit.total_dwell_seconds if end is not None else max(
                    0, (tracks.frame_index + 1 - visit.entered_frame) / fps,
                )
                self.visits.append({
                    'id': identity, 'zone': visit.zone_id,
                    'visit': visit.session_sequence, 'entered_seconds': visit.entered_frame / fps,
                    'exited_seconds': None if end is None else end / fps,
                    'dwell_seconds': round(seconds, 3),
                    'status': 'closed' if end is not None else 'open',
                })

    def report(self, *, complete=False):
        totals = defaultdict(float)
        visits = [dict(v) for v in self.visits]
        for visit in visits:
            if complete and visit['status'] == 'open':
                visit['status'] = 'end of video (partial)'
            totals[(visit['id'], visit['zone'])] += visit['dwell_seconds']
        return {
            'frames_processed': self.frames, 'video_seconds_processed': round(self.seconds, 3),
            'entries': sum(e['type'] == 'entry' for e in self.crossings),
            'exits': sum(e['type'] == 'exit' for e in self.crossings),
            'observed_ids': len(self.observed), 'crossings': self.crossings,
            'visits': visits,
            'zone_totals': [{'id': i, 'zone': z, 'dwell_seconds': round(s, 3)}
                            for (i, z), s in sorted(totals.items())],
            'notes': 'IDs are camera-local tracking identities, not verified unique customers. '
                     'People already visible at the start are not invented entry events. '
                     'Zone time follows the current centroid and occlusion-grace logic; '
                     'unfinished visits are partial and are not physical exits.',
        }
