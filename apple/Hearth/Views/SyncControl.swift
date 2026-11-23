import HearthKit
import SwiftUI

/// The toolbar's sync control, on every screen of the books: when the banks were last
/// updated, which opens how each of them stands, and the button that asks them again now.
///
/// With asking switched off it says so, and its button leads to Settings, where it is
/// switched on: nothing here looks like a sync that does not happen.
struct SyncControl: View {
    let model: SyncModel

    @Environment(\.openSettings) private var openSettings
    @State private var showing = false

    var body: some View {
        if model.banks != nil {
            HStack(spacing: 2) {
                Button {
                    showing.toggle()
                } label: {
                    HStack(spacing: 5) {
                        if model.needsAttention {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .foregroundStyle(Palette.warning)
                        }
                        Text(model.label())
                            .foregroundStyle(Palette.ink2)
                            .monospacedDigit()
                    }
                    .padding(.horizontal, 4)
                    .fixedSize()
                }
                .help("How your banks stand")
                // A button in the toolbar is read as one element: its words are the control's.
                .accessibilityLabel(model.label())
                .accessibilityValue(model.needsAttention ? "Needs you" : "")
                .accessibilityIdentifier("sync.status")
                .popover(isPresented: $showing, arrowEdge: .bottom) {
                    SyncDetail(model: model, openSettings: openSettings)
                }
                action
            }
            // A toolbar item keeps the accessibility of the view it first showed, so what it says
            // and offers is a new view each time either changes: VoiceOver, and the tests, then
            // read what is on screen.
            .id(
                Appearance(
                    label: model.label(), attention: model.needsAttention, syncing: model.isSyncing,
                    mayAsk: model.mayAsk))
            .onChange(of: model.answered) {
                // The person asked; what came of it is said where they asked.
                showing = true
            }
        }
    }

    @ViewBuilder
    private var action: some View {
        if model.isSyncing {
            ProgressView()
                .controlSize(.small)
                .frame(width: 22)
                .accessibilityLabel("Updating")
                .accessibilityIdentifier("sync.progress")
        } else if model.mayAsk {
            Button {
                Task { await model.syncNow() }
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .help("Ask your banks for anything new")
            .accessibilityLabel("Update now")
            .accessibilityIdentifier("sync.now")
        } else {
            Button {
                openSettings()
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .help("Hearth is not asking your banks. Turn it on in Settings.")
            .accessibilityLabel("Turn on updates in Settings")
            .accessibilityIdentifier("sync.settings")
        }
    }
}

/// Everything the control says and offers, which is its identity.
private struct Appearance: Hashable {
    let label: String
    let attention: Bool
    let syncing: Bool
    let mayAsk: Bool
}

/// How each of the viewer's banks stands, and what the last sync brought or what went wrong.
private struct SyncDetail: View {
    let model: SyncModel
    let openSettings: OpenSettingsAction

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Your banks")
                .font(.headline)
                .foregroundStyle(Palette.ink)
            if let banks = model.banks {
                if !model.mayAsk {
                    Text("Hearth is not asking your banks for anything. You can let it in Settings.")
                        .foregroundStyle(Palette.ink2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if banks.banks.isEmpty {
                    Text("You have no bank connected. Connect one in Hearth in the browser.")
                        .foregroundStyle(Palette.ink2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                ForEach(banks.banks) { bank in
                    BankLine(bank: bank)
                }
                // Each bank says what it brought; with several, so does the whole.
                if !model.isSyncing, banks.banks.count > 1, let arrived = banks.arrived {
                    Text(arrived)
                        .foregroundStyle(Palette.ink2)
                        .accessibilityIdentifier("sync.arrived")
                }
                ForEach(banks.troubles, id: \.self) { trouble in
                    Label {
                        Text(trouble).fixedSize(horizontal: false, vertical: true)
                    } icon: {
                        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(Palette.warning)
                    }
                    .foregroundStyle(Palette.ink)
                    .accessibilityIdentifier("sync.trouble")
                }
            }
            HStack {
                Spacer()
                if model.mayAsk {
                    Button("Update Now") { Task { await model.syncNow() } }
                        .disabled(!model.canSyncNow)
                        .keyboardShortcut(.defaultAction)
                } else {
                    Button("Open Settings") { openSettings() }
                        .keyboardShortcut(.defaultAction)
                }
            }
        }
        .font(.system(size: 13))
        .padding(16)
        .frame(width: 340, alignment: .leading)
    }
}

private struct BankLine: View {
    let bank: Bank

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(bank.institution)
                .foregroundStyle(Palette.ink)
                .lineLimit(2)
            Spacer(minLength: 12)
            VStack(alignment: .trailing, spacing: 2) {
                Text(bank.freshness())
                    .foregroundStyle(Palette.ink2)
                    .monospacedDigit()
                if let added = bank.added {
                    Text(Banks.count(added))
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.ink3)
                }
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("sync.bank.\(bank.institution)")
    }
}
