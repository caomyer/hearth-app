# Hearth design

High-fidelity design for Hearth: a design system, every screen of the MVP, and the states nobody designs but everyone hits.

Open `index.html` in a browser.
Every file renders straight from disk, with no build step, no server and no network.
Every figure and name is invented.

- `index.html` lists every screen and state.
- `system.html` is the design system: number formatting, type, colour with measured contrast, gain and loss, absence, space, motion, the accessibility floor, components and chart rules.
- `rationale.md` covers what was chosen and rejected, where the design departs from the approved behaviour, and the three places it is still weakest.
- `hearth.css` holds every token and component; `design.js` adds chart tooltips and a light and dark switch for review.
- `screens/` holds the eleven MVP screens; `states/` holds the seven states.
