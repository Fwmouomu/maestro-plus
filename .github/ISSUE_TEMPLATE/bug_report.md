---
name: Bug report
about: Something failed or behaved wrongly
labels: bug
---

Run `health_check` first and paste its output below — it answers the most common
cause (missing binary, unauthorized device) in one call.

**What happened**

<!-- One or two sentences. What did you expect, what did you get? -->

**Tool and inputs**

<!-- Which tool, the flow file (trim to the smallest failing case), and the device -->

**Environment**

```
maestro --version:
adb devices -l:
OS:
```

**health_check output**

<details><summary>paste here</summary>

```

```

</details>

**If this is about `debug_failure`'s attribution:** what was the real root cause?
Calibration data — failures where someone knows the truth — is the scarcest
input this project can receive, and it is worth more than a stack trace.
