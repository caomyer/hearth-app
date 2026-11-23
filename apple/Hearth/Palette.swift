import AppKit
import SwiftUI

/// Hearth's colours, from its design system (`design/hearth.css`): the same values, light and
/// dark, so the app and the pages read as one product. Dark is its own measured set, not an
/// inversion.
enum Palette {
    static let ink = dynamic(light: 0x1B1B19, dark: 0xECECE8)
    static let ink2 = dynamic(light: 0x4F4F4A, dark: 0xB9B9B2)
    static let ink3 = dynamic(light: 0x6A6A64, dark: 0x99998F)
    static let line = dynamic(light: 0xE2E2DC, dark: 0x33332F)
    static let surface = dynamic(light: 0xFFFFFF, dark: 0x1C1C1A)
    /// Blue is reserved for "you can act on this"; it never encodes data.
    static let accent = dynamic(light: 0x2851C4, dark: 0x8EACFF)
    static let accentBackground = dynamic(light: 0xEAEFFB, dark: 0x1D2742)
    /// Favourable and unfavourable, not up and down, and never without a glyph and a word.
    static let favourable = dynamic(light: 0x16703E, dark: 0x5FCF8D)
    static let unfavourable = dynamic(light: 0xB3261E, dark: 0xFF8F84)
    static let warning = dynamic(light: 0x8A5100, dark: 0xF1B55A)
    static let warningBackground = dynamic(light: 0xFCF1DA, dark: 0x33280F)
    static let chartLine = dynamic(light: 0x1B1B19, dark: 0xECECE8)
    static let chartGrid = dynamic(light: 0xEBEBE6, dark: 0x2C2C29)

    private static func dynamic(light: UInt32, dark: UInt32) -> Color {
        Color(
            nsColor: NSColor(name: nil) { appearance in
                let isDark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
                return NSColor(hex: isDark ? dark : light)
            })
    }
}

extension NSColor {
    fileprivate convenience init(hex: UInt32) {
        self.init(
            srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
    }
}
