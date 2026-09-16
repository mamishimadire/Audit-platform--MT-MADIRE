"""
The vocabulary a test execution's status can take. Replaces the old binary
completed/failed (which conflated four genuinely different situations under
one "failed" bucket: a real control problem, a field that isn't mapped yet,
a connection outage, and — under "completed" — a run that found nothing to
even check). Never invent a result: if the platform cannot actually judge
the control, the status says so plainly instead of defaulting to PASS or a
generic FAILED.

  PASS               The test ran and found no problems.
  EXCEPTION          The test ran and found one or more real problems —
                      see this execution's linked exceptions.
  MAPPING_REQUIRED    An active rule exists, but a field it needs isn't
                      approved-mapped yet (or a mapped field's physical
                      column has since disappeared) — nothing was tested.
  NOT_TESTABLE        A required table hasn't even been discovered/connected
                      for this organization yet — the same "nothing was
                      tested" situation as MAPPING_REQUIRED, one step
                      earlier (see get_mapping_readiness's per-object
                      entity_id — None means this, not just unmapped
                      fields on a table that does exist).
  INSUFFICIENT_DATA   The test ran successfully but found zero records to
                      check — there is nothing yet to judge this control
                      by, so this is deliberately not the same as PASS.
  ERROR               A technical problem (connection, timeout, an
                      unexpected exception) stopped the test from running
                      at all — a statement about the platform's ability to
                      reach the data, not a finding about the control.

Stored as a plain string (TestExecution.status is a free-text column, no DB
enum) so a Gateway build that predates a newly added status string is never
rejected — the values below are the current, complete set both
app.services.direct_execution_service and gateway.gateway.main classify
into, and the only ones anything in this codebase currently writes.
"""

PASS = "pass"
EXCEPTION = "exception"
MAPPING_REQUIRED = "mapping_required"
NOT_TESTABLE = "not_testable"
INSUFFICIENT_DATA = "insufficient_data"
ERROR = "error"

# Statuses where a real attempt to run the test happened and either
# succeeded outright or found genuine problems — as opposed to the test
# never actually running (MAPPING_REQUIRED/NOT_TESTABLE) or a purely
# technical interruption (ERROR). Used to keep "currently failing"
# dashboard/UI language scoped to actual control problems, not blockers.
RAN = {PASS, EXCEPTION, INSUFFICIENT_DATA}

# Statuses meaning "this control was never actually tested" — distinct from
# a control that WAS tested and either passed or failed (found exceptions).
BLOCKED = {MAPPING_REQUIRED, NOT_TESTABLE}

# BLOCKED plus ERROR — every status that is neither a real pass nor a real
# exception, so dashboard/UI code has one thing to check against rather
# than re-deriving "not pass and not exception" ad hoc in each place. A
# control in any of these needs a human to look at it; none of them are a
# judgment about whether the control itself is working.
NEEDS_ATTENTION = BLOCKED | {ERROR}

DESCRIPTIONS: dict[str, str] = {
    PASS: "The test ran and found no problems.",
    EXCEPTION: "The test ran and found one or more real problems.",
    MAPPING_REQUIRED: "This control has a rule, but its data isn't fully mapped and approved yet, so nothing has been tested.",
    NOT_TESTABLE: "This control needs a table that hasn't been discovered or connected yet, so it cannot be tested at all.",
    INSUFFICIENT_DATA: "The test ran, but there were no records to check — there is nothing yet to judge this control by.",
    ERROR: "The test could not run because of a technical problem (like a connection failure), not because of anything wrong with the control itself.",
}


def classify_completed_run(*, records_analyzed: int | None, exceptions_found: int) -> str:
    """Called after a rule engine (either app.services.rule_evaluation or
    gateway.gateway.rule_engine) finishes WITHOUT raising — the run itself
    succeeded, so the only question is what it found."""
    if not records_analyzed:
        return INSUFFICIENT_DATA
    if exceptions_found > 0:
        return EXCEPTION
    return PASS
