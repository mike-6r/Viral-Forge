# ViralForge performance report

Date: 2026-08-02

## Measured production run

| Stage | Result |
| --- | --- |
| API health | HTTP 200, 1.727 ms |
| API readiness | HTTP 200, 2.079 ms |
| Source preparation through analysis start | about 4 minutes 37 seconds; included download and source transcode |
| Media analysis | 75.9 seconds |
| Opportunity generation | 0.15 seconds |
| Producer recommendations | 0.15 seconds |
| Portrait clip render | 215.1 seconds |
| Quality report | 0.26 seconds |
| Rendered-media inspection | 33.4 seconds |

The source preparation and portrait render were CPU-intensive but progressed continuously. VPS disk usage after the run was 28% (32 GB of 116 GB used).

No performance change was made during this acceptance pass; the measured render latency is an operational capacity consideration, not a confirmed correctness fault.
