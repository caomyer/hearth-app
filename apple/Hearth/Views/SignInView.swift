import HearthKit
import SwiftUI

/// Signing in to a household's books, as the pages' own sign-in does. The engine checks the
/// password; this screen only hands it over, and says nothing about the household.
struct SignInView: View {
    let session: Session
    let problem: Session.SignInProblem?

    @State private var username = ""
    @State private var password = ""
    @FocusState private var focus: Field?

    private enum Field {
        case username, password
    }

    var body: some View {
        VStack(spacing: 0) {
            VStack(spacing: 4) {
                Mark()
                    .foregroundStyle(Palette.ink)
                    .frame(width: 40, height: 40)
                    .padding(.bottom, 8)
                Text("Hearth")
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                Text("Household finances, on this Mac.")
                    .foregroundStyle(Palette.ink2)
            }
            .padding(.bottom, 24)

            VStack(alignment: .leading, spacing: 0) {
                if let problem {
                    Problem(text: Self.sentence(for: problem))
                        .padding(.bottom, 16)
                }
                label("Username")
                TextField("", text: $username)
                    .textContentType(.username)
                    .autocorrectionDisabled()
                    .focused($focus, equals: .username)
                    .modifier(FieldStyle(focused: focus == .username, invalid: false))
                    .accessibilityLabel("Username")
                    .accessibilityIdentifier("signIn.username")
                    .onSubmit { focus = .password }
                label("Password").padding(.top, 14)
                SecureField("", text: $password)
                    .textContentType(.password)
                    .focused($focus, equals: .password)
                    .modifier(FieldStyle(focused: focus == .password, invalid: problem == .rejected))
                    .accessibilityLabel("Password")
                    .accessibilityIdentifier("signIn.password")
                    .onSubmit(submit)
                Button(action: submit) {
                    HStack(spacing: 8) {
                        if session.isSigningIn {
                            ProgressView().controlSize(.small)
                        }
                        Text("Sign in")
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(PrimaryButtonStyle())
                .keyboardShortcut(.defaultAction)
                .disabled(username.trimmingCharacters(in: .whitespaces).isEmpty || password.isEmpty || session.isSigningIn)
                .padding(.top, 22)
                .accessibilityIdentifier("signIn.submit")
            }
            .font(.system(size: 13))
            .padding(24)
            .background(RoundedRectangle(cornerRadius: 10).fill(Palette.surface))
            .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Palette.line, lineWidth: 1))

            Text("Forgot your password? Ask a household admin to reset it.")
                .font(.system(size: 12))
                .foregroundStyle(Palette.ink3)
                .multilineTextAlignment(.center)
                .padding(.top, 16)

            Spacer(minLength: 0)
        }
        .frame(width: 340)
        .padding(.top, 72)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onAppear { focus = .username }
    }

    private func label(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 13, weight: .medium))
            .foregroundStyle(Palette.ink)
            .padding(.bottom, 6)
            .accessibilityHidden(true)
    }

    private func submit() {
        let typed = (username, password)
        guard !typed.0.trimmingCharacters(in: .whitespaces).isEmpty, !typed.1.isEmpty else { return }
        Task {
            await session.signIn(username: typed.0, password: typed.1)
            // Refused: the password is typed again, as the pages ask for it again.
            if case .signIn = session.screen {
                password = ""
                focus = .password
            }
        }
    }

    /// What each refusal says. A wrong password and an unknown username are one sentence,
    /// because the engine does not say which it was.
    static func sentence(for problem: Session.SignInProblem) -> String {
        switch problem {
        case .rejected:
            "That username and password don't match. Check caps lock, or ask an admin to reset it."
        case .throttled:
            "Too many attempts. Wait a few minutes, then try again."
        case .mustChangePassword:
            "That is a one-time password. Choose your own in Hearth in your browser, then sign in here with it."
        case .savedSignInFailed:
            "The password saved on this Mac no longer works. Sign in again."
        }
    }
}

/// Why the last sign-in did not get in, in the pages' error style.
private struct Problem: View {
    let text: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: "xmark.circle")
                .foregroundStyle(Palette.unfavourable)
            Text(text)
                .foregroundStyle(Palette.ink)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.vertical, 10)
        .padding(.leading, 12)
        .padding(.trailing, 14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.errorBackground)
        .overlay(alignment: .leading) {
            Rectangle().fill(Palette.unfavourable).frame(width: 3)
        }
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("signIn.problem")
    }
}
