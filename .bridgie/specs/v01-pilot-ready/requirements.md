# Requirements — credit-memo-agent v0.1 pilot readiness

## R1 · ubiquitous · risk: money
THE SYSTEM SHALL compute every monetary figure, ratio and count that appears in a memo in deterministic code (`metrics.py`), never in a language model.

## R2 · ubiquitous · risk: money
THE SYSTEM SHALL attach to every metric the ids of the statement transactions it was computed from, so any figure can be traced to statement lines.

## R3 · event · risk: money
WHEN the model drafts a memo, THE SYSTEM SHALL provide it only the metrics table and the policy result, never the raw transactions.

## R4 · unwanted · risk: money
IF a drafted memo contains a number that is not a metric value (allowing rounding), or a metric value without its [Mn] citation in the same sentence, THEN THE SYSTEM SHALL NOT finalise the memo without flagging it and, after at most two redrafts, presenting it to a human marked as not grounded.

## R5 · state · risk: money
WHILE a memo awaits review, THE SYSTEM SHALL NOT produce a final memo; finalisation SHALL require a recorded human decision with reviewer identity.

## R6 · ubiquitous · risk: data
THE SYSTEM SHALL categorise transactions by deterministic ordered rules; an unmatched credit SHALL be `other`, never guessed.

## R7 · ubiquitous · risk: none
THE SYSTEM SHALL express lending policy as data (YAML) with pass / refer / fail outcomes, not in prompts or code constants.

## R8 · ubiquitous · risk: security
THE SYSTEM SHALL contain no real customer, employer or personal financial data; all committed statements SHALL be synthetic.

## R9 · event · risk: data
WHEN a statement CSV is malformed (missing columns, bad dates, non-numeric amounts, inconsistent balances, duplicate ids), THE SYSTEM SHALL fail with a clear error rather than compute metrics from bad input.

## R10 · ubiquitous · risk: none
THE SYSTEM SHALL run its tests and offline evals in CI without any API key.

## P1 · property
For any synthetic statement from `synth.generate`, every metric in M2, M5, M6, M7, M8, M11, M12 SHALL equal the generator's ground truth within 0.01.
Validates: R1, R6

## P2 · property
For any memo text and metrics table, `guard.check` SHALL flag every number not derivable from a metric value, except integers 0–31 without £ or % and four-digit years.
Validates: R4
