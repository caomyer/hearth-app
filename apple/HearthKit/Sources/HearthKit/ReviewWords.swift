import Foundation

/// What Review says of a decision, in the review pages' own words where they have them
/// (`hearth.web.routes.review.OUTCOMES`).
public enum ReviewWords {
    static let stopped = "Hearth's engine stopped, so that was not saved. Quit Hearth and open it again."

    /// What a decision did, as the notice under the queue says it.
    public static func notice(for decision: Decision, _ result: ActResult) -> Notice? {
        let label = destination(of: decision)
        switch result.outcome {
        case .accepted:
            if case .accept(let row) = decision, row.isReconciling {
                return Notice(
                    text: "Confirmed. The difference stays booked as it is.",
                    tone: .done, ruling: result.ruling, rows: result.rows)
            }
            let text =
                decision.count == 1 && result.rows == 1
                ? "Accepted as \(label)." : "Accepted \(result.rows.formatted()) as \(label)."
            return Notice(text: text, tone: .done, ruling: result.ruling, rows: result.rows)
        case .booked:
            let text: String
            if case .moveGroup = decision, result.rows > 1 {
                text = "Saved \(result.rows.formatted()) under \(label)."
            } else {
                text = "Saved under \(label)."
            }
            return Notice(text: text, tone: .done, ruling: result.ruling, rows: result.rows)
        case .moved:
            let them = result.rows == 1 ? "It moved" : "They moved"
            let text =
                "Moved to \(label). \(them) between your accounts, so \(result.rows == 1 ? "it is" : "they are") neither spending nor income."
            return Notice(text: text, tone: .done, ruling: result.ruling, rows: result.rows)
        case .excluded:
            return Notice(
                text: "Marked as not a transaction. It books nothing.", tone: .done, ruling: result.ruling,
                rows: result.rows)
        case .changed:
            return Notice(text: changed(result, of: decision), tone: .problem, ruling: result.ruling, rows: result.rows)
        case .notOneDecision:
            return Notice(
                text:
                    "That group is not one kind of transaction, so it cannot be accepted all at once. Its transactions are waiting for a look one by one.",
                tone: .problem)
        case .notAnAccount:
            return Notice(
                text: "That transaction is still waiting. Enter a full category name, like Expenses:Groceries.",
                tone: .problem)
        case .notYours:
            return Notice(
                text:
                    "That transaction is still waiting. It can go to a spending or income category, like Expenses:Groceries, or move to one of your own accounts in Hearth. If the money went to an account Hearth does not see, such as savings at a bank you have not connected or a car loan, name that account in Hearth in the browser.",
                tone: .problem)
        case .banksAccount:
            return Notice(
                text:
                    "That transaction is still waiting. That account's balance is the one its bank reports, so only the bank's own transactions go in it: moving this one there would count money the bank says is not there.",
                tone: .problem)
        case .banksRow:
            return Notice(
                text:
                    "That transaction is still waiting. Its bank counts it in the balance it reports for this account, so Hearth cannot leave it out without disagreeing with the bank. Here it can go to any spending or income category.",
                tone: .problem)
        case .missing:
            return Notice(
                text: "That is no longer waiting for you. Here is what is waiting now.", tone: .problem)
        case .undone, .started, .running, .notAllowed, .signedOut:
            return nil
        }
    }

    /// What taking a decision back did.
    public static func undone(_ result: ActResult) -> Notice? {
        switch result.outcome {
        case .undone:
            let text =
                result.rows == 1
                ? "Taken back. That transaction is waiting for review again."
                : "Taken back. \(result.rows.formatted()) transactions are waiting for review again."
            return Notice(text: text, tone: .done)
        case .missing:
            return Notice(text: "That decision can no longer be taken back.", tone: .problem)
        default:
            return nil
        }
    }

    /// What an Undo would take back, for the person to read before they press it.
    public static func undoHelp(_ notice: Notice) -> String {
        notice.rows == 1
            ? "Take back exactly this decision: the transaction will wait for review again."
            : "Take back exactly this decision: \(notice.rows.formatted()) transactions will wait for review again."
    }

    private static func changed(_ result: ActResult, of decision: Decision) -> String {
        let left = result.changed
        guard result.rows > 0 else {
            return left == 1 && decision.count == 1
                ? "That transaction changed since Hearth showed it, so it was left waiting. Here it is as it is now."
                : "Those changed since Hearth showed them, so they were left waiting. Here they are as they are now."
        }
        let done = "\(result.rows.formatted()) decided."
        let waiting =
            left == 1
            ? "1 changed since Hearth showed it, so it was left waiting."
            : "\(left.formatted()) changed since Hearth showed them, so they were left waiting."
        return "\(done) \(waiting)"
    }

    private static func destination(of decision: Decision) -> String {
        switch decision {
        case .accept(let row): row.suggested?.label ?? ""
        case .acceptGroup(_, let rows): rows.first?.suggested?.label ?? ""
        case .book(_, let to), .moveGroup(_, let to), .moveRow(_, let to): to.label
        case .exclude: ""
        }
    }

    /// A count of transactions: "1 transaction", "1,144 transactions".
    public static func transactions(_ count: Int) -> String {
        count == 1 ? "1 transaction" : "\(count.formatted()) transactions"
    }

    /// What a group's question asks, as the "Where did it go?" page asks it.
    public static func question(_ question: WhereGroup.Question) -> String {
        switch question {
        case .paidFrom: "Paid from?"
        case .paidTo: "Which card did it pay?"
        case .loan: "What is it paying?"
        case .came: "Where did it come from?"
        case .went: "Where did it go?"
        }
    }

    /// The answer that names one of the viewer's own accounts hearth does not see.
    public static func mine(_ question: WhereGroup.Question) -> String {
        switch question {
        case .loan: "A loan of mine that Hearth does not see"
        case .paidTo: "A card of mine that Hearth does not see"
        case .paidFrom, .went, .came: "An account of mine that Hearth does not see"
        }
    }

    /// The answer that names a category.
    public static func other(_ question: WhereGroup.Question) -> String {
        question == .loan ? "A bill" : "Someone else"
    }
}
