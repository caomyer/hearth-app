"""The review of an import's set-aside pile: nothing is booked that nobody checked.

An import sets aside every row it could not book with confidence, each with a proposed
balancing account, the categoriser's confidence, where it came from, and the group of rows like
it. A member reviews only the rows on their own accounts, rules on whole groups at once where
one decision covers a kind of transaction, and corrects or rejects single rows.

Rulings are durable and keyed to what a row says, never to where it sits in a file, so a
re-download that adds or reorders rows cannot move a ruling onto another transaction. A group
ruling covers the rows that were in the group when it was made, and only those: a row that
arrives later waits for someone to look at it. Nothing here books a row nobody ruled on, and
nothing books anything after a delay.

See `docs/review.md`.
"""
