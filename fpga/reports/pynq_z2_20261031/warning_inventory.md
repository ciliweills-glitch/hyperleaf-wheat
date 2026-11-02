# Vivado warning inventory

Counts are log occurrences, not unique hardware faults. Initial failed/obsolete attempts are preserved.

| Severity | ID | Occurrences |
|---|---|---|
| CRITICAL WARNING | BD 41-737 | 1 |
| CRITICAL WARNING | PSU-1 | 1 |
| CRITICAL WARNING | PSU-2 | 1 |
| CRITICAL WARNING | PSU-3 | 1 |
| CRITICAL WARNING | PSU-4 | 1 |
| WARNING | BD 41-2384 | 10 |
| WARNING | BD 41-2937 | 1 |
| WARNING | BD 41-702 | 2 |
| WARNING | Device 21-2174 | 4 |
| WARNING | Device 21-9320 | 4 |
| WARNING | IP_Flow 19-4994 | 4 |
| WARNING | Power 33-332 | 4 |
| WARNING | Synth 8-3332 | 4 |
| WARNING | Synth 8-3848 | 9 |
| WARNING | Synth 8-6014 | 76 |
| WARNING | Synth 8-689 | 10 |
| WARNING | Synth 8-7023 | 22 |
| WARNING | Synth 8-7071 | 62 |
| WARNING | Synth 8-7080 | 12 |
| WARNING | Synth 8-7129 | 925 |
| WARNING | Vivado 12-508 | 135 |
| WARNING | Vivado 12-7122 | 1 |
| WARNING | Vivado_Tcl 4-919 | 4 |
| WARNING | Vivado_Tcl 4-921 | 128 |

PSU-1..4 derive from the unchanged PYNQ-Z2 board preset. They remain visible; no DDR delay was zeroed or warning suppressed.
BD 41-737 was an ineffective write to a read-only reset property in the initial creation script; corrected for reproduction, and actual propagated reset polarity is independently checked as active-low.
No boot image or ps7_init is released or executed. Board validation is still required.
