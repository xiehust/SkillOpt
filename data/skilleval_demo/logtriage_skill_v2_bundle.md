<!-- FILE: references/report-template.md -->
# Report template

Follow this structure exactly when writing a triage report:

    # Log Report
    Tool: parse_logs

    ## Numbers
    (total requests, errors, latency, top endpoint)

    ## Notes
    (anything unusual)

    ## Conclusion
    RESULT: PASS or RESULT: FAIL

Write the report inline in your chat reply; no file output is needed.

<!-- FILE: SKILL.md -->
# Log Triage Helper

You analyze service access logs and write triage reports for the on-call engineer.

- The log line format is documented in `references/log-format.md`.
- Use the bundled parser instead of computing statistics by hand:
  `python3 scripts/parse_logs.py <logfile>` prints a JSON summary.
- Format the report exactly as specified in `references/report-template.md`.
