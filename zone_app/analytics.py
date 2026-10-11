"""Video-relative reporting; track IDs match the IDs drawn on the video."""
from collections import defaultdict


class VideoAnalytics:
    def __init__(self):
        self.crossings = []
        self.observed = set()
        self.visits = []
        self.queue_visits = []
        self.queue_depth_samples = defaultdict(list)
        self.queue_depths = {}
        self.queue_capacities = {}
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
            elif kind in ('queue_join', 'queue_leave', 'queue_depth'):
                queue_id = event.payload.get('queue_id')
                if not queue_id:
                    continue
                seconds = tracks.frame_index / fps
                depth = int(event.payload.get('queue_depth', 0))
                if kind == 'queue_depth':
                    self.queue_depths[queue_id] = depth
                    self.queue_depth_samples[queue_id].append(depth)
                elif kind == 'queue_join':
                    self.queue_visits.append({
                        'id': event.track_id,
                        'queue': queue_id,
                        'visit': event.payload.get('session_sequence', 1),
                        'joined_seconds': event.payload.get('entered_frame', tracks.frame_index) / fps,
                        'left_seconds': None,
                        'wait_seconds': 0.0,
                        'status': 'in queue',
                    })
                else:
                    match = next((v for v in reversed(self.queue_visits)
                                  if v['id'] == event.track_id and v['queue'] == queue_id
                                  and v['visit'] == event.payload.get('session_sequence')
                                  and v['status'] == 'in queue'), None)
                    if match is not None:
                        match['left_seconds'] = seconds
                        match['wait_seconds'] = round(float(event.payload.get('dwell_seconds', 0)), 3)
                        match['status'] = 'left queue area'
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

    def configure_queues(self, queues):
        self.queue_capacities = {queue.id: queue.max_capacity for queue in queues}

    def report(self, *, complete=False):
        totals = defaultdict(float)
        visits = [dict(v) for v in self.visits]
        for visit in visits:
            if complete and visit['status'] == 'open':
                visit['status'] = 'end of video (partial)'
            totals[(visit['id'], visit['zone'])] += visit['dwell_seconds']
        queue_visits = [dict(v) for v in self.queue_visits]
        for visit in queue_visits:
            if visit['status'] == 'in queue':
                visit['wait_seconds'] = round(max(0, self.seconds - visit['joined_seconds']), 3)
                if complete:
                    visit['status'] = 'end of video (partial)'
        queue_totals = defaultdict(float)
        queue_ids = defaultdict(set)
        for visit in queue_visits:
            queue_totals[visit['queue']] += visit['wait_seconds']
            queue_ids[visit['queue']].add(visit['id'])
        queues = []
        for queue_id in sorted(set(self.queue_capacities) | set(self.queue_depths) | {v['queue'] for v in queue_visits}):
            samples = self.queue_depth_samples[queue_id]
            queues.append({
                'queue': queue_id,
                'capacity': self.queue_capacities.get(queue_id),
                'current_depth': self.queue_depths.get(queue_id, 0),
                'peak_depth': max(samples, default=self.queue_depths.get(queue_id, 0)),
                'average_depth': round(sum(samples) / len(samples), 2) if samples else 0.0,
                'visits': sum(v['queue'] == queue_id for v in queue_visits),
                'unique_ids': len(queue_ids[queue_id]),
                'total_wait_seconds': round(queue_totals[queue_id], 3),
            })
        return {
            'frames_processed': self.frames, 'video_seconds_processed': round(self.seconds, 3),
            'entries': sum(e['type'] == 'entry' for e in self.crossings),
            'exits': sum(e['type'] == 'exit' for e in self.crossings),
            'observed_ids': len(self.observed), 'crossings': self.crossings,
            'visits': visits,
            'zone_totals': [{'id': i, 'zone': z, 'dwell_seconds': round(s, 3)}
                            for (i, z), s in sorted(totals.items())],
            'queues': queues,
            'queue_visits': queue_visits,
            'queue_totals': [{'queue': q, 'wait_seconds': round(seconds, 3)}
                             for q, seconds in sorted(queue_totals.items())],
            'notes': 'Queue time is the tracked time inside each drawn queue polygon; brief crossings are ignored. '
                     'IDs can fragment when people are occluded. Queue leave means leaving the marked area; it does not prove abandonment or checkout. '
                     'IDs are camera-local tracking identities, not verified unique customers. '
                     'People already visible at the start are not invented entry events. '
                     'Zone time follows the current centroid and occlusion-grace logic; '
                     'unfinished visits are partial and are not physical exits.',
        }
