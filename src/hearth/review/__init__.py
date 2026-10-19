"""The review of what an import booked: nothing unchecked passes for checked.

Every imported row is in the books from the moment it arrives. One nobody has checked is
flagged `!`, with a proposed balancing account, the categoriser's confidence where there is
one, where it came from, and the group of rows like it. A member reviews only the rows on their
own accounts, rules on whole groups at once where one decision covers a kind of transaction,
and corrects or rejects single rows.

Rulings are durable and keyed to a row's review id, never to where it sits in a file, so a
re-download that adds or reorders rows cannot move a ruling onto another transaction. Each is
bound to what its row said when it was made, so a row its bank has since changed is unchecked
again rather than silently kept as ruled. A group ruling covers the rows that were in the
group when it was made, and only those: a row that arrives later waits for someone to look at
it. Nothing here marks a row checked that nobody ruled on, and nothing does after a delay.

See `docs/review.md`.
"""
