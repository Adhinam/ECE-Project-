from pathlib import Path
import json

def generate_report(log_file="tracking_log.txt"):
    summary = None

    for line in Path(log_file).read_text().splitlines():
        if '"tracking_complete"' in line:
            summary = json.loads(line)

    if summary is None:
        print("No tracking summary found")
        return

    print("\nSTORE ANALYTICS REPORT")
    print("=" * 40)
    print("Visitors:", summary["unique_track_ids"])
    print("Group Entries:", summary["group_entries_detected"])
    print("Average Occupancy:", round(summary["avg_tracks_per_frame"], 2))
    print("Ended Tracks:", summary["ended_tracks"])

if __name__ == "__main__":
    generate_report()