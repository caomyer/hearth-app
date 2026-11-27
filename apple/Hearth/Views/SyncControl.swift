import HearthKit
import SwiftUI

/// The toolbar's sync control, on every screen of the books: when the banks were last
/// updated, which opens how each of them stands, and the button that asks them again now.
///
/// With asking switched off it says so plainly, as the person's own choice, and its button
/// leads to Settings, where it is switched on: nothing here looks like a sync that does not
/// happen. Only what needs the person, a bank to sign in to again or an update that went
/// wrong, is drawn as a warning.
struct SyncControl: View {
    let model: SyncModel
    /// The sample's banks are invented, and said to be.
    let isSample: Bool

    @Environment(\.openSettings) private var openSettings
    @State private var showing = false

    private var label: String { model.label(sample: isSample) }
    private var attention: Bool { model.needsAttention(sample: isSample) }

    var body: some View {
        if model.banks != nil {
            HStack(spacing: 2) {
                Button {
                    showing.toggle()
                } label: {
                    HStack(spacing: 5) {
                        if attention {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .foregroundStyle(Palette.warning)
                        }
                        Text(label)
                            .foregroundStyle(attention ? Palette.ink : Palette.ink2)
                            .monospacedDigit()
                    }
                    .padding(.horizontal, 4)
                    .fixedSize()
                }
                .help("How your banks stand")
                // A button in the toolbar is read as one element: its words are the control's.
                .accessibilityLabel(label)
                .accessibilityValue(attention ? "Needs you" : "")
                .accessibilityIdentifier("sync.status")
                .popover(isPresented: $showing, arrowEdge: .bottom) {
                    SyncDetail(model: model, isSample: isSample, openSettings: openSettings)
                }
                action
            }
            // A toolbar item keeps the accessibility of the view it first showed, so what it says
            // and offers is a new view each time either changes: VoiceOver, and the tests, then
            // read what is on screen.
            .id(Appearance(label: label, attention: attention, syncing: model.isSyncing, mayAsk: model.mayAsk))
            .onAppear {
                // A debug build asked to show how the banks stand shows it once they are known.
                if DebugLaunch.showSync { showing = true }
            }
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
            // Not a sync that will not happen: the way to where asking is switched on.
            Button {
                openSettings()
            } label: {
                Image(systemName: "gearshape")
            }
            .help("Hearth is not asking your banks. You can let it in Settings.")
            .accessibilityLabel("Open Settings to let Hearth ask your banks")
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
    let isSample: Bool
    let openSettings: OpenSettingsAction

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(isSample ? "The sample's banks" : "Your banks")
                .font(.headline)
                .foregroundStyle(Palette.ink)
                .accessibilityAddTraits(.isHeader)
            if let banks = model.banks {
                if isSample {
                    Text(
                        model.mayAsk
                            ? "They are invented, and nothing reaches Plaid."
                            : "They are invented, and nothing reaches Plaid. Hearth asks them nothing until you let it in Settings."
                    )
                    .foregroundStyle(Palette.ink2)
                    .fixedSize(horizontal: false, vertical: true)
                } else if !model.mayAsk {
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
                ForEach(banks.notes(sample: isSample), id: \.self) { note in
                    Label {
                        Text(note.text).fixedSize(horizontal: false, vertical: true)
                    } icon: {
                        Image(systemName: note.needsYou ? "exclamationmark.triangle.fill" : "info.circle")
                            .foregroundStyle(note.needsYou ? Palette.warning : Palette.ink3)
                    }
                    .foregroundStyle(note.needsYou ? Palette.ink : Palette.ink2)
                    .accessibilityElement(children: .combine)
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
                    .fixedSize()
                if let added = bank.added {
                    Text(Banks.count(added))
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.ink3)
                        .fixedSize()
                }
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("sync.bank.\(bank.institution)")
    }
}
