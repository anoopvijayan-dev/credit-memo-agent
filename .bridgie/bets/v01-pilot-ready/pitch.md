credit-memo-agent v0.1 reads one business current-account statement and drafts a credit memo for your analyst.

Every figure in the memo comes from a computed metrics table and is cited to the statement lines it came from; the draft then stops for your analyst to approve.

It currently reads a five-column CSV (id, date, description, amount, balance); it does not read PDF, CAMT or MT940 files yet.

Price: none set. The pilot is free for one anonymised statement. [GUESS: owner to confirm]

To get it: send one anonymised statement, and your analyst reviews the memo against their own spread. The code is public so you can read how every number is produced.
